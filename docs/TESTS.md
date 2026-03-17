# Test Scenarios

## Tests

| Test Name | Description | Default Range |
| --- | --- | --- |
| `cc_count` | Scale C&C nodes | 10 to 100, step 10 |
| `active_nodes` | Scale active C&C servers (m) | 2 to 6, step 1 |
| `injection` | Botmaster injection points | 1 to 6, step 1 |
| `takedown_random` | Random takedown sweep | 10% to 50%, step 10% |
| `takedown_targeted` | Targeted takedown sweep | 10% to 50%, step 10% |

## Botmaster Injection (`--inject`)

The `--inject` flag controls where the botmaster connects to inject commands. When multiple injection points are specified, the botmaster sends to all of them simultaneously (in parallel), starting propagation from multiple locations in the network.

**Default behavior:** When `--inject` is not specified, the botmaster injects into **CC1** (deterministic). This ensures sweep tests change only one variable at a time. For the `injection` sweep test, random nodes are used instead (the sweep variable is the injection count itself).

**Syntax:**

```bash
--inject CC5,CC12,CC30    # Explicit node IDs
--inject %50              # 50th percentile node (resolved per iteration)
--inject %0,%50,%100      # Three positions: start, middle, end
```

- **Explicit IDs** (`CC5,CC12,CC30`): Botmaster connects to exactly these nodes. Validated per iteration — errors if a node doesn't exist.
- **Percentage positions** (`%50`): Resolved dynamically based on current CC_COUNT. `%0` = CC1, `%50` = middle node, `%100` = last node. Useful for `cc_count` sweeps where the network size changes each iteration.
- **No `--inject`**: Defaults to CC1 for all non-injection sweep tests. The `injection` test uses random selection.

**When to use each:**

| Scenario | Recommendation |
| --- | --- |
| `cc_count` sweep (network size changes) | Omit (defaults to CC1) or use `%N` for consistent relative position |
| D-LNBot with fixed CC_COUNT | Omit (defaults to CC1) or use explicit IDs for specific positions |
| D-LNBot Formation | Omit (defaults to CC1) or use explicit IDs — CC number does not map to topological position |
| Custom topology | Omit (defaults to CC1) or use explicit IDs based on your topology structure |
| `injection` sweep | Omit (uses random selection) — this is the intended behavior for measuring injection count effects |

## Test Details

**cc_count (Scale C&C nodes):** Measures command propagation delay as the number of C&C servers increases from 10 to 100 (default m=4, injection at CC1).

**active_nodes (Scale active nodes):** Varies the number of active C&C servers (m) from 2 to 6 with a fixed 50-node network to study how overlay width affects propagation (injection at CC1). Starts at m=2 because m=1 topologies fragment under `--dev-fast-gossip`.

**injection (Botmaster injection points):** Sweeps the number of random injection points from 1 to 6. The botmaster sends to all injection points in parallel. Measures whether more injection points improve propagation speed and coverage.

**takedown_random (Random takedown):** Builds a 50-node network (m=4), then randomly removes an increasing percentage of nodes (10% to 50%) and measures propagation delay and coverage among surviving nodes (injection at CC1).

**takedown_targeted (Targeted takedown):** Same as takedown_random, but removes the highest-degree (most-connected) nodes first. This simulates a law enforcement strategy that targets the most critical C&C servers (injection at CC1).

The injection and takedown tests work with all topology modes. The scalability tests (`cc_count`, `active_nodes`) are D-LNBot-specific and cannot be used with custom topologies.

## Takedown and Injection Interaction

If an injection node is killed during takedown, it is automatically removed from the injection list. If all injection nodes are killed, the first surviving CC node is used as a fallback. This is logged as a warning.

## Coverage and Partition Detection

The takedown tests include coverage tracking and partition detection:
* **Coverage** — the fraction of surviving nodes that successfully receive the command.
* **Partition detection** — if no new node receives the command for 60 seconds, the test declares a network partition and records partial coverage as a valid data point instead of retrying.
