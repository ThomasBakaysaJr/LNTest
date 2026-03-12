import json
import logging
import os
import time
from multiprocessing import shared_memory, resource_tracker

from utils.config import cfg

log = logging.getLogger(__name__)


class ShmStatus:
    def __init__(self):
        self.shm_blocks = {}  # Keep SHM references alive to prevent Python resource tracker GC
        self.block_size = 5012  # default, overridden by calculate_blocksize()
        self.node_config_dir = cfg.TEST_STATE_DIR
        self.node_config_path = f'{self.node_config_dir}/node_config.json'

    def create_status_config(self, active_nodes, block_size):
        '''
        Create a config file for the nodes for this test run.
        '''
        config_data = {
            'active_nodes' : active_nodes,
            'max_peers' : active_nodes * 2,
            'block_size' : block_size,
            'discovery_rule' : cfg.DISCOVERY_RULE,
            'botmaster_rule' : cfg.BOTMASTER_RULE,
            'channel_creation_sleep' : cfg.NODE_CHANNEL_SLEEP,
            'status_update_interval' : cfg.NODE_UPDATE_INTERVAL,
            'channel_balance_counter' : cfg.NODE_BALANCE_COUNTER,
            'min_channel_capacity' : cfg.MIN_CHANNEL_CAPACITY,
            'max_channel_capacity' : cfg.MAX_CHANNEL_CAPACITY,
            'sleep_interval' : cfg.NODE_SLEEP_INTERVAL,
            'retry_interval' : cfg.NODE_RETRY_INTERVAL
        }

        try:
            os.makedirs(self.node_config_dir, exist_ok=True)
            with open(self.node_config_path, 'w') as f:
                json.dump(config_data, f, indent=4)
            log.info(f'Generated {self.node_config_path} with block size : {block_size}')
        except Exception as e:
            log.error(f'Error generating node status config. {e}')

    def calculate_blocksize(self, active_nodes, total_nodes):
        '''
        Calculate the size of the shm blocks for this test run.
        With sequential creation, each CC has at most:
          m outbound + m inbound + 1 innocent + 1 BM + 1 transition margin = 2*m + 3
        For small m (especially m=1), dev-fast-gossip causes nodes to accumulate
        extra channels via gossip discovery, so we enforce a minimum of 20 channel slots.
        '''
        overhead_size = cfg.SHM_OVERHEAD
        per_peer_size = cfg.SHM_PER_PEER
        buffer = cfg.SHM_BUFFER
        # 2*m channels + 6 for safety, but at least 20 slots for small-m gossip effects
        max_channels = max(active_nodes * 2 + 6, 20)

        return int((overhead_size + (max_channels * per_peer_size)) * buffer)

    def setup_shm(self, suffix, first_block=False):
        '''
        Setup the shm block for this node using incoming suffix counter.
        Make sure node_name and block_size matches the name and block_size in ln_checker.
        Unregisters from Python's resource_tracker to prevent automatic cleanup.
        Explicit cleanup is handled by remove_shm() and scripts/cleanup.sh.
        '''
        node_name = f'{suffix}_status'
        if first_block:
            log.info(f'Creating shared memory buffer for {node_name}')

        try:
            shm = shared_memory.SharedMemory(name=node_name, create=True, size=self.block_size)
            # Prevent Python's resource_tracker daemon from auto-unlinking this block.
            # Without this, the tracker destroys blocks ~2-3 min after creation,
            # which kills propagation monitoring for slow topologies (e.g. m=1 chains).
            resource_tracker.unregister(shm._name, 'shared_memory')
            self.shm_blocks[node_name] = shm  # Keep reference alive
        except FileExistsError:
            # Found a block by this name still, probably from bad cleanup. Clear and prepare it again
            log.warning(f'setup_shm: Shared memory block found for {node_name}.')
            if first_block:
                # if first_block is true, we want this to the first block of memory
                # so get rid of anything that may be here and re-create it.
                temp_shm = shared_memory.SharedMemory(name=node_name)
                temp_shm.unlink()
                # recreate memory block
                shm = shared_memory.SharedMemory(name=node_name, create=True, size=self.block_size)
                resource_tracker.unregister(shm._name, 'shared_memory')
                self.shm_blocks[node_name] = shm  # Keep reference alive

    def remove_shm(self, suffix):
        node_name = f'{suffix}_status'
        # Close and remove stored reference first
        if node_name in self.shm_blocks:
            try:
                self.shm_blocks[node_name].close()
            except Exception:
                pass
            del self.shm_blocks[node_name]
        try:
            shm = shared_memory.SharedMemory(name=node_name)
            shm.unlink()
        except FileNotFoundError:
            # we don't care if the file doesn't exists, something else probably took care of it
            pass
        except Exception as e:
            log.error(f'remove_shm: Error cleaning up memory: {e}')

    def get_node_status(self, suffix):
        '''
        Get the status of an individual node.
        If shm doesn't exist or if there is no data stored, returns None
        '''
        node_name = f'{suffix}_status'
        shm = shared_memory.SharedMemory(name=node_name)
        try:
            resource_tracker.unregister(shm._name, 'shared_memory')
        except Exception:
            pass
        data = shm.buf.tobytes().split(b'\x00', 1)[0]
        shm.close()

        if data:
            try:
                status = json.loads(data.decode('utf-8'))
            except Exception as e:
                log.error(f'retrieve_all_status: Error: json data is \n{data} with error: {e}')
                return None
            return status
        else:
            return None

    def retrieve_all_status(self, cc_nodes):
        '''
        Retrieve all running CC container statuses from shared memory.
        Returns all statuses in a list.
        '''
        all_status = list()

        for cont in cc_nodes:
            node_name = cont.name
            try:
                status = self.get_node_status(node_name)
                if not status:
                    continue

                all_status.append(status)
            except Exception as e:
                log.warning(f'retrieve_all_status: {node_name} failed to retrieve shm because {e}. Recreating shm.')
                self.setup_shm(node_name, True)
                continue
        return all_status

    def are_channels_ready(self, cc_nodes_fn):
        '''
        Wait for channel creation between nodes to finish.
        Args:
            cc_nodes_fn: callable that returns current CC node containers
        Returns:
            True when channels have finished creating,
            False when waiting time has exceeded MAX_WAIT
        '''
        start_time = time.time()

        while True:
            time.sleep(cfg.NM_SLEEP)
            cc_nodes = cc_nodes_fn()
            all_status = self.retrieve_all_status(cc_nodes)

            if (time.time() - start_time) >= cfg.NM_MAX_WAIT * cfg.NM_MAX_WAIT_MULT:
                return False
            if len(cc_nodes) == len(all_status) and all_status:
                channels_created = True
                # if a single channel is not online, then channels create will be false and we sleep
                for status in all_status:
                    if status.get('state') != 'connected':
                        channels_created = False
                        break

                if channels_created:
                    return True
