# Test Scenarios

LNTest provides five tests across three topology modes. Each test sweeps a single experimental variable while holding everything else constant.

## CLI Flags

| Flag | Description |
| --- | --- |
| `test_id` | Test to run: `cc_count`, `active_nodes`, `injection`, `takedown_random`, `takedown_targeted` |
| `--nodes N` | Fixed network size (ignored when `cc_count` is the sweep variable) |
| `--m N` | Fixed overlay width, i.e., active C&C servers per node (ignored when `active_nodes` is the sweep variable) |
| `--inject CC5,CC12` | Explicit injection points. Botmaster connects to all listed nodes in parallel |
| `--num-msg N` | Number of messages to send per sweep iteration |
| `--sweep-start N` | Override the starting value of the sweep variable |
| `--sweep-end N` | Override the ending value of the sweep variable |
| `--sweep-step N` | Override the step size |
| `--topology dlnbot` | D-LNBot chain topology (default) |
| `--topology custom` | User-supplied JSON topology |
| `--topology-file PATH` | Path to custom topology JSON (required with `--topology custom`) |
| `--dlnbot-formation` | Autonomous D-LNBot formation with staggered container launches |

## Topology x Test Compatibility

| Test | D-LNBot (default) | D-LNBot Formation | Custom |
| --- | --- | --- | --- |
| `cc_count` | ✅ | ⚠️ Nondeterministic topology | ❌ Blocked |
| `active_nodes` | ✅ | ⚠️ Confounding variable | ❌ Blocked |
| `injection` | ✅ | ⚠️ Topology variance | ✅ |
| `takedown_random` | ✅ | ✅ | ✅ |
| `takedown_targeted` | ✅ | ✅ | ✅ |

- ✅ = works as a single-variable experiment
- ⚠️ = works but a red warning is displayed before the test starts (see [Warnings](#warnings))
- ❌ = blocked with a red error message (see [Blocked Combinations](#blocked-combinations))

## Botmaster Injection (`--inject`)

The `--inject` flag controls where the botmaster connects to inject commands into the overlay network. When multiple nodes are specified, the botmaster sends to all of them simultaneously in parallel.

**Syntax:**

```
--inject CC1              # Single node
--inject CC5,CC12,CC30    # Multiple nodes (parallel injection)
```

**Default behavior by test:**

| Test | Default (no `--inject`) | Why |
| --- | --- | --- |
| `cc_count` | CC1 | Deterministic: same injection point across all sweep values |
| `active_nodes` | CC1 | Deterministic: same injection point across all sweep values |
| `injection` | Random N nodes | The sweep variable IS the injection count; random selection averages over position effects |
| `takedown_random` | CC1 | Deterministic; falls back to first surviving node if CC1 is killed |
| `takedown_targeted` | CC1 | Deterministic; falls back to first surviving node if CC1 is killed |

**Note:** `--inject` cannot be used with the `injection` sweep test. The sweep needs to vary the *number* of injection points; `--inject` fixes specific nodes, making the sweep meaningless.

---

## Test Details

### cc_count — Scale C&C Nodes

Measures how command propagation delay grows as the number of C&C servers increases.

| Parameter | Default |
| --- | --- |
| Sweep variable | `cc_count`: 10 → 100, step 10 |
| Fixed: m | 4 |
| Fixed: injection | CC1 |
| Messages per iteration | 10 |

**Compatible modes:** D-LNBot (deterministic chain), D-LNBot Formation (nondeterministic, warns). Blocked with custom topology (node count is fixed by the JSON file).

```bash
# D-LNBot chain (default)
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3

# Custom sweep range
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3 --sweep-start 200 --sweep-end 500 --sweep-step 100
```

### active_nodes — Scale Active C&C Servers (m)

Varies the overlay width parameter *m* to study how the number of neighbors per node affects propagation. Higher *m* means more edges, faster propagation, but more channels to open.

| Parameter | Default |
| --- | --- |
| Sweep variable | `active_nodes`: 2 → 6, step 1 |
| Fixed: cc_count | 50 |
| Fixed: injection | CC1 |
| Messages per iteration | 10 |

Sweep starts at m=2 because m=1 topologies fragment under CLN's `--dev-fast-gossip` flag.

**Compatible modes:** D-LNBot (deterministic). Blocked with custom topology (m has no meaning for user-defined graphs). D-LNBot Formation works but warns — m changes both the autonomous topology structure AND propagation behavior, making it a confounding variable.

```bash
# D-LNBot chain (default)
sudo venv/bin/python3 lntest.py run active_nodes --num-msg 3
```

### injection — Botmaster Injection Points

Sweeps the number of random C&C nodes the botmaster connects to in parallel. Measures whether more injection points speed up propagation.

| Parameter | Default |
| --- | --- |
| Sweep variable | `injection_count`: 1 → 6, step 1 |
| Fixed: cc_count | 50 |
| Fixed: m | 4 |
| Messages per iteration | 10 |

Each iteration picks N random nodes (where N is the current sweep value). The `--inject` flag is blocked for this test because it would fix the injection points and defeat the purpose of the sweep.

**Compatible modes:** D-LNBot (same topology each iteration), Custom (same topology each iteration). D-LNBot Formation works but warns — the topology is rebuilt nondeterministically each iteration, confounding results.

```bash
# D-LNBot chain (default)
sudo venv/bin/python3 lntest.py run injection --num-msg 3

# Custom topology
sudo venv/bin/python3 lntest.py run injection --num-msg 3 --topology custom --topology-file topologies/ring_20.json
```

### takedown_random — Random Takedown

Builds the network, then randomly removes an increasing percentage of nodes. Measures propagation delay and coverage among survivors.

| Parameter | Default |
| --- | --- |
| Sweep variable | `takedown_percentage`: 10% → 50%, step 10% |
| Fixed: cc_count | 50 |
| Fixed: m | 4 |
| Fixed: injection | CC1 (with fallback if killed) |
| Messages per iteration | 10 |

**Compatible modes:** All three. D-LNBot tests the chain topology's resilience. D-LNBot Formation tests nondeterministic topology resilience. Custom tests the resilience of specific user-defined graphs.

```bash
# D-LNBot chain (default)
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3

# Custom topology
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --topology custom --topology-file topologies/ring_20.json
```

### takedown_targeted — Targeted Takedown

Same as `takedown_random`, but removes the highest-degree (most-connected) nodes first. This simulates informed law enforcement targeting the most critical C&C servers.

| Parameter | Default |
| --- | --- |
| Sweep variable | `takedown_percentage`: 10% → 50%, step 10% |
| Fixed: cc_count | 50 |
| Fixed: m | 4 |
| Fixed: injection | CC1 (with fallback if killed) |
| Messages per iteration | 10 |

**Note:** For D-LNBot's chain topology, middle nodes all have uniform degree (2m), so targeted takedown behaves similarly to random. For D-LNBot Formation and custom topologies with non-uniform degree distributions, targeted takedown is more meaningful — it removes hub/bridge nodes first.

**Compatible modes:** All three. D-LNBot Formation is the most interesting case since formation produces non-uniform topologies with bridge nodes.

```bash
# D-LNBot Formation (most interesting for targeted takedown)
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3 --dlnbot-formation

# Custom topology
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3 --topology custom --topology-file topologies/star_20.json
```

---

## Blocked Combinations

The following flag combinations produce a red error message and abort before the test starts.

| Condition | Error |
| --- | --- |
| `--topology-file` without `--topology custom` | `--topology-file requires --topology custom` |
| `--dlnbot-formation` with `--topology` | `--dlnbot-formation and --topology are mutually exclusive` |
| `--topology custom` without `--topology-file` | `--topology custom requires --topology-file` |
| `cc_count` or `active_nodes` with `--topology custom` | `Test is not compatible with custom topology mode` |
| `active_nodes` sweep starting at m < 2 | `active_nodes sweep must start at m >= 2` |
| Takedown sweep with `--sweep-end` > 90 | `Takedown percentage cannot exceed 90%` |
| `--inject` with `injection` sweep | `--inject cannot be used with the injection sweep` |
| `--sweep-start` > `--sweep-end` | `Sweep start is greater than sweep end` |
| `--sweep-step` <= 0 | `Sweep step must be positive` |

## Warnings

The following combinations display a red warning before the confirmation prompt. The test proceeds if you confirm.

| Condition | Warning |
| --- | --- |
| `cc_count` + `--dlnbot-formation` | Topology is nondeterministic. Results will have higher variance. Run multiple repetitions. |
| `active_nodes` + `--dlnbot-formation` | NOT a single-variable experiment. m changes both topology structure and propagation behavior. |
| `--m N` + `--dlnbot-formation` | m changes the autonomous topology structure (MAX_ACTIVE_NODES, MAX_PEERS in cc_manager). |
| `injection` + `--dlnbot-formation` | Topology is rebuilt each iteration with nondeterministic formation. Results are confounded by topology variance. |

---

## Coverage and Partition Detection

All tests track coverage and detect network partitions:

- **Coverage** = (nodes that received the command) / (surviving nodes) x 100%. Recorded per message.
- **Partition detection** — if no new node receives the command for 60 seconds, the network is declared partitioned. Partial coverage is recorded as a valid data point. Takedown tests treat partitions as expected outcomes; other tests retry.

## Takedown and Injection Interaction

If a takedown kills the injection node (e.g., CC1 is randomly selected for removal), the orchestrator:

1. Removes dead nodes from the injection list.
2. If all injection nodes are dead, falls back to the first surviving CC node (sorted numerically).
3. Logs a warning with the updated injection point.

This ensures takedown tests always have a valid injection point, even at high takedown percentages.
