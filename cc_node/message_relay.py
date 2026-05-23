#NOTE!!! this file should be in the cc_node/ directory that gets mounted to the CC docker container

#This script is used to relay commands to all CC nodes it has channels with, it connects to the REST server and passes the commands to the server.   

import random
import json
import time
import os
import logging
import threading
from pathlib import Path


HOST_NAME = os.getenv("CONTAINER_NAME")

LOG_DIR = Path('logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
STATUS_DIR = Path('status')
STATUS_DIR.mkdir(parents=True, exist_ok=True)

CURRENT_MESSAGE_FILE = STATUS_DIR / f'cc_currentMessage_{HOST_NAME}.json'
log_file_path = LOG_DIR / f'noise_log_{HOST_NAME}.log'

logging.basicConfig(filename=log_file_path, level=logging.INFO, format=f"{HOST_NAME}_noise %(asctime)s - %(levelname)s - %(message)s")

import ln_checker

DISCOVERY_RULE_DIVISOR = [ln_checker.DISCOVERY_RULE_DIVISOR, ln_checker.BOTMASTER_RULE_DIVISOR // 100]

CONNECT_SLEEP = 3 # timer specifically for initialization of channels

# How long in s between background state-updater ticks (channels-ready check,
# is_node_ready bookkeeping). The receive path is event-driven via
# waitanyinvoice, so this timer no longer gates message propagation.
STATUS_TIMER = ln_checker.STATUS_UPDATE_INTERVAL

# Fallback sleep when waitanyinvoice errors out, before retrying.
WAIT_RETRY_SLEEP = 0.5

THIS_NODE = None
# for global (is it sending right now?) type ask
SENDING = False
CONNECTING = True
CREATED_CHANNELS = False

# The TLV record type used for standard text messages in keysend.
MESSAGE_TLV_TYPE = "34349334"
LAST_INVOICE_INDEX = -1


def _state_updater_loop(status, stop_event):
    """
    Background thread: periodically refresh node-readiness state. Replaces the
    state-update bookkeeping that used to piggyback on the polling loop. Runs
    every STATUS_TIMER seconds until stop_event is set.
    """
    global CONNECTING
    while not stop_event.is_set():
        try:
            if is_node_ready(status):
                CONNECTING = False
            elif ln_checker.get_state() != 'connected':
                CONNECTING = True
        except Exception as e:
            logging.warning(f'state-updater: exception {e}')
        stop_event.wait(STATUS_TIMER)


def _handle_invoice(status, invoice):
    """
    Process one paid invoice: extract the embedded command, dedup against
    already-seen counters, forward to peers. Mirrors the per-message logic
    that used to live inside the polling loop.
    """
    label = invoice.get('label', '')
    if not label.startswith('keysend'):
        return
    description = invoice.get('description', '')
    if '|' not in description:
        return
    # Strip the "keysend: " prefix the CLN receiver-side plugin attaches.
    message = description[len('keysend: '):]

    command, command_counter = process_message(message)
    if not command_counter:
        return
    processed_counters = get_processed_counters(status) | set(status.get('sent_messages'))
    if command_counter in processed_counters:
        return

    update_status(status, command, command_counter)
    logging.info(f'Sending message {message} to connected nodes.')
    send_message_to_connected_nodes(status, message, command_counter)
    logging.info(f'Sent {message} to all connected nodes.')


def main():
    """
    Event-driven receive loop. CLN's waitanyinvoice RPC blocks until a paid
    invoice with pay_index > LAST_INVOICE_INDEX arrives, eliminating the
    fixed 0..500 ms polling wait that previously dominated per-hop latency.
    """
    global SENDING
    global CONNECTING
    global LAST_INVOICE_INDEX
    load_this_node()

    # this will either load a saved status to recover from a crash
    # or it will return a new default status, which is automatically saved
    # to disk.
    status = load_status()

    # Wait until we've finished creating channels with other nodes
    # Sleep time here is different since it takes a little to find nodes and then
    # try to connect to them.
    set_state(status, 'initializing')
    if os.environ.get('SKIP_CC_MANAGER') != '1':
        while not ln_checker.get_channels():  # need to make sure we're returning stuff
            time.sleep(CONNECT_SLEEP)
        while len(ln_checker.get_channels()) < 1:
            set_state(status, 'initializing')
            time.sleep(CONNECT_SLEEP)
        logging.info('Channels have started being created.')
    else:
        logging.info('Orchestrator-controlled topology: skipping channel wait loop.')
        set_state(status, 'connected')

    # Periodic state bookkeeping moves to a daemon thread so the receive path
    # can block on waitanyinvoice without missing readiness checks.
    stop_event = threading.Event()
    updater = threading.Thread(
        target=_state_updater_loop, args=(status, stop_event), daemon=True)
    updater.start()

    # LAST_INVOICE_INDEX starts at -1; waitanyinvoice with lastpay_index=0
    # returns the first paid invoice with pay_index > 0 (CLN starts pay_index
    # at 1). Already-paid invoices replay through _handle_invoice once, which
    # is a no-op because process_message dedups by counter.
    if LAST_INVOICE_INDEX < 0:
        LAST_INVOICE_INDEX = 0

    while True:
        try:
            invoice = ln_checker.lightning_rpc.waitanyinvoice(
                lastpay_index=LAST_INVOICE_INDEX)
        except Exception as e:
            logging.warning(f'waitanyinvoice error: {e}; retrying in {WAIT_RETRY_SLEEP}s')
            time.sleep(WAIT_RETRY_SLEEP)
            continue

        if not invoice:
            continue
        pay_index = invoice.get('pay_index')
        if pay_index is None or pay_index <= LAST_INVOICE_INDEX:
            continue
        LAST_INVOICE_INDEX = pay_index

        if invoice.get('status') != 'paid':
            continue

        SENDING = True
        try:
            _handle_invoice(status, invoice)
        finally:
            SENDING = False



def send_message_to_connected_nodes(status, message, counter):
    """
    Send a message to all nodes connected to this node via channels.
    Sends to all neighbors concurrently using threads for maximum
    propagation speed.
    """
    global SENDING
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from pyln.client import LightningRpc

    connected_nodes = get_connected_nodes()
    if not connected_nodes:
        logging.warning("No connected nodes found.")
        return
    tlv_json = {MESSAGE_TLV_TYPE : encode_msg(message)}

    # scramble the list of nodes to randomize sending pattern
    random.shuffle(connected_nodes)

    # Filter out nodes that already received this message
    nodes_to_send = []
    for target_node in connected_nodes:
        if target_node in status.get('tracking_dict').keys() and counter in status.get('tracking_dict')[target_node]:
            logging.info(f'Message {counter} has already been sent to {target_node}. Aborting send.')
            continue
        nodes_to_send.append(target_node)

    if not nodes_to_send:
        return

    rpc_path = os.getenv("LIGHTNING_RPC_PATH", "/root/.lightning/regtest/lightning-rpc")
    SENDING = True

    def _keysend_worker(target_node):
        """Each thread gets its own RPC connection for thread safety."""
        try:
            rpc = LightningRpc(rpc_path)
            result = rpc.xkeysend(destination=target_node, amount_msat=1, extratlvs=tlv_json)
            return (target_node, result, None)
        except Exception as e:
            return (target_node, None, e)

    logging.info(f'Sending to {len(nodes_to_send)} nodes concurrently')

    with ThreadPoolExecutor(max_workers=len(nodes_to_send)) as pool:
        futures = {pool.submit(_keysend_worker, node): node for node in nodes_to_send}
        for future in as_completed(futures):
            target_node, result, error = future.result()
            if result:
                if target_node in status.get('tracking_dict').keys():
                    status.get('tracking_dict')[target_node].add(counter)
                else:
                    status.get('tracking_dict')[target_node] = {counter}
                logging.info(f"Message: [{message}] sent to {target_node} successfully.")
            elif error:
                logging.error(f"Error sending message to {target_node}: {error}")

def get_connected_nodes():
    """
    Retrieve all nodes that have a channel with this node and satisfy the discovery rule.
    """
    try:
        # Peers we currently have a LIVE connection to. Skip peers that are
        # offline (e.g. taken down during a takedown test): their channels are
        # auto-disabled, so forwarding to them just fails with a 205 "no usable
        # path" error and wastes time. Filtering them is coverage-neutral (live
        # peers still receive the command) and avoids the dead-peer error spam.
        online_peers = set()
        try:
            peers = ln_checker.lightning_rpc.listpeers().get('peers', [])
            online_peers = {p['id'] for p in peers if p.get('connected')}
        except Exception as e:
            logging.warning(f"get_connected_nodes: listpeers failed ({e}); not filtering offline peers.")

        connected_nodes = set()
        channels = ln_checker.lightning_rpc.listfunds().get('channels', [])
        logging.info(f"Total channels retrieved: {len(channels)}")

        for channel in channels:
            # Derive a key that matches DISCOVERY_RULE_DIVISOR / BOTMASTER_RULE_DIVISOR
            # e.g. innocent channel: 190000 sat = 190000000 msat // 10^7 = 19
            capacity = int(channel.get("amount_msat", 0)) // 10000000
            peer = channel.get('peer_id')
            logging.info(f"Channel capacity: {capacity}, Peer: {peer}")

            # Skip the innocent channel (matches discovery rule)
            if capacity in DISCOVERY_RULE_DIVISOR:
                continue
            # Skip peers that are not currently connected (only filter when we
            # successfully got the online set, so an RPC hiccup degrades to old
            # behavior rather than dropping everyone).
            if online_peers and peer not in online_peers:
                logging.info(f"Skipping offline peer {peer}.")
                continue
            connected_nodes.add(peer)
        logging.info(f"Connected (online) nodes satisfying discovery rule: {list(connected_nodes)}")
        return list(connected_nodes)

    except json.JSONDecodeError:
        logging.error("Error parsing listchannels output.")
        return []

def is_node_ready(status):
    '''
    Check if channels are still being created and balanced
    Return False if a single channel is still connecting and nodes are not balanced
    True otherwise
    '''
    global CREATED_CHANNELS

    channels = ln_checker.get_channels()
    if not channels:
        return True
    

    try:
        for channel in channels.keys():
            info = channels[channel]
            if ln_checker.evaluate_discovery_rule(int(info.get("capacity", 0)) // 1000) and info.get('state') in ln_checker.NOT_CONNECTING:
                set_state(status,'connected')
                CREATED_CHANNELS = True
                return True
        # Check if we have enough CC-to-CC peers with working channels
        cc_peers = get_connected_nodes()
        if len(cc_peers) >= 1:
            set_state(status,'connected')
            CREATED_CHANNELS = True
            return True
        if CREATED_CHANNELS:
            set_state(status,'connected')
            return True
        else:
            set_state(status,'connecting')
            return False
    except Exception as e:
        logging.info(f'Exception {e}')
        return True
    
def process_message(message):
    """
    Process a single message in the format <command>|<counter>.

    Args:
        message (str): The message to process.
    Returns:
        The counter associated with this command
        Returns nothing if it is an invalid counter
    """
    try:
        parts = message.split('|')
        if len(parts) != 2:
            logging.info(f"Ignoring invalid message format: {message}")
            return

        command, counter = parts[0], parts[1]

        # Check if the counter is valid and unprocessed
        if not counter.isdigit():
            logging.info(f"Ignoring message with invalid counter: {message}")
            return
        # we return the commmand and the counter associated with it
        return command, counter
    except Exception as e:
        logging.error(f"Error processing message: {e}")

def get_processed_counters(status):
    '''
    Get a current set of processed counters
    Only returns an intersection of all counters
    Moves counter to one global set when its been sent to all channels
    '''

    prun_msg_dict(status)
    if status.get('tracking_dict'):
        first_value = next(iter(status.get('tracking_dict').values()))
        processed_counters = first_value.copy()
    else:
        return set()
    
    for counter_set in status.get('tracking_dict').values():
        processed_counters = processed_counters & counter_set

    # Clean up, remove processed counters from the dictionary into one global set 
    # to save on memory
    for counter_set in status.get('tracking_dict').values():
        counter_set.difference_update(processed_counters)

    new_sent_messages = set(status.get('sent_messages')) | processed_counters
    status.update({
        'sent_messages': list(new_sent_messages)
    })

    return processed_counters

def prun_msg_dict(status):
    '''
    Look at our channels - remove from the dictionary if
    something happened to them (the node died or something)
    that way we resend messages in case they got dropped.
    '''
    

    channels = ln_checker.get_channels()
    to_remove = set()
    for node in status.get('tracking_dict'):
        if node not in channels:
            logging.warning(f'Node {node} has no/broken channel. Removing from tracker.')
            to_remove.add(node)
            
    for node in to_remove:
        status.get('tracking_dict').pop(node)

def set_state(status, state):
    '''
    Update current status state to state
    Save the current state to shared memory for tester_v1 to use.
    DOES NOT save to disk - call update_stats or save_status instead
    '''
    # just in case we somehow send this an empty status
    if not status:
        logging.warning(f'set_state: WARNING: Received an empty status. Attempting to load from disk.')
        status = load_status()

    #update the status
    status.update({
        'time': time.time(),
        'state': state
    })
    
    # save to shm
    ln_checker.set_status(status)

def update_status(status, message, counter):
    '''
    Update the status to reflect new changes.
    An empty status will be updated.
    Save updated status to disk.
    '''
    # check if we were given an empty status
    if not status:
        status = load_status()
        
    logging.info(f"update_status: status counter: {status.get('counter')} and new counter: {counter}")
    counter = int(counter)
    if int(counter) > status.get('counter'):
        logging.info(f'update_status: incrementing counter')
        status.update({
            'time' : time.time(),
            'last_msg_time': time.time(),
            'counter' : counter,
            'message' : message
        })
        save_status(status)

def load_status():
    '''
    Load the last saved status.
    Returns the default status if there is no saved status.
    '''
    status = {}
    # try opening a past version
    try:
        with open(CURRENT_MESSAGE_FILE, 'r') as f:
            status = json.load(f)
    except Exception as e:
        logging.warning(f'load_status: Exception. {e}')
    
    # If no status was loaded, create a default status
    # and save that to file.
    if not status:
        logging.info(f'load_status: No status found, creating default status.')
        status = {
            'time' : time.time(),
            'short_id' : ln_checker.get_short_id(THIS_NODE),
            'host_name' : HOST_NAME,
            'counter' : 0,
            'message' : 'node online',
            'last_msg_time': time.time(),
            'state' : 'initializing',
            'tracking_dict' : {},
            'sent_messages' : {}
        }
        save_status(status)
    
    return status

def save_status(status):
    '''
    Saves status as a json file to disk
    '''
    logging.info(f'save_status: Writing to disk: \n{status}')
    try:
        with open(CURRENT_MESSAGE_FILE, 'w') as f:
            json.dump(status, f, default=ln_checker.json_set_converter)
    except Exception as e:
        logging.warning(f'load_status: Exception. {e}')

def load_this_node ():
    """
    Set global THIS_NODE variable
    """
    global THIS_NODE 
    THIS_NODE = ln_checker.lightning_rpc.getinfo().get('id')


def encode_msg(in_msg):
    return in_msg.encode('utf-8').hex()

if __name__ == '__main__':
    main()
