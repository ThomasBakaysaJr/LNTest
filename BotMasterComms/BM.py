#NOTE!! this file should be in the BotMasterCommsComms(directory in home address) directory that gets mounted to the BM docker container
import subprocess
import argparse
import json
import logging
import os
import time
import re
import random

# import the ln_checker file
import ln_checker

HOST_NAME = os.getenv("CONTAINER_NAME")
logging.basicConfig(filename=f'bm_log.log', level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s")

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
        # logging.debug(f"run_lightning_cli: stdout: {result.stdout}")
        #logging.debug(f"run_lightning_cli: stderr: {result.stderr}")
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

#this wont be used since in regtest i had to mesh connect all nodes, but in testnet or mainnet this would be useful. In regtest, addresses are not properly displayed
def get_node_address(node_id):
    """
    Retrieve the address and port for a specific node ID using the 'listnodes' command.

    Args:
        node_id (str): The node ID of the target node.

    Returns:
        tuple: A tuple containing the IP address and port, or (None, None) if not found.
    """
    output = run_lightning_cli(["listnodes", node_id])
    if not output:
        logging.warning(f"Failed to retrieve node details for node ID: {node_id}.")
        return None, None

    try:
        node_details = json.loads(output).get("nodes", [])
        if not node_details:
            logging.warning(f"No details found for node ID: {node_id}.")
            return None, None

        addresses = node_details[0].get("addresses", [])
        if addresses:
            ip_address = addresses[0].get("address")
            port = addresses[0].get("port", 9735)  # Default port
            return ip_address, port
        else:
            logging.warning(f"No address found for node ID: {node_id}.")
            return None, None
    except json.JSONDecodeError:
        logging.error("Error parsing listnodes output.")
        return None, None


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


def fund_channels(num_channels = 1, entry_point = -1.0):
    '''
    Discover valid CC nodes and fund channels.
    Will return saved funded channels if they exist, to fund completely new channels
    you must call disconnect_all_channels() first.
    Parameters:
        num_channels: number of channels to create (default is 1)
        entry_point: percentage of where in the network to make these channels.
                    negative = random, 0 = first nodes, 1 = last nodes. (default is -1.0)
    '''
    # Load funded nodes if we have this saved
    funded_nodes = load_funded_nodes()

    if entry_point > 100:
        final_num_channels = num_channels * 3
    else:
        final_num_channels = num_channels

    # if the nodes are alreadyh funded then just return those.
    if len(funded_nodes) == final_num_channels:
        return funded_nodes
    
    # if we get to this point, then we need additional nodes to channel to, or we just haven't channeled to any
    # same behavior either way
    discovered_nodes = discover_cc_nodes()
    while not discovered_nodes:
        logging.info("No valid CC nodes found for funding. Retrying in 10 seconds")
        time.sleep(10)
        discovered_nodes = discover_cc_nodes()

    valid_cc_nodes = [cc_node for cc_node in discovered_nodes if cc_node not in funded_nodes]

    for _ in range(RETRY_MAX):
        new_nodes = pick_nodes(num_channels, entry_point, valid_cc_nodes)
    
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
            print(f'BM node has channeled with {num_channels} nodes successfully.')
            logging.info(f'fund_channels: BM node has channeled with {num_channels} nodes successfully.')
            break
    
    if len(funded_nodes) != final_num_channels:
        print(f'BM has failed to channel with {num_channels} nodes. Aborting')
        logging.error(f'fund_channels: Only channels with {len(funded_nodes)} instead of {num_channels} nodes. Returning None')
        return []
    else:
        return funded_nodes

def pick_nodes(num_nodes, entry_point, valid_nodes):
    '''
    Top level function for picking nodes.
    entry_point:
        <0 : random
        0-100 : position in the network
        >0 : bottom, middle and top of the network
    '''
    if entry_point < 0:
        return pick_random_nodes(num_nodes, valid_nodes)
    if entry_point > 100:
        # we want nodes from all three sections of the network
        return_nodes = set()
        for pos in range(0, 101, 50):
            return_nodes.update(select_nodes_from_list(num_nodes, pos, valid_nodes))
        return list(return_nodes)
    else:
        # normal behavior, pick nodes from the designated position
        return select_nodes_from_list(num_nodes, entry_point, valid_nodes)

def select_nodes_from_list(num_nodes, entry_point, valid_nodes):
    '''
    Helper function to pick nodes from a set of nodes,
    using entry_point as a guide of where to pick the nodes.
    This should only be called by pick nodes.
    '''
    if num_nodes > len(valid_nodes):
        logging.error(f'pick_nodes: Trying to select more nodes than exists in valid nodes. Aborting.')
        return []
    
    starting_ind = round(len(valid_nodes) * (entry_point / 100.0))

    # get how many nodes from the 'center' node do we look for
    deviation = (num_nodes - 1) // 2
    left = deviation
    right = deviation
    if num_nodes % 2 == 0:
        # if its even
        right += 1

    # Make sure we're not out of bounds
    if (margin := starting_ind - left) < 0:
        right += abs(margin)
        left += margin
    # keeping in mind, starting ind of 0
    elif (margin := (len(valid_nodes) - 1) - (starting_ind + right)) < 0:
        left += abs(margin)
        right += margin

    logging.info(f'slice is {starting_ind - left} : {starting_ind + right + 1}')

    # we return the slice of nodes to connect to
    return valid_nodes[starting_ind - left: starting_ind + right + 1]

def pick_random_nodes(num_nodes, valid_nodes):
    '''
    Pick num_nodes random nodes from a set of nodes.
    Returns a set.
    '''
    return random.sample(valid_nodes, num_nodes)
    

def save_funded_nodes(nodes):
    '''
    Save the funded nodes into a file. 
    Comma delineated.
    '''
    nodes_text = ",".join(nodes)

    with open(FUNDED_NODE_FILE, 'w') as file:
        file.write(nodes_text)
    
    logging.debug(f'save_funded_nodes: Funded nodes saved')


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

def interactive_command_sender():
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
        send_msg(user_input, counter)


    save_counter(counter)  # Save the counter when exiting

def encode_msg(in_msg):
    return in_msg.encode('utf-8').hex()

def send_msg(message, counter, funded_nodes):
    # Concatenate the user input with the counter
    message_with_counter = f"{message}|{counter}"

    # # Ensure the message is enclosed in double quotes
    # message = f'"{message_with_counter}"'
    tlv_json = json.dumps({MESSAGE_TLV_TYPE : encode_msg(message_with_counter)})
    # amount = 5  # Minimal msat for sending a message

    while load_counter() == counter:
        for node in funded_nodes:
            # Construct the lightning-cli command
            command = ["lightning-cli", "--regtest", "keysend",
            f"destination={node}",
            f"amount_msat=1",
            f"extratlvs={tlv_json}"]

            try:
                # Execute the command using shell=True to process the quotes correctly
                for _ in range(RETRY_MAX):
                    if ln_checker.wait_node_activated(node):
                        break
                    else:
                        logging.info(f'No active channel with {node} - attempting to re-channel.')
                        print('Channel disconneted, retrying connection.')
                        if not connect_and_channel_node(node):
                            logging.error(f'send_msg: ERROR: Could not connect to node. Aborting.')
                else:
                    print(f'BM cannot create channel with {node} after {RETRY_MAX} tries. Aborting')
                    logging.error(f'send_msg: ERROR: BM cannot create channel with {node} after {RETRY_MAX} tries. Aborting')
                    return False

                result = subprocess.run(command,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        text=True,
                                        check=True)
                if result.returncode == 0:
                    print(f"Command '{message_with_counter}' sent to node {FUNDED_NODE_IDS} successfully.")
                    print(f"Response: {result.stdout}")
                    save_counter(counter + 1)  # Save the updated counter after a successful send
                    return
                else:
                    print(f"Error sending command '{message_with_counter}' to node {FUNDED_NODE_IDS}: {result.stderr}")
                    logging.error(f"Error sending command '{message_with_counter}' to node {FUNDED_NODE_IDS}: {result.stderr}")
            except Exception as e:
                    print(f"Exception occurred while sending command: {e}")
                    logging.error(f"Exception occurred while sending command: {e}\nRetrying . . .")
        time.sleep(5)
        
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


def main(goal, message, num_channels, entry_point, start_fresh):

    """
    Main Botmaster logic.
    """
    logging.info("Starting Botmaster Node Script")
    connect_to_innocent() # we need to connect to innocent to see channel gossip
    load_this_node()
    logging.info(f"Funding {num_channels} channel(s)")
    # Fund num_channels channels with valid CC nodes
    funded_nodes = fund_channels(num_channels, entry_point)

    if start_fresh:
        logging.info(f'main: Closing and disconnceting from all nodes. Starting fresh')
        save_counter('0')
        disconnect_all_channels(funded_nodes)

    elif not funded_nodes:
        funded_nodes = fund_channels(num_channels, entry_point)

    # Allow interactive command sending
    if goal == 1:
        interactive_command_sender(funded_nodes)
    # used for automatic testing
    elif goal == 2:
        counter = load_counter()
        send_msg(message, counter, funded_nodes)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='BotMaster Command and Control Manager')
    # add an option to take in "clear" to disconnect all channels and start fresh
    parser.add_argument('--msg',
                        help = '''
                        The message to send to the botnet.
                        ''')
    parser.add_argument('--cc', type = int, default = 1,
                        help = '''
                        Number of CC servers to send the command to.''')
    parser.add_argument('--init', type = float, default = 0.0,
                        help = '''
                        Where in the botnet to connect as a percentage of the network.
                        <0  : Random
                        0.0 : Oldest nodes
                        50.0 : Middle of the network
                        100.0 : Youngest Nodes
                        >0 : Oldest, middle and youngest nodes'''
                        )
    parser.add_argument('--fresh', action = 'store_true',
                        help = '''
                        Close and disconnect all previously funded nodes.
                        ''')
    
    args = parser.parse_args()


    if args.msg:
        main(2, args.msg, args.cc, args.init, args.fresh)
    else:
        if input(f'Starting botmaster. Will connect to {args.cc} nodes at {args.init * 100}% of the network. Continue? y/n').lower() in ['y', 'yes']:
            main(1, args.msg, args.cc, args.init, args.fresh)
        else:
            print(f'Exiting Botmaster script.')
