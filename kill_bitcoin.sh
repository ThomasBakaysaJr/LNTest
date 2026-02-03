#!/bin/bash
# kill_bitcoin.sh

# Load config if available
if [ -f config.env ]; then
    source config.env
    HAS_CONFIG=1
else
    echo "Warning: config.env not found. Attempting to kill bitcoind process blindly."
    HAS_CONFIG=0
    # Determine current user if USER_NAME is not set (for pkill)
    USER_NAME=$(whoami)
fi

if pgrep -x "bitcoind" > /dev/null; then
    if [ $HAS_CONFIG -eq 1 ]; then
        echo "Stopping bitcoind via RPC..."
        sudo -u $USER_NAME "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" stop
        sleep 2
    fi
else
    echo "bitcoind is not running."
fi

# Double check and force kill if necessary (this works without config)
if pgrep -x "bitcoind" > /dev/null; then
    echo "bitcoind running, forcing kill..."
    sudo pkill -9 bitcoind
fi

# Only delete data if we loaded the config and know the path
if [ $HAS_CONFIG -eq 1 ]; then
    echo "Deleting regtest data"
    # Safety check
    if [ -n "$BITCOIN_DIR" ]; then
        rm -rf "$BITCOIN_DIR/regtest"
    else
        echo "Error: BITCOIN_DIR is empty. Skipping deletion."
    fi
else
    echo "Skipping regtest data deletion (config.env missing)."
    echo "Please run setup.sh now that bitcoind is stopped."
fi