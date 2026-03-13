# LNTest

A testbed for designing, deploying, and evaluating Lightning Network-based botnets. Built with production-grade Bitcoin Core and Core Lightning nodes orchestrated via Docker on a Bitcoin regtest environment, LNTest runs entirely on a single host machine with no external network dependencies.

# Table of Contents
- [Architecture](#architecture)
- [Setup](#setup)
- [Usage](#usage)
  - [Test Scenarios](#test-scenarios)
  - [Examples](#examples)
- [Utility Scripts](#utility-scripts)
- [Further Documentation](#further-documentation)

# Architecture

LNTest consists of five components:

1. **Bitcoin Core** — runs on the host machine in regtest mode, providing the on-chain base layer for all Lightning nodes.
2. **Innocent Node** — a CLN instance in a Docker container that acts as a rendezvous point for C&C server peer discovery during network formation.
3. **Botmaster Node** — a CLN instance in a Docker container that issues commands by opening a temporary Lightning channel to a C&C server and injecting keysend payments.
4. **C&C Server Nodes** — CLN instances, each in its own Docker container, forming a Lightning-based overlay by opening payment channels to one another. Commands propagate through this overlay via concurrent keysend flooding.
5. **Test Orchestrator (`lntest.py`)** — a Python script on the host that automates experiment setup, monitors propagation via POSIX shared memory, and records results.

Between test iterations, containers, shared memory, and node data are cleaned up automatically. Logs are preserved for debugging and cleared only during a full cleanup (`cleanup.sh all`). Bitcoin Core's regtest environment resets at the start of each test run; within a run, the same bitcoind instance is reused across iterations for efficiency.


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

Create a workspace directory, download Bitcoin Core from [https://bitcoincore.org/en/download/](https://bitcoincore.org/en/download/), and extract it:

```bash
mkdir -p ~/lntest
cd ~/lntest
# Move the downloaded tar file here, then:
tar -xvzf bitcoin-*
mv bitcoin-*/ bitcoin
rm bitcoin-*.tar.gz
```

Do not run Bitcoin Core yet.

## 3. Clone and configure LNTest

```bash
cd ~/lntest
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


# Usage

```bash
sudo venv/bin/python3 lntest.py <command> [options]
```

Run the sanity check to verify everything works (4 nodes, 1 message, takes a few minutes):

```bash
sudo venv/bin/python3 lntest.py small
```

Run a specific test scenario:

```bash
sudo venv/bin/python3 lntest.py run <TEST_ID> [options]
```

Run `lntest.py run --help` for all available flags (topology, takedown, sweep control, etc.).

**Tip:** To save terminal output to a file while still seeing it live:

```bash
sudo venv/bin/python3 lntest.py run 1 --num-msg 3 2>&1 | tee /tmp/test_output.log
```

---

## Topology Modes

The C&C overlay topology is controlled via flags. See [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) for detailed descriptions, JSON format, and examples.

| Mode | Flag | Description |
| --- | --- | --- |
| D-LNBot (default) | `--topology dlnbot` | Orchestrator-built uniform chain |
| D-LNBot Formation | `--dlnbot-formation` | Autonomous staggered deployment |
| Custom | `--topology custom --topology-file <path>` | User-supplied JSON graph |

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

## Examples

```bash
# Scale test up to 200 nodes in steps of 20
sudo venv/bin/python3 lntest.py run cc_count --sweep-end 200 --sweep-step 20

# Random takedown with 3 messages per iteration
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3

# Targeted takedown with autonomous D-LNBot formation
sudo venv/bin/python3 lntest.py run takedown_targeted --num-msg 3 --dlnbot-formation

# Custom ring topology
sudo venv/bin/python3 lntest.py run takedown_random --num-msg 3 --cc-count 20 --topology custom --topology-file topologies/ring_20.json

# Ad-hoc 30% targeted takedown on a scaling test
sudo venv/bin/python3 lntest.py run cc_count --takedown --takedown-pct 0.3 --takedown-strategy targeted
```

## Output

Test results are saved to the `data/` directory. See [docs/OUTPUT.md](docs/OUTPUT.md) for file formats and naming conventions.


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


# Further Documentation

- [docs/TOPOLOGIES.md](docs/TOPOLOGIES.md) — Topology modes, JSON format, and comparison
- [docs/TESTS.md](docs/TESTS.md) — Test scenario details, coverage, and partition detection
- [docs/OUTPUT.md](docs/OUTPUT.md) — Generated data structure and file formats
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common issues and fixes
