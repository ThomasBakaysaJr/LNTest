#!/bin/bash
# init_botnet.sh

set -eux

# We start fresh
./cleanup_lightning_nodes.sh

# Start the scripts in order
./1create_Innocent_node.sh
./2create_botmaster_node.sh
./3create_CC_nodesV2.sh
./4fund_wallets.sh
echo "Initialiation Finished"