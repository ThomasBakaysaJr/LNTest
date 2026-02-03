#!/usr/bin/env python3
'''
Testing manager.
This is the main script that will handle
    - Setting up the tests.
    - Recording the data.
    - Breaking down the testing environment.
    - Doing it all over again
Use -h to see the options available.
Logs for CC nodes are stored in Node_Manager/logs
Logs for the Botmaster are in Botmaster/
Most errors can be solved by crtl+c and then running the script again.
'''
import argparse
import time
import subprocess
import json
import os
import textwrap
import datetime
import copy
from dotenv import load_dotenv
from utils import docker_utils
from utils.node_manager import NodeManager
from utils import record_total_time
from utils import sys_monitor

load_dotenv('config.env')

BITCOIND_CLI = os.getenv('BITCOIN_CLI')
BITCOIND_DATA_DIR = os.getenv('BITCOIN_DIR')

LIGHTNINGD_VERSION = os.getenv('LIGHTNINGD_VERSION')
LNTEST_VERSION = os.getenv('LNTEST_VERSION')

DATA_DIR = os.getenv('TEST_DATA_DIR')

TIMES_JSON = 'time_data.json'
TOPO_JSON = 'topology_data.json'

# scripts to use
RESTART_BITCOIND_BASH = os.getenv('RESTART_BITCOIND_BASH')
FUND_WALLETS_BASH = os.getenv('FUND_WALLETS_BASH')
BITCOIN_MINER_PY = os.getenv('MINER_SCRIPT')

# constant names for the variables we use
TEST_VAR = 'test_var' # in the test_values dict, this is the key for the variable that changes
NUM_CC = 'num_cc'
ACTIVE_NODES = 'active_nodes'
BM_CC = 'bm_cc'
BM_POS = 'bm_pos'

# Unless the script isn't working properly, best to leave these values alone
MAX_WAIT = int(os.getenv('NM_MAX_WAIT', 450)) # max wait for propagation before we move on (default = 450)
MAX_TRY = 5 # number of tries per iteration before we shut this thing down (default = 5) (1 means we only try once)
SLEEP_INTERVAL = int(os.getenv('NM_SLEEP', 1))

# configurations for the tests
TEST_CONFIGS = {
    '1' : {
        'description': 'increasing number of C&C nodes',
        'var_key' : NUM_CC,
        'range' : (100, 10),
        'multiplier': 1,
        'max_messages' : 30,
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
        'range' : (6, 1),
        'multiplier': 1,
        'max_messages' : 30,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 1,
            BM_CC: 1,
            BM_POS: 50
        }
    },
    '3' : {
        'description': 'increasing number of channels the botmaster makes',
        'var_key' : BM_CC,
        'range' : (6, 1),
        'multiplier': 1,
        'max_messages' : 30,
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
        'range' : (150, 50),
        'multiplier': 1,
        'max_messages' : 30,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: -50
        }
    },
    '5' : {
        'description': 'takedown test where a random 10% of the topology gets shutdown.',
        'var_key' : NUM_CC,
        'takedown' : True,
        'range' : (50, 10),
        'multiplier': 1,
        'max_messages' : 30,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_CC: 1,
            BM_POS: 50
        }
    }
}

def add_common_arguments(parser):
    """Add common simulation arguments to the given parser."""
    group = parser.add_argument_group('Simulation Parameters')
    group.add_argument('--num-cc', dest='num_cc', type=int, help='Starting number of CC servers.')
    group.add_argument('--active-nodes', dest='active_nodes', type=int, help='Starting number of active nodes.')
    group.add_argument('--bm-cc', dest='bm_cc', type=int, help='Number of nodes the botmaster will send commands to.')
    group.add_argument('--bm-pos', dest='bm_pos', type=int, help='Botmaster connection position (e.g., 50 for middle).')
    group.add_argument('--max-msg', dest='max_msg', type=int, help='Number of messages to send per test.')
    
    takedown = parser.add_argument_group('Takedown Settings')
    takedown.add_argument('--takedown', action='store_true', help='Enable node takedown during test.')
    takedown.add_argument('--takedown-pct', dest='takedown_pct', type=float, default=0.1, help='Percentage of nodes to take down (default: 0.1).')

def main():
    '''
    Main entry point for the LNBot Testing Orchestrator.
    '''
    parser = argparse.ArgumentParser(description="LNBot Testing Orchestrator", 
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    
    subparsers = parser.add_subparsers(dest='command', required=True, help='Mode of operation')

    # Subcommand: full
    parser_full = subparsers.add_parser('full', help='Run the full testing suite.')
    add_common_arguments(parser_full)

    # Subcommand: small
    parser_small = subparsers.add_parser('small', help='Run a small sanity check suite.')
    add_common_arguments(parser_small)

    # Subcommand: run (was --test)
    parser_run = subparsers.add_parser('run', help='Run a specific test configuration.')
    parser_run.add_argument('test_id', choices=TEST_CONFIGS.keys(), help='ID of the test to run.')
    parser_run.add_argument('--max-range', dest='max_range', type=int, help='Override the max range for the test variable.')
    parser_run.add_argument('--step', dest='step', type=int, help='Override the step size for the test variable.')
    add_common_arguments(parser_run)

    args = parser.parse_args()

    docker_utils.ensure_custom_image(LNTEST_VERSION, LIGHTNINGD_VERSION)
    manager = NodeManager()
    # start recording time for total testing
    start_time = time.time()
    all_configs = []

    if args.command in ['full', 'small']:

        # print out warning that max_range does nothing with this test
        # Note: In the new structure, max_range isn't even in the namespace for full/small, 
        # so we don't need to check for it, preventing user error by design.

        test_order = TEST_CONFIGS.keys()
        if args.command == 'full':
            print(f'Running full testing suite.')
        else:
            print(f'Running small testing suite.')
        if not confirm_test():
            print(f'Exiting tester.')
            return
        else:
            print(f'Continuing')
        for test_mode in test_order:
            # if we're doing small test, we change the values here
            config = TEST_CONFIGS[test_mode].copy()
            parameters = config['parameters']
            # specific changes for small tester
            if args.command == 'small':
                config['max_messages'] = 2
                parameters[NUM_CC] = 6
                parameters[ACTIVE_NODES] = 2
                parameters[BM_CC] = 1
                parameters[BM_POS] = 50

                if config['var_key'] == NUM_CC:
                    config['range'] = (8, 2)
                elif config['var_key'] == ACTIVE_NODES:
                    config['range'] = (3, 1)
                elif config['var_key'] == BM_CC:
                    config['range'] = (2, 1)
                elif config['var_key'] == BM_POS:
                    config['range'] = (100, 50)
            # Implement changes to full testings
            # like if the user wants to change the number of bm_cc connections
            if args.command == 'full':
                testing = config['var_key']
                if testing != NUM_CC and args.num_cc is not None:
                    print(f'num_cc is set to {args.num_cc}')
                    parameters[NUM_CC] = args.num_cc
                if testing != ACTIVE_NODES and args.active_nodes is not None:
                    print(f'active nodes is set to {args.active_nodes}')
                    parameters[ACTIVE_NODES] = args.active_nodes
                if testing != BM_CC and args.bm_cc is not None:
                    print(f'bm_cc is set to {args.bm_cc}')
                    parameters[BM_CC] = args.bm_cc
                if testing != BM_POS and args.bm_pos is not None:
                    print(f'bm_pos is set to {args.bm_pos}')
                    parameters[BM_POS] = args.bm_pos
                if args.max_msg is not None:
                    print(f'max_msg is set to {args.max_msg}')
                    config['max_messages'] = args.max_msg
                        
            config['parameters'] = parameters
            config['takedown_percentage'] = args.takedown_pct
            all_configs.append(config)
            run_test(config, manager)

    elif args.command == 'run':
        config = TEST_CONFIGS[args.test_id].copy()
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
        if args.max_msg is not None:
            print(f'max_msg is set to {args.max_msg}')
            config['max_messages'] = args.max_msg
        if args.max_range is not None:
            print(f'max_range is set to {args.max_range}')
            temp_range = list(config['range'])
            temp_range[0] = args.max_range
            config['range'] = temp_range
        if args.step is not None:
            print(f'step is set to {args.step}')
            temp_range = list(config['range'])
            temp_range[1] = args.step
            config['range'] = temp_range
        if args.takedown:
            print(f'Takedown test is True')
            config['takedown'] = True

        config['parameters'] = parameters
        config['takedown_percentage'] = args.takedown_pct
        print(f'Running test with:\n{parameters}')
        if not confirm_test():
            print(f'Exiting tester.')
            return
        else:
            print(f'Continuing')

        all_configs.append(config)
        run_test(config, manager)

    # record total time
    total_time = time.time() - start_time
    record_total_time.record_total_time(total_time, all_configs)

    # we only kill the nodes here since we want to keep
    # the logs for the last run.
    manager.kill_all_nodes()
    print(f'Testing finished. Exiting.')

def run_test(in_config, manager : NodeManager):
    '''
    Testing function. Runs test based on the configuration.
    Returns true is successful, false if something fails.
    '''
    config = copy.deepcopy(in_config)
    takedown_pct = config.get('takedown_percentage', 0.1)
    overall_test_time = time.time()
    attempt = 0
    testing = config['var_key']

    parameters = config['parameters']
    start = parameters[testing]
    end, step = config['range']

    for test_value in range(start, end + 1, step):
        init_bitcoin_server()
        success = False
        # change the value we're testing for.
        parameters[testing] = test_value
        # placeholder for system monitor
        monitor = None
        
        # try up to MAX_TRY times to get a successful test
        while not success:
            # cleanup from any previous runs
            test_data = []
            manager.cleanup_test() 
            # stop any system monitor that may be running
            if monitor:
                monitor.stop()
                
            # create a new system monitor for this test
            monitor = sys_monitor.HardwareMonitor(f"{get_record_name(config)}_system_metrics.csv")
            monitor.start()
            
            if attempt > MAX_TRY:
                print(f"{testing} Test failed with paramaters {parameters} at {time.time() - overall_test_time} seconds")
                return
            
            cc_start_time = time.time()
            
            print(f'\n\n\nRunning init for a total of {parameters[NUM_CC]} nodes with values \n{parameters}.')
            channels_created = manager.setup_test(parameters[NUM_CC], parameters[ACTIVE_NODES])

            print(f'Setup finished at {get_time()}')

            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                continue            
            
            fund_nodes()
            total_setup_time = time.time() - cc_start_time
            
            print(f'Channels created in {total_setup_time} seconds.')

            if config.get('takedown', False):
                print(f'Preparing for takedown of {takedown_pct*100}% of nodes.')
                if not manager.takedown(config, takedown_pct):
                    # we stop tracking containers here because we won't be reaching the !success block below
                    success = False
                    attempt += 1
                    continue
            
            print(f'Waiting done, proceeding to testing.')
            message_start_time = time.time()
            
            '''
            ACTUAL SENDING OF MESSAGES
            '''
            for y in range(1, config['max_messages'] + 1):
                # another wait, just in case we got nodes disconnecting or something
                manager.are_channels_ready()

                manager.send_botmaster_command(y, parameters[BM_CC], parameters[BM_POS])
                send_time, success = wait_for_propagation(y, manager)
                
                if not success:
                    break

                # add the record of this data
                record = parameters.copy()
                record['message'] = y
                record['time_elapsed'] = send_time
                test_data.append(record)

                print(f'Command {y} is finished at {get_time()}. Propagation time is {send_time} seconds.')
                print(f'Time: {get_time()}')
                
            # record the test and set reset attempts
            total_send_time = time.time() - message_start_time
            record_test(config, test_data, total_setup_time, total_send_time)
            record_topology(config, manager)

            if success:
                attempt = 0
            # if not a success, add to the attempt
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
        # out of the while loop
        monitor.stop()
        stop_bitcoinminer()           

    print(f"FINISHED at {time.time() - overall_test_time} testing for {config['description']}.")
    print(f"Testing with: \n{config}")

def wait_for_propagation(command, manager : NodeManager):
    print(f'Now waiting for command {command} to propagate.')
    sending = True
    start_time = time.time()
    success = None
    while sending:
        data = manager.retrieve_all_status()
        # manager.update()
        if data:
            time_interval, done = get_time_interval(data, command)
        else:
            done = False
        if done:
            sending = False
            success = True 
        time.sleep(SLEEP_INTERVAL)
        if manager.is_kill_time(start_time, MAX_WAIT):
            success = False
            break
    if success == None:
        print(f'Somethin went wrong in the wait for propagation state. Success == None')
    return time_interval, success

def confirm_test():
    if input(f'Confirm test? y / n: ').lower() in ['y', 'yes']:
        return True
    else:
        return False

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

    file_name = f'{get_record_name(config)}_{TIMES_JSON}'
    total_times = {
        'total_setup_time' : setup_time,
        'total_send_time' : total_send_time
    }

    data = {
        'meta_data' : create_meta_data(config),
        'total_times' :  total_times,
        'test_data' : test_data
        }
    
    with open(file_name, 'w') as f:
        json.dump(data, f, indent = 4, default = json_set_converter)
    

def create_meta_data(config):
    '''
    Create the record for the recording test data.
    Write the meta_data to the file and then return 
    that to the calling function.
    '''
    variable = config['var_key']
    constants = [param for param in config['parameters'].keys() if param != variable]
    is_takedown = config.get('takedown', False)

    meta_data = {
        'experiment' : 'LNBot Simulation Experiment',
        'version' : LNTEST_VERSION,
        'description' : 'This file contains the individual propagation times for messages being distributed across the simulated network.',
        'testing': config['description'],
        'variable' : variable,
        'constants' : constants,
        'takedown' : is_takedown,
        'authors' : [
            'Anonymous Author 1'
        ]
    }

    # add the takendown nodes - if they exist
    if is_takedown:
        meta_data['takendown_nodes'] = config['takendown_nodes']

    return meta_data

def record_topology(config, manager : NodeManager):
    all_status = manager.retrieve_all_status()
    top_name = f'{get_record_name(config)}_{TOPO_JSON}'

    meta_data = create_meta_data(config)

    data = {
        'meta_data' : meta_data,
        'topology' : all_status
    }

    with open(top_name, 'w') as f:
        json.dump(data, f, indent=4, default = json_set_converter)
    
    print(f'Topology data for {len(all_status)} nodes saved as {top_name}')
    

def get_record_name(config):
    '''
    Return a file name containing the relevant info for individual runs.
    '''
    values = config.get('parameters')
    var_key = config['var_key']
    
    # get a unique code to distinguish this run
    parts = map(str, values.values())
    id = ''.join(parts)
    # a T in the name means that this was a takedown test.
    if config.get('takedown', False):
        id += 'T'
    
    filename = f'{DATA_DIR}/{var_key}_{values[var_key]}_{id}'

    return filename

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
        balance = subprocess.run([BITCOIND_CLI, f'-datadir={BITCOIND_DATA_DIR}', '-regtest', 'getbalance'], capture_output=True)
        balance = balance.stdout.strip().decode()
        if balance == '':
            balance = 0
        else:
            balance = float(balance)

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


def get_time_interval(data, top_count):
    '''
    Retrive the time interval of all statuses in data that match top_count.
    Parameters:
        data: List of statuses
        top_count: counter we are waiting for
    Returns:
        interval, is_done
        interval: time between youngest and oldest status
        id_done: All status have the same counter as top_count
    '''
    # Retrieve the status of all nodes with their counter at top count
    top_data = [status for status in data if int(status.get('counter')) == int(top_count)]
    
    # propagation is done when all of statuses have the same counter
    is_done = len(top_data) == len(data)

    times = [status.get('last_msg_time') for status in top_data]
    if times:
        interval = max(times) - min(times)
    else:
        interval = None

    if is_done:
        print(f'done with counter at {top_count}')

    return interval, is_done

def get_time():
    return datetime.datetime.now().strftime('%H:%M:%S')

def get_date():
    return datetime.datetime.now().date()

def json_set_converter(obj):
    if isinstance(obj, set):
        return list(obj)

if __name__ == "__main__":
    main()
