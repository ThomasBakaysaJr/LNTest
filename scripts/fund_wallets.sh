#!/bin/bash
# fund_wallets.sh

set -e

# Resolve LNTest root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

# RPC details for Bitcoin Core
source "$LNTEST_ROOT/config.env"

# Discover all CC and BM containers dynamically
CC_CONTAINERS=$(docker ps --filter "name=CC" --format "{{.Names}}")
BM_CONTAINER=$(docker ps --filter "name=BM" --format "{{.Names}}")

# Pre-mine mature UTXOs ONLY when the wallet can't already cover funding every
# node. Within a single run the regtest chain persists across iterations, so
# after the first iteration the wallet already holds far more than enough --
# skip the 200-block mine (~25-30s) in that case.
NODE_COUNT=$(echo "$CC_CONTAINERS" | grep -c .)
NODE_COUNT=$((NODE_COUNT + 2))   # + BM + InnocentNode
REQUIRED=$(awk "BEGIN{print $NODE_COUNT * $FUNDING_AMOUNT_BTC * 2}")   # 2x headroom for retries
BALANCE=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
    --data-binary '{"jsonrpc":"1.0","id":"bal","method":"getbalance","params":[]}' \
    -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')
BALANCE=${BALANCE:-0}

if awk "BEGIN{exit !($BALANCE < $REQUIRED)}"; then
    echo "Pre-mining 200 blocks (balance ${BALANCE} BTC < required ~${REQUIRED} BTC)..."
    premining_address=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary '{"jsonrpc": "1.0", "id": "premining", "method": "getnewaddress", "params": []}' \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')
    curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\": \"1.0\", \"id\": \"premining\", \"method\": \"generatetoaddress\", \"params\": [200, \"$premining_address\"]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC > /dev/null
    echo "Pre-mining complete."
else
    echo "Skipping pre-mine: wallet balance ${BALANCE} BTC already covers ~${REQUIRED} BTC needed."
fi

# Build the list of all containers that need funding.
ALL_NODES=""
for node in $CC_CONTAINERS; do ALL_NODES="$ALL_NODES $node"; done
[ -n "$BM_CONTAINER" ] && ALL_NODES="$ALL_NODES $BM_CONTAINER" || echo "BM node not found."
if docker ps --filter "name=$INNOCENT_NODE" --format "{{.Names}}" | grep -q "$INNOCENT_NODE"; then
    ALL_NODES="$ALL_NODES $INNOCENT_NODE"
else
    echo "InnocentNode not found."
fi

# Get a fresh address from a node, retrying since nodes launched in parallel may
# not answer newaddr the instant getinfo first succeeded.
get_addr() {
    local node=$1 addr=""
    for _ in 1 2 3 4 5; do
        addr=$(docker exec "$node" lightning-cli --regtest newaddr bech32 2>/dev/null | jq -r '.bech32')
        if [ -n "$addr" ] && [ "$addr" != "null" ]; then echo "$addr"; return 0; fi
        sleep 1
    done
    return 1
}

mine_confirm() {
    local addr
    addr=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary '{"jsonrpc":"1.0","id":"mining","method":"getnewaddress","params":[]}' \
        -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')
    curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "{\"jsonrpc\":\"1.0\",\"id\":\"mining\",\"method\":\"generatetoaddress\",\"params\":[$CONFIRMATION_BLOCKS,\"$addr\"]}" \
        -H 'content-type: text/plain;' $BITCOIND_RPC > /dev/null
}

# Fund a list of nodes in ONE sendmany, then mine to confirm.
fund_batch() {
    local outputs="{}" node addr
    for node in "$@"; do
        addr=$(get_addr "$node") || { echo "  WARNING: no address for $node"; continue; }
        outputs=$(echo "$outputs" | jq --arg a "$addr" --argjson amt "$FUNDING_AMOUNT_BTC" '. + {($a): $amt}')
    done
    [ "$(echo "$outputs" | jq 'length')" = "0" ] && return
    local params txid
    params=$(jq -n --argjson out "$outputs" '{jsonrpc:"1.0",id:"fundall",method:"sendmany",params:["",$out]}')
    txid=$(curl -s --user $RPC_USER:$RPC_PASSWORD \
        --data-binary "$params" -H 'content-type: text/plain;' $BITCOIND_RPC | jq -r '.result')
    echo "  sendmany ($(echo "$outputs" | jq 'length') outputs) txid: $txid"
    mine_confirm
}

# True if a node has no confirmed on-chain outputs.
node_unfunded() {
    local n
    n=$(docker exec "$1" lightning-cli --regtest listfunds 2>/dev/null \
        | jq '[.outputs[]|select(.status=="confirmed")]|length')
    [ -z "$n" ] || [ "$n" = "0" ]
}

echo "Funding $(echo $ALL_NODES | wc -w) wallets via sendmany..."
fund_batch $ALL_NODES

# Verify every node actually received funds; re-fund any stragglers. This
# replaces the old per-node funding safety net without per-node mining.
for attempt in 1 2 3; do
    sleep 3   # let CLN wallets sync the freshly mined UTXOs (dev-bitcoind-poll=1)
    missing=""
    for node in $ALL_NODES; do
        node_unfunded "$node" && missing="$missing $node"
    done
    if [ -z "$missing" ]; then
        echo "All wallets funded and confirmed."
        break
    fi
    echo "Verify attempt $attempt: re-funding unfunded node(s):$missing"
    fund_batch $missing
done

echo "Funding complete and transactions confirmed."
