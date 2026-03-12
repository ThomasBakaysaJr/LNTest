# LNTest

A testbed for designing, deploying, and evaluating Lightning Network-based botnets. Built with production-grade Bitcoin Core and Core Lightning nodes orchestrated via Docker on a Bitcoin regtest environment, LNTest runs entirely on a single host machine with no external network dependencies.

# Table of Contents
- [LNTest](#lntest)
- [Table of Contents](#table-of-contents)
- [Architecture](#architecture)
  - [Generated Data \& Output](#generated-data--output)
- [Setup](#setup)
- [Usage](#usage)
  - [Modes of Operation](#modes-of-operation)
  - [Topology Modes](#topology-modes)
  - [Test Scenarios (Test IDs)](#test-scenarios-test-ids)
  - [CLI Flags](#cli-flags)
  - [Examples](#examples)
- [Utility Scripts](#utility-scripts)
- [Troubleshooting](#troubleshooting)

# Architecture

LNTest consists of five components:

1. **Bitcoin Core** — runs on the host machine in regtest mode, providing the on-chain base layer for all Lightning nodes.
2. **Innocent Node** — a CLN instance in a Docker container that acts as a rendezvous point for C&C server peer discovery during network formation.
3. **Botmaster Node** — a CLN instance in a Docker container that issues commands by opening a temporary Lightning channel to a C&C server and injecting keysend payments.
4. **C&C Server Nodes** — CLN instances, each in its own Docker container, forming a Lightning-based overlay by opening payment channels to one another. Commands propagate through this overlay via concurrent keysend flooding. The overlay topology can be built as D-LNBot's intended chain (`--topology dlnbot`, default), formed autonomously with staggered launches simulating realistic malware deployment (`--dlnbot-formation`), or loaded from a user-supplied JSON file (`--topology custom`).
5. **Test Orchestrator (`lntest.py`)** — a Python script on the host that automates experiment setup, monitors propagation via POSIX shared memory, and records results.

Each test automatically resets the Bitcoin regtest environment with a fresh wallet. All nodes and their associated resources (containers, shared memory, logs) are cleaned up between test iterations to minimize cross-test interference.

## Generated Data & Output

All experimental data is saved to the `data/` directory. The testbed generates distinct files for each test configuration based on parameter values, so different configurations never overwrite each other. However, re-running the exact same configuration will overwrite previously generated data for that configuration.

### Data Structure
Filenames follow the convention: `data/<variable>_<value>_<unique_id>_<type>`.

For takedown tests, filenames include a strategy marker: `T` for random takedown, `Ttargeted` for targeted takedown. Mode suffixes indicate the topology mode: `D` for dlnbot, `F` for dlnbot-formation, `X` for custom (e.g., `TD` for random takedown on dlnbot topology).

* **Propagation Data** (`*_time_data.json`)
  * Contains the time it took for each message to propagate through the network.
  * Includes metadata about the test configuration (number of C&C nodes, active nodes, etc.).
  * Records total setup time and total message sending time.
  * For takedown tests, also records coverage percentage, number of nodes that received the message, total surviving nodes, and whether the network partitioned.

* **Topology Snapshots** (`*_topology_data.json`)
  * Captures the state of the Lightning Network at the end of each test iteration.
  * Lists every surviving node, its channels, channel capacity, and connection status.
  * For takedown tests, the metadata includes which nodes were removed and their channel details at the time of removal.

* **System Metrics** (`*_system_metrics.csv`)
  * Logs CPU and RAM usage of the host machine throughout the test.
  * Useful for analyzing the hardware overhead of running many Docker containers.

* **Execution Logs** (`*_total_times_log.json`)
  * A running log of how long each test run took to execute.
  * Timestamped by date (e.g., `YYYY-MM-DD_total_times_log.json`).

* **Orchestrator Log** (`orchestrator.log`)
  * Detailed log of orchestrator decisions, warnings, and errors with timestamps and log levels.

### Logs
Detailed logs for debugging specific node behaviors are stored in:
* `cc_node/logs/` — individual logs for every C&C node.
* `botmaster/` — logs for the Botmaster node.


# Setup

This testbed has been verified on Ubuntu 24.04 LTS and Ubuntu 25.04. The instructions below assume a fresh Ubuntu install.

## 1. Install system dependencies

Update your system and install required packages:

```bash
sudo apt update && sudo apt upgrade
sudo apt install python3-venv git -y
```

Install Docker following the official guide for Ubuntu: [https://docs.docker.com/engine/install/ubuntu/](https://docs.docker.com/engine/install/ubuntu/)

```bash
# Add Docker's official GPG key:
sudo apt install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Verify Docker is running:

```bash
sudo systemctl status docker
```

## 2. Install Bitcoin Core

Create a working directory, download Bitcoin Core from [https://bitcoincore.org/en/download/](https://bitcoincore.org/en/download/), and extract it:

```bash
mkdir -p ~/Documents/LNBot_research_project
cd ~/Documents/LNBot_research_project
# Move the downloaded tar file here, then:
tar -xvzf bitcoin-*
mv bitcoin-*/ bitcoin
rm bitcoin-*.tar.gz
```

Do not run Bitcoin Core yet.

## 3. Clone and configure LNTest

```bash
cd ~/Documents/LNBot_research_project
git clone https://github.com/LN-Testbed/DSN2026.git LNTest
cd LNTest
chmod +x setup.sh scripts/*.sh
./setup.sh
```

The setup script will:
* Check dependencies (Docker, Python, jq)
* Create the Python virtual environment and install packages
* Create config files in `~/.lightning` and `~/.bitcoin`
* Prompt for RPC username and password

You can verify the RPC credentials by checking `config.env` and the config files in `~/.bitcoin` and `~/.lightning`.

Note: `lntest.py` runs with `sudo` (required for Docker and shared memory management), so `config.env` uses absolute paths.

## 4. Verify the installation

```bash
sudo venv/bin/python3 lntest.py small
```

This runs a quick sanity check (4 nodes, 1 message, 1 iteration) to verify everything is set up correctly. Data is saved to the `data/` directory.

**Tip:** To save terminal output to a file while still seeing it live:

```bash
sudo venv/bin/python3 lntest.py run 1 --num-msg 3 2>&1 | tee /tmp/test_output.log
```


# Usage

```bash
sudo venv/bin/python3 lntest.py <command> [options]
```

## Modes of Operation

### Sanity Check (`small`)

Quick end-to-end verification: creates 4 nodes, builds a minimal topology, and sends 1 message. Takes a few minutes.

```bash
sudo venv/bin/python3 lntest.py small
```

### Run a Test (`run`)

Runs a specific test scenario with full control over variable ranges and steps.

```bash
sudo venv/bin/python3 lntest.py run <TEST_ID> [options]
```

---

## Topology Modes

LNTest supports three modes that control how the C&C overlay network is formed. This is one of the most important experimental parameters, as it directly affects the resulting topology and its resilience to takedown attacks.

### D-LNBot Topology (default)

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3
# or explicitly:
sudo venv/bin/python3 lntest.py run 5 --num-msg 3 --topology dlnbot
```

Reproduces the exact topology described in the D-LNBot paper. cc_manager is disabled at startup via the `SKIP_CC_MANAGER` environment variable. After all nodes are created, the orchestrator builds the chain explicitly using CLN's `multifundchannel` command: CC_i opens channels to CC_{max(1, i-m)} through CC_{i-1}. This produces a **uniform chain topology** where middle nodes have exactly 2*N_active channels, and edge nodes ramp up/down. Each channel is funded with `push_msat` to enable bidirectional message forwarding.

### D-LNBot Formation

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3 --dlnbot-formation
```

Simulates a realistic D-LNBot deployment where C&C servers join the network autonomously. Containers are launched with staggered delays drawn from a log-normal distribution (median ~30s on regtest), modeling the variable LN setup time in D-LNBot's malware pipeline: downloading an LN light client, syncing with the blockchain, fetching funding from a pre-funded wallet, and opening+confirming channels. Each node runs cc_manager, which discovers peers via the innocent node and opens channels autonomously. The staggering ensures gossip has time to propagate between node arrivals. This mode produces a **clustered chain topology** — groups of fully-connected cliques linked by bridge nodes — which differs from both the idealized D-LNBot chain and a hub-and-spoke topology. The `--dlnbot-formation` flag is mutually exclusive with `--topology`.

### Custom

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3 --topology custom --topology-file topologies/ring_20.json
```

Lets researchers supply any arbitrary topology as a JSON file. cc_manager is disabled, and the orchestrator builds the exact graph specified. This enables testing with real-world LN snapshots, random graphs, scale-free networks, or any theoretical model on real CLN nodes.

#### JSON Format

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

Each `[from, to]` means CC{from} opens a channel to CC{to}. Node numbers are 1-indexed (CC1, CC2, ..., CCn). The `nodes` field should match the `--num-cc` value. Edges are directed (the opener holds initial balance), but `push_msat` ensures bidirectional forwarding capability.

LNTest validates the topology file and warns about self-loops, duplicate edges, out-of-range nodes, and disconnected subgraphs. Disconnected graphs are allowed (some nodes will simply not receive commands), enabling partition experiments.

#### Example Topologies

Several example topology files are included in the `topologies/` directory: `ring_20.json` (simple ring), `star_20.json` (single hub), and `chain_20_m4.json` (equivalent to `--topology dlnbot` with 20 nodes and N_active=4).

### Comparison

The three modes produce meaningfully different topologies and resilience profiles:

| Mode | Topology Shape | Formation | Vulnerability |
| --- | --- | --- | --- |
| `--topology dlnbot` | Uniform chain | Orchestrator-built | Degrades gradually, no single point of failure |
| `--dlnbot-formation` | Clustered chain with bridges | Autonomous (staggered) | Bridge nodes are critical — removing them partitions the network |
| `--topology custom` | User-defined | Orchestrator-built | Depends on the supplied graph |

---

## Test Scenarios (Test IDs)

| ID | Description | Variable Tested | Default Range |
| --- | --- | --- | --- |
| **1** | Scale C&C nodes | `num_cc` | 10 to 100, step 10 |
| **2** | Scale active nodes (N_active) | `active_nodes` | 2 to 6, step 1 |
| **3** | Botmaster connectivity | `bm_seeds` | 1 to 6, step 1 |
| **4** | Botmaster injection point | `bm_pos` | -50 to 150, step 50 |
| **5** | Random takedown sweep | `takedown_pct` | 10% to 50%, step 10% |
| **6** | Targeted takedown sweep | `takedown_pct` | 10% to 50%, step 10% |

### Test Details

**Test 1 (Scale C&C nodes):** Measures command propagation delay as the number of C&C servers increases from 10 to 100 (default N_active=4, botmaster at 50%).

**Test 2 (Scale active nodes):** Varies the active neighbor limit N_active from 2 to 6 with a fixed 50-node network to study how overlay width affects propagation. Starts at N_active=2 because N_active=1 topologies fragment under `--dev-fast-gossip`.

**Test 3 (Botmaster connectivity):** Varies how many C&C servers the botmaster opens channels to simultaneously.

**Test 4 (Botmaster injection point):** Tests five injection positions:
  * `-50` — Random position
  * `0` — Oldest nodes (beginning of the network)
  * `50` — Middle of the network
  * `100` — Youngest nodes (end of the network)
  * `150` — Multi-point injection (connects at 0%, 50%, and 100% simultaneously)

**Test 5 (Random takedown):** Builds a 50-node network (N_active=4), then randomly removes an increasing percentage of nodes (10% to 50%) and measures propagation delay and coverage among surviving nodes.

**Test 6 (Targeted takedown):** Same as Test 5, but removes the highest-degree (most-connected) nodes first. This simulates a law enforcement strategy that targets the most critical C&C servers.

All tests can be run with any topology mode via `--topology dlnbot` (default), `--dlnbot-formation`, or `--topology custom --topology-file <path>`. Running the same test on different modes produces directly comparable results that show how overlay structure affects botnet resilience.

### Coverage and Partition Detection

Tests 5 and 6 include coverage tracking and partition detection:
* **Coverage** — the fraction of surviving nodes that successfully receive the command.
* **Partition detection** — if no new node receives the command for 60 seconds, the test declares a network partition and records partial coverage as a valid data point instead of retrying.

---

## CLI Flags

Override default parameters for the `run` mode:

### Topology Parameters

* `--num-cc <INT>` — Number of C&C nodes.
* `--active-nodes <INT>` — Active neighbor limit (N_active). Each node maintains up to this many outbound channels.
* `--bm-seeds <INT>` — Number of C&C nodes the botmaster connects to.
* `--bm-pos <INT>` — Botmaster injection position (see Test 4 for values).
* `--topology <dlnbot|custom>` — Overlay topology mode. `dlnbot` (default) builds the D-LNBot sequential chain via the orchestrator. `custom` reads an arbitrary graph from a JSON file.
* `--topology-file <PATH>` — Path to custom topology JSON file (required when `--topology custom`).
* `--dlnbot-formation` — Enable autonomous D-LNBot formation with staggered container launches. Mutually exclusive with `--topology`.

### Simulation Control

* `--num-msg <INT>` — Number of messages to propagate per test iteration.

### Takedown Simulation

* `--takedown` — Enable node takedown during the test.
* `--takedown-pct <FLOAT>` — Fraction of nodes to remove (e.g., `0.2` for 20%). Default: `0.1`.
* `--takedown-strategy <random|targeted>` — `random` selects nodes uniformly at random. `targeted` removes the highest-degree nodes first. Default: `random`.

### Sweep Control (`run` mode only)

* `--sweep-start <INT>` — Override the starting value of the sweep variable.
* `--sweep-end <INT>` — Override the upper limit of the sweep variable.
* `--sweep-step <INT>` — Override the step size.

---

## Examples

**Run the scaling test up to 200 nodes in steps of 20:**

```bash
sudo venv/bin/python3 lntest.py run 1 --sweep-end 200 --sweep-step 20
```

**Run random takedown sweep with 3 messages per iteration (D-LNBot topology by default):**

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3
```

**Run targeted takedown sweep with 3 messages per iteration:**

```bash
sudo venv/bin/python3 lntest.py run 6 --num-msg 3
```

**Run random takedown with autonomous D-LNBot formation:**

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3 --dlnbot-formation
```

**Run takedown sweep on a custom ring topology:**

```bash
sudo venv/bin/python3 lntest.py run 5 --num-msg 3 --num-cc 20 --topology custom --topology-file topologies/ring_20.json
```

**Run botmaster injection point test with 3 messages:**

```bash
sudo venv/bin/python3 lntest.py run 4 --num-msg 3
```

**Enable takedown on an ad-hoc basis with 30% targeted removal:**

```bash
sudo venv/bin/python3 lntest.py run 1 --takedown --takedown-pct 0.3 --takedown-strategy targeted
```

**Start the scaling test from 50 nodes instead of the default:**

```bash
sudo venv/bin/python3 lntest.py run 1 --sweep-start 50
```


# Utility Scripts

All operational scripts are in the `scripts/` directory.

### cleanup.sh

Unified cleanup script with three modes:

```bash
# Stop containers, clear shared memory and node data (between iterations)
sudo ./scripts/cleanup.sh nodes

# Full cleanup including logs, status files, and address lists
sudo ./scripts/cleanup.sh all

# Stop bitcoind and delete regtest data
sudo ./scripts/cleanup.sh bitcoin
```

### restart_bitcoin.sh

```bash
sudo ./scripts/restart_bitcoin.sh
```

Stops Bitcoin Core, deletes regtest data for a fresh start, creates a new wallet, and starts a background block miner.

Note: Does not kill any existing background miner process — that is handled by `lntest.py`.


# Troubleshooting

### Bitcoin error

If you encounter Bitcoin errors when a test starts (typically related to wallet loading), this usually means Bitcoin Core was already running and the script could not shut it down properly, or the machine was shut down while Bitcoin Core was still running.

Kill the process (may require `sudo`):

```bash
pkill -9 bitcoind
```

### Bitcoin lock error

If the tester reports it cannot acquire a lock on the regtest folder, Bitcoin Core was not shut down by the previous run. Press Ctrl+C to exit the tester and try again — it usually resolves immediately.

### Shared memory errors

If you see "Shared memory block not found" errors during propagation monitoring, this is typically caused by Python's resource tracker prematurely unlinking shared memory blocks. The current codebase includes fixes for this (unregistering SHM blocks from the resource tracker in both host and container processes). If it still occurs, clean up manually:

```bash
sudo rm -rf /dev/shm/CC*
```

### Channel creation timeout

If the test reports "Channels were not ready in time" and retries, this is usually a transient issue with container scheduling or gossip propagation timing. The test will automatically retry up to 5 times per iteration. If it consistently fails, try reducing the number of nodes or increasing the `NM_MAX_WAIT` environment variable.
