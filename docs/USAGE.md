# Usage

LNTest runs five tests, each sweeping one variable while holding the rest fixed:

```bash
sudo venv/bin/python3 lntest.py run <test> [options]
```

## Tests

| Test | Sweeps | Held fixed | Default sweep |
| --- | --- | --- | --- |
| `cc_count` | botnet size *n* | m=4 | 10 → 100, step 10 |
| `active_nodes` | overlay width *m* | n=50 | 2 → 6, step 1 |
| `injection` | botmaster entry points | n=50, m=4 | 1 → 6, step 1 |
| `takedown_random` | % of nodes removed, random | n=50, m=4 | 10% → 50%, step 10% |
| `takedown_targeted` | % of nodes removed, highest-degree first | n=50, m=4 | 10% → 50%, step 10% |

`cc_count` and `active_nodes` test **scalability** (does delay grow with botnet size or overlay width?), `injection` tests whether **more entry points** speed propagation, and the `takedown` tests measure **resilience** (how coverage drops as nodes are removed). Removal order is reproducible: `takedown_random` uses a fixed seed (`TAKEDOWN_SEED` in `config.env`), and `takedown_targeted` always removes the highest-degree nodes first.

## Overlay topology

The overlay is the C&C network the command floods across. Choose how it is built with `--topology`:

| Mode | Select with | What you get |
| --- | --- | --- |
| **dlnbot** (default) | `--topology dlnbot` | The D-LNBot chain: a uniform line where each node connects to up to *m* neighbors on each side. Deterministic. |
| **autonomous** | `--topology autonomous` | Nodes start at staggered (log-normal, ~30 s) times and find peers on their own through the innocent node. Realistic but nondeterministic, so repeat runs for statistics. |
| **custom** | `--topology-file PATH` | Any graph you supply as JSON (passing a file implies this mode). For real LN snapshots, random graphs, or scale-free models. |

Not every test fits every mode:

| Test | dlnbot | autonomous | custom |
| --- | --- | --- | --- |
| `cc_count`, `active_nodes` | ✅ | ⚠️ | ❌ |
| `injection` | ✅ | ⚠️ | ✅ |
| `takedown_random`, `takedown_targeted` | ✅ | ⚠️ | ✅ |

- ✅ clean single-variable run.
- ⚠️ runs, but autonomous formation is nondeterministic, so repeat it for statistics.
- ❌ blocked: a custom file fixes *n*, and *m* applies only to the D-LNBot chain.

### Custom topology file

```json
{
  "nodes": 20,
  "edges": [[2, 1], [3, 1], [3, 2], [4, 2], ...]
}
```

Each `[from, to]` opens a channel from CC{from} to CC{to} (nodes are 1-indexed; each channel is funded so either end can forward). `nodes` sets how many C&C containers start, so you don't also pass `--nodes`. LNTest warns about self-loops, duplicate or out-of-range edges, and disconnected graphs, though a disconnected graph is allowed for partition experiments. Ready-made files are in `topologies/`: `ring_20.json`, `star_20.json`, `chain_20_m4.json`, and `ba_50_m4.json` (a Barabási–Albert scale-free graph).

## Options

| Flag | Description |
| --- | --- |
| `--at N` | Run a single value of the swept variable instead of the whole sweep |
| `--sweep-start` / `--sweep-end` / `--sweep-step N` | Set the sweep range (uniform spacing) |
| `--sweep-values "10,20,50,100"` | Sweep an explicit list of values (used for the paper's n=10 to 500 run) |
| `--nodes N` | Fix botnet size, when not sweeping `cc_count` |
| `--m N` | Fix overlay width, when not sweeping `active_nodes` |
| `--num-msg N` | Messages sent per sweep point (default 10) |
| `--inject CC5,CC12` | Inject from specific nodes; the botmaster opens a channel to each |
| `--topology` / `--topology-file` | Overlay mode (see above) |

The orchestrator checks your options first and stops with a clear message on combinations that can't work, such as a custom topology with `cc_count`/`active_nodes`, `--at` mixed with `--sweep-*`, or a takedown above 90%.

**Where the botmaster injects:** without `--inject`, each test chooses a default and re-resolves it every iteration. Takedown tests inject from the **most-connected surviving node** (maximum reach), `cc_count` and `active_nodes` use the **middle node** of the chain, and the `injection` sweep picks **random** nodes (it varies the count, so it can't be combined with `--inject`).

## Reading the result

For each message LNTest records its **coverage** (fraction of surviving nodes that received the command) and **propagation delay** (from the botmaster's keysend to the last node receiving it). If no new node receives a command for 60 seconds, that point is marked **partitioned**: takedown and autonomous runs keep it as a valid result, while `dlnbot` and `custom` retry. The output files are described in [OUTPUT.md](OUTPUT.md).

## Examples

```bash
# Scalability on the default chain
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3

# The full paper scalability curve (n=10 to 500) in one run
sudo venv/bin/python3 lntest.py run cc_count --sweep-values "10,20,30,40,50,60,70,80,90,100,200,300,400,500" --num-msg 10

# A single network size, no sweep
sudo venv/bin/python3 lntest.py run cc_count --at 50 --num-msg 3

# Injection sweep on a custom ring
sudo venv/bin/python3 lntest.py run injection --topology-file topologies/ring_20.json --num-msg 3

# Targeted takedown on a self-formed overlay
sudo venv/bin/python3 lntest.py run takedown_targeted --topology autonomous --num-msg 3
```
