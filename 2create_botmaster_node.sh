#!/bin/bash
# create_botmaster_node.sh

set -e

# Base directories for lightning and Bitcoin
BASE_DIR="/home/c499"    #Change this to the directory accordingly to your setup
LIGHTNING_DIR="$BASE_DIR/lightning"
BITCOIN_DIR="$BASE_DIR/.bitcoin"
PLUGIN_SCRIPT="$BASE_DIR/lightning/bootstrap.sh"

# Directory for BotMaster scripts
BOT_MASTER_DIR="$BASE_DIR/BotMasterComms"
BOT_MASTER_CONTAINER_DIR="/root/botmaster"

# Ensure bootstrap script is executable
chmod +x $PLUGIN_SCRIPT

# Function to create the BotMaster node
create_botmaster_node() {
    NODE_NAME="BM"
    NODE_PORT=19848
    NODE_LIGHTNING_DIR="$BASE_DIR/lightning-$NODE_NAME"

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --network host --name $NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $PLUGIN_SCRIPT:/root/bootstrap.sh \
        -v $BOT_MASTER_DIR:$BOT_MASTER_CONTAINER_DIR \
        elementsproject/lightningd \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT

    # Run the bootstrap script inside the container
    echo "Setting up plugin for $NODE_NAME..."
    docker exec $NODE_NAME bash /root/bootstrap.sh
}

# Create the BotMaster node
echo "Creating BotMaster node..."
create_botmaster_node
