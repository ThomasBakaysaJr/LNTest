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
innocent_channel_closed = True
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


    if not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.info(f"Connecting to Innocent Node: {INNOCENT_NODE_ADDRESS}")
        run_lightning_cli(["connect", INNOCENT_NODE_ADDRESS])

        
    # if the innocent channel is still open, lets not try to open it again.
    # or if we're at or more than max peers, shouldn't need to open up a channel with inno node
    if not innocent_channel_closed or len(ln_checker.get_channels()) >= MAX_PEERS:
        return

    # Check if we already have a channel with the Innocent Node
    if not ln_checker.has_channel_with(INNOCENT_NODE_ID):
        # Calculate funding amount based on the discovery rule
        funding_amount = DISCOVERY_RULE_DIVISOR * 10000
        # Try to govern the number of active nodes at once.
        # channel_counts = get_channel_counts()

        # logging.info(f"Inno node peers are {run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])}")
        time.sleep(15)
        output = run_lightning_cli(['listchannels', 'null', INNOCENT_NODE_ID])
        if output:
            inno_channels = json.loads(output).get('channels') if output else None
        else:
            return

        # if inno_channels:
        #     logging.info(f"Inno node peers with count of {len(inno_channels)} are {inno_channels}")
        # else:
        #     logging.info(f'Could not retrieve innocent peer data')

        # if INNOCENT_NODE_ID in channel_counts.keys():
        #     logging.warning(f'Inno node has {channel_counts[INNOCENT_NODE_ID]} channels')

        # logging.warning(f'Channel counts is {channel_counts}')

        # un-comment to limit the number of nodes connected to the innocent node at one time
        # if INNOCENT_NODE_ID in channel_counts.keys() and len(inno_channels) >= MAX_ACTIVE_NODES:
        #     logging.info(f'Trying to connect to innocent node but it currently has max number of active nodes channeled. Aborting.')
        #     return

        if inno_channels and len(inno_channels) >= MAX_ACTIVE_NODES:
            logging.info(f'Trying to connect to innocent node but it currently has max number of active nodes channeled. Aborting.')
            return

        try:
            logging.info(f"No channel with Innocent Node. Funding a channel with funding amount: {funding_amount}")
            # seeing if this helps the funding problems
            if ln_checker.check_funds():
                run_lightning_cli(["fundchannel", INNOCENT_NODE_ID, str(funding_amount)])
            innocent_channel_closed = False
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
    global innocent_channel_closed
    global outbound_channels
    global CHANNEL_OPENING_TIMES
    # logging.info("create_channels: Starting channel creation process.")

    peers_with_channels = list_peers_with_channels()
    # logging.info(f"create_channels: Current peers with channels: {peers_with_channels}")

    # Exclude Innocent node from peers_with_channels
    if INNOCENT_NODE_ID in peers_with_channels:
        peers_with_channels_excl_innocent = peers_with_channels - {INNOCENT_NODE_ID}
    else:
        peers_with_channels_excl_innocent = peers_with_channels


    # if we have reached the limit of number of channels, we disconnect from the inno node
    if len(peers_with_channels_excl_innocent) >= MAX_PEERS:
        # Now we need to close the channel with the Innocent node and disconnect
        
        extra_channels = len(peers_with_channels_excl_innocent)  - MAX_PEERS # in case we went over the limit
        
        if not innocent_channel_closed:
            logging.info("create_channels: Max channels reached. Disconnecting from innocent node.")
            close_and_disconnect_innocent()
            return
        # N channels we connect to, and the N channels made from other nodes connecting to us
        elif extra_channels > 0:
            logging.info(f'create_channels: We have {extra_channels} extra channels')
            # first, lets check and make sure those are active channels
            active_channels = [node for node in peers_with_channels_excl_innocent if ln_checker.is_node_active(node)]
            inbound_channels = set(active_channels) - set(outbound_channels)
            num_nodes_close = len(active_channels) - MAX_ACTIVE_NODES # nodes should be half inbound and half outbound
            logging.info(f'create_channels: We have {num_nodes_close} extra nodes')
            if num_nodes_close > 0:
                logging.warning(f"create_channels: Inbound channel overload, {len(inbound_channels)} > MAX_ACTIVE_NODES, disconnecting nodes.")
                # we will only disconnect nodes that made channels with us, since that should be the only
                # way to go over the channel limit
                close_and_disconnect_nodes(num_nodes_close, list(inbound_channels)) # CHANGE: we look at the entire active node list, not just outbound nodes
                return

    # checks for outbound connections. Each node should only make MAX_ACTIVE_NODES connections out
    if len(outbound_channels) >= MAX_ACTIVE_NODES and not innocent_channel_closed: # CHANGE: we don't care about outbound nodes right now
        logging.info("create_channels: Max outbound channels reached, no more channels will be created. Still connected to innocent node")
        return
    elif len(peers_with_channels_excl_innocent) < MAX_PEERS and innocent_channel_closed: # CHANGE: this was an elif before
        logging.info(f'create_channels: Number of channels ({len(peers_with_channels_excl_innocent)}) below threshold, re-opening channel to innocent node.')
        connect_to_innocent()

    # checkpoint. No point in continuing if we have filled our outbound connections or we have filled our channel capacity
    if len(outbound_channels) > MAX_ACTIVE_NODES or len(peers_with_channels_excl_innocent) >= MAX_PEERS:
        return
    # if len(peers_with_channels_excl_innocent) >= MAX_PEERS: # CHANGE: We only care about active nodes rn
    #     return
    valid_nodes = set(discover_nodes().copy())
    if not valid_nodes:
        logging.info("create_channels: No valid nodes discovered. Aborting channel creation.")
        return
    for node in valid_nodes:
        logging.info(f'create_channels: Found {len(valid_nodes)} valid channels')
        if len(peers_with_channels_excl_innocent) >= MAX_PEERS:
            logging.info("create_channels: Reached maximum peers with channels while processing nodes.")
            # Close the channel with the Innocent node and disconnect
            if not innocent_channel_closed:
                close_and_disconnect_innocent()
            break
    
        peer_id = node # remnant of a different time
        # random sleep timer so that they don't all come online and look for each other at the 
        # same time. This is to try to mitigate a bunch of nodes creating a channel to the same
        # node all at once (might also randomzie access to the channel list if this doesnt work)
        attempt = 0
        channel_counts = get_channel_counts_exclude_inno() # get the dictionary mapping node ids to the number of channels they have
        # logging.warning(f'channel counts is {channel_counts}')
        node_maxed = False
        
        # while attempt < random.randint(1, MAX_PEERS):
        #     time.sleep(random.random() * random.randint(1, 4))
        #     new_channel_counts = get_channel_counts_exclude_inno()

        #     # check connection again, if the count change since we last check, loop again and wait
        #     if peer_id in new_channel_counts.keys() and not new_channel_counts[peer_id] == channel_counts[peer_id]:
        #         channel_counts = new_channel_counts
        #         logging.info(f'create_channels: Restarting loop, channel count changed.\n{channel_counts[peer_id]}\n{new_channel_counts[peer_id]}')
        #         continue

        #     # check connection counts for this node. If not in channel_counts then it has nothing connected to it
        #     if peer_id in new_channel_counts.keys() and new_channel_counts:
        #         if new_channel_counts[peer_id] >= MAX_PEERS:
        #             logging.info(f'create_channels: Skipping node {peer_id} since it has max peers')
        #             node_maxed = True
        #             break
        #     channel_counts = new_channel_counts
        #     attempt += 1

        # checks again in case things have changed
        if peer_id in BLACKLISTED_NODES:
            logging.info(f"create_channels: Skipping blacklisted node {peer_id}.")
            continue
        if ln_checker.has_channel_with(peer_id):
            logging.info(f"create_channels: Skipping node {peer_id} as a channel already exists.")
            continue
        if peer_id in channel_counts.keys() and channel_counts[peer_id] >= MAX_PEERS:
            logging.info(f"create_channels: Skipping node {peer_id} as it already has max peers of {channel_counts[peer_id]}")
            continue
        if node_maxed == True:
            logging.info(f'create_channels: Skipping node {peer_id} as it already has max peers')
            continue
        if peer_id == THIS_NODE:
            logging.warning(f'create_channels: Trying to connect to self. Skipping.')
            continue
        if peer_id in outbound_channels: # CHANGE: We don't care about outbound nodes
            logging.warning(f'Trying to connect to a node that was in the outbound connection. Aborting this round of channel creation.')
            break
        if len(outbound_channels) >= MAX_ACTIVE_NODES:
            logging.info(f'create_channels: This node has reached max outbound channels')
            break

        # Uncomment the following line in Testnet/Mainnet to connect to the node before funding a channel
        # connect_to_node(peer_id)
        logging.info(f'Checks complete, starting to connect to {peer_id}')
        #this will allow the CCs to connect to each other by themselves instead of having to mesh connect before hand
        if peer_id not in outbound_channels and not ln_checker.does_connection_exist(peer_id):
            # make sure we're not trying to connect to a node we're already connected to
            demoGetAddressAndConnect(peer_id)
        if not ln_checker.does_connection_exist(peer_id): # CHANGE: only care if channel exists, don't care about outbound nodes
            # make sure we're not trying to connect to a node we're already connected to
            demoGetAddressAndConnect(peer_id)

        if ln_checker.does_connection_exist(peer_id):
            logging.info(f'Connected. Funding.')
            # random funding amount (replaced the minutes version since that can give 19 and 0, which breaks this discovery rule)
            funding_amount = random.randint(5,15) * 10000
            logging.info(f"create_channels: Opening channel with node {peer_id}. Funding amount: {funding_amount}")
            ln_checker.check_funds()
            result = run_lightning_cli(["fundchannel", peer_id, f'{str(funding_amount)}'])
            # fund the channel and automatically send liqudity to make sure we can communicate using this channel
            # variables are : command, node_to_fund, channel_capacity, feerate, announce, funds_sent_over
            # result = run_lightning_cli(["fundchannel", peer_id, str(funding_amount), str(0), 'true', str(funding_amount // 2)])
            if result:
                logging.info(f"create_channels: Channel successfully created with node {peer_id}.")
                CHANNEL_OPENING_TIMES[peer_id] = time.time()

                if ln_checker.wait_node_activated(peer_id):
                    ln_checker.balance_channel(peer_id)

                # outbound_channels.add(peer_id)  # Track this node # CHANGE : not tracking outbound nodes
                peers_with_channels.add(peer_id)     # Update peers set
                peers_with_channels_excl_innocent.add(peer_id)
            else:
                logging.error(f"create_channels: Failed to create channel with node {peer_id}.")
        else:
            logging.error(f"create_channels: Failed to connect to {peer_id}.")
            if peer_id in outbound_channels:
                outbound_channels.remove(peer_id) # CHANGE: just in case this does something, don't call it for outbound nodes


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

def discover_nodes():
    """
    Discover nodes strictly based on existing channels that meet the discovery rule.
    """
    # logging.info("discover_nodes: Discovering nodes with valid channels.")
    own_node_id = THIS_NODE
    inno_node = INNOCENT_NODE_ID

    # uncomment this to make sure nodes can only make channels if they are also connected to the innocent node
    # if not inno_node or not ln_checker.has_channel_with(inno_node):
    #     logging.info(f'discover_nodes: Not connected to Innocent Node, cannot connect to new nodes.')
    #     return None

    valid_nodes = []

    output = run_lightning_cli(["listchannels"])
    if not output:
        logging.error("discover_nodes: Failed to retrieve channel list.")
        return valid_nodes

    channels = json.loads(output).get("channels", [])
    # logging.info(f"discover_nodes: Total channels found: {len(channels)}")

    channel_counts_no_inno = {}

    # going to randomize access to the channel list
    random.shuffle(channels)

    for channel in channels:
        '''
        Valid nodes are those that are:
        1. Have no channel with this node
        2. Connected to innocent node with KEY amount
        3. Has less than MAX channels
        '''
        # if not evaluate_discovery_rule(int(channel.get("amount_msat", 0)) // 1000):
        #     logging.error('discover rule got procd')
        #     continue

        # add to the count dict (so we can make sure we aren't connecting to a maxed out node)
        # call a fucnction that way we get updated counts
        channel_counts_no_inno = get_channel_counts_exclude_inno()

        # channel = channels[index]
        destination = channel['destination']
        source = channel['source']

        # only check either source or destination (connected to innocent node)
        channel_with_innocent = (source == INNOCENT_NODE_ID)
        # channel_with_innocent = True # CHANGE: DON'T care if its connected to innocent node
        channel_with_self = (source == own_node_id or destination == own_node_id)
        node_is_blacklisted = (source in BLACKLISTED_NODES or destination in BLACKLISTED_NODES)
        node_is_outbound = destination in outbound_channels # CHANGE: don't care about outbound nodes
        node_is_innocent = (destination == INNOCENT_NODE_ID)
        # if this is an outbound node and is has a channel with us, that means this channel was closed from the other side
        # remove it from the outbound section and skip it, we'll get it on the go around if its still here.
        if node_is_outbound and not channel_with_self: # CHANGE: don't care about outbound nodes
            logging.warning(f'Node {destination} is an outbound node but channel does not exist anymore.')
            remove_outbound_channel(destination)

        # check connection counts for this node. If not in channel_counts then there's no channels with it
        # or its only connected to the innocent node.
        if destination in channel_counts_no_inno.keys():
            # logging.warning(f'Channel counting {destination} has {channel_counts_no_inno[destination]} channels')
            node_is_maxed = channel_counts_no_inno[destination] >= MAX_PEERS
        else:
            node_is_maxed = False # this means we didn't even count it, so it has either 1 or none channels

        # checkpoint, we only want nodes below MAX_PEERS and connected to the innocent node
        if node_is_innocent or channel_with_self or (not channel_with_innocent) or node_is_blacklisted or node_is_maxed or node_is_outbound:
            continue
        # if node_is_innocent or channel_with_self or (not channel_with_innocent) or node_is_blacklisted or node_is_maxed: # CHANGE: don't care about outbound nodes
        #     continue
        
        # add the destination node, since we check that the channel source is the innocent node
        valid_nodes.append(destination)
    
    # logging.info(f"discover_nodes: Valid nodes discovered: \n{valid_nodes}")
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

def close_and_disconnect_innocent():
    """
    Close the channel with the Innocent node and disconnect.
    """
    global innocent_channel_closed
    if not ln_checker.does_connection_exist(INNOCENT_NODE_ID):
        logging.warning('close_and_disconnect_innocent: Tried to disconnect/close with inno node when already disconnected/closed')
        innocent_channel_closed = True
        return
    logging.info(f"Closing channel with Innocent Node: {INNOCENT_NODE_ID}")
    run_lightning_cli(["close", f"id={INNOCENT_NODE_ID}"])
    logging.info(f"Disconnecting from Innocent Node: {INNOCENT_NODE_ID}")
    run_lightning_cli(["disconnect", INNOCENT_NODE_ID])
    innocent_channel_closed = True

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
    Check the outbound channel list to make sure we're still connected with those channels,
    since we don't close those channels but other nodes might.
    '''
    global outbound_channels

    # we only want to look at this node if its not in the CHANNEL_OPENING_TIMES
    # else we might disconnect from a node that is still trying to open a channel
    channels_to_check = {channel for channel in outbound_channels if channel not in CHANNEL_OPENING_TIMES.keys()}

    new_outbound_channels = ln_checker.check_channels(channels_to_check)
    if new_outbound_channels != outbound_channels:
        logging.warning(f'Removed {outbound_channels - new_outbound_channels} from outbound channel list.')
        outbound_channels = new_outbound_channels.copy() # make a hard copy

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
    
    MAX_PEERS = MAX_ACTIVE_NODES * 2

    load_this_node() # retrieve vital information and wait for node to sync

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

    while True:
        try:
            connect_to_innocent()
            create_channels()
            check_outbound_channels() # CHANGE: don't care about outbound nodes
            if balance_counter >= CHANNEL_BALANCE_COUNTER:
                balance_counter = 0
                check_channel_states()
                ln_checker.balance_all_channels()
            logging.info("main: Sleeping for 10 seconds.")
            time.sleep(CHANNEL_SLEEP_INT)
            balance_counter += 1
        except KeyboardInterrupt:
            logging.info("main: Script terminated by user.")
            break
        except Exception as e:
            logging.error(f"main: An error occurred in the main loop: {e}")
            time.sleep(5)

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
    
    time.sleep(30)

    while not node_synced:
        output = run_lightning_cli(['getinfo'])
        node_info = json.loads(output) if output else None

        if node_info and ln_checker.check_blockchain_height(node_info.get('blockheight')):
            node_synced = True
        else:
            time.sleep(1)
    
    logging.info(f'Node has synced successfully.')


if __name__ == "__main__":
    main()
