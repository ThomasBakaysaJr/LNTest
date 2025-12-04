import re
import json
import time
import random
import subprocess
import docker
from multiprocessing import shared_memory

SLEEP_INTERVAL = 2  # seconds
MAX_WAIT = 450  # seconds
WAIT_MULT = 2  # multiplier for wait time

# Directories for NodeManagerComms and BotMasterComms
NODEMAN_CCADDRESS_FILE = "NodeManagerComms/CC_address_list.txt"
BOTMASTER_CCADDRESS_FILE = "BotMasterComms/CC_address_list.txt"

KILL_NODES_BASH = './kill_nodes.sh'
INIT_BOTNET_BASH = './init_botnet.sh'
CREATE_CC_SERVER_BASH = './3create_CC_nodesV3.sh'

BM_DIR = '/root/botmaster'

NUM_CC = 'num_cc'
ACTIVE_NODES = 'active_nodes'

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
        self.node_config_path = 'testState/node_config.json'
        self.CC_PREFIX = 'CC'
        self.bm_name = 'BM'
        self.bm_script = 'BM.py'
        self.inno_name = 'InnocentNode'
        self.block_size = 5012  # bytes
        self.nodes: dict[str, Node] = {}
    
    def setup_test(self, total_nodes, active_nodes):
        ''''
        setup the number of CC servers needed
        returns true when the the cc servers have been made
        '''
        try:
            subprocess.run(
                [INIT_BOTNET_BASH, f'{total_nodes}', f'{active_nodes}']
            )
            inno_node = Node(self.inno_name)
            bm_node = Node(self.bm_name)
            self.active_nodes = active_nodes
            self.max_peers = active_nodes * 2
            self.block_size = self.calculate_blocksize(active_nodes)
            self.nodes[inno_node.name] = inno_node
            self.nodes[bm_node.name] = bm_node
        except subprocess.CalledProcessError as e:
            # This is where the error from lightning-cli lives!
            print(f"testsetup_tester failed with exit code {e.returncode}")
            print(f"  setup_test STDOUT: {e.stdout.strip()}")
            print(f"  setup_test STDERR: {e.stderr.strip()}") 
            raise # Re-raise the exception so your calling code can catch it
        except Exception as e:
            print(f"setup_test: Exception occurred: {e}")
            return None
        
        # get remainder nodes that need to be pruned
        if remainder :=  total_nodes % active_nodes:
            total_nodes += active_nodes - remainder

        # create the node configs
        self.create_status_config()

        # now we make the nodes, but we do this ACTIVE NODES at a time to get full mesh connectivity
        counter = 1
        while counter <= total_nodes:
            for i in range(active_nodes):
                try:
                    self.setup_shm("CC" + str(counter), True)
                    subprocess.run(
                        [CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}']
                    )

                    # for transition to using node class
                    new_node = Node(f'CC{counter}')
                    self.nodes[new_node.name] = new_node

                except subprocess.CalledProcessError as e:
                    # This is where the error from lightning-cli lives!
                    print(f"setup_test failed with exit code {e.returncode}")
                    print(f"  setup_test STDOUT: {e.stdout.strip()}")
                    print(f"  setup_test STDERR: {e.stderr.strip()}") 
                    raise
                except Exception as e:
                    print(f"setup_test: Exception occurred: {e}")
                    return None
                counter += 1

                if counter > total_nodes:
                    break
            # now we wait for for those nodes to fully connect before we create new nodes
            if not self.are_channels_ready():
                print(f'Channels were not ready in time')
                return False
        
        # will fix when code gets refactored.
        # for now, we kill the extra nodes that get created so that we conform to the max nodes we're supposed to have
        # get the list of running CC nodes
        if remainder:
            cc_nodes = []
            while not cc_nodes:
                cc_nodes = self.get_cc_nodes()
                time.sleep(SLEEP_INTERVAL)
            self.shutdown_nodes(cc_nodes[-remainder:])

        return True
    
    def create_status_config(self):
        '''
        Create a config file for the nodes for this test run.
        '''
        config_data = {
            'active_nodes' : self.active_nodes,
            'max_peers' : self.active_nodes * 2,
            'block_size' : self.block_size,
            'discovery_rule' : 19,
            'botmaster_rule' : 123123,
            'channel_creation_sleep' : 10,
            'status_update_interval' : 1.5,
            'channel_balance_counter' : 3
        }

        try:
            with open(self.node_config_path, 'w') as f:
                json.dump(config_data, f, indent=4)
            print(f'Generated {self.node_config_path} with block size : {self.block_size}')
        except Exception as e:
            print(f'Error generating node status config. {e}')

    def takedown(self, config, percentage):
        '''
        Takedown section for taking down a percentage of nodes.
        Will append takedown nodes to the config.
        Args:
            percentage: float, percentage of nodes to take down (e.g., 0.1 for 10%)
        '''
        cc_nodes = []
        parameters = config['parameters']
        # find the 10% of nodes we're taking down
        num_nodes_kill = int(parameters[NUM_CC] * percentage)
        
        # get the list of running CC nodes
        while not cc_nodes:
            cc_nodes = self.get_cc_nodes()
            time.sleep(SLEEP_INTERVAL)
        
        nodes_to_kill = random.sample(list(cc_nodes), num_nodes_kill)

        # we only need the name and channels of these nodes being shut down
        try:
            temp_dead_nodes = [self.get_node_status(node.name) for node in nodes_to_kill]
            dead_nodes = [
                {element : node.get(element) for element in ['short_id','host_name', 'channels']}
                for node in temp_dead_nodes
                ]
        except Exception as e:
            # something went wrong, count this test run as a failure and start again.
            print(f"run_test: ERROR: Failure in recording takedown nodes, restarting. Error is \n{e}")
            return False
            
        # add the nodes we shut down to the config
        config.update({
            'takendown_nodes': dead_nodes
        })
        # disconnect the nodes here
        print(f"Takedown test:")
        self.shutdown_nodes(nodes_to_kill)
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
        '''
        # shadowing to make sure ti works
        node_name = f'{suffix}_status'
        if first_block:
            # this will be creating the first memory buffer
            print(f'Creating shared memory buffer for {node_name}')


        try:
            shm = shared_memory.SharedMemory(name=node_name, create=True, size=self.block_size)
            shm.close()
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
                shm.close()

    def remove_shm(self, suffix):
        node_name = f'{suffix}_status'
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

    def calculate_blocksize(self, active_nodes):
        '''
        Calculate the size of the shm blocks for this test run.
        With a little wiggle room on top.
        '''
        # more than what testing has shown, just to be safe
        # tests showed:
        # ~260 overhead
        # ~120 per channel
        overhead_size = 512
        per_peer_size = 256
        # extra padding just in case
        buffer = 1.2

        return int((overhead_size + (active_nodes * per_peer_size)) * buffer)

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