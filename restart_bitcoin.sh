#!/bin/bash
# restart_bitcoin.sh


# make sure this directory is actually where the bitcoin core lives
HOME_DIR="/home/thomas/Documents/LNBot_research_project"
BITCOIN_DIR="$HOME_DIR/bitcoin/bin"
BITCOIN_DATA_DIR="/home/thomas/.bitcoin"

MINER_SCRIPT="$HOME_DIR/LNBot/mineBlocks.py"
BITCOIND="$BITCOIN_DIR/bitcoind"
BITCOIN_CLI="$BITCOIN_DIR/bitcoin-cli"

if pgrep -x "bitcoind" > /dev/null; then
    sudo -u thomas "$BITCOIN_CLI" -datadir="$BITCOIN_DATA_DIR" stop
fi
sleep 0.5
echo "Deleting regtest data"
rm -rf $BITCOIN_DATA_DIR/regtest/*
sleep 0.5
sudo -u thomas "$BITCOIND" -datadir="$BITCOIN_DATA_DIR"
sleep 1
sudo -u thomas "$BITCOIN_CLI" -datadir="$BITCOIN_DATA_DIR" --regtest createwallet ''

export PATH="$BITCOIN_DIR:$PATH"
python3 "$MINER_SCRIPT"