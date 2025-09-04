import logging
from pathlib import Path
import time
import subprocess
import json
import os
from multiprocessing import shared_memory

# how many times the checker will try
RETRY_INT = 5
SLEEP_INT= 10
DISCOVERY_RULE_DIVISOR = 19

HOST_NAME = os.getenv("CONTAINER_NAME")

# where the status file lives
STATUS_DIR = Path('status')
STATUS_DIR.mkdir(parents=True, exist_ok=True)
STATUS_FILE = STATUS_DIR / 'status_'

# channel states
NOT_CONNECTING = ['CHANNELD_NORMAL', 'ONCHAIN']
DONT_BALANCE = ['CLOSINGD_COMPLETE', 'ONCHAIN']


def run_lightning_cli(command):
    try:
        result = subprocess.run(
            ["lightning-cli", "--regtest"] + command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        # logging.info(f"run_lightning_cli: stdout: {result.stdout}")
        # logging.info(f"run_lightning_cli: stderr: {result.stderr}")
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

# thomas functions
# check and make sure we have funds before we try anything since it takes a while
# for the funds to actually become available
def check_funds():
    '''
    Check to see if funds are available to spend.
    Will wait for a specific time period before returning a bool of 
    whether the funds are available or not.
    '''
    for attempt in range(RETRY_INT):
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
        command = ["lightning-cli", f"--network=regtest", "listpeers"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for peer in peers_info.get('peers', []):
            if peer.get('id') == target_node:
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
        command = ["lightning-cli", f"--network=regtest", "listfunds"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node \
            and channel.get('state') == 'CHANNELD_NORMAL':
                # logging.info(f'Peer {target_node} is ready to receive')
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
        command = ["lightning-cli", f"--network=regtest", "listfunds"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node:
                # logging.info(f'has_channel_with: Channel exists with {target_node}')
                return True
                
    except Exception as e:
        logging.error(f'has_channel_with: Error {e}')
    # logging.info(f"has_channel_with: No channel with {target_node} found")
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
    output = run_lightning_cli(["listfunds"])
    if not output:
        logging.error("record state: Failed to retrieve channel list.")
        return None

    # Parse the output and collect unique destination node IDs
    try:
        channels = json.loads(output).get("channels", [])
        node_channels = {
            channel['peer_id']: {'short_id' : get_short_id(channel['peer_id']),
                                 'state' : channel['state'],
                                 'capacity' : channel['amount_msat'],
                                 'our_amount' : channel['our_amount_msat'] } for channel in channels
        }
        # node_funds = {}
        # for idx, node in enumerate(node_channels.keys()):
        #     node_channels[node].append({'capacity' : channels[idx].get('amount_msat')})
        #     node_channels[node].append({'out_amount': channels[idx].get('our_amount_msat')})
    except json.JSONDecodeError:
        logging.error("list_peers_with_channels: Failed to parse channel list output.")
        return None
    return node_channels

def set_state(state):
    '''
    set state to 'state'
    write immediately to json file
    '''
    node_data = create_state(state)
    write_state(node_data)

def get_status_data():
    '''
    Returns the entirety of the static status data 
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
        print(f'retrieve_all_status: {node_name} failed to retrived shm because {e}\nRecreating shm.')

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
        
def create_state(state):
    '''
    Wrapper to get a dictionary containing all the info required for the tracker.
    state is the state we're changing it to (online, sending, down, etc)
    this state is different (abstracted from) than the actual channel state
    '''
    channels = get_channels()
    this_id = get_node_id()
    # going to save this as a json
    node_data = {
        'name' : HOST_NAME,
        'id' : this_id,
        'short id' : get_short_id(this_id),
        'state' : state,
        'receiver' : 'not sending',
        'channels' : channels
    }
    return node_data

def set_sending(target_node):
    '''
    used to set this node to sending when propogating messages
    '''
    node_data = create_state('sending')
    node_data['receiver'] = get_short_id(target_node)
    write_state(node_data)

def write_state(data):
    '''
    Write the state to a shared memory buffer.
    '''
    block_size = 2048 # give ourselves a little wiggle room (each status can get to roughly 1.5KB)
    status = json.dumps(data).encode('utf-8')

    if len(status) >= block_size:
        logging.error(f'write_data: Status is greater than block_size {block_size}. Aborting write to memory.')
        return
    
    # Memory blocks are created by the tester_v1 script
    try:
        shm = shared_memory.SharedMemory(name=f'{HOST_NAME}_status')
        shm.buf[:len(status)] = status
        shm.buf[len(status):] = b'\x00' * (block_size - len(status))
        shm.close()
    except FileNotFoundError:
        logging.error(f'write_state: Error: Shared memory block for {HOST_NAME} not found.')
    except Exception as e:
        logging.error(f'write_state: Error: {e}')



def get_node_id():
    """
    set this node's id
    """
    output = run_lightning_cli(["getinfo"])
    
    return json.loads(output).get('id')

def balance_all_channels():
    for channel in get_channels():
        balance_channel(channel)

def balance_channel(target_node):
    '''
    used to balance out the channels
    can be called at anytime really
    '''
    # logging.info(f'Balancing channel with node {target_node}')
    if not has_channel_with(target_node):
        logging.warning(f'Trying to balance {target_node} but no channel exists.')
        return
    
    if wait_node_activated(target_node): # we wait for the channel to be normal so we don't spam sendkeys
        to_balance = channel_not_balanced(target_node)
        if to_balance == 0: # if the channel is balanced, channel_not_balanced returns a 0, the amount to balance otherwise
            return
        if check_funds():
            run_lightning_cli(["keysend", target_node, str(to_balance)])
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
    
def check_blockchain_height(in_height):
    '''
    Test if this blockchain height matches the actual height of the blockchain.
    '''
    output = run_bitcoin_cli(['getblockcount'])

    if not output:
        return False
    
    try:
        height = int(output)
    except ValueError:
        logging.warning(f'check_blockchain_height: Could not convert blockheight {output} to int')

    # logging.info(f'Blockchain height is {height} against incoming height of {in_height}')
    return height == in_height

def get_short_id(in_node_id):
    '''
    Return the first 5 characters of the node ID (or any string really)
    '''
    return in_node_id[:5]

def evaluate_discovery_rule(capacity):
    """
    Check if the given capacity satisfies the discovery rule.
    """
    return capacity % DISCOVERY_RULE_DIVISOR == 0