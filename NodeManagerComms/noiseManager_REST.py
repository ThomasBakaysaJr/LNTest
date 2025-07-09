#NOTE!!! this file should be in the NodeManagerComms(directory in home address) directory that gets mounted to the CC docker container

#This script is used to relay commands to all CC nodes it has channels with, it connects to the REST server and passes the commands to the server.   

import subprocess
import json
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
DISCOVERY_RULE_DIVISOR = [19, 1231]

RETRY_INT = 5
SLEEP_INT = 0.5
RETRY_COUNT = 10

# how long in s between status updates
STATUS_TIMER = 1

THIS_NODE = None
# for global (is it sending right now?) type ask
SENDING = False

# The TLV record type used for standard text messages in keysend.
MESSAGE_TLV_TYPE = "34349334"

logging.basicConfig(filename=f'noise_log_{HOST_NAME}.log', level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(levelname)s - %(message)s")

# Cache for processed counters
processed_counters = set()

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

def get_all_messages():
    '''
    Retrieve all messages using the lightning-cli listinvoices command and sorting
    through the returns.
    '''
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
            return []
        
        invoices = json.loads(result.stdout)['invoices']

        if len(invoices) == 0:
            return []

        for invoice in invoices:
            if not invoice:
                logging.info(f'skipping.')
                continue
            label = invoice.get('label', '')
            status = invoice.get('status', '')

            if status == 'paid' and label.startswith('keysend'):
                msg = invoice.get('description', '')
                if '|' in msg:
                    msg = msg[len('keysend: '):]
                    messages.append(msg)
    except Exception as e:
        logging.error(f"Exception occurred while getting messages: {e}")
        return []
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

def send_message_to_connected_nodes(message):
    """
    Send a message to all nodes connected to this node via channels and display the message content.
    """
    global SENDING
    connected_nodes = get_connected_nodes()
    if not connected_nodes:
        logging.warning("No connected nodes found.")
        return
    tlv_json = json.dumps({MESSAGE_TLV_TYPE : encode_msg(message)})

    for target_node in connected_nodes:
        SENDING = True
        ln_checker.set_sending(target_node) # for the tracker
        sendmsg_command = ["lightning-cli", "--regtest", "keysend",
            f"destination={target_node}",
            f"amount_msat=1",
            f"extratlvs={tlv_json}"]

        ln_checker.check_funds()
        ln_checker.does_connection_exist(target_node)
        ln_checker.wait_node_activated(target_node)
        # time.sleep(2) # seeing if a delay fixes the first sending error
        #for attempt in range(RETRY_COUNT): # maybe the connections are closing because of the repeated tries
        result = subprocess.run(sendmsg_command, 
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                text=True,
                                check=True)
        if result.returncode == 0:
            logging.info(f'Message: "{message}" sent to {target_node} successfully.')
            continue
        else:
            logging.error(f"Error sending message to {target_node}: {result.stdout} || {result.stderr}")


def process_message(message):
    """
    Process a single message in the format <command>|<counter>.

    Args:
        message (str): The message to process.
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

        counter = int(counter)
        if counter in processed_counters:
            # logging.info(f"Ignoring duplicate message with counter {counter}: {message}")
            return
        # record command in file
        write_to_csv(counter, command)
        # Process the command and mark the counter as processed
        logging.info(f"Processing command: {command} with counter: {counter}")

        # This doesn't really do anything important atm
        # send_command_to_server(command)

        send_message_to_connected_nodes(message)

        # Mark the counter as processed
        processed_counters.add(counter)
    except Exception as e:
        logging.error(f"Error processing message: {e}")

def write_to_csv(counter, message):
        with open(MESSAGE_LOG_FILE, 'a', newline = '') as f:
            csvwriter = csv.writer(f)
            #'time_stamp', 'first 5 of the node id', 'CC container', 'Message Counter', 'Message'
            csvwriter.writerow([time.time(), THIS_NODE[:5], HOST_NAME, counter, message])

def main():
    """
    Main function to process and send commands to the REST server.
    """
    global SENDING
    # connect_to_server()  # Register this bot with the REST server on startup
    load_this_node()
    # Create csv file with headers
    with open(MESSAGE_LOG_FILE, 'w', newline = '') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow(['Time', 'Short_ID', 'CC container', 'Message Counter', 'Message'])
        # put initial message that this node is online (more for the tracker than anthing else.)
        csvwriter.writerow([time.time(), THIS_NODE[:5], HOST_NAME, 0, 'node online'])

    ln_checker.set_state('online')

    counter = 0
    max_counter = STATUS_TIMER // SLEEP_INT
    while True:
        # Retrieve all messages using lightning-cli
        messages = get_all_messages()
        if messages:
            for message in messages:
                process_message(message)  # Process each message with command and counter
        time.sleep(SLEEP_INT)
        if SENDING or counter > max_counter:
            ln_checker.set_state('online')
            counter = 0
            SENDING = False
        else:
            counter += 1

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


if __name__ == '__main__':
    main()
