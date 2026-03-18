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
import logging
import time
import subprocess
import json
import datetime
import copy
import resource
import re
from utils.config import cfg
from utils.log import setup_logging, add_file_handler
from utils.docker_helpers import ensure_custom_image
from utils.node_manager import NodeManager
from utils import sys_monitor

log = logging.getLogger(__name__)

# ANSI escape codes for terminal colors
RED = '\033[91m'
BOLD = '\033[1m'
RESET = '\033[0m'

def warn_red(msg):
    """Print a prominent red warning to both logger and terminal."""
    log.warning(msg)
    print(f'\n{RED}{BOLD}WARNING: {msg}{RESET}\n')

def error_red(msg):
    """Print a prominent red error to the terminal and log file (not duplicated on console)."""
    # Log at DEBUG so it reaches the file handler but not the console (console level = INFO)
    log.debug(f'[BLOCKED] {msg}')
    print(f'{RED}{BOLD}ERROR: {msg}{RESET}')

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
    log.info(f'\nReceived signal {signum}. Cleaning up...')
    _cleanup_on_exit()
    raise SystemExit(1)

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

setup_logging()
cfg.load()
cfg.validate()

TIMES_JSON = 'time_data.json'
TOPO_JSON = 'topology_data.json'

# constant names for the variables we use
CC_COUNT = 'cc_count'
ACTIVE_NODES = 'active_nodes'
INJECTION_COUNT = 'injection_count'
TAKEDOWN_PCT = 'takedown_pct'

# Unless the script isn't working properly, best to leave these values alone
MAX_WAIT = cfg.NM_MAX_WAIT
MAX_TRY = 5
SLEEP_INTERVAL = cfg.NM_SLEEP

# configurations for the tests
# 'range' is (sweep_end, sweep_step); sweep_start comes from parameters[var_key]
TEST_CONFIGS = {
    'cc_count' : {
        'description': 'increasing number of C&C nodes',
        'var_key' : CC_COUNT,
        'range' : (100, 10),  # 10 -> 100, step 10
        'max_messages' : 10,
        'parameters': {
            CC_COUNT: 10,
            ACTIVE_NODES: 4,
            INJECTION_COUNT: 1,
        }
    },
    'active_nodes' : {
        'description': 'increasing number of active C&C servers (m)',
        'var_key' : ACTIVE_NODES,
        'range' : (6, 1),
        'max_messages' : 10,
        'parameters': {
            CC_COUNT: 50,
            ACTIVE_NODES: 2,
            INJECTION_COUNT: 1,
        }
    },
    'injection' : {
        'description': 'increasing number of botmaster injection points',
        'var_key' : INJECTION_COUNT,
        'range' : (6, 1),
        'max_messages' : 10,
        'parameters': {
            CC_COUNT: 50,
            ACTIVE_NODES: 4,
            INJECTION_COUNT: 1,
        }
    },
    'takedown_random' : {
        'description': 'random takedown with increasing percentage of C&C nodes removed',
        'var_key' : TAKEDOWN_PCT,
        'takedown' : True,
        'takedown_strategy': 'random',
        'range' : (50, 10),
        'max_messages' : 10,
        'parameters': {
            CC_COUNT: 50,
            ACTIVE_NODES: 4,
            INJECTION_COUNT: 1,
            TAKEDOWN_PCT: 10
        }
    },
    'takedown_targeted' : {
        'description': 'targeted takedown removing highest-degree C&C nodes',
        'var_key' : TAKEDOWN_PCT,
        'takedown' : True,
        'takedown_strategy': 'targeted',
        'range' : (50, 10),
        'max_messages' : 10,
        'parameters': {
            CC_COUNT: 50,
            ACTIVE_NODES: 4,
            INJECTION_COUNT: 1,
            TAKEDOWN_PCT: 10
        }
    }
}

def add_common_arguments(parser):
    """Add common simulation arguments to the given parser."""
    group = parser.add_argument_group('Simulation Parameters (fixed values for non-sweep variables)')
    group.add_argument('--nodes', dest='cc_count', type=int,
        help='Fixed network size (number of C&C nodes). Ignored when cc_count is the sweep variable; use --sweep-start instead.')
    group.add_argument('--m', dest='active_nodes', type=int,
        help='Fixed overlay width (active C&C servers per node). Ignored when active_nodes is the sweep variable; use --sweep-start instead.')
    group.add_argument('--inject', dest='inject', type=str, default=None,
        help='Botmaster injection points (e.g., "CC5,CC12,CC30"). '
             'Default: CC1 (deterministic). Injection sweep uses random nodes when omitted.')
    group.add_argument('--num-msg', dest='num_msg', type=int, help='Number of messages to send per test iteration.')

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

    # Subcommand: small
    parser_small = subparsers.add_parser('small', help='Quick sanity check: 4 nodes, 1 message, 1 iteration.')

    # Subcommand: run
    parser_run = subparsers.add_parser('run', help='Run a specific test configuration.')
    parser_run.add_argument('test_id', choices=TEST_CONFIGS.keys(), help='Test to run (e.g., cc_count, active_nodes, takedown_random).')
    parser_run.add_argument('--sweep-start', dest='sweep_start', type=int, help='Override the starting value of the sweep variable.')
    parser_run.add_argument('--sweep-end', dest='sweep_end', type=int, help='Override the ending value of the sweep variable.')
    parser_run.add_argument('--sweep-step', dest='sweep_step', type=int, help='Override the step size for the sweep variable.')
    add_common_arguments(parser_run)

    args = parser.parse_args()

    ensure_custom_image(cfg.LNTEST_VERSION)
    manager = NodeManager()
    # start recording time for total testing
    start_time = time.time()
    all_configs = []

    if args.command == 'small':
        # Quick sanity check: 4 nodes, 1 message, 1 iteration
        log.info('Running sanity check: 4 nodes, active_nodes=2, 1 message.')
        config = {
            'description': 'sanity check',
            'var_key': CC_COUNT,
            'range': (4, 1),
            'max_messages': 1,
            'mode': 'dlnbot',
            'parameters': {
                CC_COUNT: 4,
                ACTIVE_NODES: 2,
                INJECTION_COUNT: 1,
            }
        }
        all_configs.append(config)
        run_test(config, manager)

    elif args.command == 'run':
        config = copy.deepcopy(TEST_CONFIGS[args.test_id])
        parameters = config['parameters']
        testing = config['var_key']
        # Override fixed parameters; sweep variable is controlled via --sweep-start/end/step
        param_flags = [
            (CC_COUNT, args.cc_count, '--nodes'),
            (ACTIVE_NODES, args.active_nodes, '--m'),
        ]
        for param_key, arg_value, flag_name in param_flags:
            if arg_value is not None:
                if testing == param_key:
                    log.warning(f'{flag_name} is ignored because {param_key} is the sweep variable. '
                                f'Use --sweep-start to set the starting value.')
                else:
                    log.info(f'{param_key} is set to {arg_value}')
                    parameters[param_key] = arg_value

        # Parse --inject: explicit node IDs (e.g., CC5,CC12,CC30)
        if args.inject is not None:
            tokens = [t.strip() for t in args.inject.split(',')]
            for name in tokens:
                if not re.match(r'^CC\d+$', name):
                    error_red(f'Invalid node ID "{name}". Use CC1, CC5, etc.')
                    return
            config['inject_nodes'] = tokens
            parameters[INJECTION_COUNT] = len(tokens)
            log.info(f'Injection: explicit nodes {tokens}')
            if testing == INJECTION_COUNT:
                error_red('--inject cannot be used with the injection sweep. '
                          'The sweep needs to vary the NUMBER of injection points; '
                          '--inject fixes specific nodes. Omit --inject to use random selection.')
                return
        if args.num_msg is not None:
            log.info(f'num_msg is set to {args.num_msg}')
            config['max_messages'] = args.num_msg
        if args.sweep_start is not None:
            log.info(f'sweep_start is set to {args.sweep_start}')
            parameters[config['var_key']] = args.sweep_start
        if args.sweep_end is not None:
            log.info(f'sweep_end is set to {args.sweep_end}')
            temp_range = list(config['range'])
            temp_range[0] = args.sweep_end
            config['range'] = temp_range
        if args.sweep_step is not None:
            log.info(f'sweep_step is set to {args.sweep_step}')
            temp_range = list(config['range'])
            temp_range[1] = args.sweep_step
            config['range'] = temp_range
        # Validate sweep range
        sweep_start = parameters[config['var_key']]
        sweep_end, sweep_step = config['range']  # (end_value, step_size)
        if sweep_step <= 0:
            error_red(f'Sweep step must be positive (got {sweep_step}).')
            return
        if sweep_start > sweep_end:
            error_red(f'Sweep start ({sweep_start}) is greater than sweep end ({sweep_end}). '
                      f'No iterations would run. Check --sweep-start and --sweep-end.')
            return

        # E2: Validate m >= 2 for active_nodes sweep
        if testing == ACTIVE_NODES and parameters[ACTIVE_NODES] < 2:
            error_red('active_nodes sweep must start at m >= 2. '
                      'm=1 topologies fragment under --dev-fast-gossip.')
            return

        # E3: Cap takedown percentage at 90%
        if testing == TAKEDOWN_PCT:
            effective_end = args.sweep_end if args.sweep_end is not None else config['range'][0]
            if effective_end > 90:
                error_red(f'Takedown percentage cannot exceed 90% (got {effective_end}%). '
                          f'At least 10% of nodes must survive.')
                return

        # Determine mode: --dlnbot-formation or --topology {dlnbot, custom}
        if args.dlnbot_formation and args.topology is not None:
            error_red('--dlnbot-formation and --topology are mutually exclusive.')
            return
        if args.dlnbot_formation:
            config['mode'] = 'dlnbot-formation'
        elif args.topology is not None:
            config['mode'] = args.topology
        else:
            config['mode'] = 'dlnbot'
        log.info(f'Mode is set to {config["mode"]}')

        # Formation mode warnings
        config['warnings'] = []
        if config['mode'] == 'dlnbot-formation':
            if testing == ACTIVE_NODES:
                config['warnings'].append(
                    'active_nodes sweep with dlnbot-formation is NOT a single-variable experiment. '
                    'm changes BOTH autonomous formation topology AND propagation behavior. '
                    'Results show how different m values affect the GENERATED topology.')
            if args.active_nodes is not None and testing != ACTIVE_NODES:
                config['warnings'].append(
                    f'--m {args.active_nodes} in formation mode changes the autonomous '
                    f'topology structure (MAX_ACTIVE_NODES, MAX_PEERS in cc_manager).')
            if testing == CC_COUNT:
                config['warnings'].append(
                    'cc_count sweep with dlnbot-formation: formation topology is nondeterministic. '
                    'Results will have higher variance than dlnbot mode. '
                    'Run multiple repetitions for statistical significance.')
            if testing == INJECTION_COUNT:
                config['warnings'].append(
                    'injection sweep with dlnbot-formation: topology is rebuilt each iteration '
                    'with nondeterministic formation. Results are confounded by topology variance. '
                    'Consider using --topology dlnbot or custom for cleaner results.')

        # E1: --topology-file without --topology custom is a hard error
        if args.topology_file is not None:
            if config['mode'] != 'custom':
                error_red('--topology-file requires --topology custom. '
                          'Did you mean: --topology custom --topology-file <path>?')
                return
            else:
                config['topology_file'] = args.topology_file
        if config['mode'] == 'custom' and 'topology_file' not in config:
            error_red('--topology custom requires --topology-file.')
            return

        # Custom topology: read node count from file and block incompatible tests
        if config['mode'] == 'custom':
            import json as _json
            try:
                with open(config['topology_file'], 'r') as f:
                    topo_data = _json.load(f)
            except Exception as e:
                error_red(f'Could not read topology file: {e}')
                return
            if 'nodes' not in topo_data:
                error_red('Custom topology file must specify a "nodes" field.')
                return
            file_n = topo_data['nodes']
            if not isinstance(file_n, int) or file_n <= 0:
                error_red(f'Topology file "nodes" must be a positive integer (got {file_n}).')
                return
            if args.cc_count is not None and args.cc_count != file_n:
                log.warning(f'--nodes {args.cc_count} conflicts with topology file ({file_n} nodes). Using {file_n}.')
            parameters[CC_COUNT] = file_n
            log.info(f'Custom topology: using {file_n} nodes from {config["topology_file"]}')

            # cc_count and active_nodes sweeps don't work with custom topologies:
            # cc_count would need a different topology file per iteration,
            # active_nodes (m) is a D-LNBot-specific parameter with no effect here.
            if testing in (CC_COUNT, ACTIVE_NODES):
                error_red(f'Test "{args.test_id}" is not compatible with custom topology mode. '
                          f'Custom mode supports: injection, takedown_random, takedown_targeted.')
                return
            if args.active_nodes is not None:
                log.warning('--m is ignored in custom topology mode (m is D-LNBot-specific).')
                # Revert to test config default
                parameters[ACTIVE_NODES] = TEST_CONFIGS[args.test_id]['parameters'][ACTIVE_NODES]

        config['parameters'] = parameters
        print_execution_plan(config)
        if not confirm_test():
            log.info('Exiting tester.')
            return
        else:
            log.info('Continuing')

        all_configs.append(config)
        run_test(config, manager)

    # record total time
    total_time = time.time() - start_time
    record_total_time(total_time, all_configs)

    # we only kill the nodes here since we want to keep
    # the logs for the last run.
    manager.kill_all_nodes()
    log.info('Testing finished. Exiting.')

def run_test(in_config, manager : NodeManager):
    '''
    Testing function. Runs test based on the configuration.
    Returns true is successful, false if something fails.
    '''
    config = copy.deepcopy(in_config)
    overall_test_time = time.time()
    attempt = 0
    testing = config['var_key']

    # Add file handler for this test run
    log_file = f'{cfg.TEST_DATA_DIR}/orchestrator.log'
    add_file_handler(log_file)

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
                log.error(f"{testing} Test failed with paramaters {parameters} at {time.time() - overall_test_time} seconds")
                monitor.stop()
                _active_monitor = None
                stop_bitcoinminer()
                return

            cc_start_time = time.time()

            log.info(f'\n\n\nRunning init for a total of {parameters[CC_COUNT]} nodes with values \n{parameters}.')
            channels_created = manager.setup_test(parameters[CC_COUNT], parameters[ACTIVE_NODES], mode=config.get('mode', 'dlnbot'))

            log.info(f'Setup finished at {get_time()}')

            # checkpoint, if channels aren't created then we start again.
            if not channels_created:
                attempt += 1
                log.warning(f'Nodes have not finished creating channels in over {MAX_WAIT} seconds. Attempt is now {attempt}')
                continue

            fund_nodes()
            total_setup_time = time.time() - cc_start_time

            log.info(f'Channels created in {total_setup_time} seconds.')

            # Build topology based on mode
            mode = config.get('mode', 'dlnbot')
            if mode == 'dlnbot':
                edges = NodeManager.build_chain_edges(parameters[CC_COUNT], parameters[ACTIVE_NODES])
                log.info(f'Building D-LNBot chain topology (n={parameters[CC_COUNT]}, m={parameters[ACTIVE_NODES]}, {len(edges)} edges)...')
                if not manager.build_topology(edges):
                    log.warning('Topology build failed. Retrying...')
                    success = False
                    attempt += 1
                    continue
                log.info('Waiting 10s for node status updates...')
                time.sleep(10)
            elif mode == 'custom':
                edges = NodeManager.load_and_validate_topology(config['topology_file'], parameters[CC_COUNT])
                if edges is None:
                    log.error('Custom topology loading failed. Aborting.')
                    monitor.stop()
                    _active_monitor = None
                    stop_bitcoinminer()
                    return
                log.info(f'Building custom topology ({len(edges)} edges)...')
                if not manager.build_topology(edges):
                    log.warning('Topology build failed. Retrying...')
                    success = False
                    attempt += 1
                    continue
                log.info('Waiting 10s for node status updates...')
                time.sleep(10)
            # dlnbot-formation: cc_manager handles formation, nothing to build here

            # Resolve injection points for this iteration
            if 'inject_nodes' in config:
                # Explicit mode: validate that all specified nodes exist in this iteration
                n = parameters[CC_COUNT]
                for name in config['inject_nodes']:
                    cc_num = int(re.search(r'CC(\d+)', name).group(1))
                    if cc_num < 1 or cc_num > n:
                        error_red(f'{name} does not exist in this iteration (only CC1-CC{n} available).')
                        monitor.stop()
                        _active_monitor = None
                        stop_bitcoinminer()
                        return
            elif testing != INJECTION_COUNT:
                # No --inject specified and not an injection sweep:
                # Default to CC1 for deterministic results across sweep iterations
                config['inject_nodes'] = ['CC1']
                log.info('No --inject specified: defaulting to CC1 for deterministic sweep.')
            # else: injection sweep with no --inject → random selection (deferred to botmaster.py)

            if config.get('takedown', False):
                # Takedown percentage always comes from the sweep variable
                takedown_pct = parameters[TAKEDOWN_PCT] / 100.0
                takedown_strategy = config.get('takedown_strategy', 'random')
                log.info(f'Preparing for {takedown_strategy} takedown of {takedown_pct*100:.0f}% of nodes.')
                if not manager.takedown(config, takedown_pct, takedown_strategy):
                    log.warning('Takedown failed, retrying...')
                    success = False
                    attempt += 1
                    continue

            # Validate injection nodes survived takedown
            if config.get('takedown', False) and 'inject_nodes' in config:
                surviving_cc = {node.name for node in manager.get_cc_nodes()}
                dead_inject = [n for n in config['inject_nodes'] if n not in surviving_cc]
                if dead_inject:
                    log.warning(f'Injection nodes killed during takedown: {dead_inject}')
                    config['inject_nodes'] = [n for n in config['inject_nodes'] if n in surviving_cc]
                    if not config['inject_nodes']:
                        # All injection nodes dead — pick first surviving node
                        fallback = sorted(surviving_cc, key=lambda x: int(re.search(r'\d+', x).group()))[0]
                        config['inject_nodes'] = [fallback]
                        log.warning(f'All injection nodes were killed. Falling back to {fallback}.')
                    log.info(f'Post-takedown injection nodes: {config["inject_nodes"]}')

            log.info('Waiting done, proceeding to testing.')
            message_start_time = time.time()

            '''
            ACTUAL SENDING OF MESSAGES
            '''
            for y in range(1, config['max_messages'] + 1):
                # another wait, just in case we got nodes disconnecting or something
                # Skip for orchestrator-controlled topologies — cc_manager is not running
                if config.get('mode', 'dlnbot') == 'dlnbot-formation':
                    manager.are_channels_ready()

                inject_nodes = config.get('inject_nodes', None)
                manager.send_botmaster_command(y, inject_nodes=inject_nodes, inject_count=parameters[INJECTION_COUNT])
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
                        log.info(f'Command {y} timed out at {get_time()}. Coverage: {coverage_pct*100:.1f}% ({received}/{total} surviving nodes)')
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

                log.info(f'Command {y} is finished at {get_time()}. Propagation time is {send_time} seconds. Coverage: {coverage_pct*100:.1f}%')
                log.info(f'Time: {get_time()}')

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
                log.warning(f'Nodes have not sent propagated message in over {MAX_WAIT} seconds. Attempt is now {attempt}')
        # out of the while loop
        monitor.stop()
        _active_monitor = None
        stop_bitcoinminer()

    log.info(f"FINISHED at {time.time() - overall_test_time} testing for {config['description']}.")
    log.info(f"Testing with: \n{config}")

def wait_for_propagation(command, manager : NodeManager):
    log.info(f'Now waiting for command {command} to propagate.')
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
            log.warning(f'Coverage stalled at {last_received}/{len(data) if data else "?"} nodes for {STALE_TIMEOUT}s. Network likely partitioned.')
            success = False
            break
    if success == None:
        log.warning('Something went wrong in the wait for propagation state. Success == None')
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

    log.info(f'\n{"="*60}')
    log.info(f'  EXECUTION PLAN: {config["description"]}')
    log.info(f'{"="*60}')
    display_names = {
        'cc_count': 'CC node count',
        'active_nodes': 'active neighbors (m)',
        'injection_count': 'injection point count',
        'takedown_pct': 'takedown percentage',
    }
    log.info(f'  Sweep variable : {display_names.get(var_key, var_key)}')
    log.info(f'  Sweep range    : {start} -> {end} (step {step})')
    log.info(f'  Iterations     : {len(iterations)} values: {iterations}')
    log.info(f'  Messages/iter  : {config["max_messages"]}')
    log.info(f'  Topology mode  : {mode}')

    # Show fixed parameters (everything except the sweep variable)
    fixed = {k: v for k, v in params.items() if k != var_key}
    if fixed:
        log.info(f'  Fixed params   : {fixed}')

    if config.get('takedown', False):
        strategy = config.get('takedown_strategy', 'random')
        if var_key == TAKEDOWN_PCT:
            log.info(f'  Takedown       : {strategy}, sweep {start}%-{end}%')

    # Show injection point info
    if 'inject_nodes' in config:
        log.info(f'  Injection from : {", ".join(config["inject_nodes"])}')
    elif var_key == INJECTION_COUNT:
        log.info(f'  Injection from : random nodes (count swept {start}->{end})')
    else:
        log.info(f'  Injection from : CC1 (default)')

    if config.get('topology_file'):
        log.info(f'  Topology file  : {config["topology_file"]}')

    # Display red warnings for confounding/nondeterministic combinations
    for w in config.get('warnings', []):
        print(f'{RED}{BOLD}WARNING: {w}{RESET}')

    log.info(f'{"="*60}')

def confirm_test():
    if input(f'Confirm test? y / n: ').lower() in ['y', 'yes']:
        return True
    else:
        return False

def fund_nodes():
    try:
        subprocess.run(
            [cfg.FUND_WALLETS_BASH]
        )
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        log.error(f"tester failed with exit code {e.returncode}")
        log.error(f"  tester STDOUT: {e.stdout.strip()}")
        log.error(f"  tester STDERR: {e.stderr.strip()}")
        raise # Re-raise the exception so your calling code can catch it
    except Exception as e:
        log.error(f"tester: Exception occurred: {e}")
        return None


def record_total_time(total_time, config, output_suffix="total_times_log.json"):
    '''
    Create a running record of the total time taken for test runs
    along with their configuration(s) to a JSON file.
    '''
    import os
    os.makedirs(cfg.TEST_DATA_DIR, exist_ok=True)
    filename = datetime.datetime.now().strftime(f"data/%Y-%m-%d_{output_suffix}")

    log_entry = {
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_time": total_time,
        "config": config
    }

    with open(filename, 'a') as f:
        f.write(json.dumps(log_entry) + "\n")

    log.info(f'Recorded total time: {total_time} seconds to {filename}')


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
        'version' : cfg.LNTEST_VERSION,
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
        meta_data['takendown_nodes'] = config.get('takendown_nodes', [])
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

    log.info(f'Topology data for {len(all_status)} nodes saved as {top_name}')


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

    filename = f'{cfg.TEST_DATA_DIR}/{var_key}_{values[var_key]}_{id}'

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
        balance = subprocess.run([cfg.BITCOIN_CLI, f'-datadir={cfg.BITCOIN_DIR}', '-regtest', 'getbalance'], capture_output=True)
        balance = balance.stdout.strip().decode()
        if balance == '':
            balance = 0
        else:
            balance = float(balance)

def is_bitcoind_ready(min_balance=100.0):
    '''
    Check if bitcoind is running and has sufficient balance.
    Args:
        min_balance: Minimum BTC balance required (default 100 BTC).
                     A full restart + re-mine is triggered if balance is below this.
    Returns True if bitcoind is ready to use, False otherwise.
    '''
    try:
        result = subprocess.run(
            [cfg.BITCOIN_CLI, f'-datadir={cfg.BITCOIN_DIR}', '-regtest', 'getbalance'],
            capture_output=True, timeout=5
        )
        balance_str = result.stdout.strip().decode()
        if balance_str and float(balance_str) >= min_balance:
            return True
        elif balance_str:
            log.info(f'bitcoind balance too low ({balance_str} BTC < {min_balance} BTC), triggering restart.')
    except Exception:
        pass
    return False

def start_miner():
    '''
    Start just the background miner without restarting bitcoind.
    Uses sys.executable to ensure the same Python (venv) is used.
    '''
    import sys
    rpc_user = cfg.RPC_USER
    rpc_password = cfg.RPC_PASSWORD
    subprocess.Popen(
        [sys.executable, cfg.MINER_SCRIPT, rpc_user, rpc_password, cfg.BITCOIN_CLI],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def restart_bitcoind():
    '''
    Shut down and restart bitcoind for a fresh start.
    '''
    subprocess.Popen(
        [cfg.RESTART_BITCOIND_BASH]
    )

def get_bitcoin_miner():
    '''
    Return the bitcoinminer if its running.
    None otherwise.
    '''
    result = subprocess.run(['pgrep', '-f', f"{cfg.MINER_SCRIPT}"], capture_output=True)
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
            log.info(f"Found and killing the bitcoin miner with pid {id}.")


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
        log.info(f'done with counter at {top_count}')

    return interval, is_done

def get_time():
    return datetime.datetime.now().strftime('%H:%M:%S')


def json_set_converter(obj):
    if isinstance(obj, set):
        return list(obj)

if __name__ == "__main__":
    main()
