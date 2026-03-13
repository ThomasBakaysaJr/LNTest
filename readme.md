# LNTest

A testbed for designing, deploying, and evaluating Lightning Network-based botnets. Built with production-grade Bitcoin Core and Core Lightning nodes orchestrated via Docker on a Bitcoin regtest environment, LNTest runs entirely on a single host machine with no external network dependencies.

# Table of Contents
- [Architecture](#architecture)
- [Usage](#usage)
  - [Examples](#examples)
  - [Test Scenarios](#test-scenarios)
  - [Topology Modes](#topology-modes)
- [Further Documentation](#further-documentation)

# Architecture

LNTest consists of five components:

1. **Bitcoin Core** — runs on the host machine in regtest mode, providing the on-chain base layer for all Lightning nodes.
2. **Innocent Node** — a CLN instance in a Docker container that acts as a rendezvous point for C&C server peer discovery during network formation.
3. **Botmaster Node** — a CLN instance in a Docker container that issues commands by opening a temporary Lightning channel to a C&C server and injecting keysend payments.
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

## Examples

Measure how command propagation delay grows as the botnet scales from 10 to 100 C&C nodes:

```bash
sudo venv/bin/python3 lntest.py run cc_count --num-msg 3
```

Simulate law enforcement taking down the most-connected nodes and measure how the botnet degrades:

```bash
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3
```

Test the same takedown scenario with autonomous botnet formation instead of an orchestrator-built topology:

```bash
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3 --dlnbot-formation
```

Run a random takedown on a custom ring topology:

```bash
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --cc-count 20 --topology custom --topology-file topologies/ring_20.json
```

---

## Test Scenarios

Each test sweeps a different experimental variable. See [docs/TESTS.md](docs/TESTS.md) for detailed descriptions.

| Test Name | Description | Default Range |
| --- | --- | --- |
| `cc_count` | Scale C&C nodes | 10 to 100, step 10 |
| `active_nodes` | Scale active C&C servers (m) | 2 to 6, step 1 |
| `bm_seeds` | Botmaster connectivity | 1 to 6, step 1 |
| `bm_pos` | Botmaster injection point | -50 to 150, step 50 |
| `takedown_random` | Random takedown sweep | 10% to 50%, step 10% |
| `takedown_targeted` | Targeted takedown sweep | 10% to 50%, step 10% |

---

## Topology Modes

The C&C overlay topology is controlled via flags. See [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) for detailed descriptions, JSON format, and examples.

| Mode | Flag | Description |
| --- | --- | --- |
| D-LNBot (default) | `--topology dlnbot` | Orchestrator-built uniform chain |
| D-LNBot Formation | `--dlnbot-formation` | Autonomous staggered deployment |
| Custom | `--topology custom --topology-file <path>` | User-supplied JSON graph |

---

## Output

Test results are saved to the `data/` directory. See [docs/OUTPUT.md](docs/OUTPUT.md) for file formats and naming conventions.


# Further Documentation

- [docs/SETUP.md](docs/SETUP.md) — Installation and configuration
- [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) — Topology modes, JSON format, and comparison
- [docs/TESTS.md](docs/TESTS.md) — Test scenario details, coverage, and partition detection
- [docs/OUTPUT.md](docs/OUTPUT.md) — Generated data structure and file formats
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common issues and fixes
