#!/bin/bash
# create_botmaster_node.sh

set -e

# Base directories for lightning and Bitcoin
source config.env

# Directory for BotMaster scripts
BOT_MASTER_DIR="$LNBOT_DIR/BotMasterComms"
BOT_MASTER_CONTAINER_DIR="/root/botmaster"

# ln_checker file
LN_CHECKER_FILE="$LNBOT_DIR/ln_checker.py"


# Function to create the BotMaster node
create_botmaster_node() {
    NODE_NAME="BM"
    NODE_PORT=19848
    NODE_LIGHTNING_DIR="$LNBOT_DIR/lightning-$NODE_NAME"

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --restart unless-stopped --network host --name $NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $BOT_MASTER_DIR:$BOT_MASTER_CONTAINER_DIR \
        -v $LN_CHECKER_FILE:$BOT_MASTER_CONTAINER_DIR/ln_checker.py \
        $LNTEST_VERSION \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT \
	    --grpc-port=10011
}

# Create the BotMaster node
echo "Creating BotMaster node..."
create_botmaster_node
echo "Botmaster created"
