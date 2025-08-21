#NOTE!!! this file should be in the NodeManagerComms(directory in home address) directory that gets mounted to the CC docker container

#This script is used to relay commands to all CC nodes it has channels with, it connects to the REST server and passes the commands to the server.   

import subprocess
import json
import random
# import requests
import time
import os
import csv
import logging

import ln_checker

# Address for this bot to register with REST_server
BOT_ADDRESS = '127.0.0.9'  # Adjust if a different address is required
SERVER_URL = 'http://127.0.0.1:8000'

HOST_NAME = os.getenv("CONTAINER_NAME")
MESSAGE_LOG_FILE = f'cc_messageLog_{HOST_NAME}.csv'
CURRENT_MESSAGE_FILE = f'cc_currentMessage_{HOST_NAME}.csv'
CURRENT_COMMAND_COUNTER = 0
DISCOVERY_RULE_DIVISOR = [19, 1231]


RETRY_INT = 5
SLEEP_INT = 0.5 # INT is interval, should probably change that to something better
CONNECT_SLEEP = 10 # timer specifically for initialization of channels
RETRY_COUNT = 10

# how long in s between status updates
STATUS_TIMER = 1

THIS_NODE = None
# for global (is it sending right now?) type ask
SENDING = False
CONNECTING = True

# The TLV record type used for standard text messages in keysend.
MESSAGE_TLV_TYPE = "34349334"
# to keep track of what has been sent where (if we need to resend it.)
MESSAGE_TRACKING_DICT = {} # this is going to a be a dict of sets
SENT_MESSAGES = set()
LAST_INVOICE_INDEX = -1

logging.basicConfig(filename=f'noise_log_{HOST_NAME}.log', level=logging.INFO, format=f"{HOST_NAME}_noise %(asctime)s - %(levelname)s - %(message)s")


def connect_to_server():
    """
    Connect the bot to the REST server by registering its address.
    """
    connect_payload = {'address': BOT_ADDRESS}

    """
    We're going to try to connect to the server 15 times
    Since I've modified the scripts to run everything all at once, this is going
    to run at the same time as the REST_server.py, so we need to wait.
    """
    attempt_max = 10

    # for attempt in range(10):
    #     # lets sleep first yeah?
    #     time.sleep(SLEEP_INT)
        
    #     try:
    #         connect_response = requests.post(f'{SERVER_URL}/v1/connect/', json=connect_payload)
    #         if connect_response.status_code == 200:
    #             logging.info("Bot connected to the server successfully.")
    #             break
    #         else:
    #             logging.info(f"Server is not running yet, retrying in {SLEEP_INT} seconds.")
    #     except Exception as e:
    #         logging.error(f"Error connecting to the server: {e}")
        
    #     if attempt == attempt_max - 1:
    #         logging.error(f"Cannot connect to server after trying {attempt_max} times, CATASTROPHIC ERROR")


def send_command_to_server(command):
    """
    Send a command to REST_server to be executed on all connected bots.
    
    Args:
        command (str): The command to execute.

    this is to simulate a CC server communicating with its bots
    """
    # control_payload = {'command': command}
    # try:
    #     control_response = requests.post(f'{SERVER_URL}/v1/control/', json=control_payload)
    #     if control_response.status_code == 200:
    #         response_data = control_response.json()
    #         logging.info("Command sent to server successfully.")
    #         logging.info("Output from server:")
    #         logging.info(f"{response_data.get('responses')}")
    #         return True
    #     else:
    #         logging.error(f"Server responded with error: {control_response.status_code} - {control_response.text}")
    # except requests.exceptions.Timeout:
    #     logging.error(f"Request timed out while sending command '{command}' to server.")
    # except Exception as e:
    #     logging.error(f"Error sending request to server: {e}")

    return False

# def get_all_messages():
#     """
#     Retrieve all messages using lightning-cli and format them for processing.

#     Returns:
#         list: A list of message bodies retrieved from lightning-cli.
#     """
#     try:
#         result = subprocess.run(
#             ['lightning-cli', '--regtest', 'allmsgs'],
#             capture_output=True,
#             text=True
#         )
#         if result.returncode != 0:
#             logging.error(f"Error executing lightning-cli: {result.stderr}")
#             return []

#         raw_messages = json.loads(result.stdout)
#         message_keys = [key for key in raw_messages if key.startswith('message') and 'body' in raw_messages[key]]
#         message_keys.sort(key=lambda x: int(x[len('message'):]))

#         messages = [raw_messages[key]['body'] for key in message_keys]
#         # logging.info(f"Retrieved message bodies: {messages}")
#         return messages
#     except Exception as e:
#         logging.error(f"Exception occurred while getting messages: {e}")
#         return []

def get_new_messages():
    '''
    Retrieve all new messages using the lightning-cli listinvoices command and sorting
    through the returns.
    '''
    global LAST_INVOICE_INDEX
    messages = []
    try:
        result = subprocess.run(
            ['lightning-cli', '--regtest', 'listinvoices'],
            capture_output=True,
            text=True,
            check=True
        )
        if result.returncode != 0:
            logging.error(f"Error executing lightning-cli: {result.stderr}")
            return None
        # the messages that have been received by this node
        invoices = json.loads(result.stdout)['invoices']

        if len(invoices) == 0:
            return []
        
        new_invoices = []
        max_payindex = LAST_INVOICE_INDEX
        # each invoice contains the extra tlv that contains the message / command 
        for invoice in invoices:
            if not invoice: # make sure we actually have invoices to look at
                logging.info(f'skipping.')
                continue

            if invoice.get('status') == 'paid' and 'pay_index' in invoice and invoice['pay_index'] > LAST_INVOICE_INDEX:
                new_invoices.append(invoice)
                max_payindex = max(invoice['pay_index'], max_payindex)
               
        LAST_INVOICE_INDEX = max_payindex

        # get the actual messages out
        for invoice in new_invoices:
            label = invoice.get('label', '')
            if label.startswith('keysend'):
                msg = invoice.get('description', '')
                if '|' in msg:
                    msg = msg[len('keysend: '):]
                    messages.append(msg)
    except Exception as e:
        logging.error(f"Exception occurred while getting messages: {e}")
        return None
    return messages

def decode_msg(in_msg):
    return bytes.fromhex(in_msg).decode('utf-8', errors='ignore')

def encode_msg(in_msg):
    return in_msg.encode('utf-8').hex()

def get_connected_nodes():
    """
    Retrieve all nodes that have a channel with this node and satisfy the discovery rule.
    """
    # # Step 1: Get the node's own ID
    # try:
    #     own_node_id = THIS_NODE
    #     if not own_node_id:
    #         logging.info("Failed to retrieve own node ID from getinfo.")
    #         return []
    # except json.JSONDecodeError:
    #     logging.info("Error parsing getinfo output.")
    #     return []

    # Step 2: Get the list of channels
    # listchannels_output = subprocess.run(
    #     ['lightning-cli', '--regtest', 'listchannels'],
    #     capture_output=True,
    #     text=True
    # )
    # if listchannels_output.returncode != 0:
    #     logging.info(f"Error retrieving channels: {listchannels_output.stderr}")
    #     return []

    # get the list of peers and just send to peers that arent the innocent node
    listfunds_output = subprocess.run(
        ['lightning-cli', '--regtest', 'listfunds'],
        capture_output=True,
        text=True
    )
    if listfunds_output.returncode != 0:
        logging.warning(f"Error retrieving listfunds: {listfunds_output.stderr}")
        return []

    try:
        connected_nodes = set()
        channels = json.loads(listfunds_output.stdout).get("channels", [])
        
        logging.info(f"Total channels retrieved: {len(channels)}")
        
        for channel in channels:
            # Extract capacity in satoshis
            capacity = int(channel.get("amount_msat", 0)) // 10000000  # Convert msat to satoshis
            logging.info(f"Channel capacity: {capacity}, Peer: {channel['peer_id']}")

            # Add this channel if its not the innocent channel (i.e. doesn't match discovery rule)
            if capacity in DISCOVERY_RULE_DIVISOR:
                continue
            else:
                connected_nodes.add(channel.get('peer_id'))
        logging.info(f"Connected nodes satisfying discovery rule: {list(connected_nodes)}")
        return list(connected_nodes)

    except json.JSONDecodeError:
        logging.error("Error parsing listchannels output.")
        return []

# def send_message_to_connected_nodes(message):
#     """
#     Send a message to all nodes connected to this node via channels and display the message content.
#     """
#     connected_nodes = get_connected_nodes()
#     if not connected_nodes:
#         logging.warning("No connected nodes found.")
#         return

#     for target_node in connected_nodes:
#         sendmsg_command = [
#             'lightning-cli', '--regtest', 'sendmsg',
#             target_node,
#             message
#             #'5'  # Amount in millisatoshis
#         ]
#         ln_checker.check_funds()
#         ln_checker.does_connection_exist(target_node)
#         ln_checker.wait_node_activated(target_node)
#         # time.sleep(2) # seeing if a delay fixes the first sending error
#         #for attempt in range(RETRY_COUNT): # maybe the connections are closing because of the repeated tries
#         result = subprocess.run(sendmsg_command, 
#                                 stdout=subprocess.PIPE,
#                                 stderr=subprocess.PIPE,
#                                 text=True)
#         if result.returncode == 0:
#             logging.info(f'Message: "{message}" sent to {target_node} successfully.')
#             continue
#         else:
#             logging.error(f"Error sending message to {target_node}: {result.stdout} || {result.stderr}")

def send_message_to_connected_nodes(message, counter):
    """
    Send a message to all nodes connected to this node via channels and display the message content.
    """
    global SENDING
    global MESSAGE_TRACKING_DICT

    connected_nodes = get_connected_nodes()
    if not connected_nodes:
        logging.warning("No connected nodes found.")
        return
    tlv_json = json.dumps({MESSAGE_TLV_TYPE : encode_msg(message)})

    for target_node in connected_nodes:

        # we check first, no need to resend a message to a node that has already received it
        if target_node in MESSAGE_TRACKING_DICT.keys() and counter in MESSAGE_TRACKING_DICT[target_node]:
            logging.info(f'Message {counter} has already been sent to {target_node}. Aborting send.')
            logging.info(MESSAGE_TRACKING_DICT)
            continue

        SENDING = True
        ln_checker.set_sending(target_node) # for the status tracker
        sendmsg_command = ["lightning-cli", "--regtest", "keysend",
            f"destination={target_node}",
            f"amount_msat=1",
            f"extratlvs={tlv_json}"]

        ln_checker.check_funds()
        # if we've sent more than 1 message, we should be connected so don't check. If we drop the channel, we start at 1 anyways
        if int(counter) < 1:
            logging.info(f'First message. Doing checks')
            ln_checker.does_connection_exist(target_node)
            ln_checker.wait_node_activated(target_node)
        try:
            result = subprocess.run(sendmsg_command, 
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                check=True)
            if result.returncode == 0: # success
                if target_node in MESSAGE_TRACKING_DICT.keys(): # make a new entry for the first messages
                    MESSAGE_TRACKING_DICT[target_node].add(counter) # tracking invidual sends in case it drops
                else:
                    MESSAGE_TRACKING_DICT[target_node] = {counter}
                logging.info(f'Message: "{message}" sent to {target_node} successfully. Counter is {MESSAGE_TRACKING_DICT[target_node]}')
                continue
            else:
                logging.error(f'{MESSAGE_TRACKING_DICT}')
                logging.error(f"Error sending message to {target_node}: {result.stdout} || {result.stderr}")
        except subprocess.CalledProcessError as e:
            logging.error(f"Error sending message to {target_node}: {e}")


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

def write_to_csv(message, counter):
    global CURRENT_COMMAND_COUNTER
    
            #'time_stamp', 'first 5 of the node id', 'CC container', 'Message Counter', 'Message'
    entry = [time.time(), THIS_NODE[:5], HOST_NAME, counter, message]
    logging.info(entry)
    if int(counter) > CURRENT_COMMAND_COUNTER:
        print(f'write_to_csv: incrementing counter')
        with open(CURRENT_MESSAGE_FILE, 'w') as f:
            csvwriter = csv.writer(f)
            csvwriter.writerow(entry)

    with open(MESSAGE_LOG_FILE, 'a', newline = '') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow(entry)

def get_processed_counters():
    '''
    Get a current set of processed counters
    Only returns an intersection of all counters
    Moves counter to one global set when its been sent to all channels
    '''
    global MESSAGE_TRACKING_DICT
    global SENT_MESSAGES

    prun_msg_dict()
    if MESSAGE_TRACKING_DICT:
        first_value = next(iter(MESSAGE_TRACKING_DICT.values()))
        processed_counters = first_value.copy()
    else:
        return set()
    
    for counter_set in MESSAGE_TRACKING_DICT.values():
        processed_counters = processed_counters & counter_set

    # Clean up, remove processed counters from the dictionary into one global set 
    # to save on memory
    for counter_set in MESSAGE_TRACKING_DICT.values():
        counter_set.difference_update(processed_counters)

    SENT_MESSAGES = SENT_MESSAGES | processed_counters

    return processed_counters

def prun_msg_dict():
    '''
    Look at our channels - remove from the dictionary if
    something happened to them (the node died or something)
    that way we resend messages in case they got dropped.
    '''
    global MESSAGE_TRACKING_DICT

    channels = ln_checker.get_channels()
    for node in MESSAGE_TRACKING_DICT:
        if node not in channels:
            logging.warning(f'Node {node} has no/broken channel. Removing from tracker.')
            MESSAGE_TRACKING_DICT.pop(node)

def load_this_node ():
    """
    Set global THIS_NODE variable
    """
    global THIS_NODE 
    output = result = subprocess.run(
            ['lightning-cli', '--regtest', 'getinfo'],
            capture_output=True,
            text=True
        )
    output = result.stdout.strip()
    THIS_NODE = json.loads(output).get('id')

def main():
    """
    Main function to process and send commands to the REST server.
    """
    global SENDING
    global CONNECTING
    # connect_to_server()  # Register this bot with the REST server on startup
    load_this_node()
    # Create csv file with headers
    with open(MESSAGE_LOG_FILE, 'w', newline = '') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow(['Time', 'Short_ID', 'CC container', 'Message Counter', 'Message'])
        # put initial message that this node is online (more for the tracker than anthing else.)
        csvwriter.writerow([time.time(), THIS_NODE[:5], HOST_NAME, 0, 'node online'])
    with open(CURRENT_MESSAGE_FILE, 'w') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow([time.time(), THIS_NODE[:5], HOST_NAME, '0', '0'])

    # Wait until we've finished creating channels with other nodes
    # Sleep time here is different since it takes a little to find nodes and then
    # try to connect to them.
    
    ln_checker.set_state('initializing')
    while not ln_checker.get_channels(): # need to make sure we're returning stuff
        time.sleep(CONNECT_SLEEP)
    while len(ln_checker.get_channels()) < 2:
        ln_checker.set_state('initializing')
        time.sleep(CONNECT_SLEEP)
    logging.info('We have created a channel with the Innnocent node.')

    update_counter = 0
    max_counter = (STATUS_TIMER // SLEEP_INT) # this is so that we don't update the node too often
    
    while True:
        # Retrieve all messages using lightning-cli
        messages = get_new_messages()
        written_commands = set()

        if messages and len(messages) > 0:
            for message in messages:
                command, command_counter = process_message(message)  # seperate the command and counter
                processed_counters = get_processed_counters() | SENT_MESSAGES # combine it with the global tracker 
                if command_counter and command_counter not in processed_counters:
                    if command_counter not in written_commands: # this way we only write it once
                        written_commands.add(command_counter)
                        write_to_csv(command, command_counter)
                    processed_counters.add(command_counter)
                    logging.info(f'Sending message {message} to connected nodes.')
                    send_message_to_connected_nodes(message, command_counter)
                    logging.info(f'Sent {message} to all connected nodes.')

        time.sleep(SLEEP_INT)
        if SENDING or CONNECTING or update_counter > max_counter: # detect state
            if ln_checker.is_node_ready(): # is_node_ready automatically sets the state
                CONNECTING = False
            elif ln_checker.get_state() != 'online':
                CONNECTING = True
                
            update_counter = 0
            SENDING = False
        elif update_counter > max_counter:
            if ln_checker.is_node_ready():
                CONNECTING = True
        else:
            update_counter += 1


if __name__ == '__main__':
    main()
