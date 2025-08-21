#!/bin/bash
# init_botnet.sh

set -eux

if [ -z "${1:-}" ]; then
    NUM_LOOPS=2
else
    NUM_LOOPS="$1"
fi

# We start fresh
./cleanup_lightning_nodes.sh

# Start the scripts in order
./1create_Innocent_node.sh
./2create_botmaster_node.sh
./3create_CC_nodesV2.sh "$NUM_LOOPS"
./4fund_wallets.sh
echo "Initialiation Finished"