#!/bin/bash
# cleanup.sh - Unified cleanup script for LNTest
#
# Usage:
#   cleanup.sh nodes    - Stop/remove containers, clean SHM and node data (between iterations)
#   cleanup.sh all      - Full cleanup including logs, status files, address lists
#   cleanup.sh bitcoin  - Stop bitcoind and delete regtest data

set -e

# Resolve config.env relative to the LNTest root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$LNTEST_ROOT/config.env" ]; then
    source "$LNTEST_ROOT/config.env"
    HAS_CONFIG=1
else
    echo "Warning: config.env not found at $LNTEST_ROOT/config.env"
    HAS_CONFIG=0
fi

MODE="${1:-nodes}"

cleanup_containers() {
    echo "Stopping and removing containers..."
    docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker stop
    docker ps -a --filter "name=CC" --filter "name=BM" --filter "name=InnocentNode" -q | xargs -r docker rm
    echo "Cleaning out shared memory and docker directories..."
    rm -f /dev/shm/CC*
    rm -rf $NODE_DATA_DIR/*
    echo "Nodes killed."
}

cleanup_logs() {
    echo "Removing logs and status files..."
    rm -rf $BOT_MASTER_DIR/logs/bm_log*
    rm -f $BOT_MASTER_DIR/counter.txt
    rm -f $BOT_MASTER_DIR/funded_node.txt
    rm -f $BOT_MASTER_ADDRESS_LIST
    rm -rf $NODE_MANAGER_DIR/logs/cc_log*
    rm -rf $NODE_MANAGER_DIR/logs/noise_log*
    rm -rf $NODE_MANAGER_DIR/status/*
    rm -f $NODE_MANAGER_ADDRESS_LIST
    echo "Cleanup complete."
}

cleanup_bitcoin() {
    if [ $HAS_CONFIG -eq 0 ]; then
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

    # Force kill if still running
    if pgrep -x "bitcoind" > /dev/null; then
        echo "bitcoind still running, forcing kill..."
        sudo pkill -9 bitcoind
    fi

    # Delete regtest data
    if [ $HAS_CONFIG -eq 1 ]; then
        echo "Deleting regtest data"
        if [ -n "$BITCOIN_DIR" ]; then
            rm -rf "$BITCOIN_DIR/regtest"
        else
            echo "Error: BITCOIN_DIR is empty. Skipping deletion."
        fi
    else
        echo "Skipping regtest data deletion (config.env missing)."
    fi
}

case "$MODE" in
    nodes)
        cleanup_containers
        ;;
    all)
        cleanup_containers
        cleanup_logs
        ;;
    bitcoin)
        cleanup_bitcoin
        ;;
    *)
        echo "Usage: cleanup.sh {nodes|all|bitcoin}"
        exit 1
        ;;
esac
