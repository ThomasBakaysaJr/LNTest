#!/bin/bash
# create_CC_nodesV2.sh

set -e

BITCOIND_RPC="http://bitcoinuser:bitcoinpassword@127.0.0.1:8332"

# Base directories for lightning and Bitcoin
BASE_DIR="/home/thomas/Documents/LNBot_research_project/LNBot" # Change this to the directory accordingly to your setup
LIGHTNING_DIR="/home/thomas/.lightning"
BITCOIN_DIR="/home/thomas/.bitcoin"
PLUGIN_SCRIPT="$BASE_DIR/bootstrap.sh"
STARTUP_SCRIPT="$BASE_DIR/node_start.sh"

# Directories for NodeManagerComms and BotMasterComms
NODE_MANAGER_DIR="$BASE_DIR/NodeManagerComms"
BOT_MASTER_DIR="$BASE_DIR/BotMasterComms"

# Address list files for NodeManagerComms and BotMasterComms
NODE_MANAGER_ADDRESS_LIST="$NODE_MANAGER_DIR/CC_address_list.txt"
BOT_MASTER_ADDRESS_LIST="$BOT_MASTER_DIR/CC_address_list.txt"

# Total number of CC nodes, multiplied by 10 (so an incoming value of 1 will be 10 nodes)
# Default to 20 nodes
if [ -z "${1:-}" ]; then
    NUM_TOTAL_NODES=2
else
    NUM_TOTAL_NODES="$1"
fi
# Number of MAX_ACTIVE_NODES, so we create this many nodes at a time then wait a bit
# so that we can get the simulated nature of the OG algorithm
# default to 4 active nodes
if [ -z "${2:-}" ]; then
    NUM_ACTIVE_NODES=4
else
    NUM_ACTIVE_NODES="$2"
    NUM_NODES_CONCURRENT=$((NUM_ACTIVE_NODES * 2))
fi
echo "Creating a total of $NUM_TOTAL_NODES nodes. $NUM_ACTIVE_NODES at a time."
# Number of nodes to create at the same time (so as not to overload)

NUM_NODES=$((NUM_TOTAL_NODES * NUM_NODES_CONCURRENT))

# ln_checker file
LN_CHECKER_FILE="$BASE_DIR/ln_checker.py"

# Ensure bootstrap script is executable
chmod +x $PLUGIN_SCRIPT

# Function to fund a node's wallet
fund_node() {
    local node=$1

    # Get a new Bitcoin address from the node
    address=$(docker exec $node lightning-cli --regtest newaddr | jq -r '.bech32')

    # Send 10 BTC to the node's address           you should make sure correct user and password are used according to bitcoin.conf file!!!!!!!!!!
    txid=$(curl -s --user bitcoinuser:bitcoinpassword \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"$node\", \"method\": \"sendtoaddress\", \"params\": [\"$address\", 10]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

    echo "Sent 10 BTC to $node at address $address (txid: $txid)"
}

confirm_funds() {
# Mine blocks to confirm transactions
#           you should make sure correct user and password are used according to bitcoin.conf file!!!!!!!!!!
echo "Mining blocks to confirm transactions..."
mining_address=$(curl -s --user bitcoinuser:bitcoinpassword \
    --data-binary '{"jsonrpc": "1.0", "id": "mining", "method": "getnewaddress", "params": []}' \
    -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

curl -s --user bitcoinuser:bitcoinpassword \
    --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"mining\", \"method\": \"generatetoaddress\", \"params\": [6, \"$mining_address\"]}" \
    -H 'content-type: text/plain;' $BITCOIND_RPC
}

# Function to create a node
create_node() {
    NODE_NAME=$1
    NODE_LIGHTNING_DIR="$BASE_DIR/lightning-$NODE_NAME"
    NODE_PORT=$2
    NODE_GRPC_PORT=$3

    # Create a directory for the node
    mkdir -p $NODE_LIGHTNING_DIR

    # Run the Docker container with the specified name
    docker run -d --restart unless-stopped --network host --name $NODE_NAME \
        -e CONTAINER_NAME=$NODE_NAME \
        -v $NODE_LIGHTNING_DIR:/root/.lightning \
        -v $BITCOIN_DIR:/root/.bitcoin \
        -v $PLUGIN_SCRIPT:/root/bootstrap.sh \
        -v $NODE_MANAGER_DIR:/root/nodemanager \
        -v $LN_CHECKER_FILE:/root/nodemanager/ln_checker.py \
        -v $STARTUP_SCRIPT:/root/nodemanager/node_start.sh \
        --entrypoint /root/nodemanager/node_start.sh \
        elementsproject/lightningd:latest \
        --network=regtest \
        --addr=127.0.0.1:$NODE_PORT \
	    --grpc-port=$NODE_GRPC_PORT

    # Run the bootstrap script inside the container
    echo "Running BOOTSTRAP for $NODE_NAME..."
    docker exec $NODE_NAME bash /root/bootstrap.sh
    echo "Bootstrap for $NODE_NAME finished"

    # give the node some time to spin up before we try to fund it
    sleep 2
    write_address_to_file "$NODE_NAME"

    # we fund and confirm so that we can start connecting already.
    echo "Funding node $NODE_NAME"
    fund_node $NODE_NAME
    confirm_funds 

    # trying to see if we need to wait a bit before starting the manager script
    sleep 30

    #docker exec --workdir /root/nodemanager $NODE_NAME python3 CC_Manager.py &
    #docker exec --workdir /root/nodemanager $NODE_NAME python3 noiseManager_REST.py &

    echo "Finished setting up $NODE_NAME"
}

clear_address_file() {
    # Clear existing content in the address list files
    > $NODE_MANAGER_ADDRESS_LIST
    > $BOT_MASTER_ADDRESS_LIST
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
    NODE_ADDRESS="${NODE_ID}@${NODE_IP}:${NODE_PORT}"

    # Append the address to both address list files
    echo $NODE_ADDRESS >> $NODE_MANAGER_ADDRESS_LIST
    echo $NODE_ADDRESS >> $BOT_MASTER_ADDRESS_LIST

    echo "Address for $1 has been written to $NODE_MANAGER_ADDRESS_LIST and $BOT_MASTER_ADDRESS_LIST."
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

clear_address_file

COUNTER=1
while ((COUNTER <= $NUM_TOTAL_NODES)); do
    for (( i=1; i<=$NUM_NODES_CONCURRENT; i++ )); do
        NODE_NAME="CC$COUNTER"
        NODE_PORT=$((19848 + COUNTER))
        NODE_GRPC_PORT=$((10012 + COUNTER))
        echo "Creating node $NODE_NAME..."
        create_node $NODE_NAME $NODE_PORT $NODE_GRPC_PORT &
        # early breakout so we don't make more than the required number of cc nodes
        ((COUNTER++))
        if (( $COUNTER > $NUM_TOTAL_NODES )); then
            break
        fi
    done
    wait
done

    
# Wait for all background jobs to finish
wait

echo "Created $COUNTER CC nodes."
echo "All CC nodes created successfully."

# Wait a moment to ensure all nodes are up and running
sleep 5

# Write addresses to files
echo "Extracting addresses and writing to files..."