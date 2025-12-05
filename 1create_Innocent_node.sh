#!/bin/bash
# create_innocent_node.sh

set -e

# Base directories for lightning and Bitcoin 
source config.env

# Directories for NodeManagerComms and BotMasterComms
NODE_MANAGER_DIR="$LNBOT_DIR/NodeManagerComms"
BOT_MASTER_DIR="$LNBOT_DIR/BotMasterComms"
INNO_MANAGER_DIR="$LNBOT_DIR/InnocentManager"

# Files to store the address and ID
NODE_ADDRESS_FILE="$NODE_MANAGER_DIR/innocentAddress.txt"
NODE_ID_FILE="$NODE_MANAGER_DIR/innocentID.txt"
BOT_ADDRESS_FILE="$BOT_MASTER_DIR/innocentAddress.txt"
BOT_ID_FILE="$BOT_MASTER_DIR/innocentID.txt"

# Function to create the Innocent Node
create_innocent_node() {
    NODE_NAME="InnocentNode"
    NODE_LIGHTNING_DIR="$LNBOT_DIR/lightning-$NODE_NAME"
    NODE_PORT=19847

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container without NODE_MANAGER_DIR and without bootstrap
    docker run -d --restart unless-stopped --network host --name $NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $INNO_MANAGER_DIR:/root/ \
        $LNTEST_VERSION \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT \
	    --grpc-port=10010
    # echo "Starting REST server on innocent node"
    # docker exec $NODE_NAME python3 /root/REST_server.py &
}

# Function to extract address and ID and save to files
extract_address_and_id() {
    NODE_NAME="InnocentNode"

    # Extract the information using the Docker exec command
    NODE_INFO=$(sudo docker exec -it $NODE_NAME lightning-cli --regtest getinfo)

    # Parse the address and ID from the JSON output
    NODE_ID=$(echo $NODE_INFO | jq -r '.id')
    NODE_PORT=$(echo $NODE_INFO | jq -r '.binding[0].port')
    NODE_IP=$(echo $NODE_INFO | jq -r '.binding[0].address')
    NODE_ADDRESS="${NODE_ID}@${NODE_IP}:${NODE_PORT}"

    # Clear the NodeManagerComms files before writing
    > $NODE_ADDRESS_FILE
    > $NODE_ID_FILE

    # Write the address and ID to NodeManagerComms files
    echo $NODE_ADDRESS > $NODE_ADDRESS_FILE
    echo $NODE_ID > $NODE_ID_FILE

    echo "Address and ID have been written to $NODE_ADDRESS_FILE and $NODE_ID_FILE."

    # Clear the BotMasterComms files before writing
    > $BOT_ADDRESS_FILE
    > $BOT_ID_FILE

    # Write the address and ID to BotMasterComms files
    echo $NODE_ADDRESS > $BOT_ADDRESS_FILE
    echo $NODE_ID > $BOT_ID_FILE

    echo "Address and ID have been written to $BOT_ADDRESS_FILE and $BOT_ID_FILE."
}

# Create Innocent node
echo "Creating Innocent node..."
create_innocent_node

# Wait a moment to ensure the container is up and running
sleep 5

# Extract and save the address and ID
echo "Extracting address and ID..."
extract_address_and_id
