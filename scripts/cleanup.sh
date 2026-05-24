#!/bin/bash
# cleanup.sh - LNTest cleanup. Modes (run with no/invalid arg for full usage):
#   iter     between-iteration reset; bitcoind keeps running (regtest chain reused)
#   bitcoin  stop bitcoind + delete ~/.bitcoin/regtest/ (keeps bitcoin.conf/creds)
#   fresh    full reset to freshly-cloned + setup.sh state

set -e

# --- Resolve paths and load config ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LNTEST_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$LNTEST_ROOT/config.env" ]; then
    source "$LNTEST_ROOT/config.env"
    HAS_CONFIG=1
else
    echo "Warning: config.env not found at $LNTEST_ROOT/config.env"
    HAS_CONFIG=0
fi

MODE="${1:-}"

# --- Helpers (between-iteration) ---

stop_containers() {
    echo "Stopping and removing LNTest Docker containers..."
    local names
    names=$(docker ps -a \
        --filter "name=CC" \
        --filter "name=BM" \
        --filter "name=InnocentNode" \
        --format "{{.Names}}" 2>/dev/null || true)
    if [ -n "$names" ]; then
        echo "$names" | xargs -r docker rm -f >/dev/null 2>&1 || true
    fi
}

clear_shm() {
    echo "Clearing /dev/shm/CC*..."
    rm -f /dev/shm/CC* 2>/dev/null || true
}

clear_node_data() {
    if [ -n "${NODE_DATA_DIR:-}" ] && [ -d "$NODE_DATA_DIR" ]; then
        echo "Wiping $NODE_DATA_DIR/*..."
        rm -rf "$NODE_DATA_DIR"/*
    fi
}

clear_node_logs_and_state() {
    # Per-iteration outputs that init_botnet.sh, cc_manager.py, and
    # botmaster.py produce and that would mislead the next iteration.
    if [ -n "${BOT_MASTER_DIR:-}" ]; then
        rm -rf "$BOT_MASTER_DIR"/logs/bm_log* 2>/dev/null || true
        rm -f  "$BOT_MASTER_DIR"/counter.txt
        rm -f  "${BOT_MASTER_ADDRESS_LIST:-}"
    fi
    if [ -n "${NODE_MANAGER_DIR:-}" ]; then
        rm -rf "$NODE_MANAGER_DIR"/logs/cc_log*    2>/dev/null || true
        rm -rf "$NODE_MANAGER_DIR"/logs/noise_log* 2>/dev/null || true
        rm -rf "$NODE_MANAGER_DIR"/status/*        2>/dev/null || true
        rm -f  "${NODE_MANAGER_ADDRESS_LIST:-}"
    fi
}

# --- Helpers (bitcoin) ---

stop_bitcoind() {
    if [ $HAS_CONFIG -eq 0 ]; then
        USER_NAME=$(whoami)
    fi

    if pgrep -x "bitcoind" > /dev/null; then
        if [ $HAS_CONFIG -eq 1 ] && [ -x "${BITCOIN_CLI:-}" ]; then
            echo "Stopping bitcoind via RPC..."
            sudo -u "$USER_NAME" "$BITCOIN_CLI" -datadir="$BITCOIN_DIR" stop 2>/dev/null || true
            sleep 2
        fi
    else
        echo "bitcoind is not running."
        return 0
    fi

    if pgrep -x "bitcoind" > /dev/null; then
        echo "bitcoind still running, forcing kill..."
        pkill -9 bitcoind || true
        sleep 1
    fi
}

clear_regtest_data() {
    if [ -n "${BITCOIN_DIR:-}" ] && [ -d "$BITCOIN_DIR/regtest" ]; then
        echo "Deleting $BITCOIN_DIR/regtest..."
        rm -rf "$BITCOIN_DIR/regtest"
    fi
}

# --- Helpers (fresh-only: full repo reset) ---

clear_botmaster_runtime() {
    # Wipe everything in botmaster/ except the tracked source file.
    if [ -n "${BOT_MASTER_DIR:-}" ] && [ -d "$BOT_MASTER_DIR" ]; then
        echo "Wiping $BOT_MASTER_DIR/ (keeping botmaster.py)..."
        find "$BOT_MASTER_DIR" -mindepth 1 -maxdepth 1 \
            ! -name 'botmaster.py' \
            -exec rm -rf {} +
    fi
}

clear_cc_node_runtime() {
    # Wipe everything in cc_node/ except the tracked source files.
    if [ -n "${NODE_MANAGER_DIR:-}" ] && [ -d "$NODE_MANAGER_DIR" ]; then
        echo "Wiping $NODE_MANAGER_DIR/ (keeping cc_manager.py, message_relay.py, reject_inbound.py)..."
        find "$NODE_MANAGER_DIR" -mindepth 1 -maxdepth 1 \
            ! -name 'cc_manager.py' \
            ! -name 'message_relay.py' \
            ! -name 'reject_inbound.py' \
            -exec rm -rf {} +
    fi
}

clear_innocent_runtime() {
    # innocent/ has no tracked files; wipe contents (incl. dotfiles).
    if [ -n "${INNO_MANAGER_DIR:-}" ] && [ -d "$INNO_MANAGER_DIR" ]; then
        echo "Wiping $INNO_MANAGER_DIR/..."
        find "$INNO_MANAGER_DIR" -mindepth 1 -exec rm -rf {} + 2>/dev/null || true
    fi
}

clear_data_dir() {
    if [ -n "${TEST_DATA_DIR:-}" ] && [ -d "$TEST_DATA_DIR" ]; then
        echo "Wiping $TEST_DATA_DIR/*..."
        rm -rf "$TEST_DATA_DIR"/*
    fi
}

clear_test_state() {
    if [ -n "${TEST_STATE_DIR:-}" ] && [ -d "$TEST_STATE_DIR" ]; then
        echo "Wiping $TEST_STATE_DIR/*..."
        rm -rf "$TEST_STATE_DIR"/*
    fi
}

clear_pycache() {
    echo "Removing __pycache__/ and *.pyc (skipping venv/)..."
    find "$LNTEST_ROOT" -path "$LNTEST_ROOT/venv" -prune -o \
        -type d -name '__pycache__' -print 2>/dev/null \
        | xargs -r rm -rf
    find "$LNTEST_ROOT" -path "$LNTEST_ROOT/venv" -prune -o \
        -type f -name '*.pyc' -print 2>/dev/null \
        | xargs -r rm -f
}

refuse_if_test_running() {
    if pgrep -f "[l]ntest.py" > /dev/null; then
        echo "Error: an lntest.py process is running. Refusing 'fresh' reset." >&2
        echo "Stop the test first (Ctrl-C or kill), then retry." >&2
        exit 1
    fi
}

# --- Modes ---

case "$MODE" in
    iter)
        # Between-iteration cleanup. bitcoind keeps running.
        stop_containers
        clear_shm
        clear_node_data
        clear_node_logs_and_state
        ;;

    bitcoin)
        # Reset Bitcoin Core regtest state.
        stop_bitcoind
        clear_regtest_data
        ;;

    fresh)
        # Full reset to fresh-clone + setup.sh state.
        refuse_if_test_running
        stop_containers
        stop_bitcoind
        clear_shm
        clear_node_data
        clear_botmaster_runtime
        clear_cc_node_runtime
        clear_innocent_runtime
        clear_data_dir
        clear_test_state
        clear_pycache
        clear_regtest_data
        echo "Done. LNTest is reset to fresh-clone + setup.sh state."
        ;;

    *)
        cat <<EOF >&2
Usage: $(basename "$0") {iter|bitcoin|fresh}

  iter      Between-iteration cleanup. Stops containers, clears shared
            memory, wipes node_data/, and removes per-node logs/status/
            address-list files. Bitcoin Core keeps running so the regtest
            blockchain is reused across iterations. Called automatically
            by init_botnet.sh between iterations.

  bitcoin   Stops bitcoind and deletes ~/.bitcoin/regtest/. bitcoin.conf
            and RPC credentials are preserved. Called by
            restart_bitcoin.sh, which lntest.py invokes when bitcoind
            needs a full restart.

  fresh     Full reset to a freshly-cloned + setup.sh state. Stops
            every LNTest process (containers + bitcoind), removes all
            in-repo runtime artifacts, and deletes ~/.bitcoin/regtest/.
            Preserves: venv/, config.env, ~/.bitcoin/bitcoin.conf,
            ~/.lightning/lightning.conf, and the lntest:latest Docker
            image. Refuses to run if an lntest.py process is detected.
EOF
        exit 1
        ;;
esac
