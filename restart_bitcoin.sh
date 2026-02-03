#!/bin/bash
# restart_bitcoin.sh

# make sure this directory is actually where the bitcoin core lives
source config.env

if [ -f "./kill_bitcoin.sh" ]; then
    ./kill_bitcoin.sh
else
    echo "Error: kill_bitcoin.sh not found."
    exit 1
fi

sleep 0.5
sudo -u $USER_NAME "$BITCOIND" -datadir="$BITCOIN_DIR"
sleep 1
sudo -u $USER_NAME "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" --regtest createwallet ''

$LNBOT_DIR/venv/bin/python "$MINER_SCRIPT" "$RPC_USER" "$RPC_PASSWORD" "$BITCOIN_CLI"
