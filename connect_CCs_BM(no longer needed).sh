#!/bin/bash
# connect_CCs_BM.sh

set -e

# Base port for nodes
BASE_PORT=9735

# Discover all CC containers dynamically
CC_CONTAINERS=$(docker ps --filter "name=CC" --format "{{.Names}}")
BM_CONTAINER=$(docker ps --filter "name=BM" --format "{{.Names}}")

if [ -z "$BM_CONTAINER" ]; then
    echo "BotMaster (BM) container not found. Exiting."
    exit 1
fi

if [ -z "$CC_CONTAINERS" ]; then
    echo "No CC containers found. Exiting."
    exit 1
fi

# Function to get node info
get_node_info() {
    local node=$1
    docker exec $node lightning-cli --regtest getinfo
}

# Connect all nodes in a full mesh
for from_node in $CC_CONTAINERS $BM_CONTAINER; do
    for to_node in $CC_CONTAINERS; do
        if [ "$from_node" != "$to_node" ]; then
            # Get the public key and address of the "to" node
            node_info=$(get_node_info $to_node)
            pub_key=$(echo $node_info | jq -r '.id')
            address=$(echo $node_info | jq -r '.binding[0].address')
            port=$(echo $node_info | jq -r '.binding[0].port')

            # Ensure valid address and port
            if [[ -z "$address" || -z "$port" ]]; then
                echo "Skipping connection for $to_node (no valid address or port)."
                continue
            fi

            echo "Connecting $from_node to $pub_key@$address:$port"
            docker exec $from_node lightning-cli --regtest connect $pub_key $address $port || echo "Failed to connect $from_node to $to_node"
        fi
    done
done

echo "All nodes connected in a full mesh."
