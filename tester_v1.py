#!/usr/bin/env python3
import time
import subprocess
import glob
import csv
import json
import re
import docker
import sys
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

TIMES_CSV = 'time_data_'
RUNTIMES_CSV = 'runtime_data.csv'
SETUPTIMES_CSV = 'setuptime_data.csv'
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

MASTER_LOG_PATH = ''
COUNTER = 3 # index of the counter variable for time keeping
TIME = 0 # index of the time variable for time keeping

CHANNEL_NORMAL = 'CHANNELD_NORMAL'

# Variables that govern how much data to gather for full testing
ACTIVE_NODES_MIN_NUM = 1 # For active node testing
CC_ITERATION_MIN_NUM = 1 # For cc iteration testing


MAX_MESSAGES = 100 # number of messages to test (Prof wants 100)
ACTIVE_NODES_NUM_CC = 50 # default is 50; number of CCs for active node tests
CC_ITERATION_NUM_ACTIVE_NODES = 4 # default is 4; number of active nodes for CC tests

# Variables for active node iteration tests
ACTIVE_NODES_MAX_NUM = 6 # default is 6
CC_ITERATION_NUM_MAX = 10 # each iteration increases the number of CC servers by 10

# Unless the script isn't working properly, best to leave these values alone
MAX_WAIT = 900 # max wait for propagation before we move on (default = 300)
WAIT_MULT = 2 # Multipler to MAX_WAIT for how long to wait for channel creation.
MAX_TRY = 5 # number of tries per iteration before we shut this thing down (default = 5) (1 means we only try once)
FM_WAIT = 120 # how long to wait before trying to send the first message (to let the nodes create channels) (default = 120) #OUTDATED
SLEEP_INTERVAL = 1
SLEEP_CHANNEL_INTERVAL = 10

DOCKER_CONTAINERS = set()

def main(starting_cc_num, starting_active_node_num, mode):
    global MAX_MESSAGES
    '''
    Args:
        mode: What type of test to conduct.
        1 = number of cc iterations
        2 = number of active nodes iterations
    '''

    if mode == '1':
        print(f'Starting at {int(starting_cc_num) * 10} CC servers going to {CC_ITERATION_NUM_MAX * 10} CC servers with {starting_active_node_num} active nodes.')
    elif mode == '2':
        print(f'Starting at {starting_active_node_num} actives nodes and going to {ACTIVE_NODES_MAX_NUM} active nodes with {ACTIVE_NODES_NUM_CC} CC servers.')
    elif mode == '3':
        print(f'Starting small tester.')
    elif mode =='4':
        print(f'Starting full testing. All tests with {MAX_MESSAGES} messages.')
        print(f'Then will start at {ACTIVE_NODES_MIN_NUM} active nodes and going to {ACTIVE_NODES_MAX_NUM} active nodes with {ACTIVE_NODES_NUM_CC} CC servers.')
        print(f'Starting at {CC_ITERATION_MIN_NUM * 10} CC servers going to {CC_ITERATION_NUM_MAX * 10} CC servers with {CC_ITERATION_NUM_ACTIVE_NODES} active nodes.')
    else:
        print(f'Error. Invalid mode argument. Mode is {mode} which is neither 1, 2 nor 3.')
    
    if not confirm_execution('Run testing script script.'):
        print('Exiting . . .')
        return
    else:
        print('Starting testing script')
    
    if mode == '1':
        test_cc_iteration(starting_active_node_num, starting_cc_num, CC_ITERATION_NUM_MAX)
    elif mode == '2':
        test_active_nodes_iteration(50, starting_active_node_num, ACTIVE_NODES_MAX_NUM)
    elif mode == '3':
        MAX_MESSAGES = 10
        test_cc_iteration(4, 1, 1)
        test_active_nodes_iteration(12, 4, 4)
    elif mode == '4':
        test_active_nodes_iteration(ACTIVE_NODES_NUM_CC, ACTIVE_NODES_MIN_NUM, ACTIVE_NODES_MAX_NUM)
        test_cc_iteration(CC_ITERATION_NUM_ACTIVE_NODES, CC_ITERATION_MIN_NUM, CC_ITERATION_NUM_MAX)

    kill_nodes()
    print(f'Testing finished. Exiting.')

def test_cc_iteration(active_nodes, starting_iteration, end):
    '''
    Testing function for different number of CC servers.
    Number of active nodes is constant.
    '''
    main_start_time = time.time()
    attempt = 0
    mode = '1'
    starting_iteration = int(starting_iteration)
    for iteration in range(starting_iteration, end + 1):
        cleanup_shm()
        init_bitcoin_server()
        success = False
        total_nodes = iteration * 10
        total_nodes += total_nodes % int(active_nodes)

        while not success:
            # fail safe so it doesn't just keep failing over and over
            if attempt > MAX_TRY:
                print(f"Could not run {MAX_MESSAGES} messages for {total_nodes} CC nodes after {attempt} attempts. Shutting down.")
                kill_nodes()
                return
            
            cc_start_time = time.time()
            record_create(total_nodes, active_nodes, mode)
            
            print(f'\n\n\nRunning init for a total of {total_nodes}')
            channels_created = setup_test(int(total_nodes), int(active_nodes))

            print(f'Setup finished at {get_time()}')
            record_setup_total_time(cc_start_time, total_nodes, active_nodes, mode)
            fund_nodes()
            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                continue            

            update_containers()
            print(f'Channels created in {time.time() - cc_start_time} seconds.')

            print(f'Waiting done, proceeding to testing.')
            # ACTUAL SENDING OF MESSAGES
            for y in range(1, MAX_MESSAGES + 1):
                # another wait, just in case we got nodes disconnecting or something
                are_channels_ready()

                send_msg(y)
                send_time, success = wait_for_propagation(y)
                
                if not success:
                    break
                
                print(f'Command {y} is finished at {get_time()}. Propagation time is {send_time} seconds.')
                print(f'Time: {get_time()}')
                entry = [total_nodes, active_nodes, y, send_time]
                record_test(entry, total_nodes, active_nodes, mode)
            # record the test and set reset attempts
            if success:
                record_cc_total_time(cc_start_time, total_nodes, active_nodes, mode)
                record_topology(total_nodes, active_nodes, mode)
                untrack_containers()
                attempt = 0
            # if not a succes, add to the attempt
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                untrack_containers()
                record_topology(total_nodes, active_nodes, mode)
                record_cc_total_time(cc_start_time, total_nodes, active_nodes, mode)

    now_time = time.time()
    print(f'FINISHED testing for message propagtion different number of CC servers.')
    print(f'Testing with: {starting_iteration * 10} - {end * 10} CC servers at {MAX_MESSAGES}  \
                    messsages each, finished in {now_time - main_start_time} seconds.')
    
def test_active_nodes_iteration(num_cc, active_nodes_start, active_nodes_end):
    '''
    Testing function for different number of active nodes.
    Number of CC servers is constant.
    '''
    main_start_time = time.time()
    mode = '2'
    attempt = 0
    starting_iteration = int(active_nodes_start)
    # just in case, never have the number of active nodes be less than 1
    if starting_iteration < 1:
        starting_iteration = 1

    for active_nodes in range(starting_iteration, active_nodes_end + 1):
        cleanup_shm()
        init_bitcoin_server()
        success = False
        total_nodes = num_cc
        total_nodes += active_nodes - (total_nodes % int(active_nodes))

        while not success:
            # fail safe so it doesn't just keep failing over and over
            if attempt > MAX_TRY:
                print(f"Could not run {MAX_MESSAGES} messages for {active_nodes} active nodes after {attempt} attempts. Shutting down.")
                kill_nodes()
                return
            
            cc_start_time = time.time()
            record_create(total_nodes, active_nodes, mode)
            
            print(f'\n\n\nRunning init for {total_nodes} CC servers with {active_nodes} active nodes.')
            channels_created = setup_test(int(total_nodes), int(active_nodes))

            print(f'Setup finished at {get_time()}')
            record_setup_total_time(cc_start_time, total_nodes, active_nodes, mode)
            fund_nodes()
            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                continue            

            update_containers()
            print(f'Channels created in {time.time() - cc_start_time} seconds.')

            print(f'Waiting done, proceeding to testing.')
            # ACTUAL SENDING OF MESSAGES
            for y in range(1, MAX_MESSAGES + 1):
                # another wait, just in case we got nodes disconnecting or something
                are_channels_ready()

                send_msg(y)
                send_time, success = wait_for_propagation(y)
                
                if not success:
                    break
                
                print(f'Command {y} is finished. Propagation time is {send_time} seconds.')
                print(f'Time: {get_time()}')
                entry = [total_nodes, active_nodes, y, send_time]
                record_test(entry, total_nodes, active_nodes, mode)
            # record the test and set reset attempts
            if success:
                record_cc_total_time(cc_start_time, total_nodes, active_nodes, mode)
                record_topology(total_nodes, active_nodes, mode)
                untrack_containers()
                attempt = 0
            # if not a succes, add to the attempt
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                untrack_containers()
                record_cc_total_time(cc_start_time, total_nodes, active_nodes, mode)
                record_topology(total_nodes, active_nodes, mode)

    now_time = time.time()
    print(f'FINISHED testing for different number of active_nodes.')
    print(f'Testing with: {starting_iteration} - {active_nodes_end} active nodes with {num_cc} CC servers at {MAX_MESSAGES} messsages each, finished in {now_time - main_start_time} seconds.')

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

def record_create(cc_num, active_num, mode):

    variable = '#CCs' if mode == '1' else 'Num Active Nodes'
    constant = 'Num Active Nodes' if mode == '1' else '#CCs'

    meta_blurb = f'''
# ---
# LNBot Simulation Experiment.
# Description: This file contains the individual propagation times for messages being distributed across the simulated network.
# Variable being tested: {variable}
# Constant being tracked: {constant}
# Data Generated on {get_date()}
# Author: Prof. Kurt Ahmet; Thomas Bakaysa; Erdin, Enes ; Cebe, Mumin ; Akkaya, Kemal ; Selcuk Uluagac, A.
# ---
# Column Definitions
# 1. CCs: Number of CC servers for this test.
# 2. Active_nodes: Number of active nodes for this test.
# 3. Message: Message being propagated.
# 4. Time: Total time taken for message to propagte through the total number of CC servers.
# ---

##CCs,Active_Nodes,Message,Time_Taken\n
'''

    prefix = test_record_name(cc_num, active_num, mode)
    csv_name = f'{prefix}{TIMES_CSV}.csv'
    with open(csv_name, 'w', newline='') as f:
        f.write(meta_blurb)

def record_test(record, cc_num, active_num, mode):
    prefix = test_record_name(cc_num, active_num, mode)
    csv_name = f'{prefix}{TIMES_CSV}.csv'
    with open(csv_name, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(record)

def test_record_name(cc_num, active_num, mode):
    '''
    Return a file name containing the relevant info, ready to be appended to whatever is needed.
    '''
    filename = f'{CC_TEST_PREFIX}{cc_num}_' if mode == '1' else f'{ACTIVE_NODE_TEST_PREFIX}{active_num}_'
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


def record_cc_total_time(start_time, cc_num, active_nodes, mode):
    '''
    Records the time elapsed from the BM connecting and sending the first message to the last message.
    Record the topology of the lightning network.
    '''

    meta_blurb = f'''
# ---
# LNBot Simulation Experiment.
# Description: This file contains the total time it took to send all {MAX_MESSAGES} messages to certain number of CC nodes with a certain number of active nodes.
# Data Generated on {get_date()}
# Author: Prof. Kurt Ahmet; Thomas Bakaysa; Erdin, Enes ; Cebe, Mumin ; Akkaya, Kemal ; Selcuk Uluagac, A.
# ---
# Column Definitions
# 1. CCs: Number of CC servers for this test.
# 2. Active_nodes: Number of active nodes for this test.
# 3. Time: Total time taken for {MAX_MESSAGES} messages to propagte through the total number of CC servers.
# ---\n
'''
    # we want to read the csv file already there (or create it if it doesn't exist)
    elapsed_time = time.time() - start_time

    headers = ['#CCs', 'Active_nodes', 'Time_Taken']
    entry = pd.DataFrame({headers[0]: [cc_num], headers[1] : active_nodes, headers[2]: elapsed_time})
    df = pd.DataFrame()

    # we should have different file names for the different tests
    if mode == '1':
        file_name = f'{CC_TEST_PREFIX}_'
        header_idx = 0
    elif mode == '2':
        file_name = f'{ACTIVE_NODE_TEST_PREFIX}_'
        header_idx = 1
    file_name += RUNTIMES_CSV

    try:
        df = pd.read_csv(file_name, names = headers, comment='#')
        # delete old values if they exist
        if cc_num in df[headers[header_idx]].values:
            df = df[df[headers[header_idx]] != cc_num]
        df = pd.concat([df, entry], ignore_index=True)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        df = entry

    df = df.sort_values(by=[headers[0]]).reset_index(drop=True)

    with open(file_name, 'w') as f:
        f.write(meta_blurb)
        df.to_csv(f, index=False)
    print(f'Total time saved at {file_name}')

def record_setup_total_time(start_time, cc_num, active_nodes, mode):
    '''
    Record the time take to create and channel all the nodes for this iteration.
    '''
    meta_blurb = f'''
# ---
# LNBot Simulation Experiment.
# Description: This file contains the times it took for CC servers to spin up and finish creating channels with designated number of active nodes.
# Data Generated on {get_date()}
# Author: Prof. Kurt Ahmet; Thomas Bakaysa; Erdin, Enes ; Cebe, Mumin ; Akkaya, Kemal ; Selcuk Uluagac, A.
# ---
# Column Definitions
# 1. CCs: Number of CC servers for this test.
# 2. Active_nodes: Number of active nodes for this test.
# 3. Time: Total time taken for the CC servers to spin up and finish channeling with each other.
# --- \n
'''
    # we want to read the csv file already there (or create it if it doesn't exist)
    elapsed_time = time.time() - start_time
    num_rows = len(meta_blurb.splitlines())

    headers = ['#CCs', 'Active_nodes', 'Time_Taken']
    entry = pd.DataFrame({headers[0]: [cc_num], headers[1] : active_nodes, headers[2]: elapsed_time})
    df = pd.DataFrame()

    # we should have different file names for the different tests
    if mode == '1':
        file_name = f'{CC_TEST_PREFIX}_'
        header_idx = 0
    elif mode == '2':
        file_name = f'{ACTIVE_NODE_TEST_PREFIX}_'
        header_idx = 1
    file_name += SETUPTIMES_CSV

    try:
        df = pd.read_csv(file_name, names = headers, comment='#')
        # delete old values if they exist
        if cc_num in df[headers[header_idx]].values:
            df = df[df[headers[header_idx]] != cc_num]
        df = pd.concat([df, entry], ignore_index=True)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        df = entry

    df = df.sort_values(by=[headers[0]]).reset_index(drop=True)
    with open(file_name, 'w') as f:
        f.write(meta_blurb)
        df.to_csv(f, index=False)
    print(f'Setup times saved at {file_name}')

def record_topology(cc_num, active_nodes, mode):
    cur_top = retrieve_all_status()

    top_name = f'{CC_TEST_PREFIX}{cc_num}_' if mode == '1' else f'{ACTIVE_NODE_TEST_PREFIX}{active_nodes}_'
    top_name += TOPO_JSON

    with open(top_name, 'w') as f:
        json.dump(cur_top, f, indent=4)
    
    print(f'Topology data for {len(cur_top)} nodes saved as {top_name}')
    
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

def send_msg(message):
    command = ["docker", "exec", "-w", BM_PATH, BM_CONT, "python3", "-u", BM_SCRIPT, str(message)]
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
    warning_text = '''
Invalid arguments. tester_v1 starting_cc_number starting_active_nodes mode
mode = 0: no testing.   with arguments 0 0, print topology and message progress;
                        with arguments 0 1 save the current topology as a json file 
mode = 1: Test iterations of number of CC servers, starting at starting_cc_number (will be mult. by 10) and stopping at 100 
mode = 2: Test iterations of number of active nodes, starting at starting_active nodes and stopping at 6
mode = 3: Test mode 1 and 2 with only 12 CC nodes, 10 messages, 4 active nodes. Use to quickly test script behavior.
mode = 4: Full testing.

NOTE: starting_cc_number is in multiples of 10 i.e. 1 = 10 starting CC nodes
'''

    if len(sys.argv) > 3:
        if sys.argv[3] == '4':
            main('1', '4', '4')
        elif sys.argv[3] == '3':
            main('1', '4', '3')
        elif sys.argv[1] > '0' and sys.argv[2] > '0' and (sys.argv[3] in ['1', '2']):
            main(sys.argv[1], sys.argv[2], sys.argv[3])
        elif sys.argv[1] == '0' and sys.argv[2] == '0' and sys.argv[3] == '0':
            print_topology()
            print_messages()
            print_container_counters()
        elif sys.argv[1] == '0' and sys.argv[2] == '1' and sys.argv[3] == '0':
            # record_topology()
            pass
        else:
            print(warning_text)
    else:
        print(warning_text)