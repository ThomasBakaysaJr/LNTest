#NOTE!! this file should be in the BotMasterCommsComms(directory in home address) directory that gets mounted to the BM docker container
import subprocess
import json
import logging
import os
import time
import sys

# import the ln_checker file
import ln_checker

HOST_NAME = os.getenv("CONTAINER_NAME")
logging.basicConfig(filename=f'bm_log.log', level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s")

# Read Innocent Node Address and ID from files
with open('innocentAddress.txt', 'r') as address_file:
    INNOCENT_NODE_ADDRESS = address_file.read().strip()
with open('innocentID.txt', 'r') as id_file:
    INNOCENT_NODE_ID = id_file.read().strip()

with open('CC_address_list.txt', 'r') as id_file:
    CC_ADDRESS_LIST = id_file.read().strip()


DISCOVERY_RULE_DIVISOR = 19  # Discovery rule divisor
BM_CONNECTED_NODES = set()  # To track already connected nodes
MAX_BM_CHANNELS = 1  # Maximum number of channels BM can fund
UNIQUE_FUNDING_AMOUNT = 12312300  # A fixed, unrelated amount for BM funding
COUNTER_FILE = "counter.txt"  # File to store the counter
FUNDED_NODE_FILE = "funded_node.txt"
AUTO_TEST_COUNT = 10 # How many commands for auto_test, default is 100

THIS_NODE = None

# Global variable for the node BM funded a channel with
FUNDED_NODE_ID = None
# The TLV record type used for standard text messages in keysend.
MESSAGE_TLV_TYPE = "34349334"

def load_counter():
    """
    Load the counter from the counter file. If the file doesn't exist, initialize it to 0.
    """
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as file:
            try:
                return int(file.read().strip())
            except ValueError:
                return 0  # If the file is corrupted, reset to 0
    return 0

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
            check=True
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
        with open('CC_address_list.txt', 'r') as id_file:
            CC_ADDRESS_LIST = id_file.readlines()

        # Find the full address corresponding to the node_ID
        full_address = None
        for address in CC_ADDRESS_LIST:
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


def discover_cc_nodes():
    """
    Discover CC nodes that satisfy the discovery rule.
    """
    logging.info("Discovering CC nodes.")
    output = run_lightning_cli(["listchannels"])
    if not output:
        logging.warning("Failed to retrieve channel list.")
        return []

    channels = json.loads(output).get("channels", [])
    valid_nodes = []
    for channel in channels:
        capacity = int(channel.get("amount_msat", 0)) // 1000
        node_id = channel["destination"]

        # Exclude Innocent node and nodes already connected
        if node_id == INNOCENT_NODE_ID:
            logging.info(f"Skipping Innocent node {INNOCENT_NODE_ID} during discovery.")
            continue

        if capacity % DISCOVERY_RULE_DIVISOR == 0 and node_id not in BM_CONNECTED_NODES:
            valid_nodes.append(node_id)

    logging.info(f"Valid CC nodes discovered: {valid_nodes}")
    return valid_nodes



def fund_single_channel():
    """
    Discover a valid CC node and fund a channel with it.
    """
    global FUNDED_NODE_ID

    # Check if we have this saved already
    if os.path.exists(FUNDED_NODE_FILE):
        logging.info(f'Found funded node file, loading.')
        with open(FUNDED_NODE_FILE, 'r') as file:
            FUNDED_NODE_ID = file.read().strip()
        # make sure we have a open channel with this node
        if ln_checker.has_channel_with(FUNDED_NODE_ID):
            logging.info(f"Channel found from \n{THIS_NODE}\nto\n{FUNDED_NODE_ID}")
            return
        else:
            # IF NO channel is found, continue and lets find a different node (or just fund this one)
            logging.info(f"Channel NOT FOUND from\n{THIS_NODE}\nto\n{FUNDED_NODE_ID}")
            FUNDED_NODE_ID = None
    else:
        logging.info(f'No funded file found, finding node to connect to.')

    if len(BM_CONNECTED_NODES) >= MAX_BM_CHANNELS:
        logging.info(f"Maximum BM channels ({MAX_BM_CHANNELS}) reached. Skipping funding.")
        return

    valid_cc_nodes = discover_cc_nodes()
    while not valid_cc_nodes:
        logging.info("No valid CC nodes found for funding. Retrying")
        time.sleep(10)
        valid_cc_nodes = discover_cc_nodes()

    # Pick one node to fund a channel with
    target_node = valid_cc_nodes[0]

    # Ensure the target is not the Innocent node (extra safeguard)
    if target_node == INNOCENT_NODE_ID:
        logging.error("Attempt to fund a channel with Innocent node detected! Aborting.")
        return

    # Retrieve the address and port for the target node
    # Uncomment this for TESTNET/MAINNET
    # address, port = get_node_address(target_node)
    # if not address or not port:
    #     logging.error(f"Could not retrieve address or port for node {target_node}. Skipping.")
    #     return

    # Connect to the target node (Uncomment for TESTNET/MAINNET)
    # logging.info(f"Connecting to node {target_node} at {address}:{port}.")
    # run_lightning_cli(["connect", f"{target_node}@{address}:{port}"])

    #this will allow the CCs to connect to each other by themselves instead of having to mesh connect before hand
    if not FUNDED_NODE_ID:
        demoGetAddressAndConnect(target_node)
        
        logging.info(f"Funding channel with node {target_node} (amount: {UNIQUE_FUNDING_AMOUNT}).")
        ln_checker.check_funds()
        run_lightning_cli(["fundchannel", target_node, str(UNIQUE_FUNDING_AMOUNT)])
    print(f'Funding channel with {target_node}')
    # make sure channel is ready to receive
    ln_checker.wait_node_activated(target_node)
    print(f'Connected.')
    BM_CONNECTED_NODES.add(target_node)
    FUNDED_NODE_ID = target_node  # Save the funded node ID
    with open(FUNDED_NODE_FILE, 'w') as file:
        file.write(target_node)

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
        counter += 1

    save_counter(counter)  # Save the counter when exiting

def encode_msg(in_msg):
    return in_msg.encode('utf-8').hex()

def send_msg(message, counter):
    # Concatenate the user input with the counter
    message_with_counter = f"{message}|{counter}"

    # # Ensure the message is enclosed in double quotes
    # message = f'"{message_with_counter}"'
    tlv_json = json.dumps({MESSAGE_TLV_TYPE : encode_msg(message_with_counter)})
    # amount = 5  # Minimal msat for sending a message

    # Construct the lightning-cli command
    command = ["lightning-cli", "--regtest", "keysend",
    f"destination={FUNDED_NODE_ID}",
    f"amount_msat=1",
    f"extratlvs={tlv_json}"]

    try:
        # Execute the command using shell=True to process the quotes correctly
                
        if not ln_checker.is_node_active(FUNDED_NODE_ID):
            logging.info(f'No active channel with {FUNDED_NODE_ID} - finding new active channel')
            print('Channel disconneted, retrying connection.')
            fund_single_channel()
            
        result = subprocess.run(command,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                check=True)
        if result.returncode == 0:
            print(f"Command '{message_with_counter}' sent to node {FUNDED_NODE_ID} successfully.")
            print(f"Response: {result.stdout}")
            save_counter(counter)  # Save the updated counter after a successful send
        else:
            print(f"Error sending command '{message_with_counter}' to node {FUNDED_NODE_ID}: {result.stderr}")
            logging.error(f"Error sending command '{message_with_counter}' to node {FUNDED_NODE_ID}: {result.stderr}")
    except Exception as e:
            print(f"Exception occurred while sending command: {e}")
            logging.error(f"Exception occurred while sending command: {e}")
            
def load_this_node ():
    global THIS_NODE 
    output = get_node_info()
    THIS_NODE = output.get('id')

def auto_send(command):
    '''
    auto_send [command] when node is ready to receive.
    used for testing.
    '''
    send_msg(command)

def main(goal, message):
    """
    Main Botmaster logic.
    """
    logging.info("Starting Botmaster Node Script.")
    connect_to_innocent()
    load_this_node()
    logging.info("Funding single channel")
    # Fund a single channel with a valid CC node
    fund_single_channel()
    if not FUNDED_NODE_ID:
        print("No node has been funded yet. Exiting command sender.")
        return
    # Allow interactive command sending
    if goal == 1:
        interactive_command_sender()
    elif goal == 2:
        send_msg(message, message)



if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(2, sys.argv[1])
    else:
        main(1, None)