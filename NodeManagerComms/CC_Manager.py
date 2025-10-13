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
from pathlib import Path
import random
import sys
import os

import ln_checker

# Constants
DISCOVERY_RULE_DIVISOR = 19  # Capacity must be divisible by 19 (prime number)
BM_DIVISOR = 123123 # used to not disconnect from the BM node when its trying to send a command
MAX_ACTIVE_NODES = 4 # number of active nodes (n in the paper)
MAX_PEERS = 4     # Maximum number of peers

INNOCENT_NODE_ID = None
INNOCENT_NODE_ADDRESS = None
CC_ADDRESS_LIST = None

BLACKLISTED_NODES = {}# Nodes blacklisted for fundchannel

outbound_channels = set()
INNOCENT_CHANNEL_CLOSED = False
CHANNELS_CREATED = False
# for channels that need to restart
CHANNEL_OPENING_TIMES = {}

# Cache for nodes already queried
seen_nodes_cache = {}  # Format: {<target_node_id>: <timestamp>}
CACHE_EXPIRATION_TIME = 3600  # Cache entries expire after 1 hour

# HOW OFTEN the script looks for new channels
CHANNEL_SLEEP_INT = 10
CHANNEL_CHECK_SLEEP_INT = 60

# wait counter for how often to attempt balancing the channels
CHANNEL_BALANCE_COUNTER = 3

HOST_NAME = os.getenv("CONTAINER_NAME")

THIS_NODE = None

LOG_DIR = Path('logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file_path = LOG_DIR / f'cc_log_{HOST_NAME}.log'
logging.basicConfig(filename=log_file_path, level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s")

def run_lightning_cli(command):
    try:
        # logging.info(f"run_lightning_cli: Running command: {' '.join(command)}")
        result = subprocess.run(
            ["lightning-cli", "--regtest"] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # This is where the error from lightning-cli lives!
        logging.error(f"lightning-cli command failed with exit code {e.returncode}")
        logging.error(f"  lightning-cli STDOUT: {e.stdout.strip()}")
        logging.error(f"  lightning-cli STDERR: {e.stderr.strip()}")
        return None
    except Exception as e:
        logging.error(f"run_lightning_cli: Exception occurred: {e}")
        return None

def main(max_active_nodes):
    """
    Main script loop.
    """
    logging.info("Starting node manager script...")

    sleep_int = 1
    attempt_max = 10

    global INNOCENT_NODE_ID
    global INNOCENT_NODE_ADDRESS 
    global CC_ADDRESS_LIST
    global MAX_PEERS
    global MAX_ACTIVE_NODES
    
    # setting some important variables.
    MAX_ACTIVE_NODES = max_active_nodes
    MAX_PEERS = MAX_ACTIVE_NODES * 2

    logging.warning(f'For node {HOST_NAME}, active nodes is {MAX_ACTIVE_NODES} and max peers is {MAX_PEERS}')

    if not load_this_node(): # retrieve vital information and wait for node to sync, also determine if this node should do anything
        logging.warning(f'Node tried to start with saturated nodes. Aborting start.')
        return

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
            logging.error(f"Error loading the files: {e}")

        if attempt >= attempt_max - 1:
            logging.error(f"Cant find innocent node file after {attempt_max} tries. CATASTROPHIC ERROR")
            return

        logging.info(f"Can't find required files. Retrying in {sleep_int} seconds")
        time.sleep(sleep_int)

    balance_counter = 0
    connect_to_innocent()

    while ln_checker.has_channel_with(INNOCENT_NODE_ID) or not CHANNELS_CREATED or balance_counter == 0:
        try:
            create_channels()
            if balance_counter >= CHANNEL_BALANCE_COUNTER:
                balance_counter = 0
                # check_channel_states()
                if check_outbound_channels():
                    fund_innocent_channel()
                ln_checker.balance_all_channels()
            # logging.info("main: Sleeping for 10 seconds.")
            time.sleep(1)
            balance_counter += 1
        except KeyboardInterrupt:
            logging.info("main: Script terminated by user.")
            break
        except Exception as e:
            logging.error(f"main: An error occurred in the main loop: {e}")
            time.sleep(5)
    logging.info(f'Node has finished creating connections. Exiting out of CC_manager.')

def get_node_info():
    """
    Retrieve node information, including its ID.
    """
    output = run_lightning_cli(["getinfo"])
    return json.loads(output) if output else None

def connect_to_innocent():
    """
    Connect to the Innocent node but do not fund a channel
    """
    global CHANNELS_CREATED
    global INNOCENT_CHANNEL_CLOSED

    # the innocent channel was closed, we shouldn't open it again
    if INNOCENT_CHANNEL_CLOSED:
        return

    if not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.info(f"Connecting to Innocent Node: {INNOCENT_NODE_ADDRESS}")
        run_lightning_cli(["connect", INNOCENT_NODE_ADDRESS])
    # logging.info(f"Inno node peers are {run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])}")
    time.sleep(20)

def fund_innocent_channel():
    global INNOCENT_CHANNEL_CLOSED
    global CHANNELS_CREATED
    

    if not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.info(f"Connecting to Innocent Node: {INNOCENT_NODE_ADDRESS}")
        run_lightning_cli(["connect", INNOCENT_NODE_ADDRESS])

    # Check if we already have a channel with the Innocent Node
    if not CHANNELS_CREATED:
        # Calculate funding amount based on the discovery rule
        funding_amount = DISCOVERY_RULE_DIVISOR * 10000

        # logging.info(f"Inno node peers are {run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])}")
        # time.sleep(15)
        output = run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])
        if output:
            inno_channels = json.loads(output).get('channels') if output else None
        else:
            return

        if inno_channels and len(inno_channels) >= MAX_ACTIVE_NODES:
            logging.info(f'Trying to connect to innocent node but it currently has max number of active nodes channeled. Aborting.')
            return

        try:
            logging.info(f"No channel with Innocent Node. Funding a channel with funding amount: {funding_amount}")
            # seeing if this helps the funding problems
            if ln_checker.check_funds():
                result = run_lightning_cli(["fundchannel", INNOCENT_NODE_ID, str(funding_amount)])
                logging.info(f'Channel created with innocent node. Stopping channel creation.')
                if result:
                    INNOCENT_CHANNEL_CLOSED = False
                    CHANNELS_CREATED = True # we are done creating channels
        except Exception as e:
            logging.error(f"Funding failed: {e}")

def close_and_disconnect_innocent():
    """
    Close the channel with the Innocent node and disconnect.
    """
    global INNOCENT_CHANNEL_CLOSED
    if not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.warning('close_and_disconnect_innocent: Tried to disconnect/close with inno node when already disconnected/closed')
        INNOCENT_CHANNEL_CLOSED = True
        return
    
    try:
        logging.info(f"Closing channel with Innocent Node: {INNOCENT_NODE_ID}")
        run_lightning_cli(["close", f"id={INNOCENT_NODE_ID}"])
        logging.info(f"Disconnecting from Innocent Node: {INNOCENT_NODE_ID}")
        run_lightning_cli(["disconnect", INNOCENT_NODE_ID])    
        INNOCENT_CHANNEL_CLOSED = True
    except Exception as e:
        logging.warning(f'close_and_disconnect_innocent: Error {e}')
    
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
    # logging.info("list_peers: Fetching list of peers.")
    output = run_lightning_cli(["listpeers"])
    if not output:
        logging.error("list_peers: Failed to retrieve peer list.")
        return set()
    try:
        peers = json.loads(output).get("peers", [])
        peer_ids = set(peer["id"] for peer in peers)
        # logging.info(f"list_peers: Found peers: {peer_ids}")
        return peer_ids
    except json.JSONDecodeError:
        logging.error("list_peers: Failed to parse peer list output.")
        return set()

def channeled_with_peer(node_id, peer_channel_list):
    """
    Check if any of our channel peers (excluding the Innocent node) have a channel with the given node_id.
    """
    # logging.info(f"channeled_with_peer: Checking if any channel peer (excluding Innocent node) has a channel with node {node_id}")

    #For Regtest with mesh connected CCs, Get the list of peers we have channels with (excluding the Innocent node)
    peer_ids = peer_channel_list - {INNOCENT_NODE_ID}

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
                # logging.info(f"channeled_with_peer: Peer {source} has a channel with node {node_id}")
                return True
        # logging.info(f"channeled_with_peer: No peer has a channel with node {node_id}")
        return False
    except json.JSONDecodeError:
        logging.error("channeled_with_peer: Failed to parse channel list output.")
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
    Rules:
        Can only make maximum of MAX_ACTIVE_NODES outbound channels
        Can only have max MAX_PEERS (which is MAX_ACTIVE_NODES * 2) channels
    This should happen organically as nodes come online, since once we have the max channels we
    disconnect from the innocent node. Checks and failsafes make sure this is the case.
    """
    global INNOCENT_CHANNEL_CLOSED
    global outbound_channels
    global CHANNEL_OPENING_TIMES
    # logging.info("create_channels: Starting channel creation process.")

    if is_max_inbound_channels() and not INNOCENT_CHANNEL_CLOSED:
        logging.info(f'create_channels: We have reached incoming node saturation. Disconnecting from innocent node')
        close_and_disconnect_innocent()
        return
    elif CHANNELS_CREATED:
        return


    while True:
        # logging.info(f"Inno node peers are {run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])}")
        valid_nodes = discover_nodes()

        if not valid_nodes:
            logging.info(f'create_channels: No valid nodes found. Aborting channel creation.')
            return

        for node in valid_nodes:

            # just in case we try to connect to more (this shouldn't happen)
            if len(outbound_channels) >= MAX_ACTIVE_NODES:
                logging.info("create_channels: Reached maximum outbound peers while processing nodes.")
                return 
            
            outbound_channels.add(node)  # Track this node

            if node in outbound_channels and not ln_checker.does_connection_exist(node):
                # make sure we're not trying to connect to a node we're already connected to
                demoGetAddressAndConnect(node)

            if ln_checker.does_connection_exist(node):
                fund_channel(node)
            else:
                logging.error(f"create_channels: Failed to connect to {node}.")
        
        # last check, in case we came online too fast and missed some nodes
        new_valid_nodes = discover_nodes()
        if new_valid_nodes == valid_nodes:
            break

def fund_channel(node):
    logging.info(f'fund_channel: Connected to {node}. Funding.')
    # random funding amount (replaced the minutes version since that can give 19 and 0, which breaks this discovery rule)
    funding_amount = random.randint(5,15) * 10000
    logging.info(f"fund_channel: Opening channel with node {node}. Funding amount: {funding_amount}")
    if ln_checker.check_funds():
        result = run_lightning_cli(["fundchannel", node, f'{str(funding_amount)}'])
        if result:
            logging.info(f"fund_channel: Channel successfully created with node {node}.")
            # CHANNEL_OPENING_TIMES[node] = time.time()
    # fund the channel and automatically send liqudity to make sure we can communicate using this channel
    # variables are : command, node_to_fund, channel_capacity, feerate, announce, funds_sent_over
    # result = run_lightning_cli(["fundchannel", peer_id, str(funding_amount), str(0), 'true', str(funding_amount // 2)])
    # bug: this command doesn't work for some reason when I was creating the scripts
    else:
        logging.error(f"create_channels: Failed to create channel with node {node}.")


def list_peers_with_channels():
    """
    List all peer IDs that have at least one channel with this node.
    """
    # logging.info("list_peers_with_channels: Fetching peers with active channels.")

    own_node_id =  THIS_NODE

    if not own_node_id:
        logging.error("list_peers_with_channels: Failed to retrieve own node ID.")
        return set()

    # Query listchannels with the source set to own_node_id
    output = run_lightning_cli(["listfunds"])
    if not output:
        logging.error("list_peers_with_channels: Failed to retrieve channel list.")
        return set()

    # Parse the output and collect unique destination node IDs
    try:
        channels = json.loads(output).get("channels", [])
        peers_with_channels = set(
            [channel['peer_id'] for channel in channels]
        )
        # logging.info(f"list_peers_with_channels: Found {len(peers_with_channels)} peers with channels.")
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

def is_max_inbound_channels():
    '''
    Count how many incoming channels there are and return whether we are saturated.
    Returns:
        True if incoming nodes >= MAX_ACTIVE_NODES
    '''
    active_channels = list_peers_with_channels() - {INNOCENT_NODE_ID}
    incoming_channels = active_channels - outbound_channels
    # logging.warning(f'We currently have {len(incoming_channels)} incoming channels')
    return len(incoming_channels) >= MAX_ACTIVE_NODES

def discover_nodes():
    """
    Discover nodes strictly based on existing channels that meet the discovery rule.
    """
    # logging.info("discover_nodes: Discovering nodes with valid channels.")
    own_node_id = THIS_NODE
    inno_node = INNOCENT_NODE_ID

    valid_nodes = []

    output = run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])

    if not output:
        logging.error("discover_nodes: Failed to retrieve channel list.")
        return valid_nodes

    channels = json.loads(output).get("channels", [])
    # logging.info(f"discover_nodes: Total channels found: {len(channels)}")

    for channel in channels:
        '''
        Valid nodes are those that are:
        1. Have no channel with this node
        2. Connected to innocent node with KEY amount
        '''

        # immediately through out channels that don't have the discovery rule
        if not ln_checker.evaluate_discovery_rule(int(channel.get("amount_msat", 0)) // 1000):
            continue

        destination = channel['destination']
        source = channel['source']

        # only check either source or destination (connected to innocent node)
        # a check, but this shouldn't be called if we're connected to the innnocent node anyway
        if (source == own_node_id or destination == own_node_id):
            logging.error(f'discover_nodes: Is connected to innocent node. Not supposed to creating channels after connection to Innocent node.')
        if (len(outbound_channels) >= MAX_ACTIVE_NODES):
            logging.info(f'Node outbound channels is saturated.')
            return []
        
        node_is_blacklisted = (source in BLACKLISTED_NODES or destination in BLACKLISTED_NODES)
        node_is_outbound = destination in outbound_channels
        node_is_innocent = (destination == INNOCENT_NODE_ID)

        # checkpoint, we only want nodes connceted to the innocent node while our outbound nodes < Max active nodes
        if node_is_innocent or node_is_blacklisted or node_is_outbound:
            continue
        
        # add the destination node, since we check that the channel source is the innocent node
        valid_nodes.append(destination)
    
    logging.info(f"discover_nodes: Valid nodes discovered: \n{valid_nodes}")
    return valid_nodes

def get_channel_counts():
    '''
    return a dictionary containing all destination nodes and how many connections they have
    '''
    return channel_counter(False)

def get_channel_counts_exclude_inno():
    '''
    return a dictionary containing all destination nodes and how many connections they have.
    does not count connections to the innocent node
    '''
    return channel_counter(True)

def channel_counter(exclude_inno):
    '''
    Helper that does the actual channel counting
    Shouldn't be calling this directly.
    '''
    output = run_lightning_cli(["listchannels"])
    if not output:
        logging.error("discover_nodes: Failed to retrieve channel list.")
        return {}
    
    
    channels = json.loads(output).get("channels", [])
    channels_counted = dict()

    for channel in channels:
        destination = channel['destination']
        source = channel['source']

        if (exclude_inno and destination == INNOCENT_NODE_ID) or is_bm_node(destination): # skip counting the innocent node or BM node
            continue

        # using sets, so duplicates shouldn't matter
        if source in channels_counted.keys(): # we have counted a channel FROM this source
            channels_counted[source].add(destination)
        else: # We haven't counted a channel FROM this source
            channels_counted[source] = set()
            channels_counted[source].add(destination)

    channel_counts = {source: len(channels) for source, channels in channels_counted.items()}
    return channel_counts

def close_and_disconnect_nodes(num_nodes_close, channel_list):
    '''
    Close num_nodes_close channels from channel_list to keep within MAX_PEERS
    Args:
        num_nodes_close : number of channels to close
        channel_list : list of channels to select from
    '''
    # so this is goin to first check to make sure that we do have a channel with this node
    # then we disconnect. 
    # The list shouldn't contain the innocent node, but I'm not going to check for that

    # check against len of the channel list
    if len(channel_list) < num_nodes_close:
        logging.error(f'close_and_disconnect_nodes: Just tried to close {num_nodes_close} channels on a channel list of length {len(channel_list)}')
        return

    # check to make sure we're not trying to close the connection to the BM node
    channel_list = [channel for channel in channel_list if not is_bm_node(channel)]

    nodes_to_close = random.sample(channel_list, num_nodes_close)
    for node_id in nodes_to_close:
        run_lightning_cli(["close", f"id={node_id}"])

def is_bm_node(node_id):
    '''
    Check if this is the bm node by looking at the channels capacity
    '''
    # no point in even checking if we don't have a channel with it
    if not ln_checker.has_channel_with(node_id):
        return False
    capacity = ln_checker.get_capacity(node_id)

    return (int(capacity) % BM_DIVISOR) == 0 if capacity else False

def check_channel_states():
    ''''
    Check each channel. If it's been too long and it hasn't gone to normal we drop it.
    On the main net this wouldn't be a problem, probably.
    '''
    global CHANNEL_OPENING_TIMES

    if len(CHANNEL_OPENING_TIMES) == 0:
        return

    # check if any channels got stuck in AWAITING and thus weren't added to the dicitionary
    channels = ln_checker.get_channels()
    for channel in channels:
        channel_state = channels[channel].get('state')
        if channel not in CHANNEL_OPENING_TIMES.keys() and channel_state not in ln_checker.NOT_CONNECTING:
            logging.warning(f'check_channel_states: Channel with {channel} is abnormal and not tracked. Tracking. . .')
            CHANNEL_OPENING_TIMES[channel] = time.time()

    logging.info(f'check_channel_states: Checking channel states for {len(CHANNEL_OPENING_TIMES)} nodes')

    to_remove = []
    for node in CHANNEL_OPENING_TIMES.keys():
        start_time = CHANNEL_OPENING_TIMES[node]
        elasped_time = time.time() - start_time
        logging.info(f'check_channel_states: Checking node {node}')
        if not ln_checker.has_channel_with(node): # check first that we still have a channel with this node
            logging.info(f'check_channel_states: Channel with {node} no longer exists. Removing from pending list.')
            to_remove.append(node)
        elif ln_checker.is_node_active(node): # channel is normal, we're no longer watching this channel now
            logging.info(f'check_channel_states: Channel with {node} is normal. Removing from pending list.')
            to_remove.append(node)
        elif elasped_time >= CHANNEL_CHECK_SLEEP_INT: # channel took too long to become normal, close the channel
            logging.warning(f'check_channel_states: Channel with node {node} took too long to become normal. Closing channel.')
            run_lightning_cli(['close', node])
            to_remove.append(node)
    if len(to_remove) > 0:
        for node in to_remove:
            CHANNEL_OPENING_TIMES.pop(node)
            logging.info(f'Popped {node}')
    logging.info(f'check_channel_states: Finished checking channels')

def check_outbound_channels():
    '''
    Check the outbound channel list to make sure we're still connected with those channels.
    Balance and fund those channels if we haven't
    '''
    outbound_connected = True
    for node in outbound_channels:
        if not ln_checker.has_channel_with(node):
            if ln_checker.does_connection_exist(node):
                    outbound_connected = False
                    fund_channel(node)
            else:
                logging.warning(f'check_outbound_channels: Node {node} is in outbound but we dont have a connection with it.')
    
    return outbound_connected

def remove_outbound_channel(node_id):
    '''
    Remove the channel from outbound connections.
    '''
    global outbound_channels

    # if ln_checker.has_channel_with(node_id):
    #     logging.warning(f'remove_outbound_channel: Closing channel with {node_id}')
    #     close_and_disconnect_nodes(1, [node_id])

    if not ln_checker.has_channel_with(node_id):
        outbound_channels = outbound_channels - {node_id}
    

def load_this_node ():
    """
    Set global THIS_NODE variable
    Wait for this node to synch with bitcoin and lightning
    """
    global THIS_NODE 
    output = get_node_info()
    THIS_NODE = output.get('id')
    
    node_synced = False
    node_info = None

    logging.info(f'Waiting for node to sync with blockchain')

    while not node_synced:
        output = run_lightning_cli(['getinfo'])
        node_info = json.loads(output) if output else None

        if node_info and ln_checker.check_blockchain_height(node_info.get('blockheight')):
            node_synced = True
        else:
            time.sleep(1)
    
    if len(list_peers_with_channels()) >= MAX_PEERS:
        close_and_disconnect_innocent()
        return False

    logging.info(f'Node has synced successfully.')
    return True


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] > '0':
        main(int(sys.argv[1]))
    else:
        main(4)