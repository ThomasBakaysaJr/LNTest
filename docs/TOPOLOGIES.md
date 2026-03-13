# Topology Modes

LNTest supports three modes that control how the C&C overlay network is formed. This is one of the most important experimental parameters, as it directly affects the resulting topology and its resilience to takedown attacks.

## D-LNBot Topology (default)

```bash
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3
# or explicitly:
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --topology dlnbot
```

Reproduces the exact topology described in the D-LNBot paper. cc_manager is disabled at startup via the `SKIP_CC_MANAGER` environment variable. After all nodes are created, the orchestrator builds the chain explicitly using CLN's `multifundchannel` command: CC_i opens channels to CC_{max(1, i-m)} through CC_{i-1}. This produces a **uniform chain topology** where middle nodes have exactly 2m channels, and edge nodes ramp up/down. Each channel is funded with `push_msat` to enable bidirectional message forwarding.

## D-LNBot Formation

```bash
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --dlnbot-formation
```

Simulates a realistic D-LNBot deployment where C&C servers join the network autonomously. Containers are launched with staggered delays drawn from a log-normal distribution (median ~30s on regtest), modeling the variable LN setup time in D-LNBot's malware pipeline: downloading an LN light client, syncing with the blockchain, fetching funding from a pre-funded wallet, and opening+confirming channels. Each node runs cc_manager, which discovers peers via the innocent node and opens channels autonomously. The staggering ensures gossip has time to propagate between node arrivals. This mode produces a **clustered chain topology** — groups of fully-connected cliques linked by bridge nodes — which differs from both the idealized D-LNBot chain and a hub-and-spoke topology. The `--dlnbot-formation` flag is mutually exclusive with `--topology`.

## Custom

```bash
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --topology custom --topology-file topologies/ring_20.json
```

Lets researchers supply any arbitrary topology as a JSON file. cc_manager is disabled, and the orchestrator builds the exact graph specified. This enables testing with real-world LN snapshots, random graphs, scale-free networks, or any theoretical model on real CLN nodes.

### JSON Format

```json
{
  "nodes": 20,
  "edges": [
    [2, 1],
    [3, 1],
    [3, 2],
    [4, 2],
    [4, 3]
  ]
}
```

Each `[from, to]` means CC{from} opens a channel to CC{to}. Node numbers are 1-indexed (CC1, CC2, ..., CCn). The `nodes` field determines how many C&C containers are created (there is no need to pass `--cc-count` separately). Edges are directed (the opener holds initial balance), but `push_msat` ensures bidirectional forwarding capability.

LNTest validates the topology file and warns about self-loops, duplicate edges, out-of-range nodes, and disconnected subgraphs. Disconnected graphs are allowed (some nodes will simply not receive commands), enabling partition experiments.

### Example Topologies

Several example topology files are included in the `topologies/` directory: `ring_20.json` (simple ring), `star_20.json` (single hub), `chain_20_m4.json` (equivalent to `--topology dlnbot` with 20 nodes and m=4), and `ba_50_m4.json` (Barabási–Albert scale-free graph with 50 nodes).

## Comparison

The three modes produce meaningfully different topologies and resilience profiles:

| Mode | Topology Shape | Formation | Vulnerability |
| --- | --- | --- | --- |
| `--topology dlnbot` | Uniform chain | Orchestrator-built | Degrades gradually, no single point of failure |
| `--dlnbot-formation` | Clustered chain with bridges | Autonomous (staggered) | Bridge nodes are critical — removing them partitions the network |
| `--topology custom` | User-defined | Orchestrator-built | Depends on the supplied graph |
