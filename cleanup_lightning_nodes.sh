#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
BASE_DIR="/home/c499"   #Change this to the directory accordingly to your setup

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm

# Remove all associated directories
echo "Removing directories..."
rm -rf $BASE_DIR/lightning-CC*
rm -rf $BASE_DIR/lightning-BM
rm -rf $BASE_DIR/lightning-InnocentNode

echo "Cleanup complete."
