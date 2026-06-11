#!/usr/bin/env bash
# run_all_paper_tests.sh - run every LNTest paper experiment unattended.
#
# Put this at the repo root (next to lntest.py) and run it from there:
#   cd /path/to/LNTest && sudo ./run_all_paper_tests.sh
#
# Configurable via env vars (defaults in parentheses):
#   LNTEST_DIR    repo root holding lntest.py    (this script's directory)
#   TERMINAL_DIR  where per-test logs are saved   ($LNTEST_DIR/data/terminal_logs)
#   OWNER         user to chown logs back to      (the sudo invoker)
#   TEST_TIMEOUT  per-test hard cap, seconds       (21600 = 6 h)
# Overrides must pass through sudo, so prefix the command with `env`:
#   sudo env LNTEST_DIR=/opt/LNTest TERMINAL_DIR=/data/logs ./run_all_paper_tests.sh
#
# Survive logout + keep the machine awake:
#   tmux new -s lntest
#   sudo systemd-inhibit --what=sleep:idle:handle-lid-switch --why=LNTest ./run_all_paper_tests.sh
#
# Preview the plan without running anything:  DRY_RUN=1 ./run_all_paper_tests.sh
#
# NOTE on runtime: the n=10->500 scalability sweep, the 10 autonomous takedown reps,
# and the 10 autonomous formation reps dominate; ~13 h total on our hardware.
#
# NOTE on collisions: tests repeated 5x (autonomous takedowns, autonomous formation,
# random injection)
# regenerate identical data filenames each rep, so each rep's outputs are
# renamed with a _repN tag IMMEDIATELY after it runs (by exact filename, so a
# later rep can never overwrite an earlier one). All results stay flat in data/.

LNTEST_DIR="${LNTEST_DIR:-$(cd "$(dirname "$0")" && pwd)}"   # repo root (dir holding lntest.py)
TERMINAL_DIR="${TERMINAL_DIR:-$LNTEST_DIR/data/terminal_logs}"  # per-test terminal logs (inside data/, already gitignored)
PY="${PY:-venv/bin/python3}"                                 # python in the repo venv (relative to LNTEST_DIR)
OWNER="${OWNER:-${SUDO_USER:-$(id -un)}}"                    # logs chown'd back to this user (sudo invoker)
TEST_TIMEOUT="${TEST_TIMEOUT:-21600}"                        # hard cap per test (6 h); 5.1 sweep is the long pole
DRY_RUN="${DRY_RUN:-0}"

STAMP="$(date +%Y%m%d_%H%M%S)"
MASTER_LOG="$TERMINAL_DIR/_overnight_master_${STAMP}.log"
PASS=0; FAIL=0; TMO=0
declare -a RESULTS=()

log() {
    local line="$(date '+%F %T')  $*"
    echo "$line"
    [ -d "$TERMINAL_DIR" ] && echo "$line" >> "$MASTER_LOG"
}

# --- Preflight (fail fast on a broken setup, before you walk away) ---
mkdir -p "$TERMINAL_DIR" 2>/dev/null
if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: must run as root. Use: sudo $0"; exit 1
fi
cd "$LNTEST_DIR" || { echo "ERROR: cannot cd to $LNTEST_DIR"; exit 1; }
[ -x "$PY" ]      || { echo "ERROR: $LNTEST_DIR/$PY not found - run setup.sh first"; exit 1; }
[ -f lntest.py ]  || { echo "ERROR: lntest.py not found in $LNTEST_DIR"; exit 1; }
[ -f topologies/ba_50_m4.json ] || log "WARN: topologies/ba_50_m4.json missing - the 2 BA takedown tests will fail."

trap 'log "INTERRUPTED by signal - stopping batch."; exit 130' INT TERM

clean_slate() {   # remove any leftover containers/shm (even from a killed run); keeps bitcoind
    if [ "$DRY_RUN" = "1" ]; then echo "[DRY] bash scripts/cleanup.sh iter"; return; fi
    bash scripts/cleanup.sh iter >/dev/null 2>&1 || true
}

# run_test <label> <lntest.py args...>
run_test() {
    local label="$1"; shift
    local ts logf start rc dur
    ts="$(date +%Y%m%d_%H%M%S)"
    logf="$TERMINAL_DIR/${label}_${ts}.log"
    clean_slate
    log "START  $label  -> $(basename "$logf")"
    start=$(date +%s)
    if [ "$DRY_RUN" = "1" ]; then
        echo "[DRY] yes | timeout -k 120 $TEST_TIMEOUT $PY -u lntest.py $* 2>&1 | tee -a <log>"
        rc=0
    else
        # 'yes' auto-answers the "Confirm test? y/n" prompt so it never blocks.
        echo "### $label | lntest.py $* | $(date '+%F %T')" > "$logf" 2>/dev/null || true
        yes 2>/dev/null | timeout -k 120 "$TEST_TIMEOUT" "$PY" -u lntest.py "$@" 2>&1 | tee -a "$logf"
        rc=${PIPESTATUS[1]}
    fi
    # The orchestrator sometimes exits 0 on an internal failure; flag it from the log.
    if [ "$DRY_RUN" != "1" ] && [ "$rc" -eq 0 ] && \
       grep -qE "Traceback \(most recent call last\)|Test failed with parameters|could not build topology" "$logf" 2>/dev/null; then
        rc=1
    fi
    dur=$(( $(date +%s) - start ))
    if [ "$rc" -eq 0 ]; then
        PASS=$((PASS+1)); RESULTS+=("PASS     $label (${dur}s)"); log "DONE   $label (rc=0, ${dur}s)"
    elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        TMO=$((TMO+1));  RESULTS+=("TIMEOUT  $label (${dur}s, cap ${TEST_TIMEOUT}s)"); log "TIMEOUT $label (rc=$rc, ${dur}s)"
    else
        FAIL=$((FAIL+1)); RESULTS+=("FAIL     $label (rc=$rc, ${dur}s)"); log "FAIL   $label (rc=$rc, ${dur}s) - see $logf"
    fi
    return 0   # never abort the batch
}

# rename_out <base_id> <tag>: rename a run's 3 output files by EXACT name, inserting
# _<tag> (e.g. data/<base>_time_data.json -> data/<base>_<tag>_time_data.json).
# Exact names (no globs) => a later repeat can never re-match/overwrite an earlier one.
rename_out() {
    local base="$1" tag="$2" t
    for t in time_data.json topology_data.json system_metrics.csv; do
        if [ "$DRY_RUN" = "1" ]; then
            echo "[DRY] mv data/${base}_${t} -> data/${base}_${tag}_${t}"
        else
            mv -f "data/${base}_${t}" "data/${base}_${tag}_${t}" 2>/dev/null || true
        fi
    done
}

# tag_takedown <suffix: TF|TtargetedF> <tag>: tag all 5 percentage-point files of an
# autonomous takedown run (n=50,m=4 => id 5041<pct>) so repeats don't collide.
tag_takedown() {
    local suf="$1" tag="$2" pct
    for pct in 10 20 30 40 50; do rename_out "takedown_pct_${pct}_5041${pct}${suf}" "$tag"; done
}

log "===== LNTest overnight batch START (dry_run=$DRY_RUN, timeout=${TEST_TIMEOUT}s/test) ====="

# Smoke test first (4 nodes); continue regardless of result.
run_test smoke_small small

# 5.5 Injection point (n=50, dlnbot). Runs BEFORE 5.1: the position cases share
# the filename cc_count_50_5041D, so each is tagged immediately, then 5.1 writes
# a clean n=50 afterward.
# Random injection x5 (picks a random node each time -> repeat for statistics).
for k in 1 2 3 4 5; do
    run_test "5.5_inject_random_rep$k" run injection --at 1 --num-msg 10
    rename_out injection_count_1_5041D "rep$k"
done
run_test 5.5_inject_bottom run cc_count --at 50 --inject CC1 --num-msg 10
rename_out cc_count_50_5041D inj-CC1
run_test 5.5_inject_middle run cc_count --at 50 --inject CC25 --num-msg 10
rename_out cc_count_50_5041D inj-CC25
run_test 5.5_inject_top    run cc_count --at 50 --inject CC50 --num-msg 10
rename_out cc_count_50_5041D inj-CC50
run_test 5.5_inject_multi  run cc_count --at 50 --inject CC1,CC25,CC50 --num-msg 10
rename_out cc_count_50_5043D inj-multi

# 5.1 Scalability (dlnbot) - single run over the full non-uniform range
run_test 5.1_scalability run cc_count --sweep-values "10,20,30,40,50,60,70,80,90,100,200,300,400,500" --num-msg 10

# 5.2 Autonomous formation topology - non-deterministic -> 5 reps each (n=20, n=50),
# tagged _repK so reps never collide (same approach as 5.3 autonomous / 5.5 random).
for k in 1 2 3 4 5; do
    run_test "5.2_formation_n20_rep$k" run cc_count --topology autonomous --at 20 --num-msg 10
    rename_out cc_count_20_2041F "rep$k"
    run_test "5.2_formation_n50_rep$k" run cc_count --topology autonomous --at 50 --num-msg 10
    rename_out cc_count_50_5041F "rep$k"
done

# 5.3 Resilience to takedowns.
# dlnbot chain + BA scale-free are deterministic -> run once each.
run_test 5.3_takedown_random_dlnbot     run takedown_random   --num-msg 10
run_test 5.3_takedown_targeted_dlnbot   run takedown_targeted --num-msg 10
run_test 5.3_takedown_random_ba         run takedown_random   --topology-file topologies/ba_50_m4.json --num-msg 10
run_test 5.3_takedown_targeted_ba       run takedown_targeted --topology-file topologies/ba_50_m4.json --num-msg 10
# Autonomous formation is non-deterministic -> 5 reps each, tagged so none collide.
for k in 1 2 3 4 5; do
    run_test "5.3_takedown_random_autonomous_rep$k"   run takedown_random   --topology autonomous --num-msg 10
    tag_takedown TF "rep$k"
done
for k in 1 2 3 4 5; do
    run_test "5.3_takedown_targeted_autonomous_rep$k" run takedown_targeted --topology autonomous --num-msg 10
    tag_takedown TtargetedF "rep$k"
done

# 5.4 Active neighbor count m (n=50, n=100, n=200)
run_test 5.4_active_nodes_n50  run active_nodes --num-msg 10
run_test 5.4_active_nodes_n100 run active_nodes --nodes 100 --num-msg 10
run_test 5.4_active_nodes_n200 run active_nodes --nodes 200 --num-msg 10

log "===== LNTest overnight batch COMPLETE: $PASS passed, $FAIL failed, $TMO timed out ====="
{ echo; echo "RESULTS (in run order):"; for r in "${RESULTS[@]}"; do echo "  $r"; done; } | tee -a "$MASTER_LOG"

chown -R "$OWNER" "$TERMINAL_DIR" 2>/dev/null || true
