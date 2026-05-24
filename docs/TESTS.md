# Tests

LNTest runs five tests. Each sweeps a single experimental variable while holding everything else fixed:

```bash
sudo venv/bin/python3 lntest.py run <test> [options]
```

## The five tests

| Test | Sweeps | Fixed | Default range |
| --- | --- | --- | --- |
| `cc_count` | botnet size *n* | m=4, inject=middle | 10 → 100, step 10 |
| `active_nodes` | overlay width *m* | n=50, inject=middle | 2 → 6, step 1 |
| `injection` | # of botmaster injection points | n=50, m=4 | 1 → 6, step 1 |
| `takedown_random` | % of nodes removed (random) | n=50, m=4, inject=middle | 10% → 50%, step 10% |
| `takedown_targeted` | % of nodes removed (highest-degree first) | n=50, m=4, inject=middle | 10% → 50%, step 10% |

- **Scalability** (`cc_count`, `active_nodes`) — how propagation delay scales with botnet size and overlay width. Delay grows ~linearly with *n*; *m* has little effect, because parallel forwarding advances the wavefront one hop per step regardless of width. D-LNBot-specific.
- **Injection** (`injection`) — whether more parallel entry points speed propagation. Each iteration picks *N* random C&C nodes **once**, and the botmaster injects from all of them for every message in that iteration.
- **Resilience** (`takedown_random`, `takedown_targeted`) — how coverage degrades as nodes are removed. The overlay is built once and nodes are removed cumulatively across the sweep. On the uniform-degree D-LNBot chain, targeted ≈ random; on non-uniform topologies (formation, custom) targeted removes hubs/bridges first and bites harder.

## CLI flags

| Flag | Description |
| --- | --- |
| `--nodes N` | Fixed botnet size (ignored when `cc_count` is the swept variable) |
| `--m N` | Fixed overlay width (ignored when `active_nodes` is the swept variable) |
| `--num-msg N` | Messages sent per sweep iteration |
| `--sweep-start N` / `--sweep-end N` / `--sweep-step N` | Override the swept variable's range |
| `--inject CC5,CC12` | Explicit injection point(s); the botmaster injects from all in parallel |
| `--topology dlnbot` | D-LNBot chain (default) |
| `--topology custom --topology-file PATH` | User-supplied JSON overlay |
| `--dlnbot-formation` | Autonomous staggered formation (mutually exclusive with `--topology`) |

## Topology modes & compatibility

See [TOPOLOGIES.md](TOPOLOGIES.md) for what each mode builds.

| Test | dlnbot | dlnbot-formation | custom |
| --- | --- | --- | --- |
| `cc_count` | ✅ | ⚠️ | ❌ |
| `active_nodes` | ✅ | ⚠️ | ❌ |
| `injection` | ✅ | ⚠️ | ✅ |
| `takedown_random` | ✅ | ✅ | ✅ |
| `takedown_targeted` | ✅ | ✅ | ✅ |

✅ clean single-variable experiment · ⚠️ runs but prints a red warning — formation rebuilds a fresh, nondeterministic overlay each iteration, so results have higher variance (and for `active_nodes`, *m* changes both the formed topology and propagation) · ❌ blocked, because `cc_count`/`active_nodes` must vary *n*/*m*, which a fixed custom file can't provide.

**Blocked combinations** (abort with a red error before the run): `--topology-file` without `--topology custom`; `--dlnbot-formation` together with `--topology`; `--topology custom` without `--topology-file`; `cc_count`/`active_nodes` with `--topology custom`; `active_nodes` starting at m < 1; takedown `--sweep-end` > 90; `--inject` with the `injection` sweep; `--sweep-start` > `--sweep-end`; `--sweep-step` ≤ 0.

## Injection points (`--inject`)

`--inject` sets where the botmaster injects, e.g. `--inject CC5,CC12,CC30` (all in parallel). Default is the **middle node** (`CC⌈n/2⌉`) for every test except `injection`, which picks *N* random nodes per iteration (and rejects `--inject`, since that sweep varies the *count*). The middle is the default because the chain *end* gets orphaned into a tiny fragment under targeted takedown. In takedown tests, if the injection node is removed, the orchestrator falls back to the lowest-numbered survivor.

**m=1 note:** `active_nodes` defaults to start at m=2 but allows m=1 via `--sweep-start 1`. On the dlnbot chain m=1 is a connected line (single-path, so fragile to a dropped keysend); under formation it may fragment, which is recorded as a partition.

## Coverage & partition detection

- **Coverage** = (nodes that received the command) / (surviving nodes), recorded per message. Propagation delay is measured from the instant the botmaster's keysend leaves until the last surviving node receives it — setup and channel-open costs are excluded.
- **Partition** — if no new node receives a command for 60 s, the network is declared partitioned and the partial coverage is recorded. Takedown tests treat partitions as expected outcomes; other tests retry the iteration.

## Examples

```bash
# Scalability on the default D-LNBot chain
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3

# Custom sweep range
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3 --sweep-start 20 --sweep-end 40 --sweep-step 10

# Injection sweep on a custom overlay
sudo venv/bin/python3 lntest.py run injection --num-msg 3 --topology custom --topology-file topologies/ring_20.json

# Targeted takedown on an autonomously-formed (non-uniform) overlay
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3 --dlnbot-formation
```
