import logging
import time
import subprocess
import json
import os
from pathlib import Path
from pyln.client import LightningRpc
from multiprocessing import shared_memory

# global configs : should update from testState/node_config
RETRY_INT = 5
SLEEP_INT= 10
DISCOVERY_RULE_DIVISOR = 19
BOTMASTER_RULE_DIVISOR = 123123
SHM_BLOCK_SIZE = 5012

# where the config file lives
BASE_DIR = Path(__file__).parent.resolve()
NODE_CONFIG_PATH = BASE_DIR / 'testState/node_config.json'

try:
    if NODE_CONFIG_PATH.exists():
        with open(NODE_CONFIG_PATH, 'r') as f:
            config = json.load(f)
            SHM_BLOCK_SIZE = config.get('block_size', SHM_BLOCK_SIZE)
            DISCOVERY_RULE_DIVISOR = config.get('discovery_rule', DISCOVERY_RULE_DIVISOR)
            BOTMASTER_RULE_DIVISOR = config.get('botmaster_rule', BOTMASTER_RULE_DIVISOR)
        logging.info(f'ln_checker: Loaded from configs {NODE_CONFIG_PATH}')
    else:
        logging.warning(f'ln_checker: WARNING: No config found at {NODE_CONFIG_PATH}. Proceeding with defaults')
except Exception as e:
    print(f'ln_checker: ERROR loading config: {e}')

HOST_NAME = os.getenv("CONTAINER_NAME")

# where the status file lives
STATUS_DIR = Path('status')
STATUS_DIR.mkdir(parents=True, exist_ok=True)
STATUS_FILE = STATUS_DIR / 'status_'


# channel states
NOT_CONNECTING = ['CHANNELD_NORMAL', 'ONCHAIN']
DONT_BALANCE = ['CLOSINGD_COMPLETE', 'ONCHAIN']

# lightning rpc connection
lightning_rpc = LightningRpc("/root/.lightning/regtest/lightning-rpc")

def run_bitcoin_cli(command):
    try:
        result = subprocess.run(
            ["bitcoin-cli", "--regtest"] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # logging.info(f"run_bitcoin_cli: stdout: {result.stdout}")
        # logging.info(f"run_bitcoin_cli: stderr: {result.stderr}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        # This is where the error from bitcoin-cli lives!
        logging.error(f"bitcoin-cli command failed with exit code {e.returncode}")
        logging.error(f"  bitcoin-cli STDOUT: {e.stdout.strip()}")
        logging.error(f"  bitcoin-cli STDERR: {e.stderr.strip()}")
        return None
    except Exception as e:
        logging.error(f"run_bitcoin_cli: Exception occurred: {e}")
        return None

# check and make sure we have funds before we try anything since it takes a while
# for the funds to actually become available
def check_funds():
    '''
    Check to see if funds are available to spend.
    Will wait for a specific time period before returning a bool of 
    whether the funds are available or not.
    '''
    for attempt in range(RETRY_INT):
        # check output if funds are available
        result_data = lightning_rpc.listfunds()
        if result_data.get('outputs'):
            on_chain_funds_exist = False
            total_on_chain_msat = 0

            for output in result_data['outputs']:
                if output.get('status') == 'confirmed' and not output.get('reserved', False):
                    on_chain_funds_exist = True
                    total_on_chain_msat += output.get('amount_msat', 0)

            if on_chain_funds_exist:
                time.sleep(2) # adding a timer here, a little buffer
                logging.info(f"On-chain funds found! Total confirmed and spendable: {total_on_chain_msat / 1000:.8f} BTC")
                return True
            else:
                logging.info("No confirmed and spendable on-chain funds found. Re-trying")
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: check_funds: No valid funds after {attempt * SLEEP_INT} seconds.')
    return False

# wait until the connection with this node is active
def wait_node_activated(target_node):
    ''''
    Wait till channel with target_node is normal
    '''
    for attempt in range(RETRY_INT):
        if is_node_active(target_node):
            return True
        else:
            logging.info(f"Peer not ready. Trying again in {SLEEP_INT} seconds")
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: wait_node_activated: {target_node} not active after {attempt * SLEEP_INT} seconds.')
    return False

def wait_connection_exists(target_node):
    '''
    wait for a connection between this node and target_node to exists.
    Used to before funding a channel since we need to wait for a connection first.
    '''
    for attempt in range(RETRY_INT):
        # check to make sure we're not waiting on a channel that no longer exists
        if does_connection_exist(target_node):
            return True
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: wait_connection_exists: {target_node} not a peer after {attempt * SLEEP_INT} seconds.')

def does_connection_exist(target_node):
    '''
    Returns wether a connection between this node and target_node exists.
    '''
    try:
        peers_info = lightning_rpc.listpeers()
        for peer in peers_info.get('peers', []):
            if peer.get('id') == target_node and peer.get('connected', False):
                # logging.info(f'Valid peer: {target_node}')
                return True
    except Exception as e:
        logging.error(f'does_channel_exists: Error {e}')
    return False

# check if the connection with this node is active
def is_node_active(target_node):
    '''
    Check whether the channel with this node is CHANNELD_NORMAL
    '''
    try:
        peers_info = lightning_rpc.listfunds()
        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node \
            and channel.get('state') == 'CHANNELD_NORMAL':
                return True
    except Exception as e:
        logging.error(f'is_node_active: Error {e}')
    return False

def has_channel_with(target_node):
    '''
    Check if this node has a channel with target_node.
    Does not check the state of the channel, just that it exists.
    '''
    try:
        peers_info = lightning_rpc.listfunds()
        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node:
                return True
    except Exception as e:
        logging.error(f'has_channel_with: Error {e}')
    return False

def check_channels(channels: set) -> set:
    '''
    Check a list of multiple nodes and return a list of nodes from that list that have active connections.
    Args:
        channels : Set of channels to check the connection status of.
    Returns:
        A set of channels from the incoming list that have an active channel.
    '''
    return_list = [channel for channel in channels if has_channel_with(channel)]

    return set(return_list)

def get_channels():
    """
    Returns a dictionary of all the channels associated with this node.
    Returns:
        Key = Opposing node that this channel connects to
        Values = [short_id, state, capacity, our_amount]
            short_id : shortened id of the node_id
            state : state of the channel (CHANNELD_NORMAL, etc)
            capacity : total capacity of the channel
            our_amount : our liquidity in this channel
    """
    # Query listchannels with the source set to own_node_id
    channels = lightning_rpc.listfunds().get('channels')
    if not channels:
        return {}

    # Parse the output and collect unique destination node IDs
    try:
        node_channels = {
            channel['peer_id']: {'short_id' : get_short_id(channel['peer_id']),
                                 'state' : channel['state'],
                                 'capacity' : channel['amount_msat'],
                                 'our_amount' : channel['our_amount_msat'] } for channel in channels
        }
    except Exception as e:
        logging.error(f"list_peers_with_channels: ERROR: {e}.")
        return {}
    return node_channels

def set_status(status):
    '''
    Use the incoming status to set this node's state in shared memory
    '''
    node_data = create_shared_status(status)
    write_status(node_data)

def get_status_data():
    '''
    Load and return status data stored in shared memory
    Returns an empty dict if no valid shm file 
    '''
    node_name = f'{HOST_NAME}_status'
    data = {}
    try:
        shm = shared_memory.SharedMemory(name=node_name)
        data = shm.buf.tobytes().split(b'\x00', 1)[0]
        shm.close()

        if not data:
            return data

        data = json.loads(data.decode('utf-8'))
    except Exception as e:
        print(f'retrieve_all_status: {node_name} failed to retrived shm because {e}')

    return data

def get_state():
    ''''
    get the state of this node
    '''
    data = get_status_data()
    if data:
        return data['state']
    else:
        return 'no data'
    
def get_capacity(node):
    '''
    Returns the list of channels currently recorded for this node.
    This uses a static file that may not be completely up to date.
    '''
    data = get_status_data()
    if data:
        return data['channels'][node]['capacity']
    else:
        return None
        
def create_shared_status(status, state = None):
    '''
    Trimming status and adding relevant info to be stored in shared memory
    '''
    channels = get_channels()

    '''
    incoming status by default have attributes
    ------------------------------------------
    time
    short_id
    host-name
    counter
    message
    state
    tracking_dict
    sent_messages
    '''

    # lighten node status to add to shm
    node_data = {
        'time' : status.get('time'),
        'short_id' : status.get('short_id'),
        'host_name' : status.get('host_name'),
        'counter' : status.get('counter'),
        'message' : status.get('message'),
        'last_msg_time' : status.get('last_msg_time'),
        'state' : state if state else status.get('state'),
        'receiver' : 'not sending',
        'channels' : channels
    }

    return node_data

def set_sending(status, target_node):
    '''
    used to set this node to sending when propogating messages
    '''
    node_data = create_shared_status(status, 'sending')
    node_data['receiver'] = get_short_id(target_node)
    write_status(node_data)

def write_status(status):
    '''
    Write the state to a shared memory buffer.
    '''
    
    try:
        status = json.dumps(status, default=json_set_converter).encode('utf-8')
    except Exception as e:
        logging.info(f'Trying to dumps status \n{status}')
        logging.info(f'write_status: Error: {e}')
    if len(status) >= SHM_BLOCK_SIZE:
        logging.error(f'write_status: Status is greater than block_size {SHM_BLOCK_SIZE}. Aborting write to memory.')
        return

    # Memory blocks are created by lntest
    try:
        shm = shared_memory.SharedMemory(name=f'{HOST_NAME}_status')
        shm.buf[:len(status)] = status
        shm.buf[len(status):] = b'\x00' * (SHM_BLOCK_SIZE - len(status))
        shm.close()
    except FileNotFoundError:
        logging.error(f'write_status: Error: Shared memory block for {HOST_NAME} not found.')
    except Exception as e:
        logging.error(f'write_status: Error: {e}')

def get_node_id():
    """
    set this node's id
    """
    return lightning_rpc.getinfo()

def balance_all_channels():
    for channel in get_channels():
        balance_channel(channel)

def balance_channel(target_node):
    '''
    used to balance out the channels
    can be called at anytime really
    '''
    if not has_channel_with(target_node):
        logging.warning(f'Trying to balance {target_node} but no channel exists.')
        return
    
    if wait_node_activated(target_node): # we wait for the channel to be normal so we don't spam sendkeys
        to_balance = channel_not_balanced(target_node)
        if to_balance == 0: # if the channel is balanced, channel_not_balanced returns a 0, the amount to balance otherwise
            return
        if check_funds():
            lightning_rpc.keysend(destination=target_node, amount_msat=to_balance)
    else:
        logging.info(f'balance_channel: Channel with {target_node} is not normal. Moving on.')

    logging.info(f'Attempted to balance channel with node {target_node}')

def channel_not_balanced(target_node):
    '''
    Determine whether the channel with this node is balanced.
    Returns:
        Return 0 if balanced
        Returns the amount to keysend to balance the channel
    '''
    channel = get_channels()[target_node]
    capacity = channel['capacity']
    our_msat = channel['our_amount']

    return (our_msat - (capacity // 2)) if (our_msat > (capacity * .7)) else 0
    
def is_synched():
    '''
    Determine if we're synched with the blockchain  height
    '''
    block_height = run_bitcoin_cli(['getblockcount'])
    lightning_height = lightning_rpc.getinfo().get('blockheight', False)
    
    if not block_height:
        return False
    
    try:
        height = int(block_height)
    except ValueError:
        logging.warning(f'check_blockchain_height: Could not convert blockheight {block_height} to int')

    logging.info(f'Blockchain height is {height} against incoming height of {lightning_height}')
    return height == lightning_height

def get_short_id(in_node_id):
    '''
    Return the last 8 characters of the node ID (or any string really)
    '''
    return in_node_id[-8:]

def evaluate_discovery_rule(capacity):
    """
    Check if the given capacity satisfies the discovery rule.
    """
    return capacity % DISCOVERY_RULE_DIVISOR == 0
    
def json_set_converter(obj):
    if isinstance(obj, set):
        return list(obj)
