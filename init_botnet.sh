#!/bin/bash
# init_botnet.sh

set -eux

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

# We start fresh
./cleanup_lightning_nodes.sh

# Start the scripts in order
./1create_Innocent_node.sh
./2create_botmaster_node.sh
# ./3create_CC_nodesV2.sh "$TOTAL_NODES" "$ACTIVE_NODES"
# ./4fund_wallets.sh
# echo "Initialiation Finished"