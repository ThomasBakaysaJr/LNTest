#!/usr/bin/env python3
import argparse
import time
import subprocess
import glob
import csv
import json
import re
import docker
import sys
import shlex
import os
from dotenv import load_dotenv
from datetime import datetime
from multiprocessing import shared_memory
import pandas as pd

load_dotenv('config.env')

BITCOIND_DIR = os.getenv('BITCOIND_PATH')
BITCOIND_DATA_DIR = os.getenv('BITCOIN_DIR')

BM_PATH = '/root/botmaster'
BM_SCRIPT = 'BM.py'
BM_CONT = 'BM'
CC_MESSAGE_PREFIX = 'NodeManagerComms/status/cc_messageLog_*'
CC_CUR_MESSAGE_PREFIX = 'NodeManagerComms/status/cc_currentMessage_*'
STATUS_JSON_PREFIX = 'status/status_CC*'

DATA_DIR = 'data/'

TIMES_JSON = 'time_data.json'
RUNTIMES_JSON = 'runtime_data.json'
SETUPTIMES_JSON = 'setuptime_data.json'
TOPO_JSON = 'topology_data.json'

# scripts to use
RESTART_BITCOIND_BASH = "./restart_bitcoin.sh"
INIT_BOTNET_BASH = './init_botnet.sh'
CREATE_CC_SERVER_BASH = './3create_CC_nodesV3.sh'
FUND_WALLETS_BASH = './4fund_wallets.sh'
CLEANUP_NODES_BASH = './cleanup_lightning_nodes.sh'
KILL_NODES_BASH = './kill_nodes.sh'
BITCOIN_MINER_PY = 'mineBlocks.py'

CC_TEST_PREFIX = f'{DATA_DIR}CC_ITERATION_'
ACTIVE_NODE_TEST_PREFIX = f'{DATA_DIR}ACTIVE_NODE_ITERATION_'
BM_CC_TEST_PREFIX = f'{DATA_DIR}BM_CC_ITERATION_'
BM_POS_TEST_PREFIX = f'{DATA_DIR}BM_POS_ITERATION_'

MASTER_LOG_PATH = ''
COUNTER = 3 # index of the counter variable for time keeping
TIME = 0 # index of the time variable for time keeping

CHANNEL_NORMAL = 'CHANNELD_NORMAL'

# # Variables that govern how much data to gather for full testing
# ACTIVE_NODES_MIN_NUM = 1 # For active node testing
# CC_ITERATION_MIN_NUM = 1 # For cc iteration testing


# MAX_MESSAGES = 100 # number of messages to test (Prof wants 100)
# ACTIVE_NODES_NUM_CC = 50 # default is 50; number of CCs for active node tests
# CC_ITERATION_NUM_ACTIVE_NODES = 4 # default is 4; number of active nodes for CC tests

# # constants for active node iteration tests
# ACTIVE_NODES_MAX_NUM = 6 # default is 6
# CC_ITERATION_NUM_MAX = 10 # each iteration increases the number of CC servers by 10

# # constant for the botmaster instructions
# BM_CC_VALUES = [1, 2, 4, 8]
# BM_POS_VALUES = [0.0, 0.5, 1.0]

# constant names for the variables we use
TEST_VAR = 'test_var' # in the test_values dict, this is the key for the variable that changes
NUM_CC = 'num_cc'
ACTIVE_NODES = 'active_nodes'
BM_CC = 'bm_cc'
BM_POS = 'bm_pos'

# Unless the script isn't working properly, best to leave these values alone
MAX_WAIT = 450 # max wait for propagation before we move on (default = 450)
WAIT_MULT = 2 # Multipler to MAX_WAIT for how long to wait for channel creation.
MAX_TRY = 5 # number of tries per iteration before we shut this thing down (default = 5) (1 means we only try once)
FM_WAIT = 120 # how long to wait before trying to send the first message (to let the nodes create channels) (default = 120) #OUTDATED
SLEEP_INTERVAL = 1
SLEEP_CHANNEL_INTERVAL = 10

DOCKER_CONTAINERS = set()

# configurations for the tests
TEST_CONFIGS = {
    '1' : {
        'description': 'increasing number of C&C nodes',
        'var_key' : NUM_CC,
        'start': 10,
        'range' : (100, 10),
        'multiplier': 1,
        'max_messages' : 100,
        'parameters': {
            NUM_CC: 10,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: 50
        }
    },
    '2' : {
        'description': 'increasing number of active nodes',
        'var_key' : ACTIVE_NODES,
        'start' : 1,
        'range' : (6, 1),
        'multiplier': 1,
        'max_messages' : 100,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: 50
        }
    },
    '3' : {
        'description': 'increasing number of channels the botmaster makes',
        'var_key' : BM_CC,
        'start' : 1,
        'range' : (8, 2),
        'multiplier': 1,
        'max_messages' : 100,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: 50
        }
    },
    '4' : {
        'description': 'different botmaster channel connection locations',
        'var_key' : BM_POS,
        'start' : 0,
        'range' : (100, 50),
        'multiplier': 1,
        'max_messages' : 100,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: 0
        }
    }
}

def main():
    global MAX_MESSAGES
    '''
    Args:
        mode: What type of test to conduct.
        1 = number of cc iterations
        2 = number of active nodes iterations
    '''

    parser = argparse.ArgumentParser(description="LNBot Testing Orchestrator.",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter )

    mode = parser.add_mutually_exclusive_group(required = True)
    mode.add_argument('--full', action = 'store_true', help = 'Run the full testing suite.')
    mode.add_argument('--small', action = 'store_true', help = 'Run a small testing suite to make sure everything works.')
    mode.add_argument('--test', choice = TEST_CONFIGS.keys(), help = 'Run tests on individual factors.')

    # Define optional arguments for starting values
    parser.add_argument('--num_cc', type = int, default = None, 
                        help='Starting number of CC servers.')
    parser.add_argument('--active_nodes', type = int, default = None, 
                        help='Starting number of active nodes.')
    parser.add_argument('--bm_cc', type = int, default = None,
                        help = 'Number of nodes the botmaster will send commands to')
    parser.add_argument('--bm_pos', type = float, default = None,
                        help = '''
                        Where in the botnet to connect as a percentage of the network.
                        <0  : Random
                        0.0 : Oldest nodes
                        0.5 : Middle of the network
                        1.0 : Youngest Nodes
                        ''')
    
    args = parser.parse_args()

    if args.full or args.small:
        test_order = TEST_CONFIGS.keys()

        for test_mode in test_order:
            # if we're doing small test, we change the values here
            config = TEST_CONFIGS[test_mode].copy()
            parameters = config['parameters']
            if args.small:
                config['max_messages'] = 10
                parameters[NUM_CC] = 1
                parameters[ACTIVE_NODES] = 4
                parameters[BM_CC] = 1
                parameters[BM_POS] = 0

                if config['var_key'] == NUM_CC:
                    config['range'] = (20, 10)
                elif config['var_key'] == ACTIVE_NODES:
                    config['range'] = (5, 1)
                elif config['var_key'] == BM_CC:
                    config['range'] = (2, 1)
                elif config['var_key'] == BM_POS:
                    config['range'] = (50, 50)

            config['parameters'] = parameters
            run_test(config)
    
    elif args.test:
        config = TEST_CONFIGS[args.test].copy()
        parameters = config['parameters']
        if args.num_cc is not None:
            print(f'num_cc is set to {args.num_cc}')
            parameters[NUM_CC] = args.num_cc
        if args.active_nodes is not None:
            print(f'active nodes is set to {args.active_nodes}')
            parameters[ACTIVE_NODES] = args.active_nodes
        if args.bm_cc is not None:
            print(f'bm_cc is set to {args.bm_cc}')
            parameters[BM_CC] = args.bm_cc
        if args.bm_pos is not None:
            print(f'bm_pos is set to {args.bm_pos}')
            parameters[BM_POS] = args.bm_pos

        config['parameters'] = parameters
        run_test(config)

    kill_nodes()
    print(f'Testing finished. Exiting.')

def run_test(in_config):
    '''
    Testing function. Runs test based on the configuration.
    Returns true is successful, false if something fails.
    '''
    config = in_config.copy()
    overall_test_time = time.time()
    attempt = 0
    testing = config['var_key']
    
    parameters = config['parameters']
    start = config['start']
    end, step = config['range']

    test_data = []

    for test_value in range(start, end + 1, step):
        cleanup_shm()
        init_bitcoin_server()
        success = False
        # change the value we're testing for.
        parameters[testing] = test_value

        # calc how many nodes we need to spin up (active nodes needs to divide into it)
        total_nodes = parameters[NUM_CC]
        total_nodes += parameters[ACTIVE_NODES] - (total_nodes % int(parameters[ACTIVE_NODES]))

        while not success:
            if attempt > MAX_TRY:
                print(f"{testing} Test failed with paramaters {parameters} at {time.time() - overall_test_time} seconds")
                kill_nodes()
                return
            
            cc_start_time = time.time()
            
            print(f'\n\n\nRunning init for a total of {parameters[NUM_CC]} nodes with values \n{parameters}.')
            channels_created = setup_test(int(total_nodes), int(parameters[ACTIVE_NODES]))

            print(f'Setup finished at {get_time()}')
            fund_nodes()
            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                continue            

            update_containers()
            total_setup_time = time.time() - cc_start_time
            print(f'Channels created in {total_setup_time} seconds.')

            print(f'Waiting done, proceeding to testing.')
            message_start_time = time.time()
            # ACTUAL SENDING OF MESSAGES
            for y in range(1, config['max_messages'] + 1):
                # another wait, just in case we got nodes disconnecting or something
                are_channels_ready()

                send_msg(y, parameters[BM_CC], parameters[BM_POS])
                send_time, success = wait_for_propagation(y)
                
                if not success:
                    break

                # add the record of this data
                record = config.get('parameters').copy()
                record['message'] = y
                record['time_elapsed'] = send_time
                test_data.append(record)

                print(f'Command {y} is finished at {get_time()}. Propagation time is {send_time} seconds.')
                print(f'Time: {get_time()}')
            # record the test and set reset attempts
            total_send_time = time.time() - message_start_time
            record_test(config, test_data, total_setup_time, total_send_time)
            if success:
                untrack_containers()
                attempt = 0
            # if not a succes, add to the attempt
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                untrack_containers()

    now_time = time.time()
    print(f'FINISHED at {overall_test_time - now_time} testing for {config['description']}.')
    print(f'Testing with: \n{config}')

def init_bitcoin_server():
    '''
    Helper to auto restart the bitcoind server
    and restart the bitcoinminer as well.
    '''
    stop_bitcoinminer()
    time.sleep(0.5)
    restart_bitcoind()
    
    balance = 0.0
    while balance <= 0.0:
        time.sleep(5)
        balance = subprocess.run([BITCOIND_DIR, f'-datadir={BITCOIND_DATA_DIR}', '-regtest', 'getbalance'], capture_output=True)
        balance = balance.stdout.strip().decode()
        if balance == '':
            balance = 0
        else:
            balance = float(balance)

def confirm_execution(message):
    confirmation = ['y','yes','ye']
    negation = ['n','no']
    
    while True:
        user_input = input(f'{message} y/n? :')
        user_input = str.lower(user_input)
        if user_input in confirmation:
            return True
        elif user_input in negation:
            return False
        else:
            print(f'{user_input} is an invalid option')

def record_test(config, test_data, setup_time, total_send_time):
    '''
    Add a record for the current test.
    Creates the file if it doesn't exist.
    Parameters:
        config : The config for this test
        test_data : A list of test_data records
        setup_time : Total time taken by nodes to setup channels
        total_send_time : Total time taken to send all messages
    '''

    file_name = get_record_name(config)
    total_times = {
        'total_setup_time' : setup_time,
        'total_send_time' : total_send_time
    }

    data = {
        'meta_data' : create_meta_data(config),
        'total_times' :  total_times,
        'test_data' : test_data
        }

    # we save this one and the topology 
    record_topology(config)
    with open(file_name, 'w') as f:
        json.dump(data, f, indent = 4)
    

def create_meta_data(config):
    '''
    Create the record for the recording test data.
    Write the meta_data to the file and then return 
    that to the calling function.
    '''
    variable = config['var_key']
    constants = [param for param in config['parameters'].keys() if param != variable]  

    meta_data = {
        'experiment' : 'LNBot Simulation Experiment',
        'description' : 'This file contains the individual propagation times for messages being distributed across the simulated network.',
        'testing': config['description'],
        'variable' : variable,
        'constants' : constants,
        'authors' : [
            'Professor Kurt, Ahmet',
            'Bakaysa, Thomas',
            'Erdin, Enes',
            'Cebe, Mumin',
            'Akkaya, Kemal',
            'Selcuk Uluagac, A.'
        ]
    }

    return meta_data

def record_topology(config):
    cur_top = retrieve_all_status()
    top_name = get_record_name(config)
    top_name += TOPO_JSON

    with open(top_name, 'w') as f:
        json.dump(cur_top, f, indent=4)
    
    print(f'Topology data for {len(cur_top)} nodes saved as {top_name}')
    

def get_record_name(config):
    '''
    Return a file name containing the relevant info for individual runs.
    '''
    values = config.get('parameters')
    var_key = config['var_key']
    filename = f'{DATA_DIR}{var_key}_{values[var_key]}_'

    return filename

def retrieve_all_status():
    cc_containers = get_cc_containers()
    all_status = list()

    for cont in cc_containers:
        node_name = f'{cont.name}_status'
        try:
            shm = shared_memory.SharedMemory(name=node_name)
            data = shm.buf.tobytes().split(b'\x00', 1)[0]
            shm.close()

            if not data:
                continue

            status = json.loads(data.decode('utf-8'))
            all_status.append(status)
        except Exception as e:
            # print(f'retrieve_all_status: {node_name} failed to retrived shm because {e}\nRecreating shm.')
            setup_shm(node_name)
            continue
    return all_status

def kill_nodes():
    '''
    Cleanup nodes and unlink the shared memory
    '''
    cleanup_shm()
    subprocess.run([KILL_NODES_BASH])

def restart_bitcoind():
    '''
    Shut down and restart bitcoind for a fresh start.
    '''
    subprocess.Popen(
        [RESTART_BITCOIND_BASH]
    )

def get_bitcoin_miner():
    '''
    Return the bitcoinminer if its running.
    None otherwise.
    '''
    result = subprocess.run(['pgrep', '-f', f"{BITCOIN_MINER_PY}"], capture_output=True)
    if result.returncode == 0:
        return result.stdout.decode().strip()
    else:
        return None

def stop_bitcoinminer():
    '''
    Stop the bitcoinminer if it exists.
    Does nothing otherwise
    '''
    if pid := get_bitcoin_miner():
        for id in pid.split():
            subprocess.run(["kill", str(id)])
            print(f"Found and killing the bitcoin miner with pid {id}.")

def are_channels_ready():
    '''
    Wait for channel creation between nodes to finish
    Returns:
        Returns True when channels has finished creating
        False when waiting time has exceeded MAX_WAIT
    '''
    start_time = time.time()
    
    while True:
        time.sleep(SLEEP_INTERVAL)
        cur_top = retrieve_all_status()
        update_containers()

        if is_kill_time(start_time, MAX_WAIT * WAIT_MULT):
            return False
        if len(get_cc_containers()) == len(cur_top) and cur_top:
            channels_created = True
            # if a single channel is not online, then channels create will be false and we sleep
            for status in cur_top:
                if status.get('state') != 'connected':
                    channels_created = False
                    break

            if channels_created:
                return True

def wait_for_propagation(command):
    print(f'Now waiting for command {command} to propagate.')
    sending = True
    start_time = time.time()
    success = None
    while sending:
        data = update_data()
        update_containers()
        if data:
            time_interval, done = get_time_interval(data, command)
        else:
            done = False
        if done:
            sending = False
            success = True 
        time.sleep(SLEEP_INTERVAL)
        if is_kill_time(start_time, MAX_WAIT):
            success = False
            break
    if success == None:
        print(f'Somethin went wrong in the wait for propagation state. Success == None')
    return time_interval, success

def is_kill_time(start_time, wait_time):
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

def send_msg(message, num_cc, where_cc):
    command_str = (f"docker exec -w {BM_PATH} {BM_CONT} python3 -u {BM_SCRIPT} "
                    f"--msg {message} --cc {num_cc} --init {where_cc}")
    command = shlex.split(command_str)
    print(f'Time: {get_time()}. Sending message {message} . . .')
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stderr:
        print(f'Errors are {result.stderr}')
    
def setup_test(total_nodes, active_nodes):
    ''''
    setup the number of CC servers needed
    returns true when the the cc servers have been made
    '''
    try:
        subprocess.run(
            [INIT_BOTNET_BASH, f'{total_nodes}', f'{active_nodes}']
        )
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        print(f"testsetup_tester failed with exit code {e.returncode}")
        print(f"  setup_test STDOUT: {e.stdout.strip()}")
        print(f"  setup_test STDERR: {e.stderr.strip()}") 
        raise # Re-raise the exception so your calling code can catch it
    except Exception as e:
        print(f"setup_test: Exception occurred: {e}")
        return None
    
    # now we make the nodes, but we do this ACTIVE NODES at a time to get full mesh connectivity
    counter = 1
    while counter <= total_nodes:
        for i in range(active_nodes):
            try:
                setup_shm(counter)
                subprocess.run(
                    [CREATE_CC_SERVER_BASH, f'{counter}', f'{active_nodes}']
                )
            except subprocess.CalledProcessError as e:
                # This is where the error from lightning-cli lives!
                print(f"setup_test failed with exit code {e.returncode}")
                print(f"  setup_test STDOUT: {e.stdout.strip()}")
                print(f"  setup_test STDERR: {e.stderr.strip()}") 
                raise # Re-raise the exception so your calling code can catch it
            except Exception as e:
                print(f"setup_test: Exception occurred: {e}")
                return None
            counter += 1

            if counter > total_nodes:
                break
        # now we wait for for those nodes to fully connect before we create new nodes
        if not are_channels_ready():
            print(f'Channels were not ready in time')
            return False
    return True

def setup_shm(suffix):
    '''
    Setup the shm block for this node using incoming suffix counter
    Make sure node_name and block_size matches the name and block_size in ln_checker.
    '''
    if 'status' not in f'{suffix}':
        # this will be creating the first memory buffer
        node_name = f'CC{suffix}_status'
        print(f'Creating shared memeory buffer for {node_name}')
    else:
        # this will be recreating it if the shm dies for some reason; silent
        node_name = suffix

    block_size = 5012

    try:
        shm = shared_memory.SharedMemory(name=node_name, create=True, size=block_size)
        shm.close()
    except FileExistsError:
        # Found a block by this name still, probably from bad cleanup. Clear and prepare it again
        print(f'setup_shm: Shared memory block found for {node_name}. Clearing. . .')
        temp_shm = shared_memory.SharedMemory(name=node_name)
        temp_shm.unlink()

        # recreate memory block
        shm = shared_memory.SharedMemory(name=node_name, create=True, size=block_size)
        shm.close()

def cleanup_shm():
    '''
    Clear out all the shared memory blocks.
    '''
    cc_containers = get_cc_containers()
    for cont in cc_containers:
        node_name = cont.name
        try:
            shm = shared_memory.SharedMemory(name=node_name)
            shm.unlink()
        except Exception as e:
            pass
            # print(f'cleanup_shm: ERROR in cleaning up memory. Error: {e}')

def fund_nodes():
    try:
        subprocess.run(
            [FUND_WALLETS_BASH]
        )
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        print(f"tester failed with exit code {e.returncode}")
        print(f"  tester STDOUT: {e.stdout.strip()}")
        print(f"  tester STDERR: {e.stderr.strip()}") 
        raise # Re-raise the exception so your calling code can catch it
    except Exception as e:
        print(f"tester: Exception occurred: {e}")
        return None
    
def update_data():
    msg_files = sort_files(glob.glob(CC_CUR_MESSAGE_PREFIX))
    all_data = []

    # get all the data
    for msg_file in msg_files:
        with open(msg_file, 'r') as of:
            reader = csv.reader(of)
            data = list(reader)
            if data:
                all_data.append(data[-1])
    return all_data

def get_time_interval(data, counter):
    top_count = str(counter)
    top_data = [row for row in data if row[COUNTER] == top_count]

    is_done = len(top_data) == len(data)

    times = [float(row[TIME]) for row in top_data]
    if times:
        interval = max(times) - min(times)
    else:
        interval = None

    if is_done:
        print(f'done with counter at {top_count}')

    return interval, is_done

def update_containers():
    global DOCKER_CONTAINERS

    containers = set(get_containers())
    
    if not DOCKER_CONTAINERS:
        DOCKER_CONTAINERS = containers
        print(f'Total of {len(DOCKER_CONTAINERS)} containers/nodes being tracked.')
    else:
        dead_cont = DOCKER_CONTAINERS - containers
        DOCKER_CONTAINERS = DOCKER_CONTAINERS - dead_cont
        if dead_cont:
            print(f'The following containers have died. Recorded at {get_time()}')
            for container in dead_cont:
                print(f'{container.name}')

def untrack_containers():
    global DOCKER_CONTAINERS

    DOCKER_CONTAINERS = set()

def get_cc_containers():
    '''
    Get the set of all CC server docker containers
    '''
    try:
        client = docker.from_env()
        containers = set(client.containers.list(filters={'status' : 'running', 'name': '^CC'}))
    except docker.errors.DockerException as e:
        print(f'get_cc_containers: Error with docker module. Error: {e}')
        return list()

    containers = sort_containers(containers)
    return containers

def get_containers():
    '''
    Return a set of all currently running docker containers.
    '''
    try:
        client = docker.from_env()
        containers = set(client.containers.list(filters={'status' : 'running'}))
    except docker.errors.DockerException as e:
        print(f'get_containers: Error with docker module. Error: {e}')
        return list()
    
    containers = sort_containers(containers)
    return containers

def print_topology():
    topology = retrieve_all_status()
    if topology:
        for node in topology:
            print(f'{node.get('name')} : {node.get('short id')} : {node.get('state')} : channel count = {len(node.get('channels'))}')
            for channel in node.get('channels'):
                print(node.get('channels')[channel])
            print('')

def print_messages():
    messages = update_data()
    for msg in messages:
        print(f'{msg}')

def print_container_counters():
    containers = get_containers()
    print(f'Total of {len(containers)} active containers.')

def sort_files(in_files):
    '''
    Takes in a list of files and returns the list sorted alphabetically and numerically 
    (ensures that cc15 comes after cc9)
    '''
    file_dict = {}
    for file in in_files:
        idx = re.findall(r'\d+', file)
        idx = int(idx[0])
        file_dict[idx] = file

    sorted_files_dict = dict(sorted(file_dict.items()))
    return list(sorted_files_dict.values())

def sort_containers(in_containers):
    '''
    Takes in a set of containers and returns the list sorted alphabetically and numerically 
    (ensures that cc15 comes after cc9)
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
    return return_set


def get_time():
    return datetime.now().strftime('%H:%M:%S')

def get_date():
    return datetime.now().date()

if __name__ == "__main__":
    main()