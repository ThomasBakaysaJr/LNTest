#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
BASE_DIR="/home/thomas/Documents/LNBot_research_project/LNBot"   #Change this to the directory accordingly to your setup

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm -v

# Remove all associated directories
echo "Removing directories and logs..."
rm -rf $BASE_DIR/lightning-CC*
rm -rf $BASE_DIR/lightning-BM
rm -rf $BASE_DIR/lightning-InnocentNode
rm -rf $BASE_DIR/BotMasterComms/bm_log*
rm -f $BASE_DIR/BotMasterComms/counter.txt
rm -f $BASE_DIR/BotMasterComms/funded_node.txt
rm -f $BASE_DIR/BotMasterComms/CC_address_list.txt
rm -rf $BASE_DIR/NodeManagerComms/logs/cc_log*
rm -rf $BASE_DIR/NodeManagerComms/logs/noise_log*
rm -rf $BASE_DIR/NodeManagerComms/status/*
rm -f $BASE_DIR/NodeManagerComms/CC_address_list.txt

echo "Cleanup complete."
