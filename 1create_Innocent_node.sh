#!/bin/bash
# create_innocent_node.sh

set -e

# Base directories for lightning and Bitcoin 
source config.env

# Default BITCOIN_HOST if not set
BITCOIN_HOST=${BITCOIN_HOST:-"127.0.0.1"}

# Function to create the Innocent Node
create_innocent_node() {
    NODE_NAME="InnocentNode"
    NODE_LIGHTNING_DIR="$NODE_DATA_DIR/lightning-$NODE_NAME"
    NODE_PORT=$INNOCENT_PORT

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container without NODE_MANAGER_DIR and without bootstrap
    echo "LNTEST_VERSION is: $LNTEST_VERSION"
    docker run -d --restart unless-stopped --network host --name $NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $INNO_MANAGER_DIR:/root/ \
        $LNTEST_VERSION \
        --network=$NETWORK_TYPE \
        --addr=$BITCOIN_HOST:$NODE_PORT \
	    --grpc-port=$INNOCENT_GRPC_PORT
}

# Function to extract address and ID and save to files
extract_address_and_id() {
    NODE_NAME="InnocentNode"

    # Extract the information using the Docker exec command
    NODE_INFO=$(sudo docker exec -it $NODE_NAME lightning-cli --regtest getinfo)

    # Parse the address and ID from the JSON output
    NODE_ID=$(echo $NODE_INFO | jq -r '.id')
    # Use explicitly configured host and port instead of parsing dynamic bindings
    NODE_ADDRESS="${NODE_ID}@${BITCOIN_HOST}:${INNOCENT_PORT}"

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
