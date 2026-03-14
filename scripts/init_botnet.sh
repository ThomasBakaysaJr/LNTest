#!/bin/bash
# init_botnet.sh

set -eu

if [ -z "${1:-}" ]; then
    TOTAL_NODES=2
else
    TOTAL_NODES="$1"
fi

if [ -z "${2:-}" ]; then
    ACTIVE_NODES=2
else
    ACTIVE_NODES="$2"
fi

# Resolve LNTest root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

# We start fresh
"$SCRIPT_DIR/cleanup.sh" all

# Base directories for lightning and Bitcoin
source "$LNTEST_ROOT/config.env"

# Default BITCOIN_HOST if not set
BITCOIN_HOST=${BITCOIN_HOST:-"127.0.0.1"}

###############################################################################
# Create Innocent Node
###############################################################################

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
	    --grpc-port=$INNOCENT_GRPC_PORT \
        --developer \
        --dev-bitcoind-poll=1 \
        --dev-fast-gossip
}

extract_address_and_id() {
    NODE_NAME="InnocentNode"

    # Extract the information using the Docker exec command
    NODE_INFO=$(sudo docker exec $NODE_NAME lightning-cli --regtest getinfo)

    # Parse the address and ID from the JSON output
    NODE_ID=$(echo $NODE_INFO | jq -r '.id')
    # Use explicitly configured host and port instead of parsing dynamic bindings
    NODE_ADDRESS="${NODE_ID}@${BITCOIN_HOST}:${INNOCENT_PORT}"

    # Clear the cc_node files before writing
    > $NODE_ADDRESS_FILE
    > $NODE_ID_FILE

    # Write the address and ID to cc_node files
    echo $NODE_ADDRESS > $NODE_ADDRESS_FILE
    echo $NODE_ID > $NODE_ID_FILE

    echo "Address and ID have been written to $NODE_ADDRESS_FILE and $NODE_ID_FILE."

    # Clear the botmaster files before writing
    > $BOT_ADDRESS_FILE
    > $BOT_ID_FILE

    # Write the address and ID to botmaster files
    echo $NODE_ADDRESS > $BOT_ADDRESS_FILE
    echo $NODE_ID > $BOT_ID_FILE

    echo "Address and ID have been written to $BOT_ADDRESS_FILE and $BOT_ID_FILE."
}

echo "Creating Innocent node..."
create_innocent_node

# Wait a moment to ensure the container is up and running
sleep 5

# Extract and save the address and ID
echo "Extracting address and ID..."
extract_address_and_id

###############################################################################
# Create BotMaster Node
###############################################################################

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
	    --grpc-port=$BOTMASTER_GRPC_PORT \
        --developer \
        --dev-bitcoind-poll=1 \
        --dev-fast-gossip
}

echo "Creating BotMaster node..."
create_botmaster_node
echo "Botmaster created"
