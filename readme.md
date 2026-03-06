# LNTest

A testbed for designing, deploying, and evaluating Lightning Network-based botnets. Built with production-grade Bitcoin Core and Core Lightning nodes orchestrated via Docker on a Bitcoin regtest environment, LNTest runs entirely on a single host machine with no external network dependencies.

# Table of Contents
- [LNTest](#lntest)
- [Table of Contents](#table-of-contents)
- [Architecture](#architecture)
  - [Generated Data \& Output](#generated-data--output)
- [Pre-requisites \& Compatibility](#pre-requisites--compatibility)
- [Set-up](#set-up)
- [Running the Test Suite](#running-the-test-suite)
  - [Modes of Operation](#modes-of-operation)
  - [Test Scenarios (Test IDs)](#test-scenarios-test-ids)
  - [Customization \& Flags](#customization--flags)
  - [Examples](#examples)
- [Utility Scripts](#utility-scripts)
- [Common Problems and Fixes](#common-problems-and-fixes)

# Architecture

LNTest consists of five components:

1. **Bitcoin Core** — runs on the host machine in regtest mode, providing the on-chain base layer for all Lightning nodes.
2. **Innocent Node** — a CLN instance in a Docker container that acts as a rendezvous point for C&C server peer discovery during network formation.
3. **Botmaster Node** — a CLN instance in a Docker container that issues commands by opening a temporary Lightning channel to a C&C server and injecting keysend payments.
4. **C&C Server Nodes** — CLN instances, each in its own Docker container, forming a Lightning-based overlay by opening payment channels to one another. Commands propagate through this overlay via flooding.
5. **Test Orchestrator (`lntest.py`)** — a Python script on the host that automates experiment setup, monitors propagation via POSIX shared memory, and records results.

Each test automatically resets the Bitcoin regtest environment with a fresh wallet. All nodes and their associated resources (containers, shared memory, logs) are cleaned up between test iterations to minimize cross-test interference.

## Generated Data & Output

All experimental data is saved to the `data/` directory. The testbed generates distinct files for each test configuration based on parameter values, so different configurations never overwrite each other. However, re-running the exact same configuration will overwrite previously generated data for that configuration.

### Data Structure
Filenames follow the convention: `data/<variable>_<value>_<unique_id>_<type>`.

For takedown tests, filenames include a strategy marker: `T` for random takedown, `Ttargeted` for targeted takedown.

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

### Logs
Detailed logs for debugging specific node behaviors are stored in:
* `NodeManagerComms/logs/` — individual logs for every C&C node.
* `BotMasterComms/` — logs for the Botmaster node.


# Pre-requisites & Compatibility

This testbed has been verified on the following Linux distributions:

* Ubuntu 24.04 LTS
* Ubuntu 25.04

This guide assumes a fresh install of Ubuntu.

### Update Ubuntu

Ensure your system is up to date.

```bash
sudo apt update  
sudo apt upgrade
```

### Python

This project uses bash and Python scripts with a dedicated virtual environment.

Install venv:

```bash
sudo apt install python3-venv -y
```

### Docker

All Lightning nodes run as individual Docker containers. Follow the official Docker installation guide for Ubuntu:

- [https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository) 

Install the apt resources:

```bash
# Add Docker's official GPG key:
sudo apt update
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
```

Install Docker:

```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Verify Docker is running:

```bash
sudo systemctl status docker
```

### Git

Install git:

```bash
sudo apt install git
```

# Set-up

Note: The setup script should handle different directory names, but this has only been tested inside the user's home directory.

## 1. Create directory for LNTest and the Bitcoin-Core files

```bash
cd ~/Documents
mkdir LNBot_research_project  
cd LNBot_research_project
```

## 2. Install Bitcoin-core

Download the Bitcoin Core tar file from [https://bitcoincore.org/en/download/](https://bitcoincore.org/en/download/) and move it into the `LNBot_research_project` directory.

Extract, rename, and clean up:

```bash
tar -xvzf bitcoin-*
mv bitcoin-*/ bitcoin
rm bitcoin-*.tar.gz
```

Do not run Bitcoin Core yet.

## 3. Setting up LNTest

### 3.1 Clone the repo

```bash
cd ~/Documents/LNBot_research_project  
git clone https://github.com/LN-Testbed/DSN2026.git LNTest
```

### 3.2 Add execute permissions to bash files

```bash
cd LNTest
chmod +x *.sh
```

### 3.3 Run setup.sh

```bash
./setup.sh
```

The setup script will:
* Check dependencies
* Create the Python virtual environment
* Install required Python packages
* Create config files in `~/.lightning` and `~/.bitcoin`
* Ask for RPC username and password

You can verify the RPC credentials by checking `config.env` and the config files in `~/.bitcoin` and `~/.lightning`.

Note: The script uses paths in `config.env` to locate files. Since `lntest.py` runs with `sudo` (required for Docker and shared memory management), it needs absolute paths rather than relative ones.

## 4. Running lntest

Because this script manages Docker containers and shared memory, it must be run with `sudo`. To use the correct Python interpreter with all dependencies, provide the absolute path to the virtual environment's interpreter:

```bash
sudo venv/bin/python3 lntest.py small
```

This runs a quick sanity check to verify everything is set up correctly. Data is saved to the `data/` directory.

**Tip:** To save terminal output to a file while still seeing it live, use `tee`:

```bash
sudo venv/bin/python3 lntest.py run 1 --max-msg 3 2>&1 | tee /tmp/test_output.log
```


# Running the Test Suite

```bash
sudo venv/bin/python3 lntest.py <command> [options]
```

## Modes of Operation

### 1. Small Check (`small`)

Runs a minimal version of every test to verify the environment is configured correctly.

```bash
sudo venv/bin/python3 lntest.py small
```

### 2. Full Suite (`full`)

Runs the complete battery of experiments (Test IDs 1 through 6).

```bash
sudo venv/bin/python3 lntest.py full
```

### 3. Specific Test Run (`run`)

Runs a specific test scenario with full control over variable ranges and steps.

```bash
sudo venv/bin/python3 lntest.py run <TEST_ID> [options]
```

---

## Test Scenarios (Test IDs)

| ID | Description | Variable Tested | Default Range |
| --- | --- | --- | --- |
| **1** | Scale C&C nodes | `num_cc` | 10 to 100, step 10 |
| **2** | Scale active nodes (N_active) | `active_nodes` | 2 to 6, step 1 |
| **3** | Botmaster connectivity | `bm_cc` | 1 to 6, step 1 |
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

### Coverage and Partition Detection

Tests 5 and 6 include coverage tracking and partition detection:
* **Coverage** — the fraction of surviving nodes that successfully receive the command.
* **Partition detection** — if no new node receives the command for 60 seconds, the test declares a network partition and records partial coverage as a valid data point instead of retrying.

---

## Customization & Flags

Override default parameters for any mode (`small`, `full`, or `run`):

### Topology Parameters

* `--num-cc <INT>` — Number of C&C nodes.
* `--active-nodes <INT>` — Active neighbor limit (N_active). Each node maintains up to this many outbound channels.
* `--bm-cc <INT>` — Number of C&C nodes the botmaster connects to.
* `--bm-pos <INT>` — Botmaster injection position (see Test 4 for values).

### Simulation Control

* `--max-msg <INT>` — Number of messages to propagate per test iteration.

### Takedown Simulation

* `--takedown` — Enable node takedown during the test.
* `--takedown-pct <FLOAT>` — Fraction of nodes to remove (e.g., `0.2` for 20%). Default: `0.1`.
* `--takedown-strategy <random|targeted>` — `random` selects nodes uniformly at random. `targeted` removes the highest-degree nodes first. Default: `random`.

### Range Control (only for `run` mode)

* `--max-range <INT>` — Override the upper limit of the test variable.
* `--step <INT>` — Override the step size.

---

## Examples

**Run the scaling test up to 200 nodes in steps of 20:**

```bash
sudo venv/bin/python3 lntest.py run 1 --max-range 200 --step 20
```

**Run random takedown sweep with 3 messages per iteration:**

```bash
sudo venv/bin/python3 lntest.py run 5 --max-msg 3
```

**Run targeted takedown sweep with 3 messages per iteration:**

```bash
sudo venv/bin/python3 lntest.py run 6 --max-msg 3
```

**Run botmaster injection point test with 3 messages:**

```bash
sudo venv/bin/python3 lntest.py run 4 --max-msg 3
```

**Run the full suite with a custom active node count:**

```bash
sudo venv/bin/python3 lntest.py full --active-nodes 8
```

**Enable takedown on an ad-hoc basis with 30% targeted removal:**

```bash
sudo venv/bin/python3 lntest.py run 1 --takedown --takedown-pct 0.3 --takedown-strategy targeted
```


# Utility Scripts

### kill_nodes.sh

```bash
sudo ./kill_nodes.sh
```

Stops and removes all Docker containers created during the test. Clears shared memory. Removes persistent Docker directories. Does not remove logs in `NodeManagerComms/logs/`.

### cleanup_lightning_nodes.sh

```bash
sudo ./cleanup_lightning_nodes.sh  
```

Same as `kill_nodes.sh` but also clears logs. This is the script that `lntest.py` calls between test iterations.

### restart_bitcoin.sh

```bash
sudo ./restart_bitcoin.sh  
```

Stops Bitcoin Core, deletes regtest data for a fresh start, creates a new wallet, and starts a background block miner.

Note: Does not kill any existing background miner process — that is handled by `lntest.py`.


# Common Problems and Fixes

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
