#!/bin/bash
# restart_bitcoin.sh


# make sure this directory is actually where the bitcoin core lives
source config.env

MINER_SCRIPT="$BASE_DIR/LNBot/mineBlocks.py"
BITCOIND="$BITCOIN_CORE_DIR/bitcoind"
BITCOIN_CLI="$BITCOIN_CORE_DIR/bitcoin-cli"

if pgrep -x "bitcoind" > /dev/null; then
    sudo -u $USER_NAME "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" stop
fi
sleep 0.5
echo "Deleting regtest data"
rm -rf $BITCOIN_DIR/regtest/*
sleep 0.5
sudo -u $USER_NAME "$BITCOIND" -datadir="$BITCOIN_DIR"
sleep 1
sudo -u $USER_NAME "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" --regtest createwallet ''

$BASE_DIR/LNBot/venv/bin/python "$MINER_SCRIPT" "$RPC_USER" "$RPC_PASSWORD" "$BITCOIN_CLI"
