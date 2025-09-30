#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
source config.env

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm 

# Remove all associated directories
echo "Removing directories and logs..."
rm -rf $LNBOT_DIR/lightning-CC*
rm -rf $LNBOT_DIR/lightning-BM
rm -rf $LNBOT_DIR/lightning-InnocentNode
rm -rf $LNBOT_DIR/BotMasterComms/bm_log*
rm -f $LNBOT_DIR/BotMasterComms/counter.txt
rm -f $LNBOT_DIR/BotMasterComms/funded_node.txt
rm -f $LNBOT_DIR/BotMasterComms/CC_address_list.txt
rm -rf $LNBOT_DIR/NodeManagerComms/logs/cc_log*
rm -rf $LNBOT_DIR/NodeManagerComms/logs/noise_log*
rm -rf $LNBOT_DIR/NodeManagerComms/status/*
rm -f $LNBOT_DIR/NodeManagerComms/CC_address_list.txt
rm -rf /dev/shm/CC*

echo "Cleanup complete."
