#!/bin/bash
# create_CC_nodesV2.sh

set -e

# Base directories for lightning and Bitcoin
BASE_DIR="/home/c499" # Change this to the directory accordingly to your setup
LIGHTNING_DIR="$BASE_DIR/lightning"
BITCOIN_DIR="$BASE_DIR/.bitcoin"
PLUGIN_SCRIPT="$BASE_DIR/lightning/bootstrap.sh"

# Directories for NodeManagerComms and BotMasterComms
NODE_MANAGER_DIR="$BASE_DIR/NodeManagerComms"
BOT_MASTER_DIR="$BASE_DIR/BotMasterComms"

# Address list files for NodeManagerComms and BotMasterComms
NODE_MANAGER_ADDRESS_LIST="$NODE_MANAGER_DIR/CC_address_list.txt"
BOT_MASTER_ADDRESS_LIST="$BOT_MASTER_DIR/CC_address_list.txt"

# Ensure bootstrap script is executable
chmod +x $PLUGIN_SCRIPT

# Function to create a node
create_node() {
    NODE_NAME=$1
    NODE_LIGHTNING_DIR="$BASE_DIR/lightning-$NODE_NAME"
    NODE_PORT=$2

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --network host --name $NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $PLUGIN_SCRIPT:/root/bootstrap.sh \
        -v $NODE_MANAGER_DIR:/root/nodemanager \
        elementsproject/lightningd \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT

    # Run the bootstrap script inside the container
    echo "Setting up plugin for $NODE_NAME..."
    docker exec $NODE_NAME bash /root/bootstrap.sh
}

# Function to extract addresses and write to files
write_addresses_to_file() {
    # Clear existing content in the address list files
    > $NODE_MANAGER_ADDRESS_LIST
    > $BOT_MASTER_ADDRESS_LIST

    # Iterate through nodes to extract addresses
    for i in $(seq 1 $NUM_NODES); do
        NODE_NAME="CC$i"

        # Extract the information using the Docker exec command
        NODE_INFO=$(sudo docker exec -it $NODE_NAME lightning-cli --regtest getinfo)

        # Parse the address from the JSON output
        NODE_ID=$(echo $NODE_INFO | jq -r '.id')
        NODE_PORT=$(echo $NODE_INFO | jq -r '.binding[0].port')
        NODE_IP=$(echo $NODE_INFO | jq -r '.binding[0].address')
        NODE_ADDRESS="${NODE_ID}@${NODE_IP}:${NODE_PORT}"

        # Append the address to both address list files
        echo $NODE_ADDRESS >> $NODE_MANAGER_ADDRESS_LIST
        echo $NODE_ADDRESS >> $BOT_MASTER_ADDRESS_LIST
    done

    echo "Addresses have been written to $NODE_MANAGER_ADDRESS_LIST and $BOT_MASTER_ADDRESS_LIST."
}

# Number of nodes to create
NUM_NODES=4

# Create CC nodes concurrently
for i in $(seq 1 $NUM_NODES); do
    NODE_NAME="CC$i"
    NODE_PORT=$((19848 + i))
    echo "Creating node $NODE_NAME..."
    create_node $NODE_NAME $NODE_PORT &
done

# Wait for all background jobs to finish
wait

echo "All CC nodes created successfully."

# Wait a moment to ensure all nodes are up and running
sleep 5

# Write addresses to files
echo "Extracting addresses and writing to files..."
write_addresses_to_file
