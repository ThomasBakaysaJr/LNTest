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

# Only pre-mine when the wallet can't already cover funding for every node
# (the regtest chain persists across iterations, so usually it can). The BM is
# funded separately and more heavily, so add its share to the target.
NODE_COUNT=$(echo "$CC_CONTAINERS" | grep -c .)
NODE_COUNT=$((NODE_COUNT + 1))   # + InnocentNode
REQUIRED=$(awk "BEGIN{print $NODE_COUNT * $FUNDING_AMOUNT_BTC * 2 + $BM_FUNDING_AMOUNT_BTC * 2}")

rpc() {  # rpc METHOD [JSON_PARAMS]; echoes .result
    curl -s --user "$RPC_USER:$RPC_PASSWORD" \
        --data-binary "{\"jsonrpc\":\"1.0\",\"id\":\"fw\",\"method\":\"$1\",\"params\":${2:-[]}}" \
        -H 'content-type: text/plain;' "$BITCOIND_RPC" | jq -r '.result'
}

BALANCE=$(rpc getbalance); BALANCE=${BALANCE:-0}

# Mine until `getbalance` (counts only mature >=100-conf coins) covers REQUIRED.
# Each round is sized to the current block subsidy because regtest halves it
# every 150 blocks, so a fixed block count under-mines as the chain grows. The
# cap turns an unreachable target into a clear error instead of a silent under-fund.
if awk "BEGIN{exit !($BALANCE < $REQUIRED)}"; then
    echo "Pre-mining to reach ~${REQUIRED} BTC (current balance ${BALANCE} BTC)..."
    premining_address=$(rpc getnewaddress)
    rounds=0
    while awk "BEGIN{exit !($BALANCE < $REQUIRED)}"; do
        rounds=$((rounds + 1))
        if [ "$rounds" -gt 40 ]; then
            echo "  ERROR: pre-mine could not reach ${REQUIRED} BTC (balance ${BALANCE} BTC after $((rounds-1)) rounds)." >&2
            echo "         The regtest subsidy has likely halved too far. Lower FUNDING_AMOUNT_BTC or reset the chain." >&2
            break
        fi
        HEIGHT=$(rpc getblockcount); HEIGHT=${HEIGHT:-0}
        BLOCKS=$(awk "BEGIN{
            era=int($HEIGHT/150); subsidy=50.0/(2^era); if (subsidy<=0) subsidy=1e-8;
            need=($REQUIRED-$BALANCE)/subsidy;
            b=int(need)+101;          # +100 so the freshly mined coinbase matures
            if (b<120) b=120;          # always mature at least one useful batch
            if (b>5000) b=5000;        # cap blocks per round
            print b
        }")
        rpc generatetoaddress "[$BLOCKS, \"$premining_address\"]" > /dev/null
        BALANCE=$(rpc getbalance); BALANCE=${BALANCE:-0}
    done
    echo "Pre-mining complete (balance ${BALANCE} BTC, target ~${REQUIRED} BTC)."
else
    echo "Skipping pre-mine: wallet balance ${BALANCE} BTC already covers ~${REQUIRED} BTC needed."
fi

# CC nodes + InnocentNode get the lean per-node amount; the BM is funded
# separately below with a larger balance for its discovery-rule channels.
CC_NODES=""
for node in $CC_CONTAINERS; do CC_NODES="$CC_NODES $node"; done
if docker ps --filter "name=$INNOCENT_NODE" --format "{{.Names}}" | grep -q "$INNOCENT_NODE"; then
    CC_NODES="$CC_NODES $INNOCENT_NODE"
else
    echo "InnocentNode not found."
fi
[ -n "$BM_CONTAINER" ] || echo "BM node not found."

# Get a fresh address, retrying since parallel-launched nodes may not answer
# newaddr immediately.
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

# fund_batch AMOUNT_BTC node...  -- fund every node in ONE sendmany, then confirm.
fund_batch() {
    local amount=$1; shift
    local outputs="{}" node addr
    for node in "$@"; do
        addr=$(get_addr "$node") || { echo "  WARNING: no address for $node"; continue; }
        outputs=$(echo "$outputs" | jq --arg a "$addr" --argjson amt "$amount" '. + {($a): $amt}')
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

echo "Funding $(echo $CC_NODES | wc -w) CC/Innocent wallets ($FUNDING_AMOUNT_BTC BTC each)..."
fund_batch "$FUNDING_AMOUNT_BTC" $CC_NODES
if [ -n "$BM_CONTAINER" ]; then
    echo "Funding BM ($BM_FUNDING_AMOUNT_BTC BTC for discovery-rule channels)..."
    fund_batch "$BM_FUNDING_AMOUNT_BTC" "$BM_CONTAINER"
fi

# Verify every node actually received funds; re-fund any stragglers at its amount.
for attempt in 1 2 3; do
    sleep 3   # let CLN wallets sync the freshly mined UTXOs (dev-bitcoind-poll=1)
    missing_cc=""; missing_bm=""
    for node in $CC_NODES; do
        node_unfunded "$node" && missing_cc="$missing_cc $node"
    done
    [ -n "$BM_CONTAINER" ] && node_unfunded "$BM_CONTAINER" && missing_bm="$BM_CONTAINER"
    if [ -z "$missing_cc" ] && [ -z "$missing_bm" ]; then
        echo "All wallets funded and confirmed."
        break
    fi
    [ -n "$missing_cc" ] && { echo "Verify attempt $attempt: re-funding:$missing_cc"; fund_batch "$FUNDING_AMOUNT_BTC" $missing_cc; }
    [ -n "$missing_bm" ] && { echo "Verify attempt $attempt: re-funding BM"; fund_batch "$BM_FUNDING_AMOUNT_BTC" "$missing_bm"; }
done

echo "Funding complete and transactions confirmed."
