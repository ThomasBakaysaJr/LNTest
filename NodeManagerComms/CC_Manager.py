#NOTE this file should be in the NodeManagerComms(directory in home address) directory that gets mounted to the CC docker container



#nodeManagerFinalVersion.py
# This script is designed to run on a Lightning Network node (c-lightning) and manage the discovery of CC nodes and creating channels with them 
# centered on a discovery rule. The script will connect to an "Innocent" node and fund a channel meeting the discovery rule amount. It will then connect and 
# create channels with other nodes that meet the discovery rule, while avoiding duplicates and blacklisted nodes (i.e. innocent node). 
# The script will avoid connecting to nodes that already have a channel with a peer. 
# The script will also close the channel with the Innocent node and disconnect once the maximum number of peers with channels 
# is reached, making the CC node no longer discoverable.
# The script is designed to run in a loop, creating channels with new nodes every 30 seconds. 

import subprocess
import json
import time
import logging

import os

# Constants
DISCOVERY_RULE_DIVISOR = 19  # Capacity must be divisible by 19 (prime number)
MAX_PEERS = 4  # Maximum number of peers

INNOCENT_NODE_ID = None
INNOCENT_NODE_ADDRESS = None
CC_ADDRESS_LIST = None

BLACKLISTED_NODES = {}# Nodes blacklisted for fundchannel

channel_created_nodes = set()
innocent_channel_closed = False

# Cache for nodes already queried
seen_nodes_cache = {}  # Format: {<target_node_id>: <timestamp>}
CACHE_EXPIRATION_TIME = 3600  # Cache entries expire after 1 hour

HOST_NAME = os.getenv("CONTAINER_NAME")

logging.basicConfig(filename=f'cc_logs_{HOST_NAME}.log', level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(message)s")

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
        logging.info(f"run_lightning_cli: stdout: {result.stdout}")
        logging.info(f"run_lightning_cli: stderr: {result.stderr}")
        if result.returncode != 0:
            logging.error(f"run_lightning_cli: Command failed with error: {result.stderr.strip()}")
            return None
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        logging.error(f"lightning-cli command failed with exit code {e.returncode}")
        logging.error(f"  lightning-cli STDOUT: {e.stdout.strip()}")
        logging.error(f"  lightning-cli STDERR: {e.stderr.strip()}")
        raise # Re-raise the exception so your calling code can catch it
    except Exception as e:
        logging.error(f"run_lightning_cli: Exception occurred: {e}")
        return None

def get_node_info():
    """
    Retrieve node information, including its ID.
    """
    output = run_lightning_cli(["getinfo"])
    return json.loads(output) if output else None

def evaluate_discovery_rule(capacity):
    """
    Check if the given capacity satisfies the discovery rule.
    """
    return capacity % DISCOVERY_RULE_DIVISOR == 0

def connect_to_innocent():
    """
    Connect to the Innocent node and fund a channel if necessary.
    """
    global innocent_channel_closed
    if innocent_channel_closed:
        logging.info("Channel with Innocent Node has been closed. Skipping connection.")
        return

    logging.info(f"Connecting to Innocent Node: {INNOCENT_NODE_ADDRESS}")
    run_lightning_cli(["listfunds"])
    run_lightning_cli(["connect", INNOCENT_NODE_ADDRESS])
    
    # Check if we already have a channel with the Innocent Node
    peers_with_channels = list_peers_with_channels()
    if INNOCENT_NODE_ID not in peers_with_channels:
        # Calculate funding amount based on the discovery rule
        funding_amount = DISCOVERY_RULE_DIVISOR * 10000

        try:
            logging.info(f"No channel with Innocent Node. Funding a channel with funding amount: {funding_amount}")
            # seeing if this helps the funding problems
            check_funds()
            logging.info('Made it out of check funds, funding channgel with inno node now.')
            run_lightning_cli(["fundchannel", INNOCENT_NODE_ID, str(funding_amount)])
            logging.info(f"so the command ran, but uh. yeah.")
        except Exception as e:
            logging.error(f"Funding failed: {e}")
    else:
        logging.info("Channel with Innocent Node already exists. Skipping fundchannel.")

#DON'T TAKE THIS OUT this won't get used in Regtest
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
        logging.error(f"Failed to retrieve node details for node ID: {node_id}.")
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

#For testnet/mainnet, in regtest you'd have to manually connect nodes together because listnodes wont properly display the address needed to connect to a node
def connect_to_node(node_id):
    """
    Connect to a node given its node ID by retrieving its address and port.

    Args:
        node_id (str): The node ID of the target node.
    """
    ip_address, port = get_node_address(node_id)
    if ip_address and port:
        node_address = f"{node_id}@{ip_address}:{port}"
        logging.info(f"Connecting to node: {node_address}")
        run_lightning_cli(["connect", node_address])
    else:
        logging.warning(f"Could not retrieve address for node {node_id}. Skipping connection.")



def list_peers():
    """
    List all connected peers and extract their IDs.
    """
    logging.info("list_peers: Fetching list of peers.")
    output = run_lightning_cli(["listpeers"])
    if not output:
        logging.error("list_peers: Failed to retrieve peer list.")
        return set()
    try:
        peers = json.loads(output).get("peers", [])
        peer_ids = set(peer["id"] for peer in peers)
        logging.info(f"list_peers: Found peers: {peer_ids}")
        return peer_ids
    except json.JSONDecodeError:
        logging.error("list_peers: Failed to parse peer list output.")
        return set()

def channeled_with_peer(node_id):
    """
    Check if any of our channel peers (excluding the Innocent node) have a channel with the given node_id.
    """
    logging.info(f"channeled_with_peer: Checking if any channel peer (excluding Innocent node) has a channel with node {node_id}")

    #For Regtest with mesh connected CCs, Get the list of peers we have channels with (excluding the Innocent node)
    peer_ids = list_peers_with_channels() - {INNOCENT_NODE_ID}

    #for TESTNET/MAINNET 
    #peer_ids = list_peers() - {INNOCENT_NODE_ID}

    if not peer_ids:
        logging.info("channeled_with_peer: No channel peers to check.")
        return False

    # Retrieve all known channels
    output = run_lightning_cli(["listchannels"])
    if not output:
        logging.error("channeled_with_peer: Failed to retrieve channel list.")
        return False

    try:
        channels = json.loads(output).get("channels", [])
        for channel in channels:
            source = channel["source"]
            destination = channel["destination"]
            if source in peer_ids and destination == node_id:
                logging.info(f"channeled_with_peer: Peer {source} has a channel with node {node_id}")
                return True
        logging.info(f"channeled_with_peer: No peer has a channel with node {node_id}")
        return False
    except json.JSONDecodeError:
        logging.error("channeled_with_peer: Failed to parse channel list output.")
        return False

#This will be implemented some other time, right now it's not necessary 
def wait_for_channel_active(peer_id, timeout=60):
    """
    Wait for the channel with the given peer_id to become active.

    Args:
        peer_id (str): The node ID of the peer.
        timeout (int): Maximum time to wait in seconds.

    Returns:
        bool: True if the channel becomes active, False if timeout occurs.
    """
    logging.info(f"wait_for_channel_active: Waiting for channel with {peer_id} to become active.")
    start_time = time.time()
    while time.time() - start_time < timeout:
        output = run_lightning_cli(["listchannels"])
        if not output:
            logging.error("wait_for_channel_active: Failed to retrieve channel list.")
            time.sleep(2)
            continue

        try:
            channels = json.loads(output).get("channels", [])
            for channel in channels:
                # Check if the channel involves the peer and is active
                if ((channel.get("source") == peer_id or channel.get("destination") == peer_id) and channel.get("active")):
                    logging.info(f"wait_for_channel_active: Channel with {peer_id} is now active.")
                    return True
        except json.JSONDecodeError:
            logging.error("wait_for_channel_active: Failed to parse channel list output.")

        time.sleep(2)
    logging.error(f"wait_for_channel_active: Timeout waiting for channel with {peer_id} to become active.")
    return False


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



def create_channels():
    """
    Create channels only with nodes that meet the discovery rule and avoid duplicates.
    """
    global innocent_channel_closed
    logging.info("create_channels: Starting channel creation process.")

    peers_with_channels = list_peers_with_channels()
    logging.info(f"create_channels: Current peers with channels: {peers_with_channels}")

    # Exclude Innocent node from peers_with_channels
    if INNOCENT_NODE_ID in peers_with_channels:
        peers_with_channels_excl_innocent = peers_with_channels - {INNOCENT_NODE_ID}
    else:
        peers_with_channels_excl_innocent = peers_with_channels

    if len(peers_with_channels_excl_innocent) >= MAX_PEERS:
        logging.info("create_channels: Max peers with channels reached, no more channels will be created.")
        # Now we need to close the channel with the Innocent node and disconnect
        if not innocent_channel_closed:
            close_and_disconnect_innocent()
        return

    valid_nodes = discover_nodes()
    if not valid_nodes:
        logging.warning("create_channels: No valid nodes discovered. Aborting channel creation.")
        return

    for node in valid_nodes:
        if len(peers_with_channels_excl_innocent) >= MAX_PEERS:
            logging.info("create_channels: Reached maximum peers with channels while processing nodes.")
            # Close the channel with the Innocent node and disconnect
            if not innocent_channel_closed:
                close_and_disconnect_innocent()
            break

        peer_id = node["node_id"]

        # Skip blacklisted nodes and nodes with existing channels
        if peer_id in BLACKLISTED_NODES:
            logging.info(f"create_channels: Skipping blacklisted node {peer_id}.")
            continue
        if peer_id in peers_with_channels:
            logging.info(f"create_channels: Skipping node {peer_id} as a channel already exists.")
            continue
        if peer_id in channel_created_nodes:
            logging.info(f"create_channels: Skipping node {peer_id} as a channel already exists.")
            continue
        if channeled_with_peer(peer_id):
            logging.info(f"create_channels: Skipping node {peer_id} as our peer already has a channel with it.")
            continue

        # Uncomment the following line in Testnet/Mainnet to connect to the node before funding a channel
        # connect_to_node(peer_id)

        #this will allow the CCs to connect to each other by themselves instead of having to mesh connect before hand
        demoGetAddressAndConnect(peer_id)

        # Calculate funding amount based on the current time
        current_minutes = int(time.strftime("%M"))
        funding_amount = current_minutes * 10000

        logging.info(f"create_channels: Opening channel with node {peer_id}. Funding amount: {funding_amount}")
        check_funds()
        result = run_lightning_cli(["fundchannel", peer_id, str(funding_amount)])
        if result:
            logging.info(f"create_channels: Channel successfully created with node {peer_id}.")

            # Wait for 30 seconds to allow the channel to become active
            logging.info("create_channels: Waiting for 60 seconds for the channel to confirm.")
            time.sleep(60)

            # Calculate the amount to send via keysend (e.g., 50% of the funding amount)
            keysend_amount_msat = (funding_amount * 1000) // 2  # Convert to msat and take half

            logging.info(f"create_channels: Sending {keysend_amount_msat} msat to node {peer_id} via keysend.")
            keysend_result = run_lightning_cli(["keysend", peer_id, str(keysend_amount_msat)])
            if keysend_result:
                logging.info(f"create_channels: Successfully sent {keysend_amount_msat} msat to node {peer_id}.")
            else:
                logging.error(f"create_channels: Failed to send keysend payment to node {peer_id}.")

            channel_created_nodes.add(peer_id)  # Track this node
            peers_with_channels.add(peer_id)     # Update peers set
            peers_with_channels_excl_innocent.add(peer_id)
        else:
            logging.error(f"create_channels: Failed to create channel with node {peer_id}.")




def list_peers_with_channels():
    """
    List all peer IDs that have at least one channel with this node.
    """
    logging.info("list_peers_with_channels: Fetching peers with active channels.")

    # Retrieve own node ID
    node_info = get_node_info()
    own_node_id = node_info["id"] if node_info else None

    if not own_node_id:
        logging.error("list_peers_with_channels: Failed to retrieve own node ID.")
        return set()

    # Query listchannels with the source set to own_node_id
    output = run_lightning_cli(["listchannels", f'source={own_node_id}'])
    if not output:
        logging.error("list_peers_with_channels: Failed to retrieve channel list.")
        return set()

    # Parse the output and collect unique destination node IDs
    try:
        channels = json.loads(output).get("channels", [])
        peers_with_channels = set(
            channel["destination"]
            for channel in channels
        )
        logging.info(f"list_peers_with_channels: Found {len(peers_with_channels)} peers with channels.")
        return peers_with_channels
    except json.JSONDecodeError:
        logging.error("list_peers_with_channels: Failed to parse channel list output.")
        return set()

    
    # Earlier version (commented out) for TESTNET/MAINNET
    """
    output = run_lightning_cli(["listpeers"])
    if not output:
        logging.error("list_peers_with_channels: Failed to retrieve peer list.")
        return []

    peers = json.loads(output).get("peers", [])
    peers_with_channels = [peer for peer in peers if peer.get("num_channels", 0) > 0]
    logging.info(f"list_peers_with_channels: Found {len(peers_with_channels)} peers with channels.")
    return peers_with_channels
    """

def discover_nodes():
    """
    Discover nodes strictly based on existing channels that meet the discovery rule.
    """
    logging.info("discover_nodes: Discovering nodes with valid channels.")
    node_info = get_node_info()
    own_node_id = node_info["id"] if node_info else None
    valid_nodes = []

    output = run_lightning_cli(["listchannels"])
    if not output:
        logging.error("discover_nodes: Failed to retrieve channel list.")
        return valid_nodes

    channels = json.loads(output).get("channels", [])
    logging.info(f"discover_nodes: Total channels found: {len(channels)}")

    for channel in channels:
        if not evaluate_discovery_rule(int(channel.get("amount_msat", 0)) // 1000):
            continue

        node_id = channel["source"]
        if node_id == own_node_id or node_id in BLACKLISTED_NODES:
            continue

        valid_nodes.append({"node_id": node_id})

    logging.info(f"discover_nodes: Valid nodes discovered: {valid_nodes}")
    return valid_nodes

def close_and_disconnect_innocent():
    """
    Close the channel with the Innocent node and disconnect.
    """
    global innocent_channel_closed
    logging.info(f"Closing channel with Innocent Node: {INNOCENT_NODE_ID}")
    run_lightning_cli(["close", f"id={INNOCENT_NODE_ID}"])
    logging.info(f"Disconnecting from Innocent Node: {INNOCENT_NODE_ID}")
    run_lightning_cli(["disconnect", INNOCENT_NODE_ID])
    innocent_channel_closed = True

def main():
    """
    Main script loop.
    """
    logging.info("Starting node manager script...")
    logging.info(f"Working directory is {os.getcwd()}")

    sleep_int = 1
    attempt_max = 10

    global INNOCENT_NODE_ID
    global INNOCENT_NODE_ADDRESS 
    global CC_ADDRESS_LIST
    
    for attempt in range(attempt_max):
        try:
            with open('innocentAddress.txt', 'r') as address_file:
                INNOCENT_NODE_ADDRESS = address_file.read().strip()
            with open('innocentID.txt', 'r') as id_file:
                INNOCENT_NODE_ID = id_file.read().strip()

            with open('CC_address_list.txt', 'r') as id_file:
                CC_ADDRESS_LIST = id_file.read().strip()
            
            BLACKLISTED_NODES = {INNOCENT_NODE_ID}
            logging.info(f"Found Innocent node at {INNOCENT_NODE_ADDRESS}")
            break
        except Exception as e:
            logging.info(f"Error loading the files: {e}")

        if attempt >= attempt_max - 1:
            logging.info(f"Cant find innocent node file after {attempt_max} tries. CATASTROPHIC ERROR DUDE")
            return

        logging.info(f"Can't find required files. Retrying in {sleep_int} seconds")
        time.sleep(sleep_int)

    time.sleep(sleep_int)

    connect_to_innocent()   

    while True:
        try:
            create_channels()
            logging.info("main: Sleeping for 10 seconds.")
            time.sleep(30)
        except KeyboardInterrupt:
            logging.info("main: Script terminated by user.")
            break
        except Exception as e:
            logging.error(f"main: An error occurred in the main loop: {e}")
            time.sleep(5)


# thomas functions
# check and make sure we have funds before we try anything since it takes a while
# for the funds to actually become available
def check_funds():
    funds_available = False
    while not funds_available:
        try:
            logging.info(f"checkfunds: Making sure funds are available.")
            result = subprocess.run(
                ["lightning-cli", "--regtest"] + ['listfunds'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True
            )
        except subprocess.CalledProcessError as e:
            # This is where the error from lightning-cli lives!
            logging.error(f"lightning-cli command failed with exit code {e.returncode}")
            logging.error(f"  lightning-cli STDOUT: {e.stdout.strip()}")
            logging.error(f"  lightning-cli STDERR: {e.stderr.strip()}") 
            raise # Re-raise the exception so your calling code can catch it
        except Exception as e:
            logging.error(f"check_funds: Exception occurred: {e}")
            return None
        
        # check output if funds are available
        result_data = json.loads(result.stdout)
        if result_data.get('outputs'):
            on_chain_funds_exist = False
            total_on_chain_msat = 0

            for output in result_data['outputs']:
                if output.get('status') == 'confirmed':
                    logging.info('we founds confirmed funds')
                    on_chain_funds_exist = True
                    total_on_chain_msat += output.get('amount_msat', 0)

            if on_chain_funds_exist:
                logging.info(f"On-chain funds found! Total confirmed and spendable: {total_on_chain_msat / 1000:.8f} BTC")
                funds_available = True
                return
            else:
                logging.info("No confirmed and spendable on-chain funds found.\n\
                      Checking again in 5 seconds.")
        logging.info('Sleeping in check funds')
        time.sleep(5)
            
    

if __name__ == "__main__":
    main()
