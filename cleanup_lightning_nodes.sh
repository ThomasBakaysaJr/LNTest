#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
BASE_DIR="/home/thomas/Documents/LNBot/Other_files"   #Change this to the directory accordingly to your setup

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm

# Remove all associated directories
echo "Removing directories and logs..."
rm -rf $BASE_DIR/lightning-CC*
rm -rf $BASE_DIR/lightning-BM
rm -rf $BASE_DIR/lightning-InnocentNode
rm -rf $BASE_DIR/BotMasterComms/cc_logs*
rm -f $BASE_DIR/BotMasterComms/counter.txt
rm -f $BASE_DIR/BotMasterComms/funded_node.txt
rm -rf $BASE_DIR/NodeManagerComms/cc_logs*
rm -rf $BASE_DIR/NodeManagerComms/cc_messageLog_*
rm -rf $BASE_DIR/NodeManagerComms/cc_noise_*

echo "Cleanup complete."
