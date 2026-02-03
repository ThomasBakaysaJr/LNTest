#!/bin/bash
# create_botmaster_node.sh

set -e

# Base directories for lightning and Bitcoin
source config.env

# Default BITCOIN_HOST if not set
BITCOIN_HOST=${BITCOIN_HOST:-"127.0.0.1"}

# Function to create the BotMaster node
create_botmaster_node() {
    NODE_PORT=$BOTMASTER_PORT
    NODE_LIGHTNING_DIR="$NODE_DATA_DIR/lightning-$BOTMASTER_NODE"
    LIGHTNING_CONTAINER_DIR="/root/.lightning"

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --restart unless-stopped --network host --name $BOTMASTER_NODE \
        -e LIGHTNING_RPC_PATH="$LIGHTNING_CONTAINER_DIR/regtest/lightning-rpc" \
        -e NODE_ADDRESS_FILE=$BOT_ADDRESS_FILE \
        -e NODE_ID_FILE=$BOT_ID_FILE \
        -e NODE_MANAGER_ADDRESS_LIST=$BOT_MASTER_ADDRESS_LIST \
        -v $NODE_LIGHTNING_DIR:$LIGHTNING_CONTAINER_DIR \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $BOT_MASTER_DIR:$BOT_MASTER_CONTAINER_DIR \
        -v $LN_CHECKER_FILE:$BOT_MASTER_CONTAINER_DIR/ln_checker.py \
        $LNTEST_VERSION \
        --network=$NETWORK_TYPE \
        --addr=$BITCOIN_HOST:$NODE_PORT \
	    --grpc-port=$BOTMASTER_GRPC_PORT
}

# Create the BotMaster node
echo "Creating BotMaster node..."
create_botmaster_node
echo "Botmaster created"
