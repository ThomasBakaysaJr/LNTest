import re
import json
import time
import random
import subprocess
import docker
import os
from dotenv import load_dotenv
from multiprocessing import shared_memory, resource_tracker

load_dotenv('config.env')

SLEEP_INTERVAL = int(os.getenv('NM_SLEEP', 2))  # seconds
MAX_WAIT = int(os.getenv('NM_MAX_WAIT', 450))  # seconds
WAIT_MULT = int(os.getenv('NM_MAX_WAIT_MULT', 2))  # multiplier for wait time

# Directories for NodeManagerComms and BotMasterComms
NODEMAN_CCADDRESS_FILE = os.getenv('NODE_MANAGER_ADDRESS_LIST')
BOTMASTER_CCADDRESS_FILE = os.getenv('BOT_MASTER_ADDRESS_LIST')

KILL_NODES_BASH = os.getenv('KILL_NODES_BASH')
INIT_BOTNET_BASH = os.getenv('INIT_BOTNET_BASH')
CREATE_CC_SERVER_BASH = os.getenv('CREATE_CC_SERVER_BASH')

BM_DIR = os.getenv('BOT_MASTER_CONTAINER_DIR')

NUM_CC = os.getenv('NUM_CC', 'num_cc')
ACTIVE_NODES = os.getenv('ACTIVE_NODES', 'active_nodes')

class Node:
    '''
    Represents a single LN node container.
    Handles shared memory access for status retrieval.
    '''
    def __init__(self, container_name, block_size = 5012):
        self.name = container_name
        self.block_size = block_size
        self.shm_name = f'{self.name}_status'
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
                print(f'Container {self.name} not found.')
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

    def get_node_status(self):
        '''
        Get the status of an individual node.
        If shm doesn't exist or if there is no data stored, returns None
        '''
        try:
            shm = shared_memory.SharedMemory(self.shm_name)
            try:
                resource_tracker.unregister(shm._name, 'shared_memory')
            except Exception:
                pass
            data = shm.buf.tobytes().split(b'\x00', 1)[0]
            shm.close()
            
            if not data:
                return None
            
            return json.loads(data.decode('utf-8'))
                                            
        except FileNotFoundError:
            # shm block doesn't exist or node is dead
            return None
        except Exception as e:
            print(f'get_node_status: ERROR accessing shared memory for {self.name}. Error: {e}')
            return None
    
    def stop(self):
        '''
        Stops the Docker container.
        '''
        if self.is_running:
            self.container.stop()
            print(f'Stopped container {self.name}')
        else:
            print(f'Container {self.name} is not running.')

    def kill(self):
        '''
        Kills and removes the Docker container.
        '''
        
        try:
            if self.container:
                self.container.remove(force=True, v = True)
        except Exception as e:
            print(f'kill: Error killing container {self.name}: {e}')

        if self.client:
            try:
                self.client.close()
            except Exception as e:
                print(f'kill: Error closing Docker client for container {self.name}: {e}')
        
        self._container = None

        print(f'Killed container {self.name}')

    def send_botmaster_command(self, command):
        '''
        Sends a command to the Docker container
        if this is the botmaster.
        '''
        if self.is_running:
            exit_code, exec_log = self.container.exec_run(command, workdir=BM_DIR)
            if exit_code != 0:
                print(f'Command "{command}" failed in container {self.name} with exit code {exit_code}.')
                print(f'Error output: {exec_log.decode("utf-8")}')
        else:
            print(f'Container {self.name} is not running.')

class NodeManager:
    def __init__(self):
        self.node_config_dir = os.getenv('TEST_STATE_DIR')
        # CHANGE to use the OS path making
        self.node_config_path = f'{self.node_config_dir}/node_config.json'
        self.CC_PREFIX = 'CC'
        self.bm_name = os.getenv('BOTMASTER_NODE', 'BM')
        self.bm_script = os.getenv('BOTMASTER_SCRIPT')
        self.inno_name = os.getenv('INNOCENT_NODE', 'InnocentNode')
        self.nodes: dict[str, Node] = {}
        self.shm_blocks = {}  # Keep SHM references alive to prevent Python resource tracker GC
    
    def setup_test(self, total_nodes, active_nodes, topology=None):
        '''
        Setup the number of CC servers needed.
        Returns true when the CC servers have been made.
        
        If topology='chain', CC_Manager is killed in each container immediately
        after creation to prevent autonomous channel formation.
        '''
        try:
            subprocess.run(
                [INIT_BOTNET_BASH, f'{total_nodes}', f'{active_nodes}']
            )
            inno_node = Node(self.inno_name)
            bm_node = Node(self.bm_name)
            self.active_nodes = active_nodes
            self.max_peers = active_nodes * 2
            self.block_size = self.calculate_blocksize(active_nodes, total_nodes)
            self.nodes[inno_node.name] = inno_node
            self.nodes[bm_node.name] = bm_node
        except subprocess.CalledProcessError as e:
            print(f"testsetup_tester failed with exit code {e.returncode}")
            print(f"  setup_test STDOUT: {e.stdout.strip()}")
            print(f"  setup_test STDERR: {e.stderr.strip()}") 
            raise
        except Exception as e:
            print(f"setup_test: Exception occurred: {e}")
            return None

        # create the node configs
        self.create_status_config()

        counter = 1
        skip_flag = '1' if topology in ('chain', 'custom') else '0'
        while counter <= total_nodes:
            try:
                self.setup_shm("CC" + str(counter), True)
                subprocess.run(
                    [CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}', skip_flag]
                )
                new_node = Node(f'CC{counter}')
                self.nodes[new_node.name] = new_node
            except subprocess.CalledProcessError as e:
                print(f"setup_test failed with exit code {e.returncode}")
                print(f"  setup_test STDOUT: {e.stdout.strip()}")
                print(f"  setup_test STDERR: {e.stderr.strip()}")
                raise
            except Exception as e:
                print(f"setup_test: Exception occurred: {e}")
                return None
            counter += 1

        # For chain/custom topology, skip channel readiness - no natural channels exist
        if topology in ('chain', 'custom'):
            print(f'Orchestrator-controlled topology: skipping channel readiness check.', flush=True)
            return True

        # Wait for all channels to be established
        if not self.are_channels_ready():
            print(f'Channels were not ready in time')
            return False

        return True
    
    def create_status_config(self):
        '''
        Create a config file for the nodes for this test run.
        '''
        config_data = {
            'active_nodes' : self.active_nodes,
            'max_peers' : self.active_nodes * 2,
            'block_size' : self.block_size,
            'discovery_rule' : int(os.getenv('DISCOVERY_RULE', 19)),
            'botmaster_rule' : int(os.getenv('BOTMASTER_RULE', 123123)),
            'channel_creation_sleep' : int(os.getenv('NODE_CHANNEL_SLEEP', 10)),
            'status_update_interval' : float(os.getenv('NODE_UPDATE_INTERVAL', 1.5)),
            'channel_balance_counter' : int(os.getenv('NODE_BALANCE_COUNTER', 3)),
            'min_channel_capacity' : int(os.getenv('MIN_CHANNEL_CAPACITY', 50000)),
            'max_channel_capacity' : int(os.getenv('MAX_CHANNEL_CAPACITY', 150000)),
            'sleep_interval' : int(os.getenv('NODE_SLEEP_INTERVAL', 3)),
            'retry_interval' : int(os.getenv('NODE_RETRY_INTERVAL', 5))
        }

        try:
            os.makedirs(self.node_config_dir, exist_ok=True)
            with open(self.node_config_path, 'w') as f:
                json.dump(config_data, f, indent=4)
            print(f'Generated {self.node_config_path} with block size : {self.block_size}')
        except Exception as e:
            print(f'Error generating node status config. {e}')

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
        num_nodes_kill = int(parameters[NUM_CC] * percentage)
        
        # get the list of running CC nodes
        while not cc_nodes:
            cc_nodes = self.get_cc_nodes()
            time.sleep(SLEEP_INTERVAL)
        
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
                        print(f'  Warning: could not query container {node.name}: {e}', flush=True)
                    dead_nodes.append(node_data)
        except Exception as e:
            print(f"run_test: ERROR: Failure in recording takedown nodes, restarting. Error is \n{e}", flush=True)
            return False
            
        # add the nodes we shut down to the config
        config.update({
            'takendown_nodes': dead_nodes
        })
        # disconnect the nodes here
        print(f"Takedown test:", flush=True)
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
        
        print(f'Targeted takedown selecting {num_to_kill} highest-degree nodes:')
        for nd in node_degrees[:num_to_kill]:
            print(f'  {nd[0].name}: {nd[1]} channels')
        
        return selected

    @staticmethod
    def build_chain_edges(n, m):
        '''
        Generate the D-LNBot chain edge set.
        CC_i connects to CC_{max(1, i-m)} through CC_{i-1}.
        Returns a set of (from, to) tuples.
        '''
        edges = set()
        for i in range(2, n + 1):
            for j in range(max(1, i - m), i):
                edges.add((i, j))
        return edges

    @staticmethod
    def load_and_validate_topology(file_path, n):
        '''
        Load a custom topology from a JSON file and validate it.
        
        Expected format:
        {
          "nodes": 50,
          "edges": [[2, 1], [3, 1], [3, 2], ...]
        }
        
        Each [from, to] means CC{from} opens a channel to CC{to}.
        Returns a set of (from, to) tuples.
        '''
        import json as _json
        import os as _os

        if not _os.path.exists(file_path):
            print(f'  ERROR: Topology file not found: {file_path}', flush=True)
            return None

        try:
            with open(file_path, 'r') as f:
                data = _json.load(f)
        except Exception as e:
            print(f'  ERROR: Could not parse topology file: {e}', flush=True)
            return None

        # Validate structure
        if 'edges' not in data:
            print(f'  ERROR: Topology file missing "edges" field.', flush=True)
            return None

        file_nodes = data.get('nodes', n)
        if file_nodes != n:
            print(f'  WARNING: Topology file specifies {file_nodes} nodes but LNTest is running {n} nodes. Using {n}.', flush=True)

        raw_edges = data['edges']
        edges = set()
        skipped_self = 0
        skipped_dup = 0
        skipped_range = 0

        for edge in raw_edges:
            if len(edge) != 2:
                print(f'  WARNING: Skipping malformed edge: {edge}', flush=True)
                continue
            src, dst = int(edge[0]), int(edge[1])

            # Self-loop check
            if src == dst:
                skipped_self += 1
                continue

            # Range check
            if src < 1 or src > n or dst < 1 or dst > n:
                skipped_range += 1
                continue

            # Duplicate check
            if (src, dst) in edges:
                skipped_dup += 1
                continue

            edges.add((src, dst))

        # Print warnings
        if skipped_self > 0:
            print(f'  WARNING: Filtered {skipped_self} self-loop(s).', flush=True)
        if skipped_dup > 0:
            print(f'  WARNING: Filtered {skipped_dup} duplicate edge(s).', flush=True)
        if skipped_range > 0:
            print(f'  WARNING: Filtered {skipped_range} out-of-range edge(s) (valid range: 1-{n}).', flush=True)

        if len(edges) == 0:
            print(f'  ERROR: No valid edges in topology file.', flush=True)
            return None

        # Connectivity check (BFS from node 1)
        from collections import deque
        adj = {i: set() for i in range(1, n + 1)}
        for src, dst in edges:
            adj[src].add(dst)
            adj[dst].add(src)

        visited = set()
        queue = deque([1])
        visited.add(1)
        while queue:
            node = queue.popleft()
            for neighbor in adj[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)

        isolated = [i for i in range(1, n + 1) if i not in visited]
        if isolated:
            print(f'  WARNING: Graph is disconnected. {len(isolated)} node(s) unreachable from CC1: '
                  f'{isolated[:10]}{"..." if len(isolated) > 10 else ""}', flush=True)
            print(f'  These nodes will not receive commands. Proceeding anyway.', flush=True)

        # Summary
        max_degree = max(len(adj[i]) for i in range(1, n + 1))
        min_degree = min(len(adj[i]) for i in range(1, n + 1))
        avg_degree = sum(len(adj[i]) for i in range(1, n + 1)) / n
        print(f'  Loaded {len(edges)} edges for {n} nodes '
              f'(avg_degree={avg_degree:.1f}, min={min_degree}, max={max_degree}).', flush=True)

        return edges

    def build_topology(self, edges):
        '''
        Build an arbitrary topology on a clean network from a set of edges.
        Each edge is a (from, to) tuple where from and to are CC node numbers.
        
        Uses multifundchannel to open all of a node's channels in a single
        on-chain transaction, avoiding UTXO exhaustion issues.
        
        Prerequisites: SKIP_CC_MANAGER=1 was set during setup_test() so
        no autonomous channels exist. This method only opens channels.
        '''
        import json as _json
        
        cc_nodes = self.get_cc_nodes()
        if not cc_nodes:
            print('build_topology: No CC nodes found.', flush=True)
            return False
        
        def cc_num(container):
            return int(container.name.replace('CC', ''))
        cc_nodes_sorted = sorted(cc_nodes, key=cc_num)
        n = len(cc_nodes_sorted)
        
        def get_cli_error(output):
            if output[0]:
                try:
                    err_data = _json.loads(output[0].decode('utf-8'))
                    if 'message' in err_data:
                        return err_data['message']
                except Exception:
                    return output[0].decode('utf-8').strip()[:120]
            if output[1]:
                return output[1].decode('utf-8').strip()[:120]
            return 'unknown'
        
        def mine_blocks(num_blocks=6):
            bitcoin_cli = os.getenv('BITCOIN_CLI')
            bitcoin_dir = os.getenv('BITCOIN_DIR')
            if bitcoin_cli and bitcoin_dir:
                try:
                    result = subprocess.run(
                        [bitcoin_cli, f'-datadir={bitcoin_dir}', '-regtest', 'getnewaddress'],
                        capture_output=True, text=True
                    )
                    if result.returncode == 0:
                        addr = result.stdout.strip()
                        subprocess.run(
                            [bitcoin_cli, f'-datadir={bitcoin_dir}', '-regtest',
                             'generatetoaddress', str(num_blocks), addr],
                            capture_output=True
                        )
                except Exception as e:
                    print(f'    Warning: could not mine blocks: {e}', flush=True)
        
        # ── Phase 1: Gather node info ──
        print(f'  Phase 1: Gathering node info for {n} nodes...', flush=True)
        node_info = {}
        
        for container in cc_nodes_sorted:
            num = cc_num(container)
            try:
                exit_code, output = container.exec_run(
                    'lightning-cli --regtest getinfo', demux=True
                )
                if exit_code == 0 and output[0]:
                    info = _json.loads(output[0].decode('utf-8'))
                    binding = info.get('binding', [{}])
                    addr = binding[0].get('address', '127.0.0.1') if binding else '127.0.0.1'
                    port = binding[0].get('port', 19849 + num) if binding else 19849 + num
                    node_info[num] = {
                        'pubkey': info['id'],
                        'address': f"{info['id']}@{addr}:{port}",
                        'container': container,
                        'name': container.name
                    }
                else:
                    print(f'  ERROR: Could not get info for CC{num}', flush=True)
                    return False
            except Exception as e:
                print(f'  ERROR: Exception getting info for CC{num}: {e}', flush=True)
                return False
        
        print(f'  Target: {len(edges)} edges for {n} nodes', flush=True)
        
        # ── Phase 2: Open channels using multifundchannel ──
        # Group edges by source node so each node opens all its outbound channels in one tx
        from collections import defaultdict
        outbound = defaultdict(list)
        for src, dst in edges:
            outbound[src].append(dst)
        
        print(f'  Phase 2: Opening channels via multifundchannel...', flush=True)
        total_opened = 0
        total_failed = 0
        
        for src_num in sorted(outbound.keys()):
            targets = outbound[src_num]
            from_info = node_info[src_num]
            container = from_info['container']
            
            # Connect to all targets first
            for dst_num in targets:
                to_info = node_info[dst_num]
                container.exec_run(
                    f'lightning-cli --regtest connect {to_info["address"]}', demux=True
                )
            
            # Build multifundchannel destinations
            destinations = []
            for dst_num in targets:
                to_info = node_info[dst_num]
                destinations.append({
                    "id": to_info['pubkey'],
                    "amount": "50000",
                    "push_msat": 25000000
                })
            
            dest_json = _json.dumps(destinations)
            cmd = f"lightning-cli --regtest multifundchannel '{dest_json}'"
            
            exit_code, output = container.exec_run(cmd, demux=True)
            
            target_names = ','.join([f'CC{j}' for j in targets])
            if exit_code == 0:
                total_opened += len(targets)
                print(f'    CC{src_num} -> [{target_names}]: {len(targets)} channels opened', flush=True)
            else:
                err = get_cli_error(output)
                print(f'    CC{src_num} -> [{target_names}]: FAILED - {err}', flush=True)
                total_failed += len(targets)
            
            # Mine after each source node to confirm the funding tx
            mine_blocks(6)
            time.sleep(1)
        
        print(f'  Opened {total_opened} channels ({total_failed} failed).', flush=True)
        
        # Final mining to ensure all channels reach CHANNELD_NORMAL
        print(f'    Mining 20 blocks to finalize...', flush=True)
        mine_blocks(20)
        wait_time = max(15, n // 3)
        print(f'    Waiting {wait_time}s for channels to activate...', flush=True)
        time.sleep(wait_time)
        
        # ── Phase 3: Verify final topology ──
        print(f'  Phase 3: Verifying topology...', flush=True)
        degree_counts = {}
        for num, info in node_info.items():
            try:
                exit_code, output = info['container'].exec_run(
                    'lightning-cli --regtest listpeerchannels', demux=True
                )
                if exit_code == 0 and output[0]:
                    result = _json.loads(output[0].decode('utf-8'))
                    normal = [c for c in result.get('channels', []) if c.get('state') == 'CHANNELD_NORMAL']
                    degree_counts[num] = len(normal)
            except Exception:
                degree_counts[num] = 0
        
        if degree_counts:
            avg = sum(degree_counts.values()) / len(degree_counts)
            # Compute expected degrees from the edge set
            expected_degrees = {i: 0 for i in range(1, n + 1)}
            for src, dst in edges:
                expected_degrees[src] += 1
                expected_degrees[dst] += 1
            expected_avg = sum(expected_degrees.values()) / n
            
            print(f'  Final topology: avg_degree={avg:.1f} (expected={expected_avg:.1f}), '
                  f'min={min(degree_counts.values())}, max={max(degree_counts.values())}', flush=True)
            
            mismatches = 0
            for i in sorted(degree_counts.keys()):
                expected = expected_degrees[i]
                actual = degree_counts[i]
                if actual != expected:
                    mismatches += 1
                    print(f'    CC{i}: {actual} channels MISMATCH (expected {expected})', flush=True)
            
            # Show edge nodes for reference
            edge_nodes = [i for i in sorted(degree_counts.keys()) 
                         if degree_counts[i] != max(degree_counts.values())]
            if edge_nodes and mismatches == 0:
                # Show first/last few nodes
                show = edge_nodes[:3] + edge_nodes[-3:] if len(edge_nodes) > 6 else edge_nodes
                for i in show:
                    print(f'    CC{i}: {degree_counts[i]} channels OK', flush=True)
            
            if mismatches == 0:
                print(f'  All nodes match expected topology!', flush=True)
            else:
                print(f'  {mismatches} nodes have mismatched channel counts.', flush=True)
        
        print(f'  Topology build complete.', flush=True)
        return True

    def retrieve_all_status(self):
        '''
        Retrieve all running CC container statuses from shared memory
        Returns all statuses in a list
        '''
        nodes = self.get_cc_nodes()
        all_status = list()

        for cont in nodes:
            node_name = cont.name
            try:
                status = self.get_node_status(node_name)
                if not status:
                    continue

                all_status.append(status)
            except Exception as e:
                print(f'retrieve_all_status: {node_name} failed to retrived shm because {e}\nRecreating shm.')
                self.setup_shm(node_name, True)
                continue
        return all_status
    
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
                    print(f'retrieve_all_status: Error: json data is \n{data} with error: {e}')
                    return None
                return status
            else:
                return None

    def setup_shm(self, suffix, first_block = False):
        '''
        Setup the shm block for this node using incoming suffix counter
        Make sure node_name and block_size matches the name and block_size in ln_checker.
        Unregisters from Python's resource_tracker to prevent automatic cleanup.
        Explicit cleanup is handled by remove_shm() and cleanup_lightning_nodes.sh.
        '''
        # shadowing to make sure ti works
        node_name = f'{suffix}_status'
        if first_block:
            # this will be creating the first memory buffer
            print(f'Creating shared memory buffer for {node_name}')


        try:
            shm = shared_memory.SharedMemory(name=node_name, create=True, size=self.block_size)
            # Prevent Python's resource_tracker daemon from auto-unlinking this block.
            # Without this, the tracker destroys blocks ~2-3 min after creation,
            # which kills propagation monitoring for slow topologies (e.g. m=1 chains).
            resource_tracker.unregister(shm._name, 'shared_memory')
            self.shm_blocks[node_name] = shm  # Keep reference alive
        except FileExistsError:
            # Found a block by this name still, probably from bad cleanup. Clear and prepare it again
            print(f'setup_shm: warning: Shared memory block found for {node_name}.')
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
            print(f'remove_shm: ERROR in cleaning up memory. Error: {e}')


    def are_channels_ready(self):
        '''
        Wait for channel creation between nodes to finish
        Returns:
            Returns True when channels has finished creating
            False when waiting time has exceeded MAX_WAIT
        '''
        start_time = time.time()
        
        while True:
            time.sleep(SLEEP_INTERVAL)
            all_status = self.retrieve_all_status()

            if self.is_kill_time(start_time, MAX_WAIT * WAIT_MULT):
                return False
            if len(self.get_cc_nodes()) == len(all_status) and all_status:
                channels_created = True
                # if a single channel is not online, then channels create will be false and we sleep
                for status in all_status:
                    if status.get('state') != 'connected':
                        channels_created = False
                        break

                if channels_created:
                    return True

    def shutdown_nodes(self, nodes):
        '''
        Shutdown these nodes. 
        Remove them from the tracker so that the tester won't
        wait for them. Will also unlink them from shared memory
        '''
        print(f'Shutting down nodes. Nodes being shut down are:\n\
            {[node.name for node in nodes]}')
        
        # stop nodes and remove them from shared memory.
        for node in nodes:
            self.kill_node(node)
            
        # ad hoc solution for removing from cc_list
        # eventually plan to have just one list and then simply mount that
        # to all containers.
        node_names = [node.name for node in nodes]
        
        with open(NODEMAN_CCADDRESS_FILE, 'r') as file:
            cc_file = file.readlines()
        
        new_cc_list = []
        for line in cc_file:
            name = line.split()[0]
            if name not in node_names:
                new_cc_list.append(line)
        
        with open(NODEMAN_CCADDRESS_FILE, 'w') as file:
            file.write(''.join(new_cc_list))
        with open(BOTMASTER_CCADDRESS_FILE, 'w') as file:
            file.write(''.join(new_cc_list))

    def sort_containers(self, in_containers):
        '''
        Takes in a set of containers and returns the list sorted alphabetically and numerically 
        (ensures that cc15 comes after cc9) and non numbered containers at the end.
        '''
        container_dict = {}
        non_numbered_containers = list()

        for container in in_containers:
            if 'CC' not in container.name:
                non_numbered_containers.append(container)
                continue

            idx = re.findall(r'\d+', container.name)
            idx = int(idx[0])
            container_dict[idx] = container

        sorted_container_dict = dict(sorted(container_dict.items()))
        return_set = list(sorted_container_dict.values())
        return_set = return_set + non_numbered_containers

        # check to make sure we're not losing any containers
        assert len(return_set) == len(in_containers)

        return return_set

    def calculate_blocksize(self, active_nodes, total_nodes):
        '''
        Calculate the size of the shm blocks for this test run.
        With sequential creation, each CC has at most:
          m outbound + m inbound + 1 innocent + 1 BM + 1 transition margin = 2*m + 3
        For small m (especially m=1), dev-fast-gossip causes nodes to accumulate
        extra channels via gossip discovery, so we enforce a minimum of 20 channel slots.
        '''
        overhead_size = int(os.getenv('SHM_OVERHEAD', 512))
        per_peer_size = int(os.getenv('SHM_PER_PEER', 256))
        buffer = float(os.getenv('SHM_BUFFER', 1.2))
        # 2*m channels + 6 for safety, but at least 20 slots for small-m gossip effects
        max_channels = max(active_nodes * 2 + 6, 20)

        return int((overhead_size + (max_channels * per_peer_size)) * buffer)

    def kill_node(self, node : Node):
        '''
        Cleanup a single node and unlink the shared memory.
        Remove from nodes tracker.
        '''
        node.kill()
        self.remove_shm(node.name)
            
    def kill_all_nodes(self):
        '''
        Cleanup all nodes and unlink shared memory
        '''
        for node in self.nodes.values():
            self.kill_node(node)
        self.nodes.clear()

    def get_all_nodes(self):
        '''
        Return a snapshot of the current active containers.
        '''
        nodes = [node.container for node in self.nodes.values() if node.is_running]
        return nodes
    
    def get_cc_nodes(self):
        '''
        Return a list of all active CC nodes.
        '''
        nodes = self.get_all_nodes()
        nodes = [node for node in nodes if node.name.startswith(self.CC_PREFIX)]

        return nodes

    def cleanup_test(self):
        '''
        Cleanup all nodes and shared memory.
        '''
        self.kill_all_nodes()

        subprocess.run([KILL_NODES_BASH])
    
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
        
    
    def send_botmaster_command(self, message, seeds, position):
        '''
        Sends a command to the BotMaster container.
        '''

        bm_node = self.nodes.get(self.bm_name)
        if not bm_node:
            print(f'BotMaster node not found.')
            return None
        command_str = (f"--msg {message} --cc {seeds} --init {position}")
        command = f'python3 -u {self.bm_script} {command_str}'

        return bm_node.send_botmaster_command(command)