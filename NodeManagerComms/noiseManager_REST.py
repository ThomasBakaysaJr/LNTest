#NOTE!!! this file should be in the NodeManagerComms(directory in home address) directory that gets mounted to the CC docker container

#This script is used to relay commands to all CC nodes it has channels with, it connects to the REST server and passes the commands to the server.   

import subprocess
import json
import requests
import time
import os
import csv
import logging

# Address for this bot to register with REST_server
BOT_ADDRESS = '127.0.0.9'  # Adjust if a different address is required
SERVER_URL = 'http://127.0.0.1:8000'

HOST_NAME = os.getenv("CONTAINER_NAME")
MESSAGE_LOG_FILE = f'cc_messageLog_{HOST_NAME}.csv'

logging.basicConfig(filename=f'cc_noise_logs_{HOST_NAME}.log', level=logging.INFO, format=f"{HOST_NAME} %(asctime)s - %(message)s")

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
    sleep_int = 5
    attempt_max = 10

    for attempt in range(10):
        # lets sleep first yeah?
        time.sleep(sleep_int)
        
        try:
            connect_response = requests.post(f'{SERVER_URL}/v1/connect/', json=connect_payload)
            if connect_response.status_code == 200:
                logging.info("Bot connected to the server successfully.")
                break
            else:
                logging.info(f"Server is not running yet, retrying in {sleep_int} seconds.")
        except Exception as e:
            logging.info(f"Error connecting to the server: {e}")
        
        if attempt == attempt_max - 1:
            logging.info(f"Cannot connect to server after trying {attempt_max} times, CATASTROPHIC ERROR")


def send_command_to_server(command):
    """
    Send a command to REST_server to be executed on all connected bots.
    
    Args:
        command (str): The command to execute.
    """
    control_payload = {'command': command}
    try:
        control_response = requests.post(f'{SERVER_URL}/v1/control/', json=control_payload)
        if control_response.status_code == 200:
            response_data = control_response.json()
            logging.info("Command sent to server successfully.")
            logging.info("Output from server:")
            logging.info(f"{response_data.get('responses')}")
        else:
            logging.info(f"Server responded with error: {control_response.status_code} - {control_response.text}")
    except requests.exceptions.Timeout:
        logging.info(f"Request timed out while sending command '{command}' to server.")
    except Exception as e:
        logging.info(f"Error sending request to server: {e}")

def get_all_messages():
    """
    Retrieve all messages using lightning-cli and format them for processing.

    Returns:
        list: A list of message bodies retrieved from lightning-cli.
    """
    try:
        result = subprocess.run(
            ['lightning-cli', '--regtest', 'allmsgs'],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            logging.info(f"Error executing lightning-cli: {result.stderr}")
            return []

        raw_messages = json.loads(result.stdout)
        message_keys = [key for key in raw_messages if key.startswith('message') and 'body' in raw_messages[key]]
        message_keys.sort(key=lambda x: int(x[len('message'):]))

        messages = [raw_messages[key]['body'] for key in message_keys]
        logging.info(f"Retrieved message bodies: {messages}")
        return messages
    except Exception as e:
        logging.info(f"Exception occurred while getting messages: {e}")
        return []


def get_connected_nodes():
    """
    Retrieve all nodes that have a channel with this node and satisfy the discovery rule.
    """
    # Step 1: Get the node's own ID
    getinfo_output = subprocess.run(
        ['lightning-cli', '--regtest', 'getinfo'],
        capture_output=True,
        text=True
    )
    if getinfo_output.returncode != 0:
        logging.info(f"Error retrieving own node ID: {getinfo_output.stderr}")
        return []

    try:
        own_node_id = json.loads(getinfo_output.stdout).get("id")
        if not own_node_id:
            logging.info("Failed to retrieve own node ID from getinfo.")
            return []
    except json.JSONDecodeError:
        logging.info("Error parsing getinfo output.")
        return []

    # Step 2: Get the list of channels
    listchannels_output = subprocess.run(
        ['lightning-cli', '--regtest', 'listchannels'],
        capture_output=True,
        text=True
    )
    if listchannels_output.returncode != 0:
        logging.info(f"Error retrieving channels: {listchannels_output.stderr}")
        return []

    try:
        connected_nodes = set()
        channels = json.loads(listchannels_output.stdout).get("channels", [])
        
        logging.info(f"Total channels retrieved: {len(channels)}")
        
        for channel in channels:
            # Extract capacity in satoshis
            capacity = int(channel.get("amount_msat", 0)) // 1000  # Convert msat to satoshis
            logging.info(f"Channel capacity: {capacity}, Source: {channel['source']}, Destination: {channel['destination']}")

            # Check if the channel involves this node
            if channel["source"] == own_node_id:
                connected_nodes.add(channel["destination"])
                logging.info(f"Added node {channel['destination']} as it satisfies the discovery rule.")
            elif channel["destination"] == own_node_id:
                connected_nodes.add(channel["source"])
                logging.info(f"Added node {channel['source']} as it satisfies the discovery rule.")

        logging.info(f"Connected nodes satisfying discovery rule: {list(connected_nodes)}")
        return list(connected_nodes)

    except json.JSONDecodeError:
        logging.info("Error parsing listchannels output.")
        return []

def send_message_to_connected_nodes(message):
    """
    Send a message to all nodes connected to this node via channels and display the message content.
    """
    connected_nodes = get_connected_nodes()
    if not connected_nodes:
        logging.info("No connected nodes found.")
        return

    for target_node in connected_nodes:
        sendmsg_command = [
            'lightning-cli', '--regtest', 'sendmsg',
            target_node,
            message,
            '5'  # Amount in millisatoshis
        ]
        result = subprocess.run(sendmsg_command, capture_output=True, text=True)
        if result.returncode == 0:
            logging.info(f'Message: "{message}" sent to {target_node} successfully.')
        else:
            logging.info(f"Error sending message to {target_node}: {result.stderr}")

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
            logging.info(f"Ignoring duplicate message with counter {counter}: {message}")
            return

        # Process the command and mark the counter as processed
        logging.info(f"Processing command: {command} with counter: {counter}")
        # record command in file
        write_to_csv(counter, command)
        send_command_to_server(command)  # Send the command to the REST server

        # Broadcast the message to connected nodes
        send_message_to_connected_nodes(message)

        # Mark the counter as processed
        processed_counters.add(counter)
    except Exception as e:
        logging.info(f"Error processing message: {e}")

def write_to_csv(counter, message):
        with open(MESSAGE_LOG_FILE, 'a', newline = '') as f:
            csvwriter = csv.writer(f)
            #'CC container', 'Message Counter', 'Message'
            csvwriter.writerow([time.time(), HOST_NAME, counter, message])

def main():
    """
    Main function to process and send commands to the REST server.
    """
    connect_to_server()  # Register this bot with the REST server on startup
    # Create csv file with headers
    with open(MESSAGE_LOG_FILE, 'w', newline = '') as f:
        csvwriter = csv.writer(f)
        csvwriter.writerow(['Time','CC container', 'Message Counter', 'Message'])

    while True:
        # Retrieve all messages using lightning-cli
        messages = get_all_messages()
        if messages:
            for message in messages:
                process_message(message)  # Process each message with command and counter
        else:
            logging.info("No messages retrieved.")
        time.sleep(5)


if __name__ == '__main__':
    main()
