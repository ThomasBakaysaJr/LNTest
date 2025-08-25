import time
import subprocess
import glob
import csv
import json
import re
import docker
import sys
from datetime import datetime
import pandas as pd

BM_PATH = '/root/botmaster'
BM_SCRIPT = 'BM.py'
BM_CONT = 'BM'
CC_MESSAGE_PREFIX = 'NodeManagerComms/status/cc_messageLog_*'
CC_CUR_MESSAGE_PREFIX = 'NodeManagerComms/status/cc_currentMessage_*'
STATUS_JSON_PREFIX = 'status/status_CC*'

TIMES_CSV = 'time_data_'
RUNTIMES_CSV = 'runtime_data.csv'
TOPO_JSON = 'topology_data_'

MASTER_LOG_PATH = ''
COUNTER = 3 # index of the counter variable for time keeping
TIME = 0 # index of the time variable for time keeping

CHANNEL_NORMAL = 'CHANNELD_NORMAL'

# Variables that govern how much data to gather
NUM_CC_ITERATIONS = 10 # each iteration increases the number of CC servers by 10
MIN_CHANNELS = 4 # these two govern number of channels for the nodes
MAX_CHANNELS = 4
MAX_MESSAGES = 100 # number of messages to test (Prof wants 100)

# Unless the script isn't working properly, best to leave these values alone
MAX_WAIT = 450 # max wait for propagation before we move on (default = 300)
WAIT_MULT = 2 # Multipler to MAX_WAIT for how long to wait for channel creation.
MAX_TRY = 5 # number of tries per iteration before we shut this thing down (default = 5) (1 means we only try once)
FM_WAIT = 120 # how long to wait before trying to send the first message (to let the nodes create channels) (default = 120) #OUTDATED
SLEEP_INTERVAL = 1
SLEEP_CHANNEL_INTERVAL = 10

DOCKER_CONTAINERS = set()

# def main(starting_iteration, active_nodes):
#     if starting_iteration == 0:
#         print(f'Invalid arguments provided. Provide in order of STARTING_ITERATION, NUM_ACTIVE_NODES to determine a custom starting iteration.')
#         starting_iteration = 1
#         active_nodes = 4
#     print(f'Starting iteration is {starting_iteration} with {active_nodes} active nodes.')
    
#     if not confirm_execution('Run testing script script.'):
#         print('Exiting . . .')
#         return
#     else:
#         print('Starting testing script')

#     main_start_time = time.time()
#     attempt = 0
#     starting_iteration = int(starting_iteration)
#     for x in range(starting_iteration, NUM_CC_ITERATIONS + 1):
#         success = False
#         total_nodes = x * 10
#         while not success:
#             # fail safe so it doesn't just keep failing over and over
#             if attempt > MAX_TRY:
#                 print(f"Could not run {MAX_MESSAGES} messages for {total_nodes} CC nodes after {attempt} attempts. Shutting down.")
#                 kill_nodes()
#                 return
            
#             cc_start_time = time.time()
#             record_create(total_nodes)
            
#             print(f'\n\n\nRunning init for a total of {total_nodes}')
#             setup_test(total_nodes, active_nodes)
#             print(f'Setup finished at {get_time()}')
            
#             print(f'Waiting for channels to be created . . .')
#             update_containers()
#             channels_created = are_channels_ready()
#             print(f'Channels created in {time.time() - cc_start_time} seconds.')

#             # checkpoint, if channels aren't created then we start again.
#             if not channels_created:
#                 attempt += 1
#                 print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
#                 continue

#             print(f'Waiting done, proceeding to testing.')
#             # ACTUAL SENDING OF MESSAGES
#             for y in range(1, MAX_MESSAGES + 1):
#                 # another wait, just in case we got nodes disconnecting or something
#                 are_channels_ready()

#                 send_msg(y)
#                 send_time, success = wait_for_propagation(y)
                
#                 if not success:
#                     break
                
#                 print(f'Command {y} is finished. Propagation time is {send_time} seconds.')
#                 print(f'Time: {get_time()}')
#                 entry = [total_nodes, y, send_time]
#                 record_test(entry, total_nodes)
#             # record the test and set reset attempts
#             if success:
#                 record_cc_total_time(cc_start_time, total_nodes)
#                 untrack_containers()
#                 attempt = 0
#             # if not a succes, add to the attempt
#             else:
#                 attempt += 1
#                 print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
#                 untrack_containers()
#                 print_topology()
#                 record_cc_total_time(cc_start_time, total_nodes)

#     now_time = time.time()
#     print(f'Testing with: {starting_iteration * 10} - {NUM_CC_ITERATIONS * 10} CC servers at {MAX_MESSAGES} messsages each finished in {now_time - main_start_time} seconds.')
#     print(f'Total runtime data saved in {RUNTIMES_CSV}')
#     kill_nodes()

def main(starting_iteration, active_nodes):
    if starting_iteration == 0:
        print(f'Invalid arguments provided. Provide in order of STARTING_ITERATION, NUM_ACTIVE_NODES to determine a custom starting iteration.')
        starting_iteration = 1
        active_nodes = 4
    print(f'Starting iteration is {starting_iteration} with {active_nodes} active nodes.')
    
    if not confirm_execution('Run testing script script.'):
        print('Exiting . . .')
        return
    else:
        print('Starting testing script')

    main_start_time = time.time()
    attempt = 0
    starting_iteration = int(starting_iteration)
    for x in range(starting_iteration, NUM_CC_ITERATIONS + 1):
        success = False
        total_nodes = x * 10
        while not success:
            # fail safe so it doesn't just keep failing over and over
            if attempt > MAX_TRY:
                print(f"Could not run {MAX_MESSAGES} messages for {total_nodes} CC nodes after {attempt} attempts. Shutting down.")
                kill_nodes()
                return
            
            cc_start_time = time.time()
            record_create(total_nodes)
            
            print(f'\n\n\nRunning init for a total of {total_nodes}')
            channels_created = setup_test(int(total_nodes), int(active_nodes))
            print(f'Setup finished at {get_time()}')
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
                entry = [total_nodes, y, send_time]
                record_test(entry, total_nodes)
            # record the test and set reset attempts
            if success:
                record_cc_total_time(cc_start_time, total_nodes)
                untrack_containers()
                attempt = 0
            # if not a succes, add to the attempt
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                untrack_containers()
                print_topology()
                record_cc_total_time(cc_start_time, total_nodes)

    now_time = time.time()
    print(f'Testing with: {starting_iteration * 10} - {NUM_CC_ITERATIONS * 10} CC servers at {MAX_MESSAGES} messsages each finished in {now_time - main_start_time} seconds.')
    print(f'Total runtime data saved in {RUNTIMES_CSV}')
    kill_nodes()

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

def record_create(in_suffix):
    suffix = in_suffix
    csv_name = f'{TIMES_CSV}{suffix}_CC_nodes.csv'
    with open(csv_name, 'w', newline='') as f:
        pass

def record_test(record, in_suffix):
    suffix = in_suffix
    csv_name = f'{TIMES_CSV}{suffix}_CC_nodes.csv'
    with open(csv_name, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(record)

def retrieve_all_status():
    files = sort_files(glob.glob(f'NodeManagerComms/{STATUS_JSON_PREFIX}'))
    current_topology = []
    try:
        for status_file in files:
            with open(status_file, 'r') as f:
                if f:
                    node_status = json.load(f)
            current_topology.append(node_status)
        return current_topology
    except Exception as e:
        pass

def record_cc_total_time(start_time, cc_count):
    '''
    Records the total time elapsed from the initialization of the nodes to the last message.
    Record the topology of the lightning network.
    '''
    # we want to read the csv file already there (or create it if it doesn't exist)
    cc_num = cc_count
    elapsed_time = time.time() - start_time

    headers = ['#CCs', 'Time_Taken']
    entry = pd.DataFrame({headers[0]: [cc_num], headers[1]: elapsed_time})
    df = pd.DataFrame()

    try:
        df = pd.read_csv(RUNTIMES_CSV)
        # delete old values if they exist
        if cc_num in df[headers[0]].values:
            df = df[df[headers[0]] != cc_num]
        df = pd.concat([df, entry], ignore_index=True)
    except FileNotFoundError:
        df = entry

    df = df.sort_values(by=[headers[0]]).reset_index(drop=True)

    df.to_csv(RUNTIMES_CSV, index=False)

    top_name = f'{TOPO_JSON}{cc_num}.json'
    cur_top = retrieve_all_status()
    with open(top_name, 'w') as f:
        json.dump(cur_top, f, indent=4)

    print(f'Topology data saved as {top_name}')
    print(f'Individual run times saved at {TIMES_CSV}{cc_num}_CC_nodes.csv')
    
def kill_nodes():
    subprocess.run(
        ["./kill_nodes.sh"]
    )

def are_channels_ready():
    '''
    Wait for channel creation between nodes to finish
    Returns:
        Returns True when channels has finished creating
        False when waiting time has exceeded MAX_WAIT
    '''
    start_time = time.time()
    counter = 0
    
    while True:
        if counter > 2:
            return True
        cur_top = retrieve_all_status()
        update_containers()

        channels_created = True
        if is_kill_time(start_time, MAX_WAIT * WAIT_MULT):
            return False
        if cur_top:
            for status in cur_top:
                if status.get('state') != 'online':
                    channels_created = False
                    counter = 0
                    continue
        else:
            channels_created = False

        if channels_created:
            counter += 1
        
        if counter < 1:
            time.sleep(SLEEP_CHANNEL_INTERVAL)
        else:
            time.sleep(SLEEP_CHANNEL_INTERVAL // 2)

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
    print(f'Sending message {message} . . .')
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stderr:
        print(f'Errors are {result.stderr}')

# def setup_test(total_nodes, active_nodes):
#     ''''
#     setup the number of CC servers needed
#     returns true when the the cc servers have been made
#     '''
#     try:
#         subprocess.run(
#             ["./init_botnet.sh", f'{total_nodes}', f'{active_nodes}']
#         )
#     except subprocess.CalledProcessError as e:
#         # This is where the error from lightning-cli lives!
#         print(f"tester failed with exit code {e.returncode}")
#         print(f"  tester STDOUT: {e.stdout.strip()}")
#         print(f"  tester STDERR: {e.stderr.strip()}") 
#         raise # Re-raise the exception so your calling code can catch it
#     except Exception as e:
#         print(f"tester: Exception occurred: {e}")
#         return None
    
def setup_test(total_nodes, active_nodes):
    ''''
    setup the number of CC servers needed
    returns true when the the cc servers have been made
    '''
    try:
        subprocess.run(
            ["./init_botnet.sh", f'{total_nodes}', f'{active_nodes}']
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
    
    # know we make the nodes, but we do this ACTIVE NODES at a time to get full mesh connectivity
    counter = 1
    while counter < total_nodes:
        for i in range(2):
            try:
                subprocess.run(
                    ["./3create_CC_nodesV3.sh", f'{counter}']
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
            counter += 1

            if counter > total_nodes:
                break
        # now we wait for for those nodes to fully connect before we create new nodes
        if not are_channels_ready():
            print(f'Channels were not ready in time')
            return False
    return True

def fund_nodes():
    try:
        subprocess.run(
            ["./4fund_wallets.sh"]
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
    msg_files = glob.glob(CC_CUR_MESSAGE_PREFIX)
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

    try:
        client = docker.from_env()
        containers = set(client.containers.list(filters={'status' : 'running'}))
    except docker.errors.DockerException as e:
        print(f'Error with docker module. Error: {e}')
    
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
    try:
        client = docker.from_env()
        containers = client.containers.list(filters={'status' : 'running'})
        print(f'Total of {len(containers)} active containers.')
    except docker.errors.DockerException as e:
        print(f'Error with docker module. Error: {e}')

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

def get_time():
    return datetime.now().strftime('%H:%M:%S')

if __name__ == "__main__":
    if len(sys.argv) > 2:
        if sys.argv[1] > '0' and sys.argv[2] > '0':
            main(sys.argv[1], sys.argv[2])
        else:
            print_topology()
            print_messages()
            print_container_counters()
    else:
        main(0, 0)