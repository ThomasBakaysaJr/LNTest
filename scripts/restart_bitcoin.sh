#!/bin/bash
# restart_bitcoin.sh

# Resolve LNTest root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

source "$LNTEST_ROOT/config.env"

"$SCRIPT_DIR/cleanup.sh" bitcoin

sleep 0.5
sudo -u $USER_NAME "$BITCOIND" -datadir="$BITCOIN_DIR"
sleep 1
sudo -u $USER_NAME "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" --regtest createwallet ''

$LNBOT_DIR/venv/bin/python "$MINER_SCRIPT" "$RPC_USER" "$RPC_PASSWORD" "$BITCOIN_CLI"
