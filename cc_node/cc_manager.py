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
import signal

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

    def _alarm_handler(signum, frame):
        raise TimeoutError("CC_Manager loop iteration timed out")

    signal.signal(signal.SIGALRM, _alarm_handler)

    # Formation timeout: once CHANNELS_CREATED, exit after NODE_FORMATION_TIMEOUT
    # seconds even if the innocent channel isn't closed yet. Prevents late-joining
    # nodes from looping forever waiting for MAX_ACTIVE_NODES inbound connections.
    NODE_FORMATION_TIMEOUT = int(os.getenv('NODE_FORMATION_TIMEOUT', 60))
    formation_start = None

    while ln_checker.has_channel_with(INNOCENT_NODE_ID) or not CHANNELS_CREATED or balance_counter == 0:
        if CHANNELS_CREATED:
            if formation_start is None:
                formation_start = time.time()
            elif time.time() - formation_start > NODE_FORMATION_TIMEOUT:
                logging.info(f'CC_Manager: Formation timeout ({NODE_FORMATION_TIMEOUT}s). Exiting topology formation.')
                break
        try:
            signal.alarm(90)  # 90s watchdog for blocking RPC calls
            create_channels()
            if balance_counter >= CHANNEL_BALANCE_COUNTER:
                balance_counter = 0
                # will return true if we have channels with all outbound nodes
                # once that's true, then we fund a channel with the innocent node
                if check_outbound_channels():
                    fund_innocent_channel()
            time.sleep(1)
            balance_counter += 1
        except TimeoutError:
            logging.warning("main: Loop iteration timed out (SIGALRM). Continuing.")
        except KeyboardInterrupt:
            logging.info("main: Script terminated by user.")
            break
        except Exception as e:
            logging.error(f"main: An error occurred in the main loop: {e}")
            time.sleep(5)
        finally:
            signal.alarm(0)  # Cancel any pending alarm
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
        logging.info(f'create_channels: Reached m={MAX_ACTIVE_NODES} inbound connections. Closing innocent channel per Algorithm 2.')
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

            if not ln_checker.does_connection_exist(node):
                # make sure we're not trying to connect to a node we're already connected to
                demoGetAddressAndConnect(node)

            if ln_checker.does_connection_exist(node):
                fund_channel(node)
                OUTBOUND_CHANNELS.add(node)  # Only track after successful connection
            else:
                logging.error(f"create_channels: Failed to connect to {node}.")
        
        # last check, in case we came online too fast and missed some nodes
        new_valid_nodes = discover_nodes()
        if new_valid_nodes == valid_nodes:
            break


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
        try:
            ln_checker.lightning_rpc.connect(INNOCENT_NODE_ADDRESS)
        except Exception as e:
            logging.warning(f"fund_innocent_channel: Could not connect to Innocent Node: {e}")

    # Check if we're still creating channels
    if not CHANNELS_CREATED:
        # If we already have the innocent channel, check if outbound is ready
        if ln_checker.has_channel_with(INNOCENT_NODE_ID):
            if len(OUTBOUND_CHANNELS) >= MAX_ACTIVE_NODES:
                logging.info(f'Outbound channels established. Marking channel creation as complete.')
                CHANNELS_CREATED = True
            return

        # Calculate funding amount based on the discovery rule
        funding_amount = DISCOVERY_RULE_DIVISOR * 10000
        inno_channels = ln_checker.lightning_rpc.listchannels(source=INNOCENT_NODE_ID).get('channels')
        if inno_channels is not None and len(inno_channels) >= MAX_ACTIVE_NODES:
            logging.info(f'Trying to connect to innocent node but it currently has max number of active nodes channeled. Aborting.')
            # If outbound channels are working, mark as done
            if len(OUTBOUND_CHANNELS) >= MAX_ACTIVE_NODES:
                logging.info(f'Outbound channels are working. Marking channels as created despite innocent saturation.')
                CHANNELS_CREATED = True
            return

        try:
            logging.info(f"No channel with Innocent Node. Funding a channel with funding amount: {funding_amount}")
            if ln_checker.check_funds():
                result = ln_checker.lightning_rpc.fundchannel(INNOCENT_NODE_ID, funding_amount)
                if result:
                    logging.info(f'Channel funded with innocent node.')
                    INNOCENT_CHANNEL_CLOSED = False
                    # Only mark as fully done if outbound channels are established
                    if len(OUTBOUND_CHANNELS) >= MAX_ACTIVE_NODES:
                        logging.info(f'Outbound channels ready. Channel creation complete.')
                        CHANNELS_CREATED = True
                    else:
                        logging.info(f'Innocent channel funded but waiting for outbound channels ({len(OUTBOUND_CHANNELS)}/{MAX_ACTIVE_NODES}).')
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
    

def fund_channel(node):
    logging.info(f'fund_channel: Connected to {node}. Funding.')
    # random funding amount
    funding_amount = random.randint(ln_checker.MIN_CHANNEL_CAPACITY, ln_checker.MAX_CHANNEL_CAPACITY)
    # Push half the capacity to the remote side so both ends can send keysends.
    # Without this, only the opener has funds and the remote cannot relay messages.
    push_amount = funding_amount // 2 * 1000  # half capacity, in msat
    logging.info(f"fund_channel: Opening channel with node {node}. Funding amount: {funding_amount}, push_msat: {push_amount}")
    if ln_checker.check_funds():
        try:
            result = ln_checker.lightning_rpc.fundchannel(node, funding_amount, push_msat=push_amount)
            if result:
                logging.info(f"fund_channel: Channel successfully created with node {node}.")
        except Exception as e:
            logging.error(f"fund_channel: fundchannel failed for {node}: {e}")
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
            continue
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
