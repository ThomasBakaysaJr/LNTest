#!/bin/bash
# create_CC_nodesV2.sh

set -e

source config.env

STARTUP_SCRIPT="$LNBOT_DIR/node_start.sh"

# Directories
NODE_MANAGER_DIR="$LNBOT_DIR/NodeManagerComms"
BOT_MASTER_DIR="$LNBOT_DIR/BotMasterComms"
NODE_STATE_DIR="$LNBOT_DIR/testState"

# Address list files for NodeManagerComms and BotMasterComms
NODE_MANAGER_ADDRESS_LIST="$NODE_MANAGER_DIR/CC_address_list.txt"
BOT_MASTER_ADDRESS_LIST="$BOT_MASTER_DIR/CC_address_list.txt"

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

# ln_checker file
LN_CHECKER_FILE="$LNBOT_DIR/ln_checker.py"


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
    address=$(docker exec $node lightning-cli --regtest newaddr | jq -r '.bech32')

    # Send 10 BTC to the node's address           you should make sure correct user and password are used according to bitcoin.conf file!!!!!!!!!!
    txid=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"$node\", \"method\": \"sendtoaddress\", \"params\": [\"$address\", 10]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

    echo "Sent 10 BTC to $node at address $address (txid: $txid)"
}

confirm_funds() {
# Mine blocks to confirm transactions
#           you should make sure correct user and password are used according to bitcoin.conf file!!!!!!!!!!
echo "Mining blocks to confirm transactions..."
mining_address=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary '{"jsonrpc": "1.0", "id": "mining", "method": "getnewaddress", "params": []}' \
    -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"mining\", \"method\": \"generatetoaddress\", \"params\": [6, \"$mining_address\"]}" \
    -H 'content-type: text/plain;' $BITCOIND_RPC
}

# Function to create a node
create_node() {
    NODE_NAME=$1
    NODE_LIGHTNING_DIR="$LNBOT_DIR/lightning-$NODE_NAME"
    NODE_PORT=$2
    NODE_GRPC_PORT=$3
    NUM_ACTIVE_NODES=$4

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --restart unless-stopped --network host --ipc=host --name $NODE_NAME \
        -e CONTAINER_NAME=$NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $NODE_MANAGER_DIR:/root/nodemanager \
        -v $NODE_STATE_DIR:/root/nodemanager/testState \
        -v $LN_CHECKER_FILE:/root/nodemanager/ln_checker.py \
        -v $STARTUP_SCRIPT:/root/nodemanager/node_start.sh \
        --entrypoint /root/nodemanager/node_start.sh \
        $LNTEST_VERSION \
        $NUM_ACTIVE_NODES \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT \
	    --grpc-port=$NODE_GRPC_PORT

    # wait for the lightning daemon to be up and running
    wait_for_node_ready "$NODE_NAME"

    write_address_to_file "$NODE_NAME"

    # we fund and confirm so that we can start connecting already.
    echo "Funding node $NODE_NAME"
    fund_node $NODE_NAME
    confirm_funds 

    #docker exec --workdir /root/nodemanager $NODE_NAME python3 CC_Manager.py &
    #docker exec --workdir /root/nodemanager $NODE_NAME python3 noiseManager_REST.py &

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
    NODE_PORT=$(echo $NODE_INFO | jq -r '.binding[0].port')
    NODE_IP=$(echo $NODE_INFO | jq -r '.binding[0].address')
    NODE_ADDRESS="${NODE_NAME} ${NODE_ID}@${NODE_IP}:${NODE_PORT}"

    # Append the address to both address list files
    echo $NODE_ADDRESS >> $NODE_MANAGER_ADDRESS_LIST
    echo $NODE_ADDRESS >> $BOT_MASTER_ADDRESS_LIST

    # echo "Address for $1 has been written to $NODE_MANAGER_ADDRESS_LIST and $BOT_MASTER_ADDRESS_LIST."
}

# Create CC nodes concurrently
# for x in $(seq 1 $NUM_TOTAL_NODES); do
#     mult=$((((x - 1)) * NUM_NODES_CONCURRENT))
#     for i in $(seq 1 $NUM_NODES_CONCURRENT); do
#         count=$((mult + i))
#         NODE_NAME="CC$count"
#         NODE_PORT=$((19848 + count))
#         NODE_GRPC_PORT=$((10012 + count))
#         echo "Creating node $NODE_NAME..."
#         create_node $NODE_NAME $NODE_PORT $NODE_GRPC_PORT &
#     done
#     wait
# done
# echo "Created $COUNTER CC nodes."

NODE_NAME="CC$suffix"
NODE_PORT=$((19848 + suffix))
NODE_GRPC_PORT=$((10012 + suffix))
echo "Creating node CC$suffix with $active_nodes active nodes."
create_node $NODE_NAME $NODE_PORT $NODE_GRPC_PORT $active_nodes &
# early breakout so we don't make more than the required number of cc nodes
    
