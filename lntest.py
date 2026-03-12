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
import datetime
import copy
import resource
from dotenv import load_dotenv
from utils import docker_utils
from utils.node_manager import NodeManager
from utils import record_total_time
from utils import sys_monitor

# Raise file descriptor limit to avoid "Too many open files" with large node counts
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (min(65536, hard), hard))

# Global reference to active monitor for cleanup on exit
_active_monitor = None

import atexit
import signal

def _cleanup_on_exit():
    """Kill orphan child processes (monitor, miner) on exit or Ctrl+C."""
    global _active_monitor
    if _active_monitor is not None:
        try:
            _active_monitor.stop()
        except Exception:
            pass
        _active_monitor = None
    try:
        stop_bitcoinminer()
    except Exception:
        pass

atexit.register(_cleanup_on_exit)

def _signal_handler(signum, frame):
    """Handle SIGINT/SIGTERM: cleanup then exit."""
    print(f'\nReceived signal {signum}. Cleaning up...', flush=True)
    _cleanup_on_exit()
    raise SystemExit(1)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

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
BM_SEEDS = 'bm_seeds'
BM_POS = 'bm_pos'
TAKEDOWN_PCT = 'takedown_pct'

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
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 10,
            ACTIVE_NODES: 4,
            BM_SEEDS: 1,
            BM_POS: 50
        }
    },
    '2' : {
        'description': 'increasing number of active nodes',
        'var_key' : ACTIVE_NODES,
        'range' : (6, 1),
        'multiplier': 1,
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 2,
            BM_SEEDS: 1,
            BM_POS: 50
        }
    },
    '3' : {
        'description': 'increasing number of botmaster seed connections',
        'var_key' : BM_SEEDS,
        'range' : (6, 1),
        'multiplier': 1,
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_SEEDS: 1,
            BM_POS: 50
        }
    },
    '4' : {
        'description': 'different botmaster channel connection locations',
        'var_key' : BM_POS,
        'range' : (150, 50),
        'multiplier': 1,
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_SEEDS: 1,
            BM_POS: -50
        }
    },
    '5' : {
        'description': 'random takedown with increasing percentage of C&C nodes removed',
        'var_key' : TAKEDOWN_PCT,
        'takedown' : True,
        'takedown_strategy': 'random',
        'range' : (50, 10),
        'multiplier': 1,
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_SEEDS: 1,
            BM_POS: 50,
            TAKEDOWN_PCT: 10
        }
    },
    '6' : {
        'description': 'targeted takedown removing highest-degree C&C nodes',
        'var_key' : TAKEDOWN_PCT,
        'takedown' : True,
        'takedown_strategy': 'targeted',
        'range' : (50, 10),
        'multiplier': 1,
        'max_messages' : 10,
        'parameters': {
            NUM_CC: 50,
            ACTIVE_NODES: 4,
            BM_SEEDS: 1,
            BM_POS: 50,
            TAKEDOWN_PCT: 10
        }
    }
}

def add_common_arguments(parser):
    """Add common simulation arguments to the given parser."""
    group = parser.add_argument_group('Simulation Parameters')
    group.add_argument('--num-cc', dest='num_cc', type=int, help='Starting number of CC servers.')
    group.add_argument('--active-nodes', dest='active_nodes', type=int, help='Starting number of active nodes.')
    group.add_argument('--bm-seeds', dest='bm_seeds', type=int, help='Number of seed nodes the botmaster connects to.')
    group.add_argument('--bm-pos', dest='bm_pos', type=int, help='Botmaster connection position (e.g., 50 for middle).')
    group.add_argument('--num-msg', dest='num_msg', type=int, help='Number of messages to send per test iteration.')
    
    takedown = parser.add_argument_group('Takedown Settings')
    takedown.add_argument('--takedown', action='store_true', help='Enable node takedown during test.')
    takedown.add_argument('--takedown-pct', dest='takedown_pct', type=float, default=0.1, help='Percentage of nodes to take down (default: 0.1).')
    takedown.add_argument('--takedown-strategy', dest='takedown_strategy', choices=['random', 'targeted'], default=None, help='Takedown strategy: random or targeted (highest-degree nodes).')

    topology = parser.add_argument_group('Topology Settings')
    topology.add_argument('--topology', dest='topology', choices=['dlnbot', 'custom'], default=None, help='Topology mode: dlnbot (D-LNBot sequential chain, built by orchestrator) or custom (user-supplied JSON topology file, built by orchestrator). Default is dlnbot unless --dlnbot-formation is used.')
    topology.add_argument('--topology-file', dest='topology_file', default=None, help='Path to custom topology JSON file (required when --topology custom).')
    topology.add_argument('--dlnbot-formation', dest='dlnbot_formation', action='store_true', default=False, help='Enable autonomous D-LNBot formation with staggered container launches. cc_manager discovers peers via innocent node. Mutually exclusive with --topology.')

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
    parser_run.add_argument('--sweep-start', dest='sweep_start', type=int, help='Override the starting value of the sweep variable.')
    parser_run.add_argument('--sweep-end', dest='sweep_end', type=int, help='Override the ending value of the sweep variable.')
    parser_run.add_argument('--sweep-step', dest='sweep_step', type=int, help='Override the step size for the sweep variable.')
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
            print(f'Running full testing suite.', flush=True)
        else:
            print(f'Running small testing suite.', flush=True)
        if not confirm_test():
            print(f'Exiting tester.', flush=True)
            return
        else:
            print(f'Continuing', flush=True)
        for test_mode in test_order:
            # if we're doing small test, we change the values here
            config = TEST_CONFIGS[test_mode].copy()
            parameters = config['parameters']
            # specific changes for small tester
            if args.command == 'small':
                config['max_messages'] = 2
                parameters[NUM_CC] = 6
                parameters[ACTIVE_NODES] = 2
                parameters[BM_SEEDS] = 1
                parameters[BM_POS] = 50

                if config['var_key'] == NUM_CC:
                    config['range'] = (8, 2)
                elif config['var_key'] == ACTIVE_NODES:
                    config['range'] = (3, 1)
                elif config['var_key'] == BM_SEEDS:
                    config['range'] = (2, 1)
                elif config['var_key'] == BM_POS:
                    config['range'] = (100, 50)
                elif config['var_key'] == TAKEDOWN_PCT:
                    config['range'] = (30, 10)
            # Implement changes to full testings
            # like if the user wants to change the number of bm_seeds connections
            if args.command == 'full':
                testing = config['var_key']
                if testing != NUM_CC and args.num_cc is not None:
                    print(f'num_cc is set to {args.num_cc}', flush=True)
                    parameters[NUM_CC] = args.num_cc
                if testing != ACTIVE_NODES and args.active_nodes is not None:
                    print(f'active nodes is set to {args.active_nodes}', flush=True)
                    parameters[ACTIVE_NODES] = args.active_nodes
                if testing != BM_SEEDS and args.bm_seeds is not None:
                    print(f'bm_seeds is set to {args.bm_seeds}', flush=True)
                    parameters[BM_SEEDS] = args.bm_seeds
                if testing != BM_POS and args.bm_pos is not None:
                    print(f'bm_pos is set to {args.bm_pos}', flush=True)
                    parameters[BM_POS] = args.bm_pos
                if args.num_msg is not None:
                    print(f'num_msg is set to {args.num_msg}', flush=True)
                    config['max_messages'] = args.num_msg
                        
            config['parameters'] = parameters
            config['takedown_percentage'] = args.takedown_pct
            # Determine mode: --dlnbot-formation or --topology {dlnbot, custom}
            if args.dlnbot_formation and args.topology is not None:
                print('ERROR: --dlnbot-formation and --topology are mutually exclusive.', flush=True)
                return
            if args.dlnbot_formation:
                config['mode'] = 'dlnbot-formation'
            elif args.topology is not None:
                config['mode'] = args.topology
            else:
                config['mode'] = 'dlnbot'
            if args.topology_file is not None:
                config['topology_file'] = args.topology_file
            if config['mode'] == 'custom' and args.topology_file is None:
                print('ERROR: --topology custom requires --topology-file.', flush=True)
                return
            all_configs.append(config)
            run_test(config, manager)

    elif args.command == 'run':
        config = TEST_CONFIGS[args.test_id].copy()
        parameters = config['parameters']
        testing = config['var_key']
        # Only override fixed parameters; sweep variable is set via --sweep-start
        if testing != NUM_CC and args.num_cc is not None:
            print(f'num_cc is set to {args.num_cc}', flush=True)
            parameters[NUM_CC] = args.num_cc
        if testing != ACTIVE_NODES and args.active_nodes is not None:
            print(f'active_nodes is set to {args.active_nodes}', flush=True)
            parameters[ACTIVE_NODES] = args.active_nodes
        if testing != BM_SEEDS and args.bm_seeds is not None:
            print(f'bm_seeds is set to {args.bm_seeds}', flush=True)
            parameters[BM_SEEDS] = args.bm_seeds
        if testing != BM_POS and args.bm_pos is not None:
            print(f'bm_pos is set to {args.bm_pos}', flush=True)
            parameters[BM_POS] = args.bm_pos
        if args.num_msg is not None:
            print(f'num_msg is set to {args.num_msg}', flush=True)
            config['max_messages'] = args.num_msg
        if args.sweep_start is not None:
            print(f'sweep_start is set to {args.sweep_start}', flush=True)
            parameters[config['var_key']] = args.sweep_start
        if args.sweep_end is not None:
            print(f'sweep_end is set to {args.sweep_end}', flush=True)
            temp_range = list(config['range'])
            temp_range[0] = args.sweep_end
            config['range'] = temp_range
        if args.sweep_step is not None:
            print(f'sweep_step is set to {args.sweep_step}', flush=True)
            temp_range = list(config['range'])
            temp_range[1] = args.sweep_step
            config['range'] = temp_range
        if args.takedown:
            print(f'Takedown test is True', flush=True)
            config['takedown'] = True
        if args.takedown_strategy is not None:
            print(f'Takedown strategy is set to {args.takedown_strategy}', flush=True)
            config['takedown_strategy'] = args.takedown_strategy
        # Determine mode: --dlnbot-formation or --topology {dlnbot, custom}
        if args.dlnbot_formation and args.topology is not None:
            print('ERROR: --dlnbot-formation and --topology are mutually exclusive.', flush=True)
            return
        if args.dlnbot_formation:
            config['mode'] = 'dlnbot-formation'
        elif args.topology is not None:
            config['mode'] = args.topology
        else:
            config['mode'] = 'dlnbot'
        print(f'Mode is set to {config["mode"]}', flush=True)
        if args.topology_file is not None:
            config['topology_file'] = args.topology_file
        if config['mode'] == 'custom' and args.topology_file is None:
            print('ERROR: --topology custom requires --topology-file.', flush=True)
            return

        config['parameters'] = parameters
        config['takedown_percentage'] = args.takedown_pct
        print_execution_plan(config)
        if not confirm_test():
            print(f'Exiting tester.', flush=True)
            return
        else:
            print(f'Continuing', flush=True)

        all_configs.append(config)
        run_test(config, manager)

    # record total time
    total_time = time.time() - start_time
    record_total_time.record_total_time(total_time, all_configs)

    # we only kill the nodes here since we want to keep
    # the logs for the last run.
    manager.kill_all_nodes()
    print(f'Testing finished. Exiting.', flush=True)

def run_test(in_config, manager : NodeManager):
    '''
    Testing function. Runs test based on the configuration.
    Returns true is successful, false if something fails.
    '''
    config = copy.deepcopy(in_config)
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
            global _active_monitor
            monitor = sys_monitor.HardwareMonitor(f"{get_record_name(config)}_system_metrics.csv")
            monitor.start()
            _active_monitor = monitor
            
            if attempt > MAX_TRY:
                print(f"{testing} Test failed with paramaters {parameters} at {time.time() - overall_test_time} seconds", flush=True)
                monitor.stop()
                _active_monitor = None
                stop_bitcoinminer()
                return
            
            cc_start_time = time.time()
            
            print(f'\n\n\nRunning init for a total of {parameters[NUM_CC]} nodes with values \n{parameters}.', flush=True)
            channels_created = manager.setup_test(parameters[NUM_CC], parameters[ACTIVE_NODES], mode=config.get('mode', 'dlnbot'))

            print(f'Setup finished at {get_time()}', flush=True)

            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                print(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}', flush=True)
                continue            
            
            fund_nodes()
            total_setup_time = time.time() - cc_start_time
            
            print(f'Channels created in {total_setup_time} seconds.', flush=True)

            # Build topology based on mode
            mode = config.get('mode', 'dlnbot')
            if mode == 'dlnbot':
                edges = NodeManager.build_chain_edges(parameters[NUM_CC], parameters[ACTIVE_NODES])
                print(f'Building D-LNBot chain topology (n={parameters[NUM_CC]}, m={parameters[ACTIVE_NODES]}, {len(edges)} edges)...', flush=True)
                if not manager.build_topology(edges):
                    print('Topology build failed. Retrying...', flush=True)
                    success = False
                    attempt += 1
                    continue
                print(f'Waiting 10s for node status updates...', flush=True)
                time.sleep(10)
            elif mode == 'custom':
                edges = NodeManager.load_and_validate_topology(config['topology_file'], parameters[NUM_CC])
                if edges is None:
                    print('Custom topology loading failed. Aborting.', flush=True)
                    monitor.stop()
                    _active_monitor = None
                    stop_bitcoinminer()
                    return
                print(f'Building custom topology ({len(edges)} edges)...', flush=True)
                if not manager.build_topology(edges):
                    print('Topology build failed. Retrying...', flush=True)
                    success = False
                    attempt += 1
                    continue
                print(f'Waiting 10s for node status updates...', flush=True)
                time.sleep(10)
            # dlnbot-formation: cc_manager handles formation, nothing to build here

            if config.get('takedown', False):
                # Derive takedown percentage from parameter (integer %) or fallback
                if TAKEDOWN_PCT in parameters:
                    takedown_pct = parameters[TAKEDOWN_PCT] / 100.0
                else:
                    takedown_pct = config.get('takedown_percentage', 0.1)
                takedown_strategy = config.get('takedown_strategy', 'random')
                print(f'Preparing for {takedown_strategy} takedown of {takedown_pct*100:.0f}% of nodes.', flush=True)
                if not manager.takedown(config, takedown_pct, takedown_strategy):
                    print(f'Takedown failed, retrying...', flush=True)
                    success = False
                    attempt += 1
                    continue
            
            print(f'Waiting done, proceeding to testing.', flush=True)
            message_start_time = time.time()
            
            '''
            ACTUAL SENDING OF MESSAGES
            '''
            for y in range(1, config['max_messages'] + 1):
                # another wait, just in case we got nodes disconnecting or something
                # Skip for orchestrator-controlled topologies — cc_manager is not running
                if config.get('mode', 'dlnbot') == 'dlnbot-formation':
                    manager.are_channels_ready()

                manager.send_botmaster_command(y, parameters[BM_SEEDS], parameters[BM_POS])
                cmd_start_time = time.time()
                send_time, success = wait_for_propagation(y, manager)
                
                # Calculate coverage (what fraction of surviving nodes received the command)
                coverage_pct, received, total = get_coverage(y, manager)

                if not success:
                    if config.get('takedown', False):
                        # Network partition is a valid result for takedown tests — record it
                        actual_elapsed = time.time() - cmd_start_time
                        record = parameters.copy()
                        record['message'] = y
                        record['time_elapsed'] = actual_elapsed
                        record['coverage'] = coverage_pct
                        record['nodes_received'] = received
                        record['nodes_total'] = total
                        record['partitioned'] = True
                        test_data.append(record)
                        print(f'Command {y} timed out at {get_time()}. Coverage: {coverage_pct*100:.1f}% ({received}/{total} surviving nodes)', flush=True)
                    break

                # add the record of this data
                record = parameters.copy()
                record['message'] = y
                record['time_elapsed'] = send_time
                record['coverage'] = coverage_pct
                record['nodes_received'] = received
                record['nodes_total'] = total
                record['partitioned'] = False
                test_data.append(record)

                print(f'Command {y} is finished at {get_time()}. Propagation time is {send_time} seconds. Coverage: {coverage_pct*100:.1f}%', flush=True)
                print(f'Time: {get_time()}', flush=True)
                
            # record the test and set reset attempts
            total_send_time = time.time() - message_start_time
            record_test(config, test_data, total_setup_time, total_send_time)
            record_topology(config, manager)

            if success:
                attempt = 0
            # if not a success, add to the attempt
            elif config.get('takedown', False):
                # Network partition is a valid data point for takedown tests, advance to next iteration
                success = True
                attempt = 0
            else:
                attempt += 1
                print(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}', flush=True)
        # out of the while loop
        monitor.stop()
        _active_monitor = None
        stop_bitcoinminer()           

    print(f"FINISHED at {time.time() - overall_test_time} testing for {config['description']}.", flush=True)
    print(f"Testing with: \n{config}", flush=True)

def wait_for_propagation(command, manager : NodeManager):
    print(f'Now waiting for command {command} to propagate.', flush=True)
    sending = True
    start_time = time.time()
    success = None
    time_interval = None
    last_received = 0
    last_change_time = time.time()
    STALE_TIMEOUT = 60  # If no new node receives the message for 60s, network is partitioned
    while sending:
        data = manager.retrieve_all_status()
        if data:
            time_interval, done = get_time_interval(data, command)
            # Track coverage progress for early partition detection
            received = sum(1 for s in data if int(s.get('counter', 0)) >= int(command))
            if received > last_received:
                last_received = received
                last_change_time = time.time()
        else:
            done = False
        if done:
            sending = False
            success = True 
        time.sleep(SLEEP_INTERVAL)
        if manager.is_kill_time(start_time, MAX_WAIT):
            success = False
            break
        # Early exit: if coverage hasn't improved for STALE_TIMEOUT, network is partitioned
        if (time.time() - last_change_time) >= STALE_TIMEOUT and last_received > 0:
            print(f'Coverage stalled at {last_received}/{len(data) if data else "?"} nodes for {STALE_TIMEOUT}s. Network likely partitioned.', flush=True)
            success = False
            break
    if success == None:
        print(f'Somethin went wrong in the wait for propagation state. Success == None', flush=True)
    return time_interval, success

def get_coverage(command, manager : NodeManager):
    '''
    Calculate what fraction of surviving nodes received the given command.
    Returns:
        (coverage_pct, received_count, total_count)
    '''
    data = manager.retrieve_all_status()
    if not data:
        return 0.0, 0, 0
    total = len(data)
    received = sum(1 for s in data if int(s.get('counter', 0)) >= int(command))
    coverage = received / total if total > 0 else 0.0
    return coverage, received, total

def print_execution_plan(config):
    '''Print a human-readable execution plan before confirmation.'''
    params = config['parameters']
    var_key = config['var_key']
    start = params[var_key]
    end, step = config['range']
    iterations = list(range(start, end + 1, step))
    mode = config.get('mode', 'dlnbot')

    print(f'\n{"="*60}', flush=True)
    print(f'  EXECUTION PLAN: {config["description"]}', flush=True)
    print(f'{"="*60}', flush=True)
    print(f'  Sweep variable : {var_key}', flush=True)
    print(f'  Sweep range    : {start} -> {end} (step {step})', flush=True)
    print(f'  Iterations     : {len(iterations)} values: {iterations}', flush=True)
    print(f'  Messages/iter  : {config["max_messages"]}', flush=True)
    print(f'  Topology mode  : {mode}', flush=True)

    # Show fixed parameters (everything except the sweep variable)
    fixed = {k: v for k, v in params.items() if k != var_key}
    if fixed:
        print(f'  Fixed params   : {fixed}', flush=True)

    if config.get('takedown', False):
        strategy = config.get('takedown_strategy', 'random')
        if var_key == TAKEDOWN_PCT:
            print(f'  Takedown       : {strategy}, sweep {start}%-{end}%', flush=True)
        else:
            pct = config.get('takedown_percentage', 0.1)
            print(f'  Takedown       : {strategy}, {pct*100:.0f}%', flush=True)

    if config.get('topology_file'):
        print(f'  Topology file  : {config["topology_file"]}', flush=True)
    print(f'{"="*60}', flush=True)

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
        print(f"tester failed with exit code {e.returncode}", flush=True)
        print(f"  tester STDOUT: {e.stdout.strip()}", flush=True)
        print(f"  tester STDERR: {e.stderr.strip()}", flush=True)
        raise # Re-raise the exception so your calling code can catch it
    except Exception as e:
        print(f"tester: Exception occurred: {e}", flush=True)
        return None
    

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
        meta_data['takedown_strategy'] = config.get('takedown_strategy', 'random')

    meta_data['mode'] = config.get('mode', 'dlnbot')
    if config.get('topology_file'):
        meta_data['topology_file'] = config['topology_file']

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
    
    print(f'Topology data for {len(all_status)} nodes saved as {top_name}', flush=True)
    

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
        strategy = config.get('takedown_strategy', 'random')
        id += 'T' if strategy == 'random' else 'Ttargeted'
    # Mode suffix in filename
    mode = config.get('mode', 'dlnbot')
    if mode == 'dlnbot':
        id += 'D'
    elif mode == 'dlnbot-formation':
        id += 'F'
    elif mode == 'custom':
        id += 'X'
    
    filename = f'{DATA_DIR}/{var_key}_{values[var_key]}_{id}'

    return filename

def init_bitcoin_server():
    '''
    Ensure bitcoind is running with funds and miner is active.
    Only does a full restart if bitcoind is not running or has no funds.
    Reuses the existing bitcoind instance between iterations to avoid
    the overhead of deleting regtest data and re-mining 101 blocks.
    '''
    # Check if bitcoind is already running with funds
    if is_bitcoind_ready():
        # Ensure miner is running
        if not get_bitcoin_miner():
            start_miner()
        return
    
    # Full restart needed (first time or after crash)
    stop_bitcoinminer()
    time.sleep(0.5)
    restart_bitcoind()
    
    balance = 0.0
    while balance <= 0.0:
        time.sleep(1)
        balance = subprocess.run([BITCOIND_CLI, f'-datadir={BITCOIND_DATA_DIR}', '-regtest', 'getbalance'], capture_output=True)
        balance = balance.stdout.strip().decode()
        if balance == '':
            balance = 0
        else:
            balance = float(balance)

def is_bitcoind_ready():
    '''
    Check if bitcoind is running and has a positive balance.
    Returns True if bitcoind is ready to use, False otherwise.
    '''
    try:
        result = subprocess.run(
            [BITCOIND_CLI, f'-datadir={BITCOIND_DATA_DIR}', '-regtest', 'getbalance'],
            capture_output=True, timeout=5
        )
        balance_str = result.stdout.strip().decode()
        if balance_str and float(balance_str) > 0:
            return True
    except Exception:
        pass
    return False

def start_miner():
    '''
    Start just the background miner without restarting bitcoind.
    Uses sys.executable to ensure the same Python (venv) is used.
    '''
    import sys
    rpc_user = os.getenv('RPC_USER')
    rpc_password = os.getenv('RPC_PASSWORD')
    subprocess.Popen(
        [sys.executable, BITCOIN_MINER_PY, rpc_user, rpc_password, BITCOIND_CLI],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

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
            print(f"Found and killing the bitcoin miner with pid {id}.", flush=True)


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
        print(f'done with counter at {top_count}', flush=True)

    return interval, is_done

def get_time():
    return datetime.datetime.now().strftime('%H:%M:%S')


def json_set_converter(obj):
    if isinstance(obj, set):
        return list(obj)

if __name__ == "__main__":
    main()
