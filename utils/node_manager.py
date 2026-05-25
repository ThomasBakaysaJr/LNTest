import logging
import re
import time
import random
import subprocess
import docker

from utils.config import cfg

log = logging.getLogger(__name__)
from utils.docker_helpers import get_cc_nodes
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
        Run a botmaster command in the BM container. Returns the keysend send
        time (float) from the __SEND_TS__ marker, or None (warmup/failure).
        '''
        if not self.is_running:
            log.warning(f'Container {self.name} is not running.')
            return None
        exit_code, exec_log = self.container.exec_run(command, workdir=cfg.BOT_MASTER_CONTAINER_DIR)
        output = exec_log.decode('utf-8', 'replace') if exec_log else ''
        if exit_code != 0:
            log.error(f'Command "{command}" failed in container {self.name} with exit code {exit_code}.')
            log.error(f'Error output: {output}')
            return None
        match = re.search(r'__SEND_TS__ (\d+(?:\.\d+)?)', output)
        return float(match.group(1)) if match else None

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
            log.info('Creating innocent and botmaster nodes...')
            # capture output (shown only on failure) to keep the console clean
            result = subprocess.run(
                [cfg.INIT_BOTNET_BASH, f'{total_nodes}', f'{active_nodes}'],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                log.error(f"init_botnet.sh failed with exit code {result.returncode}.")
                if result.stdout.strip():
                    log.error(f"  stdout: {result.stdout.strip()}")
                if result.stderr.strip():
                    log.error(f"  stderr: {result.stderr.strip()}")
                return None
            log.info('Innocent and botmaster nodes created.')
            inno_node = Node(self.inno_name)
            bm_node = Node(self.bm_name)
            self.shm.block_size = self.shm.calculate_blocksize(active_nodes, total_nodes)
            self.nodes[inno_node.name] = inno_node
            self.nodes[bm_node.name] = bm_node
        except Exception as e:
            log.error(f"setup_test: Exception occurred: {e}")
            return None

        # create the node configs
        self.shm.create_status_config(active_nodes, self.shm.block_size)

        # Determine cc_manager behavior
        skip_flag = '1' if mode in ('dlnbot', 'custom') else '0'

        if mode in ('dlnbot', 'custom'):
            # Orchestrator-built modes: containers are independent and funded in
            # one batch later (fund_wallets.sh), so launch them in parallel. SHM
            # buffers are created first since each container writes status on start.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            for counter in range(1, total_nodes + 1):
                self.shm.setup_shm("CC" + str(counter))

            def _launch(counter):
                return counter, subprocess.run(
                    [cfg.CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}', skip_flag],
                    capture_output=True, text=True
                )

            max_workers = min(16, total_nodes)
            log.info(f'Launching {total_nodes} containers in parallel (max_workers={max_workers})...')
            failed = []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(_launch, c) for c in range(1, total_nodes + 1)]
                for fut in as_completed(futures):
                    try:
                        c, res = fut.result()
                        if res.returncode != 0:
                            failed.append(c)
                            log.error(f"CC{c} create failed (exit {res.returncode}): "
                                      f"{(res.stderr or '').strip()[:200]}")
                    except Exception as e:
                        log.error(f"Container launch raised: {e}")
                        failed.append(-1)
            if failed:
                log.error(f"setup_test: {len(failed)} container(s) failed to launch.")
                return None
            # Register Node objects on the main thread (after all are up).
            for counter in range(1, total_nodes + 1):
                self.nodes[f'CC{counter}'] = Node(f'CC{counter}')
        else:
            # dlnbot-formation: serial launch with staggered delays, simulating
            # the real D-LNBot malware pipeline (download client → sync → fund →
            # open channels). Per-node funding stays in create_cc_node.sh because
            # cc_manager forms channels live during this loop.
            counter = 1
            while counter <= total_nodes:
                try:
                    self.shm.setup_shm("CC" + str(counter))
                    log.info(f'Launching CC{counter} ({counter}/{total_nodes})...')
                    # capture output (shown only on failure) to keep the console clean
                    res = subprocess.run(
                        [cfg.CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}', skip_flag],
                        capture_output=True, text=True
                    )
                    if res.returncode != 0:
                        log.error(f"CC{counter} create failed (exit {res.returncode}): "
                                  f"{(res.stderr or res.stdout or '').strip()[:300]}")
                        return None
                    new_node = Node(f'CC{counter}')
                    self.nodes[new_node.name] = new_node
                except Exception as e:
                    log.error(f"setup_test: Exception occurred: {e}")
                    return None

                # Log-normal inter-arrival: median ~30s, clamped to [10, 90]s
                if counter < total_nodes:
                    delay = random.lognormvariate(math.log(30), 0.5)
                    delay = max(10, min(delay, 90))
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

    def rank_nodes_for_takedown(self, strategy='random'):
        '''
        Return all current CC nodes in removal order for a nested takedown sweep,
        ranked ONCE on the intact topology:
          - 'targeted': highest channel-degree first.
          - 'random'  : a single random permutation.
        The sweep removes order[:target] at each percentage, so larger
        percentages strictly contain smaller ones.
        '''
        cc_nodes = []
        while not cc_nodes:
            cc_nodes = self.get_cc_nodes()
            time.sleep(cfg.NM_SLEEP)
        cc_nodes = list(cc_nodes)

        if strategy == 'targeted':
            node_degrees = []
            for node in cc_nodes:
                try:
                    status = self.get_node_status(node.name)
                    degree = len(status['channels']) if status and 'channels' in status else 0
                except Exception:
                    degree = 0
                node_degrees.append((node, degree))
            node_degrees.sort(key=lambda x: -x[1])
            ranked = [nd[0] for nd in node_degrees]
            log.info('Nested targeted takedown order (highest-degree first): '
                     f'{[(n.name, d) for n, d in node_degrees[:10]]} ...')
        else:
            ranked = cc_nodes[:]
            # Fixed seed; isolated RNG so autonomous formation stays nondeterministic.
            random.Random(cfg.TAKEDOWN_SEED).shuffle(ranked)
            log.info(f'Nested random takedown order (seed={cfg.TAKEDOWN_SEED}): '
                     f'{[n.name for n in ranked[:10]]} ...')
        return ranked

    def most_connected_survivor(self):
        '''
        Best injection point among survivors: highest-degree node in the largest
        surviving component (maximizes reach). Deterministic tie-breaks; None if empty.
        '''
        statuses = self.retrieve_all_status()
        survivor_names = self.get_cc_names()
        if not statuses:
            return survivor_names[0] if survivor_names else None

        def cc_index(name):
            m = re.search(r'\d+', name)
            return int(m.group()) if m else 0

        # short_id -> host_name (survivors only; removed peers drop out).
        sid2host = {s.get('short_id'): s.get('host_name')
                    for s in statuses if s.get('short_id') and s.get('host_name')}
        adj = {s['host_name']: set() for s in statuses if s.get('host_name')}
        for s in statuses:
            host = s.get('host_name')
            if host is None:
                continue
            for ch in (s.get('channels') or {}).values():
                if ch.get('state', 'CHANNELD_NORMAL') != 'CHANNELD_NORMAL':
                    continue
                peer = sid2host.get(ch.get('short_id'))
                if peer and peer != host:
                    adj[host].add(peer)
                    adj[peer].add(host)

        if not adj:
            return survivor_names[0] if survivor_names else None

        seen, components = set(), []
        for start in adj:
            if start in seen:
                continue
            stack, comp = [start], []
            while stack:
                u = stack.pop()
                if u in seen:
                    continue
                seen.add(u)
                comp.append(u)
                stack.extend(adj[u] - seen)
            components.append(comp)

        # Largest component, then highest-degree node in it (ties: lowest index).
        largest = max(components, key=lambda c: (len(c), -min(cc_index(x) for x in c)))
        best = max(largest, key=lambda x: (len(adj[x]), -cc_index(x)))
        log.info(f'Most-connected survivor: {best} (degree {len(adj[best])}, '
                 f'largest component {len(largest)}/{len(survivor_names)} survivors).')
        return best

    def execute_takedown(self, config, nodes_to_kill):
        '''
        Shut down a specific list of nodes (one step of a nested takedown sweep).
        The removed names are logged to orchestrator.log. Returns what was shut down.
        '''
        if not nodes_to_kill:
            return []
        log.info(f'Takedown: removing {len(nodes_to_kill)} node(s): {[n.name for n in nodes_to_kill]}')
        self.shutdown_nodes(nodes_to_kill)
        return nodes_to_kill

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

    def get_cc_names(self):
        '''
        Tracked CC node names (no Docker calls). Killed nodes are dropped from
        self.nodes, so this is the live survivor set the propagation loop polls.
        '''
        return [name for name in self.nodes if name.startswith(self.CC_PREFIX)]

    def retrieve_all_status(self):
        '''Delegate to ShmStatus (shared-memory only, no Docker).'''
        return self.shm.retrieve_all_status(self.get_cc_names())

    def get_node_status(self, suffix):
        '''Delegate to ShmStatus.'''
        return self.shm.get_node_status(suffix)

    def are_channels_ready(self):
        '''Delegate to ShmStatus.'''
        return self.shm.are_channels_ready(self.get_cc_names)

    # ── Container operations delegation ──

    def get_cc_nodes(self):
        '''Delegate to docker_helpers (container objects for exec_run; not the hot loop).'''
        return get_cc_nodes(self.nodes, self.CC_PREFIX)

    def kill_node(self, node: Node):
        '''
        Cleanup a single node, unlink its shared memory, and drop it from the tracker.
        '''
        node.kill()
        self.shm.remove_shm(node.name)
        self.nodes.pop(node.name, None)

    def kill_all_nodes(self):
        '''
        Cleanup all nodes and unlink shared memory. Containers are force-removed
        in a single batched `docker rm -f` (much faster than one-at-a-time at large n).
        '''
        nodes = list(self.nodes.values())
        names = [n.name for n in nodes]
        if names:
            try:
                subprocess.run(['docker', 'rm', '-f'] + names, capture_output=True, text=True)
            except Exception as e:
                log.warning(f'kill_all_nodes: batched docker rm failed ({e}); falling back to serial.')
                for node in nodes:
                    self.kill_node(node)
                self.nodes.clear()
                return
        # Unlink shared memory + release each node's docker client.
        for node in nodes:
            try:
                self.shm.remove_shm(node.name)
            except Exception:
                pass
            try:
                node.client.close()
            except Exception:
                pass
            node._container = None
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

        # capture to keep the console clean; surface only errors
        result = subprocess.run([cfg.KILL_NODES_BASH, 'iter'],
                                capture_output=True, text=True)
        if result.returncode != 0:
            log.warning(f"cleanup.sh iter exited {result.returncode}: "
                        f"{(result.stderr or '').strip()}")

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

    def warmup_botmaster_channels(self, inject_nodes=None, inject_count=1):
        '''
        Open the botmaster's channel(s) to the injection target(s) without sending
        a message. Called once during setup so the channel-open cost is not counted
        in the per-command propagation delay measured by the timed loop.
        '''
        bm_node = self.nodes.get(self.bm_name)
        if not bm_node:
            log.error('BotMaster node not found.')
            return None
        if inject_nodes:
            command_str = f"--warmup --node-ids {','.join(inject_nodes)}"
        else:
            command_str = f"--warmup --count {inject_count}"
        command = f'python3 -u {self.bm_script} {command_str}'

        return bm_node.send_botmaster_command(command)
