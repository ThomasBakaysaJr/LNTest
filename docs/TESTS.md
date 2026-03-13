# Test Scenarios

## Tests

| Test Name | Description | Default Range |
| --- | --- | --- |
| `cc_count` | Scale C&C nodes | 10 to 100, step 10 |
| `active_nodes` | Scale active C&C servers (m) | 2 to 6, step 1 |
| `bm_seeds` | Botmaster connectivity | 1 to 6, step 1 |
| `bm_pos` | Botmaster injection point | -50 to 150, step 50 |
| `takedown_random` | Random takedown sweep | 10% to 50%, step 10% |
| `takedown_targeted` | Targeted takedown sweep | 10% to 50%, step 10% |

## Test Details

**cc_count (Scale C&C nodes):** Measures command propagation delay as the number of C&C servers increases from 10 to 100 (default m=4, botmaster at 50%).

**active_nodes (Scale active nodes):** Varies the number of active C&C servers (m) from 2 to 6 with a fixed 50-node network to study how overlay width affects propagation. Starts at m=2 because m=1 topologies fragment under `--dev-fast-gossip`.

**bm_seeds (Botmaster connectivity):** Varies how many C&C servers the botmaster opens channels to simultaneously.

**bm_pos (Botmaster injection point):** Tests five injection positions:
  * `-50` — Random position
  * `0` — Oldest nodes (beginning of the network)
  * `50` — Middle of the network
  * `100` — Youngest nodes (end of the network)
  * `150` — Multi-point injection (connects at 0%, 50%, and 100% simultaneously)

**takedown_random (Random takedown):** Builds a 50-node network (m=4), then randomly removes an increasing percentage of nodes (10% to 50%) and measures propagation delay and coverage among surviving nodes.

**takedown_targeted (Targeted takedown):** Same as takedown_random, but removes the highest-degree (most-connected) nodes first. This simulates a law enforcement strategy that targets the most critical C&C servers.

All tests can be run with any topology mode via `--topology dlnbot` (default), `--dlnbot-formation`, or `--topology custom --topology-file <path>`. Running the same test on different modes produces directly comparable results that show how overlay structure affects botnet resilience.

## Coverage and Partition Detection

The takedown tests include coverage tracking and partition detection:
* **Coverage** — the fraction of surviving nodes that successfully receive the command.
* **Partition detection** — if no new node receives the command for 60 seconds, the test declares a network partition and records partial coverage as a valid data point instead of retrying.
