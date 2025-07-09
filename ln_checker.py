import logging
import time
import subprocess
import json
import os

# how many times the checker will try
RETRY_INT = 10
SLEEP_INT= 5

HOST_NAME = os.getenv("CONTAINER_NAME")

# where the status file lives
STATUS_FILE = 'status_'

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

# thomas functions
# check and make sure we have funds before we try anything since it takes a while
# for the funds to actually become available
def check_funds():
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
                    on_chain_funds_exist = True
                    total_on_chain_msat += output.get('amount_msat', 0)

            if on_chain_funds_exist:
                logging.info(f"On-chain funds found! Total confirmed and spendable: {total_on_chain_msat / 1000:.8f} BTC")
                return True
            else:
                logging.info("No confirmed and spendable on-chain funds found. Re-trying")
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: check_funds: No valid funds after {attempt * SLEEP_INT} seconds.')
    return False

# wait until the connection with this node is active
def wait_node_activated(target_node):
    for attempt in range(RETRY_INT):
        if is_node_active(target_node):
            return True
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: wait_node_activated: {target_node} not active after {attempt * SLEEP_INT} seconds.')

def wait_connection_exists(target_node):
    for attempt in range(RETRY_INT):
        # check to make sure we're not waiting on a channel that no longer exists
        if does_connection_exist(target_node):
            return True
        time.sleep(SLEEP_INT)
    logging.warning(f'ln_checker: wait_connection_exists: {target_node} not a peer after {attempt * SLEEP_INT} seconds.')

def does_connection_exist(target_node):
    try:
        command = ["lightning-cli", f"--network=regtest", "listpeers"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for peer in peers_info.get('peers', []):
            if peer.get('id') == target_node:
                logging.info(f'Valid peer: {target_node}')
                return True
    except Exception as e:
        logging.error(f'does_channel_exists: Error {e}')
    return False

# check if the connection with this node is active
def is_node_active(target_node):
    try:
        command = ["lightning-cli", f"--network=regtest", "listfunds"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node \
            and channel.get('state') == 'CHANNELD_NORMAL':
                logging.info(f'Peer {target_node} is ready to receive')
                return True
        logging.info("Peer not ready. Trying again in 5 seconds")
    except Exception as e:
        logging.error(f'is_node_active: Error {e}')
    return False

def has_channel_with(target_node):
    try:
        command = ["lightning-cli", f"--network=regtest", "listfunds"]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        peers_info = json.loads(result.stdout)

        for channel in peers_info.get('channels', []):
            if channel.get('peer_id') == target_node:
                logging.info(f'Channel exists with {target_node}')
                return True
            else:
                logging.info(f"No channel with {target_node} found")
    except Exception as e:
        logging.error(f'has_channel_with: Error {e}')
    return False

def get_channels():
    """
    First lets get all the nodes that have a channel with this one
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
    node_data = get_state(state)
    write_state(node_data)

def get_state(state):
    '''
    Wrapper to get a dictionary containing all the info required for the tracker
    state is the state we're changing it to (online, sending, down, etc)
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
    node_data = get_state('sending')
    node_data['receiver'] = get_short_id(target_node)
    write_state(node_data)

def write_state(data):
    name = data['name']
    filename = f'{STATUS_FILE}{name}.json'
    with open(filename, 'w') as file:
        json.dump(data, file, indent=4)

def get_node_id():
    """
    set this node's id
    """
    output = run_lightning_cli(["getinfo"])
    
    return json.loads(output).get('id')

def get_short_id(in_node_id):
    '''
    Return the first 5 characters of the node ID (or any string really)
    '''
    return in_node_id[:5]