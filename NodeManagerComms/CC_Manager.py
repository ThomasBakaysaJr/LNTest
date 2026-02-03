#nodeManagerFinalVersion.py
# This script is designed to run on a Lightning Network node (c-lightning) and manage the discovery of CC nodes and creating channels with them 
# centered on a discovery rule. The script will connect to an "Innocent" node and fund a channel meeting the discovery rule amount. It will then connect and 
# create channels with other nodes that meet the discovery rule, while avoiding duplicates and blacklisted nodes (i.e. innocent node). 
# The script will avoid connecting to nodes that already have a channel with a peer. 
# The script will also close the channel with the Innocent node and disconnect once the maximum number of peers with channels 
# is reached, making the CC node no longer discoverable.
import json
import time
import logging
from pathlib import Path
import random
import os

HOST_NAME = os.getenv("CONTAINER_NAME")

THIS_NODE = None

LOG_DIR = Path('logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
log_file_path = LOG_DIR / f'cc_log_{HOST_NAME}.log'
logging.basicConfig(filename=log_file_path, level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s")

import ln_checker

# Constants
DISCOVERY_RULE_DIVISOR = ln_checker.DISCOVERY_RULE_DIVISOR
BM_DIVISOR = ln_checker.BOTMASTER_RULE_DIVISOR
MAX_ACTIVE_NODES = ln_checker.ACTIVE_NODES
MAX_PEERS = ln_checker.MAX_PEERS 

INNOCENT_NODE_ID = None
INNOCENT_NODE_ADDRESS = None
CC_ADDRESS_LIST = None


OUTBOUND_CHANNELS = set()
INNOCENT_CHANNEL_CLOSED = False
CHANNELS_CREATED = False
# for channels that need to restart
CHANNEL_OPENING_TIMES = {}

# HOW OFTEN the script looks for new channels
CHANNEL_CHECK_SLEEP_INT = 60

# wait counter for how often to attempt balancing the channels
CHANNEL_BALANCE_COUNTER = ln_checker.CHANNEL_BALANCE_COUNTER

def main():
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
    MAX_ACTIVE_NODES = ln_checker.ACTIVE_NODES
    MAX_PEERS = ln_checker.MAX_PEERS

    logging.info(f'For node {HOST_NAME}, active nodes is {MAX_ACTIVE_NODES} and max peers is {MAX_PEERS}')

    # Filenames from environment variables or defaults
    # We use os.path.basename because these files are mounted into the container's working directory
    # but the env vars might contain the full host path.
    innocent_addr_file = os.path.basename(os.getenv('NODE_ADDRESS_FILE', 'innocentAddress.txt'))
    innocent_id_file = os.path.basename(os.getenv('NODE_ID_FILE', 'innocentID.txt'))
    cc_addr_list_file = os.path.basename(os.getenv('NODE_MANAGER_ADDRESS_LIST', 'CC_address_list.txt'))

    if not load_this_node(): # retrieve vital information and wait for node to sync
        logging.warning(f'Node tried to start with saturated nodes. Aborting start.')
        return

    for _ in range(attempt_max):
        try:
            with open(innocent_addr_file, 'r') as address_file:
                INNOCENT_NODE_ADDRESS = address_file.read().strip()
            with open(innocent_id_file, 'r') as id_file:
                INNOCENT_NODE_ID = id_file.read().strip()

            with open(cc_addr_list_file, 'r') as id_file:
                CC_ADDRESS_LIST = id_file.read().strip()
            
            logging.info(f"Found Innocent node at {INNOCENT_NODE_ADDRESS}")
            break
        except Exception as e:
            logging.error(f"Error loading the files: {e}")

        logging.info(f"Can't find required files. Retrying in {sleep_int} seconds")
        time.sleep(sleep_int)

    balance_counter = 0
    connect_to_innocent()

    while ln_checker.has_channel_with(INNOCENT_NODE_ID) or not CHANNELS_CREATED or balance_counter == 0:
        try:
            create_channels()
            if balance_counter >= CHANNEL_BALANCE_COUNTER:
                balance_counter = 0
                # will return true if we have channels with all outbound nodes
                # once that's true, then we fund a channel with the innocent node
                if check_outbound_channels():
                    fund_innocent_channel()
                ln_checker.balance_all_channels()
            time.sleep(1)
            balance_counter += 1
        except KeyboardInterrupt:
            logging.info("main: Script terminated by user.")
            break
        except Exception as e:
            logging.error(f"main: An error occurred in the main loop: {e}")
            time.sleep(5)
    logging.info(f'Node has finished creating connections. Exiting out of CC_manager.')


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
    global OUTBOUND_CHANNELS
    global CHANNEL_OPENING_TIMES

    if is_max_inbound_channels() and not INNOCENT_CHANNEL_CLOSED:
        logging.info(f'create_channels: We have reached incoming node saturation. Disconnecting from innocent node')
        close_and_disconnect_innocent()
        return
    elif CHANNELS_CREATED:
        return


    while True:
        valid_nodes = discover_nodes()

        if not valid_nodes:
            logging.info(f'create_channels: No valid nodes found. Aborting channel creation.')
            return

        for node in valid_nodes:

            # just in case we try to connect to more (this shouldn't happen)
            if len(OUTBOUND_CHANNELS) >= MAX_ACTIVE_NODES:
                logging.info("create_channels: Reached maximum outbound peers while processing nodes.")
                return 
            
            OUTBOUND_CHANNELS.add(node)  # Track this node

            if node in OUTBOUND_CHANNELS and not ln_checker.does_connection_exist(node):
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

def channeled_with_peer(node_id, peer_channel_list):
    """
    Check if any of our channel peers (excluding the Innocent node) have a channel with the given node_id.
    """

    #For Regtest with mesh connected CCs, Get the list of peers we have channels with (excluding the Innocent node)
    peer_ids = peer_channel_list - {INNOCENT_NODE_ID}

    if not peer_ids:
        logging.info("channeled_with_peer: No channel peers to check.")
        return False
    
    channels = ln_checker.lightning_rpc.listchannels().get('channels', [])
    for channel in channels:
        source = channel["source"]
        destination = channel["destination"]
        if source in peer_ids and destination == node_id:
            return True
    return False

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
            ln_checker.lightning_rpc.close(node)
            to_remove.append(node)
    if len(to_remove) > 0:
        for node in to_remove:
            CHANNEL_OPENING_TIMES.pop(node)
            logging.info(f'Popped {node}')
    logging.info(f'check_channel_states: Finished checking channels')

#this is for the demo instead of using meshconnect, CCs can look at the shared address list and connect to each other by themselves
def demoGetAddressAndConnect(node_ID):
    """
    Reads the CC_address_list.txt file, extracts the full address corresponding to the given node ID,
    and connects to the node using the lightning-cli command.

    :param node_ID: The ID of the node to connect to.
    """
    try:
        # Read the CC_address_list.txt file
        cc_addr_list_file = os.path.basename(os.getenv('NODE_MANAGER_ADDRESS_LIST', 'CC_address_list.txt'))
        with open(cc_addr_list_file, 'r') as id_file:
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
        result = ln_checker.lightning_rpc.connect(full_address)

        if result:
            logging.info(f"Successfully connected to node {node_ID} at {full_address}.")
        else:
            logging.error(f"Failed to connect to node {node_ID} at {full_address}.")
    except Exception as e:
        logging.error(f"demoGetAddressAndConnect: Exception occurred: {e}")

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
        ln_checker.lightning_rpc.connect(INNOCENT_NODE_ADDRESS)

def fund_innocent_channel():
    global INNOCENT_CHANNEL_CLOSED
    global CHANNELS_CREATED
    

    if not INNOCENT_CHANNEL_CLOSED and not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.info(f"Connecting to Innocent Node: {INNOCENT_NODE_ADDRESS}")
        ln_checker.lightning_rpc.connect(INNOCENT_NODE_ADDRESS)

    # Check if we're still creating channels
    if not CHANNELS_CREATED:
        # Calculate funding amount based on the discovery rule
        funding_amount = DISCOVERY_RULE_DIVISOR * 10000
        inno_channels = ln_checker.lightning_rpc.listchannels(source=INNOCENT_NODE_ID).get('channels')
        if inno_channels is not None and len(inno_channels) >= MAX_ACTIVE_NODES:
            logging.info(f'Trying to connect to innocent node but it currently has max number of active nodes channeled. Aborting.')
            return

        try:
            logging.info(f"No channel with Innocent Node. Funding a channel with funding amount: {funding_amount}")
            # seeing if this helps the funding problems
            if ln_checker.check_funds():
                result = ln_checker.lightning_rpc.fundchannel(INNOCENT_NODE_ID, funding_amount)
                if result:
                    logging.info(f'Channel created with innocent node. Stopping channel creation.')
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
        ln_checker.lightning_rpc.close(INNOCENT_NODE_ID)
        logging.info(f"Disconnecting from Innocent Node: {INNOCENT_NODE_ID}")
        ln_checker.lightning_rpc.disconnect(INNOCENT_NODE_ID)
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
    try:
        node_details = ln_checker.lightning_rpc.listnodes(node_id).get('nodes', [])
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

def fund_channel(node):
    logging.info(f'fund_channel: Connected to {node}. Funding.')
    # random funding amount
    funding_amount = random.randint(ln_checker.MIN_CHANNEL_CAPACITY, ln_checker.MAX_CHANNEL_CAPACITY)
    logging.info(f"fund_channel: Opening channel with node {node}. Funding amount: {funding_amount}")
    if ln_checker.check_funds():
        result = ln_checker.lightning_rpc.fundchannel(node, funding_amount)
        if result:
            logging.info(f"fund_channel: Channel successfully created with node {node}.")
    else:
        logging.error(f"create_channels: Failed to create channel with node {node}.")


def list_peers_with_channels():
    """
    List all peer IDs that have at least one channel with this node.
    """

    own_node_id =  THIS_NODE

    if not own_node_id:
        logging.error("list_peers_with_channels: Failed to retrieve own node ID.")
        return set()

    # Parse the output and collect unique destination node ID
    channels = ln_checker.lightning_rpc.listfunds().get('channels', [])
    peers_with_channels = set(
        [channel['peer_id'] for channel in channels]
    )
    return peers_with_channels


def is_max_inbound_channels():
    '''
    Count how many incoming channels there are and return whether we are saturated.
    Returns:
        True if incoming nodes >= MAX_ACTIVE_NODES
    '''
    active_channels = list_peers_with_channels() - {INNOCENT_NODE_ID}
    incoming_channels = active_channels - OUTBOUND_CHANNELS
    return len(incoming_channels) >= MAX_ACTIVE_NODES

def discover_nodes():
    """
    Discover nodes strictly based on existing channels that meet the discovery rule.
    """
    own_node_id = THIS_NODE
    valid_nodes = []

    channels = ln_checker.lightning_rpc.listchannels(None, INNOCENT_NODE_ID).get('channels', [])
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
        if (len(OUTBOUND_CHANNELS) >= MAX_ACTIVE_NODES):
            logging.info(f'Node outbound channels is saturated.')
            return []
        
        node_is_outbound = destination in OUTBOUND_CHANNELS
        node_is_innocent = (destination == INNOCENT_NODE_ID)

        # checkpoint, we only want nodes connceted to the innocent node while our outbound nodes < Max active nodes
        if node_is_innocent or node_is_outbound:
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
    channels = ln_checker.lightning_rpc.listchannels().get('channels', [])
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
        ln_checker.lightning_rpc.close(node_id)

def is_bm_node(node_id):
    '''
    Check if this is the bm node by looking at the channels capacity
    '''
    # no point in even checking if we don't have a channel with it
    if not ln_checker.has_channel_with(node_id):
        return False
    capacity = ln_checker.get_capacity(node_id)

    return (int(capacity) % BM_DIVISOR) == 0 if capacity else False

def check_outbound_channels():
    '''
    Check the outbound channel list to make sure we're still connected with those channels.
    Balance and fund those channels if we haven't
    '''
    outbound_connected = True
    for node in OUTBOUND_CHANNELS:
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
    global OUTBOUND_CHANNELS
    if not ln_checker.has_channel_with(node_id):
        OUTBOUND_CHANNELS = OUTBOUND_CHANNELS - {node_id}
    

def load_this_node ():
    """
    Set global THIS_NODE variable
    Wait for this node to synch with bitcoin and lightning
    """
    global THIS_NODE 
    output = get_node_info()
    THIS_NODE = output.get('id')

    logging.info(f'Waiting for node to sync with blockchain')

    while not ln_checker.is_synched():
        time.sleep(1)
    
    if len(list_peers_with_channels()) >= MAX_PEERS:
        close_and_disconnect_innocent()
        return False

    logging.info(f'Node has synced successfully.')
    return True

def get_node_info():
    """
    Retrieve node information, including its ID.
    """
    return ln_checker.lightning_rpc.getinfo()

if __name__ == "__main__":
    main()
