# Generated Data & Output

All experimental data is written to the `data/` directory. Each test configuration produces its own set of files keyed by parameter values, so different configurations never overwrite each other; re-running the **same** configuration overwrites its files.

## File naming

Per-iteration files follow:

```
data/<variable>_<value>_<id>_<type>
```

- `<variable>` — the swept variable: `cc_count`, `active_nodes`, `injection_count`, or `takedown_pct`.
- `<value>` — its value for this iteration.
- `<id>` — a fingerprint of the full configuration: every parameter value concatenated in the order `cc_count`, `active_nodes`, `injection_count` (plus `takedown_pct` for takedown tests), followed by markers:
  - takedown strategy: `T` (random) or `Ttargeted` (targeted) — omitted for non-takedown tests;
  - topology mode: `D` (dlnbot), `F` (autonomous), or `X` (custom).
- `<type>` — `time_data.json`, `topology_data.json`, or `system_metrics.csv`.

Examples:

| Test / mode | File |
| --- | --- |
| cc_count, dlnbot, n=10 | `cc_count_10_1041D_time_data.json` |
| active_nodes, dlnbot, m=4 | `active_nodes_4_5041D_time_data.json` |
| injection, custom, k=2 | `injection_count_2_2042X_time_data.json` |
| takedown_random, dlnbot, 10% | `takedown_pct_10_504110TD_time_data.json` |
| takedown_targeted, autonomous, 20% | `takedown_pct_20_504120TtargetedF_time_data.json` |

## Files

### `*_time_data.json`

- `total_times` — `total_setup_time` and `total_send_time`, in seconds.
- `test_data` — a list with one record per message sent in the iteration. Each record contains:
  - the parameter values for the iteration: `cc_count`, `active_nodes`, `injection_count` (and `takedown_pct` for takedown tests);
  - `message` — 1-based index of the message within this iteration;
  - `time_elapsed` — propagation delay in seconds, from the botmaster's keysend to the last surviving node receiving the command. For a partitioned message this is instead the wall-clock time until the timeout was declared;
  - `coverage` — fraction (0.0–1.0) of surviving nodes that received the command;
  - `nodes_received` — number of surviving nodes that received it;
  - `nodes_total` — number of surviving nodes polled via shared memory;
  - `partitioned` — `true` if the command did not reach every surviving node before the timeout.

In `dlnbot` and `custom` modes a timed-out message is treated as a failed iteration and retried, so no `partitioned: true` record is kept. In `autonomous` and the takedown tests a timeout is a valid outcome and is recorded with `partitioned: true`. For takedown tests a partition ends that percentage's iteration immediately, so the file holds a single `partitioned: true` record for that point rather than the full message count (the reachable set is fixed by the surviving topology, so resending cannot change coverage).

### `*_topology_data.json`

- `topology` — a snapshot taken at the end of the iteration: a list of the shared-memory status of each surviving **C&C node** — `host_name`, `short_id`, `state`, `counter` (the last command number it received), `message`, `last_msg_time`, `time`, and `channels` (a map of peer pubkey → `short_id`, `state`, `capacity`, `our_amount`). For takedown tests this is the overlay *after* removals; the removed nodes are listed in `orchestrator.log`.

### `*_system_metrics.csv`

Sampled about once per second while the iteration runs. Columns:

| Column | Meaning |
| --- | --- |
| `timestamp` | epoch seconds |
| `cpu_percent` | summed CPU% across LNTest containers |
| `ram_used_mb` | summed memory (MB) across LNTest containers |
| `ram_used_gb` | the same memory in GB |
| `container_count` | number of LNTest containers sampled |

Values are the aggregate of the LNTest containers (`CC*`, `BM`, `InnocentNode`) read from `docker stats` — not whole-host usage.

### `data/<date>_total_times_log.json`

Appended once per `lntest.py` invocation (not per iteration), one JSON object per line: `timestamp`, `total_time` (seconds for the whole invocation), and `config` (the configuration(s) run). Named by date, e.g. `2026-05-24_total_times_log.json`.

### `data/orchestrator.log`

Timestamped orchestrator log — decisions, warnings, errors, and the nodes removed in takedown tests.

## Node logs

Per-node container logs live in the repo (not under `data/`):

- `cc_node/logs/cc_log_<node>.log` — `cc_manager` (autonomous formation), one per C&C node;
- `cc_node/logs/noise_log_<node>.log` — `message_relay` (command forwarding), one per C&C node;
- `botmaster/logs/bm_log.log` — the botmaster.
