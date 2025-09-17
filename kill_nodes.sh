#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
BASE_DIR="/home/thomas/Documents/LNBot_research_project/LNBot"   #Change this to the directory accordingly to your setup

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm
echo "Cleaning out shared memory and docker directories ..."
rm -f /dev/shm/CC* # forced so that the script doesn't fail if it successfully wiped the shm links
rm -rf $BASE_DIR/lightning-CC*
rm -rf $BASE_DIR/lightning-BM
rm -rf $BASE_DIR/lightning-InnocentNode

echo "Nodes killed."
