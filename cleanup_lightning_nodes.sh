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
rm -rf $NODE_DATA_DIR/*
rm -rf $BOT_MASTER_DIR/bm_log*
rm -f $BOT_MASTER_DIR/counter.txt
rm -f $BOT_MASTER_DIR/funded_node.txt
rm -f $BOT_MASTER_ADDRESS_LIST
rm -rf $NODE_MANAGER_DIR/logs/cc_log*
rm -rf $NODE_MANAGER_DIR/logs/noise_log*
rm -rf $NODE_MANAGER_DIR/status/*
rm -f $NODE_MANAGER_ADDRESS_LIST
rm -rf /dev/shm/CC* 

echo "Cleanup complete."
