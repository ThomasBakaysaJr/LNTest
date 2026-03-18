#!/bin/bash
# create_cc_node.sh

set -e

# Resolve LNTest root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

source "$LNTEST_ROOT/config.env"

# Default BITCOIN_HOST if not set
BITCOIN_HOST=${BITCOIN_HOST:-"127.0.0.1"}

# The counter `suffix` for the CC node
# Default to 1
if [ -z "${1:-}" ]; then
    suffix=1
else
    suffix="$1"
fi
# Make sure we have a value for number of active nodes
# default to 4
if [ -z "${2:-}" ]; then
    active_nodes=4
else
    active_nodes="$2"
fi

# Optional: skip cc_manager for chain topology mode
skip_cc_manager="${3:-0}"

wait_for_node_ready() {
    local node_name=$1
    echo "Waiting for $node_name to initialize..."
    
    # Try getting info up to 30 times (30 seconds max)
    for i in {1..30}; do
        # We check if the command succeeds (exit code 0)
        if docker exec $node_name lightning-cli --regtest getinfo > /dev/null 2>&1; then
            echo "$node_name is ready."
            return 0
        fi
        sleep 1
    done
    
    echo "ERROR: $node_name failed to start in time."
    return 1
}

# Function to fund a node's wallet
fund_node() {
    local node=$1

    # Get a new Bitcoin address from the node
    address=$(docker exec $node lightning-cli --regtest newaddr bech32 | jq -r '.bech32')

    # Send 10 BTC to the node's address
    response=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"$node\", \"method\": \"sendtoaddress\", \"params\": [\"$address\", $FUNDING_AMOUNT_BTC]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC)
    txid=$(echo "$response" | jq -r '.result')
    error=$(echo "$response" | jq -r '.error')

    if [ "$txid" = "null" ] || [ -z "$txid" ]; then
        echo "ERROR: Failed to fund $node. Bitcoin Core error: $error"
        echo "       Wallet may be out of funds. Restart bitcoind or mine more blocks."
        exit 1
    fi

    echo "Sent $FUNDING_AMOUNT_BTC BTC to $node at address $address (txid: $txid)"
}

confirm_funds() {
# Mine blocks to confirm transactions
echo "Mining blocks to confirm transactions..."
mining_address=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary '{"jsonrpc": "1.0", "id": "mining", "method": "getnewaddress", "params": []}' \
    -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"mining\", \"method\": \"generatetoaddress\", \"params\": [$CONFIRMATION_BLOCKS, \"$mining_address\"]}" \
    -H 'content-type: text/plain;' $BITCOIND_RPC
}

# Function to create a node
create_node() {
    NODE_NAME=$1
    NODE_LIGHTNING_DIR="$NODE_DATA_DIR/lightning-$NODE_NAME"
    NODE_PORT=$2
    NODE_GRPC_PORT=$3
    NUM_ACTIVE_NODES=$4
    
    # Default internal paths if not set in config.env
    NODE_CONTAINER_DIR=${NODE_CONTAINER_DIR:-"/root/nodemanager"}
    LIGHTNING_CONTAINER_DIR="/root/.lightning"

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --restart unless-stopped --network host --ipc=host --name $NODE_NAME \
        -e CONTAINER_NAME=$NODE_NAME \
        -e SKIP_CC_MANAGER=$skip_cc_manager \
        -e NODE_CONTAINER_DIR=$NODE_CONTAINER_DIR \
        -e LIGHTNING_HOME=$LIGHTNING_CONTAINER_DIR \
        -e LIGHTNING_RPC_PATH="$LIGHTNING_CONTAINER_DIR/regtest/lightning-rpc" \
        -e NODE_ADDRESS_FILE=$NODE_ADDRESS_FILE \
        -e NODE_ID_FILE=$NODE_ID_FILE \
        -e NODE_MANAGER_ADDRESS_LIST=$NODE_MANAGER_ADDRESS_LIST \
        -v $NODE_LIGHTNING_DIR:$LIGHTNING_CONTAINER_DIR \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $NODE_MANAGER_DIR:$NODE_CONTAINER_DIR \
        -v $NODE_STATE_DIR:$NODE_CONTAINER_DIR/testState \
        -v $LN_CHECKER_FILE:$NODE_CONTAINER_DIR/ln_checker.py \
        -v $STARTUP_SCRIPT:$NODE_CONTAINER_DIR/node_start.sh \
        --entrypoint $NODE_CONTAINER_DIR/node_start.sh \
        $LNTEST_VERSION \
        --network=$NETWORK_TYPE \
        --addr=$BITCOIN_HOST:$NODE_PORT \
	    --grpc-port=$NODE_GRPC_PORT \
        --developer \
        --dev-bitcoind-poll=1 \
        --dev-fast-gossip

    # wait for the lightning daemon to be up and running
    wait_for_node_ready "$NODE_NAME"

    write_address_to_file "$NODE_NAME"

    # we fund and confirm so that we can start connecting already.
    echo "Funding node $NODE_NAME"
    fund_node $NODE_NAME
    confirm_funds 
    echo "Finished setting up $NODE_NAME"
}


# Function to extract addresses and write to files
write_address_to_file() {
    # Write this CC2 server to the address file
    NODE_NAME="$1"

    # Extract the information using the Docker exec command
    NODE_INFO=$(sudo docker exec $NODE_NAME lightning-cli --regtest getinfo)

    # Parse the address from the JSON output
    NODE_ID=$(echo $NODE_INFO | jq -r '.id')
    # Use explicitly configured host and port logic
    # Re-calculating port based on suffix which is available globally in the script scope
    
    CURRENT_PORT=$(($CC_PORT_BASE + suffix))
    NODE_ADDRESS="${NODE_NAME} ${NODE_ID}@${BITCOIN_HOST}:${CURRENT_PORT}"

    # Append the address to both address list files
    echo $NODE_ADDRESS >> $NODE_MANAGER_ADDRESS_LIST
    echo $NODE_ADDRESS >> $BOT_MASTER_ADDRESS_LIST
}

NODE_NAME="CC$suffix"
NODE_PORT=$(($CC_PORT_BASE + suffix))
NODE_GRPC_PORT=$(($CC_GRPC_PORT_BASE + suffix))
echo "Creating node CC$suffix with $active_nodes active nodes."
create_node $NODE_NAME $NODE_PORT $NODE_GRPC_PORT $active_nodes
    
