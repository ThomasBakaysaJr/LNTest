#!/bin/bash
# fund_wallets.sh

set -e

# RPC details for Bitcoin Core
source config.env

# Discover all CC and BM containers dynamically
CC_CONTAINERS=$(docker ps --filter "name=CC" --format "{{.Names}}")
BM_CONTAINER=$(docker ps --filter "name=BM" --format "{{.Names}}")

# Function to fund a node's wallet
fund_node() {
    local node=$1

    # Get a new Bitcoin address from the node
    address=$(docker exec $node lightning-cli --regtest newaddr | jq -r '.bech32')

    # Send 10 BTC to the node's address
    txid=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"$node\", \"method\": \"sendtoaddress\", \"params\": [\"$address\", $FUNDING_AMOUNT_BTC]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

    echo "Sent 10 BTC to $node at address $address (txid: $txid)"
}

# Fund all dynamically discovered CC nodes
if [ -n "$CC_CONTAINERS" ]; then
    for node in $CC_CONTAINERS; do
        fund_node $node
    done
else
    echo "No CC nodes found to fund."
fi

# Fund BM node separately if it exists
if [ -n "$BM_CONTAINER" ]; then
    fund_node $BM_CONTAINER
else
    echo "BM node not found."
fi

# Fund InnocentNode separately
if docker ps --filter "name=$INNOCENT_NODE" --format "{{.Names}}" | grep -q "$INNOCENT_NODE"; then
    echo "Funding InnocentNode..."
    address=$(docker exec $INNOCENT_NODE lightning-cli --regtest newaddr | jq -r '.bech32')
    txid=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"$INNOCENT_NODE\", \"method\": \"sendtoaddress\", \"params\": [\"$address\", $FUNDING_AMOUNT_BTC]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

    echo "Sent 10 BTC to InnocentNode at address $address (txid: $txid)"
else
    echo "InnocentNode not found."
fi

# Mine blocks to confirm transactions
echo "Mining blocks to confirm transactions..."
mining_address=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary '{"jsonrpc": "1.0", "id": "mining", "method": "getnewaddress", "params": []}' \
    -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')

curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"mining\", \"method\": \"generatetoaddress\", \"params\": [$CONFIRMATION_BLOCKS, \"$mining_address\"]}" \
    -H 'content-type: text/plain;' $BITCOIND_RPC

echo "Funding complete and transactions confirmed."
