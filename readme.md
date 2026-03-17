# LNTest

LNTest is a testbed for deploying and evaluating Lightning Network-based botnets. It implements the C&C overlay topology and autonomous formation protocol from [D-LNBot](https://ieeexplore.ieee.org/document/10198749/) (Kurt et al., IEEE TDSC, vol. 21, no. 4, 2024), the only LN-based botnet design in the literature. LNTest also supports custom user-defined C&C topologies, allowing researchers to evaluate how arbitrary overlay graphs behave as botnets. Each C&C node runs as a Core Lightning (CLN) instance inside its own Docker container, all connected to a single Bitcoin Core instance running on the host in regtest mode. After initial setup, all test execution is fully offline — no external network access is required.

# Table of Contents
- [Architecture](#architecture)
- [Usage](#usage)
  - [What Can You Test?](#what-can-you-test)
  - [Topology Modes](#topology-modes)
  - [Test Scenarios](#test-scenarios)
- [Further Documentation](#further-documentation)

# Architecture

LNTest consists of five components:

1. **Bitcoin Core** — runs on the host machine in regtest mode, providing the on-chain base layer for all Lightning nodes.
2. **Innocent Node** — a CLN instance in a Docker container that acts as a rendezvous point for C&C server peer discovery during network formation.
3. **Botmaster Node** — a CLN instance in a Docker container that issues commands by opening a Lightning channel to a C&C server and injecting keysend payments.
4. **C&C Server Nodes** — CLN instances, each in its own Docker container, forming a Lightning-based overlay by opening payment channels to one another. Commands propagate through this overlay via concurrent keysend flooding.
5. **Test Orchestrator (`lntest.py`)** — a Python script on the host that automates experiment setup, monitors propagation via POSIX shared memory, and records results.

Between test iterations, containers, shared memory, and node data are cleaned up automatically. Logs are preserved for debugging and cleared only during a full cleanup (`cleanup.sh all`). Bitcoin Core's regtest environment resets at the start of each test run; within a run, the same bitcoind instance is reused across iterations for efficiency.


# Usage

Follow the [setup instructions](docs/SETUP.md), then verify everything works with the sanity check (4 nodes, 1 message, takes a few minutes):

```bash
sudo venv/bin/python3 lntest.py small
```

Run experiments with:

```bash
sudo venv/bin/python3 lntest.py run <test> [options]
```

---

## What Can You Test?

LNTest provides five test scenarios grouped into three categories:

**Scalability** (D-LNBot topologies only) — How does command propagation change as the botnet grows?
- `cc_count` — Scale the number of C&C nodes (default 10 to 100)
- `active_nodes` — Vary the number of active C&C servers (*m*), which controls how many neighbors each node maintains in the chain

**Botmaster injection** — Does the number of injection points matter?
- `injection` — Sweep the number of random C&C nodes the botmaster connects to (default 1 to 6)

**Resilience to takedowns** — How does the botnet degrade when nodes are removed?
- `takedown_random` — Remove a random percentage of C&C nodes
- `takedown_targeted` — Remove the highest-degree nodes first (simulating informed law enforcement)

The `--inject` flag controls where the botmaster connects. It accepts explicit node IDs (e.g., `--inject CC5,CC12`). When multiple injection points are specified, the botmaster sends to all of them in parallel. Default is CC1 (deterministic); the `injection` sweep test uses random selection when `--inject` is omitted. See [docs/TESTS.md](docs/TESTS.md) for details.

Custom topologies support the injection and takedown tests. The scalability tests (`cc_count`, `active_nodes`) are D-LNBot-specific. All default ranges can be adjusted with `--sweep-start`, `--sweep-end`, and `--sweep-step`.

---

## Topology Modes

The C&C overlay topology is controlled via flags. See [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) for detailed descriptions, JSON format, and examples.

| Mode | Flag | Description |
| --- | --- | --- |
| D-LNBot (default) | `--topology dlnbot` | Chain-like C&C overlay |
| D-LNBot Formation | `--dlnbot-formation` | Autonomous staggered deployment |
| Custom | `--topology custom --topology-file <path>` | User-supplied JSON graph |

---

## Test Scenarios

Each test sweeps a different experimental variable. All ranges are adjustable with `--sweep-start`, `--sweep-end`, and `--sweep-step`. See [docs/TESTS.md](docs/TESTS.md) for detailed descriptions.

| Test Name | Description | Default Range |
| --- | --- | --- |
| `cc_count` | Scale C&C nodes | 10 to 100, step 10 |
| `active_nodes` | Scale active C&C servers (m) | 2 to 6, step 1 |
| `injection` | Botmaster injection points | 1 to 6, step 1 |
| `takedown_random` | Random takedown sweep | 10% to 50%, step 10% |
| `takedown_targeted` | Targeted takedown sweep | 10% to 50%, step 10% |

---

## Output

Test results are saved to the `data/` directory. See [docs/OUTPUT.md](docs/OUTPUT.md) for file formats and naming conventions.


# Further Documentation

- [docs/SETUP.md](docs/SETUP.md) — Installation and configuration
- [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) — Topology modes, JSON format, and comparison
- [docs/TESTS.md](docs/TESTS.md) — Test scenario details, coverage, and partition detection
- [docs/OUTPUT.md](docs/OUTPUT.md) — Generated data structure and file formats
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common issues and fixes
