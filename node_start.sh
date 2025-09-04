#!/bin/bash

# get number of active nodes and remove it from incoming arguments
active_nodes=$1
shift

# Start lightningd in the background
# The '$@' passes all command-line arguments to it
exec lightningd "$@" &

# Get the Process ID (PID) of the lightningd process
LND_PID=$!

echo "Waiting for lightningd to initialize..."
# Loop until 'lightning-cli getinfo' succeeds
# '&> /dev/null' silences the command's output so we don't spam the logs
while ! lightning-cli --regtest getinfo &> /dev/null; do
    echo -n "." # Print a dot to show we are waiting
    sleep 2
done
echo
echo "Lightningd is online."


# Now that lightningd is ready, start the python managers
echo "Starting background services..."
cd /root/nodemanager
python3 CC_Manager.py $active_nodes &
python3 noiseManager_REST.py &
echo "Background services started."

# Ensures the container dies if the lightning daemon dies
wait $LND_PID