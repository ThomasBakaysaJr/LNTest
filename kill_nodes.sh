#!/bin/bash
# cleanup_lightning_nodes.sh

set -e

# Base directories for lightning
BASE_DIR="/home/thomas/Documents/LNBot_research_project/LNBot"   #Change this to the directory accordingly to your setup

# Stop and remove all containers
echo "Stopping and removing containers..."
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm
echo "Clearning out shared memory..."
# rm /dev/shm/CC*

echo "Nodes killed."
