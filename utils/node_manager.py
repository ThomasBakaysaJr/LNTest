import logging
import time
import random
import subprocess
import docker

from utils.config import cfg

log = logging.getLogger(__name__)
from utils.docker_helpers import get_all_nodes, get_cc_nodes, sort_containers
from utils.shm_status import ShmStatus
from utils import topology

CC_COUNT = 'cc_count'
ACTIVE_NODES = 'active_nodes'

class Node:
    '''
    Represents a single LN node container.
    '''
    def __init__(self, container_name):
        self.name = container_name
        self.client = docker.from_env()
        self._container = None

    @property
    def container(self):
        '''
        Lazy loads and returns the Docker container object.
        '''
        if self._container is None:
            try:
                self._container = self.client.containers.get(self.name)
            except docker.errors.NotFound:
                log.warning(f'Container {self.name} not found.')
                return None
        return self._container

    @property
    def is_running(self):
        '''
        Check if the container is running.
        Refreshes the container status before checking.
        '''
        if self.container:
            self.container.reload()
            return self.container.status == 'running'
        return False

    def stop(self):
        '''
        Stops the Docker container.
        '''
        if self.is_running:
            self.container.stop()
            log.info(f'Stopped container {self.name}')
        else:
            log.info(f'Container {self.name} is not running.')

    def kill(self):
        '''
        Kills and removes the Docker container.
        '''

        try:
            if self.container:
                self.container.remove(force=True, v = True)
        except Exception as e:
            log.error(f'kill: Error killing container {self.name}: {e}')

        if self.client:
            try:
                self.client.close()
            except Exception as e:
                log.error(f'kill: Error closing Docker client for container {self.name}: {e}')

        self._container = None

        log.info(f'Killed container {self.name}')

    def send_botmaster_command(self, command):
        '''
        Sends a command to the Docker container
        if this is the botmaster.
        '''
        if self.is_running:
            exit_code, exec_log = self.container.exec_run(command, workdir=cfg.BOT_MASTER_CONTAINER_DIR)
            if exit_code != 0:
                log.error(f'Command "{command}" failed in container {self.name} with exit code {exit_code}.')
                log.error(f'Error output: {exec_log.decode("utf-8")}')
        else:
            log.warning(f'Container {self.name} is not running.')

class NodeManager:
    def __init__(self):
        self.node_config_dir = cfg.TEST_STATE_DIR
        self.node_config_path = f'{self.node_config_dir}/node_config.json'
        self.CC_PREFIX = 'CC'
        self.bm_name = cfg.BOTMASTER_NODE
        self.bm_script = cfg.BOTMASTER_SCRIPT
        self.inno_name = cfg.INNOCENT_NODE
        self.nodes: dict[str, Node] = {}
        self.shm = ShmStatus()

    def setup_test(self, total_nodes, active_nodes, mode='dlnbot'):
        '''
        Setup the number of CC servers needed.
        Returns true when the CC servers have been made.

        Modes:
          'dlnbot' or 'custom': SKIP_CC_MANAGER=1, launch all at once.
          'dlnbot-formation': SKIP_CC_MANAGER=0, stagger container launches
              to simulate realistic malware setup delay (SEI model).
        '''
        import math
        try:
            subprocess.run(
                [cfg.INIT_BOTNET_BASH, f'{total_nodes}', f'{active_nodes}']
            )
            inno_node = Node(self.inno_name)
            bm_node = Node(self.bm_name)
            self.active_nodes = active_nodes
            self.max_peers = active_nodes * 2
            self.shm.block_size = self.shm.calculate_blocksize(active_nodes, total_nodes)
            self.nodes[inno_node.name] = inno_node
            self.nodes[bm_node.name] = bm_node
        except subprocess.CalledProcessError as e:
            log.error(f"testsetup_tester failed with exit code {e.returncode}")
            log.error(f"  setup_test STDOUT: {e.stdout.strip()}")
            log.error(f"  setup_test STDERR: {e.stderr.strip()}")
            raise
        except Exception as e:
            log.error(f"setup_test: Exception occurred: {e}")
            return None

        # create the node configs
        self.shm.create_status_config(active_nodes, self.shm.block_size)

        # Determine cc_manager behavior
        skip_flag = '1' if mode in ('dlnbot', 'custom') else '0'

        counter = 1
        while counter <= total_nodes:
            try:
                self.shm.setup_shm("CC" + str(counter), True)
                subprocess.run(
                    [cfg.CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}', skip_flag]
                )
                new_node = Node(f'CC{counter}')
                self.nodes[new_node.name] = new_node
            except subprocess.CalledProcessError as e:
                log.error(f"setup_test failed with exit code {e.returncode}")
                log.error(f"  setup_test STDOUT: {e.stdout.strip()}")
                log.error(f"  setup_test STDERR: {e.stderr.strip()}")
                raise
            except Exception as e:
                log.error(f"setup_test: Exception occurred: {e}")
                return None

            # Stagger launches for dlnbot-formation mode
            # Simulates variable LN setup time from D-LNBot's malware pipeline:
            # download LN client → sync blockchain → fetch funding → open channels
            # Log-normal distribution: median ~30s, range ~10-90s on regtest
            if mode == 'dlnbot-formation' and counter < total_nodes:
                delay = random.lognormvariate(math.log(30), 0.5)
                delay = max(10, min(delay, 90))  # clamp to [10, 90]s
                log.info(f'  Staggered launch: waiting {delay:.0f}s before next container (simulating LN setup delay)...')
                time.sleep(delay)

            counter += 1

        # For orchestrator-controlled modes, skip channel readiness
        if mode in ('dlnbot', 'custom'):
            log.info('Orchestrator-controlled topology: skipping channel readiness check.')
            return True

        # dlnbot-formation: wait for cc_manager to establish channels
        if not self.are_channels_ready():
            log.warning('Channels were not ready in time')
            return False

        return True

    def create_status_config(self):
        '''Delegate to ShmStatus.'''
        self.shm.create_status_config(self.active_nodes, self.shm.block_size)

    def takedown(self, config, percentage, strategy='random'):
        '''
        Takedown section for taking down a percentage of nodes.
        Will append takedown nodes to the config.
        Args:
            percentage: float, percentage of nodes to take down (e.g., 0.1 for 10%)
            strategy: 'random' for random selection, 'targeted' for highest-degree nodes
        '''
        import json as _json
        cc_nodes = []
        parameters = config['parameters']
        num_nodes_kill = int(parameters[CC_COUNT] * percentage)

        # get the list of running CC nodes
        while not cc_nodes:
            cc_nodes = self.get_cc_nodes()
            time.sleep(cfg.NM_SLEEP)

        if strategy == 'targeted':
            nodes_to_kill = self._select_highest_degree(cc_nodes, num_nodes_kill)
        else:
            nodes_to_kill = random.sample(list(cc_nodes), num_nodes_kill)

        # Record info about nodes being shut down.
        # Try SHM first; if empty (orchestrator-controlled topology), query containers directly.
        try:
            dead_nodes = []
            for node in nodes_to_kill:
                status = self.get_node_status(node.name)
                if status and status.get('channels') is not None:
                    dead_nodes.append({
                        'short_id': status.get('short_id'),
                        'host_name': status.get('host_name'),
                        'channels': status.get('channels')
                    })
                else:
                    # SHM not populated — query container directly
                    node_data = {'host_name': node.name, 'channels': {}}
                    try:
                        ec, out = node.exec_run('lightning-cli --regtest getinfo', demux=True)
                        if ec == 0 and out[0]:
                            info = _json.loads(out[0].decode('utf-8'))
                            node_data['short_id'] = info.get('id', '')[-8:]

                        ec, out = node.exec_run('lightning-cli --regtest listpeerchannels', demux=True)
                        if ec == 0 and out[0]:
                            result = _json.loads(out[0].decode('utf-8'))
                            for ch in result.get('channels', []):
                                if ch.get('state') == 'CHANNELD_NORMAL':
                                    peer_id = ch.get('peer_id', '')
                                    node_data['channels'][peer_id] = {
                                        'short_id': peer_id[-8:],
                                        'state': ch.get('state'),
                                        'capacity': ch.get('total_msat', 0) // 1000 if ch.get('total_msat') else 0,
                                        'our_amount': ch.get('to_us_msat', 0) // 1000 if ch.get('to_us_msat') else 0
                                    }
                    except Exception as e:
                        log.warning(f'  Could not query container {node.name}: {e}')
                    dead_nodes.append(node_data)
        except Exception as e:
            log.error(f"Failure in recording takedown nodes, restarting. Error: {e}")
            return False

        # add the nodes we shut down to the config
        config.update({
            'takendown_nodes': dead_nodes
        })
        # disconnect the nodes here
        log.info("Takedown test:")
        self.shutdown_nodes(nodes_to_kill)
        return True

    def _select_highest_degree(self, cc_nodes, num_to_kill):
        '''
        Select the nodes with the highest channel degree (most connections).
        These are typically the early nodes in sequential creation that accumulate
        many inbound connections, making them high-value targets for takedown.
        '''
        node_degrees = []
        for node in cc_nodes:
            try:
                status = self.get_node_status(node.name)
                if status and 'channels' in status:
                    degree = len(status['channels'])
                else:
                    degree = 0
                node_degrees.append((node, degree))
            except Exception:
                node_degrees.append((node, 0))

        # Sort by degree descending, take top num_to_kill
        node_degrees.sort(key=lambda x: -x[1])
        selected = [nd[0] for nd in node_degrees[:num_to_kill]]

        log.info(f'Targeted takedown selecting {num_to_kill} highest-degree nodes:')
        for nd in node_degrees[:num_to_kill]:
            log.info(f'  {nd[0].name}: {nd[1]} channels')

        return selected

    # ── Topology delegation ──

    @staticmethod
    def build_chain_edges(n, m):
        '''Delegate to topology module.'''
        return topology.build_chain_edges(n, m)

    @staticmethod
    def load_and_validate_topology(file_path, n):
        '''Delegate to topology module.'''
        return topology.load_and_validate_topology(file_path, n)

    def build_topology(self, edges):
        '''Delegate to topology module.'''
        cc_nodes = self.get_cc_nodes()
        return topology.build_topology(edges, cc_nodes)

    # ── Status delegation ──

    def retrieve_all_status(self):
        '''Delegate to ShmStatus.'''
        cc_nodes = self.get_cc_nodes()
        return self.shm.retrieve_all_status(cc_nodes)

    def get_node_status(self, suffix):
        '''Delegate to ShmStatus.'''
        return self.shm.get_node_status(suffix)

    def are_channels_ready(self):
        '''Delegate to ShmStatus.'''
        return self.shm.are_channels_ready(self.get_cc_nodes)

    # ── Container operations delegation ──

    def get_all_nodes(self):
        '''Delegate to docker_helpers.'''
        return get_all_nodes(self.nodes)

    def get_cc_nodes(self):
        '''Delegate to docker_helpers.'''
        return get_cc_nodes(self.nodes, self.CC_PREFIX)

    def sort_containers(self, in_containers):
        '''Delegate to docker_helpers.'''
        return sort_containers(in_containers)

    def kill_node(self, node: Node):
        '''
        Cleanup a single node and unlink the shared memory.
        Remove from nodes tracker.
        '''
        node.kill()
        self.shm.remove_shm(node.name)

    def kill_all_nodes(self):
        '''
        Cleanup all nodes and unlink shared memory.
        '''
        for node in self.nodes.values():
            self.kill_node(node)
        self.nodes.clear()

    def shutdown_nodes(self, nodes):
        '''
        Shutdown these nodes.
        Remove them from the tracker so that the tester won't
        wait for them. Will also unlink them from shared memory.
        '''
        log.info(f'Shutting down nodes: {[node.name for node in nodes]}')

        # stop nodes and remove them from shared memory.
        for node in nodes:
            self.kill_node(node)

        # ad hoc solution for removing from cc_list
        # eventually plan to have just one list and then simply mount that
        # to all containers.
        node_names = [node.name for node in nodes]

        with open(cfg.NODE_MANAGER_ADDRESS_LIST, 'r') as file:
            cc_file = file.readlines()

        new_cc_list = []
        for line in cc_file:
            name = line.split()[0]
            if name not in node_names:
                new_cc_list.append(line)

        with open(cfg.NODE_MANAGER_ADDRESS_LIST, 'w') as file:
            file.write(''.join(new_cc_list))
        with open(cfg.BOT_MASTER_ADDRESS_LIST, 'w') as file:
            file.write(''.join(new_cc_list))

    def cleanup_test(self):
        '''
        Cleanup all nodes and shared memory.
        '''
        self.kill_all_nodes()

        subprocess.run([cfg.KILL_NODES_BASH, 'nodes'])

    # will enventually goto into a utils file
    def is_kill_time(self, start_time, wait_time):
        '''
        Determine whether too much time has elapsed and that we should kill this iteration.
        Args:
            start_time: Starting time to calculate against
            wait_time: how long to wait
        Returns:
            Returns whether the time elapsed has gone over MAX_WAIT time.
        '''
        if (time.time() - start_time) >= wait_time:
            return True
        else:
            return False


    def send_botmaster_command(self, message, inject_nodes=None, inject_count=1):
        '''
        Sends a command to the BotMaster container.
        Args:
            message: The message/command to send.
            inject_nodes: List of CC node names (e.g., ['CC5', 'CC12']). If provided,
                          botmaster connects to these specific nodes.
            inject_count: Number of random CC nodes to connect to (used when inject_nodes is None).
        '''

        bm_node = self.nodes.get(self.bm_name)
        if not bm_node:
            log.error('BotMaster node not found.')
            return None
        if inject_nodes:
            command_str = f"--msg {message} --node-ids {','.join(inject_nodes)}"
        else:
            command_str = f"--msg {message} --count {inject_count}"
        command = f'python3 -u {self.bm_script} {command_str}'

        return bm_node.send_botmaster_command(command)
