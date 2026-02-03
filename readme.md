# LNTest

A testbed for a distributed Lightning network on one machine, using production grade Bitcoin Core and Core Lightning nodes orchestrated via Docker on a Bitcoin regtest environment.

# Table of Contents
- [LNTest](#lntest)
- [Table of Contents](#table-of-contents)
- [Architecture](#architecture)
  - [Generated Data \& Output](#generated-data--output)
- [Pre-requisites \& Compatibility](#pre-requisites--compatibility)
    - [Update Ubuntu](#update-ubuntu)
    - [Python](#python)
    - [Docker](#docker)
    - [Git](#git)
- [Set-up](#set-up)
  - [1. Create directory for LNTest and the Bitcoin-Core files](#1-create-directory-for-lntest-and-the-bitcoin-core-files)
  - [2. Install Bitcoin-core](#2-install-bitcoin-core)
  - [3. Setting up LNTest](#3-setting-up-lntest)
  - [4. Running lntest](#4-running-lntest)
- [Commands and Scripts](#commands-and-scripts)
    - [Script](#script)
- [Running the Test Suite](#running-the-test-suite)
  - [Modes of Operation](#modes-of-operation)
    - [1. Small Check (`small`)](#1-small-check-small)
    - [2. Full Suite (`full`)](#2-full-suite-full)
    - [3. Specific Test Run (`run`)](#3-specific-test-run-run)
  - [Test Scenarios (Test IDs)](#test-scenarios-test-ids)
  - [Customization \& Flags](#customization--flags)
    - [Topology Parameters](#topology-parameters)
    - [Simulation Control](#simulation-control)
    - [Takedown Simulation](#takedown-simulation)
    - [Range Control (Only for `run` mode)](#range-control-only-for-run-mode)
  - [Examples](#examples)
    - [kill\_nodes.sh](#kill_nodessh)
    - [cleanup\_lightning\_nodes.sh](#cleanup_lightning_nodessh)
    - [restart\_bitcoin.sh](#restart_bitcoinsh)
- [Common Problems and Fixes](#common-problems-and-fixes)
    - [Bitcoin error](#bitcoin-error)
    - [Bitcoin lock error](#bitcoin-lock-error)


# Architecture

The botnet has 5 essential components. We have the main tester script that runs on the host machine, the Innocent Node, Botmaster Node and the CC Server Nodes, all of which run on individual docker containers running a custom docker image that is essentially the “elementsproject/lightningd:v25.09” image with python installed. Finally we have bitcoin core running a regtest server on the host machine to simulate the bitcoin network.

Each test automatically restarts the bitcoin server with a fresh wallet. In an effort to minimize the impact of previous tests on the next, all nodes and their associated resources are taken down after every test.

## Generated Data & Output

All experimental data is automatically saved to the `data/` directory. The testbed generates distinct files for each test configuration to ensure no data is overwritten or lost. However, this only applies per configuration as the exact same type of test will overwrite already present data.

### Data Structure
The filenames generally follow a specific convention based on the test parameters: `data/<variable>_<value>_<unique_id>_<type>`.

* **Propagation Data** (`*_time_data.json`)
  * Contains the precise time it took for each message to propagate through the network.
  * Includes metadata about the test (e.g., number of CC nodes, active nodes).
  * Records total setup time vs. total message sending time.

* **Topology Snapshots** (`*_topology_data.json`)
  * Captures the state of the Lightning Network at the end of the test.
  * Lists every node, its channels, channel capacity, and connection status.
  * Useful for visualizing the mesh network created during the experiment.

* **System Metrics** (`*_system_metrics.csv`)
  * Logs CPU and RAM usage of the host machine throughout the test duration.
  * Useful for analyzing the hardware overhead of running high-density docker simulations.

* **Execution Logs** (`*_total_times_log.json`)
  * A running log of how long each full test suite took to execute.
  * Helps track performance improvements or regressions in the testbed itself.
  * *Note: These files are timestamped by date (e.g., `YYYY-MM-DD_total_times_log.json`).*

### Logs
Detailed logs for debugging specific node behaviors are stored in:
* `NodeManagerComms/logs/`: Individual logs for every Command & Control (CC) node.
* `BotMasterComms/`: Logs for the Botmaster node actions.


# Pre-requisites & Compatibility

This testbed has been verified on the following Linux distributions:

* Ubuntu 24.04 LTS
* Ubuntu 25.04

This guide assumes that the user is starting from a fresh install of Ubuntu.

### Update Ubuntu

Be sure your system is up to date.

```bash
sudo apt update  
sudo apt upgrade
```

### Python

This project uses bash and python scripts. We will specifically create a virtual environment for this project.

Install venv for python so we can create the virtual environments.

```bash
sudo apt install python3-venv -y
```

### Docker

All lightning nodes will be individual docker containers. Reference the following guide, the pertinent instructions have been provided.

- [https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository](https://docs.docker.com/engine/install/ubuntu/#install-using-the-repository) 

Install the Apt resources.

```bash
# Add Docker's official GPG key:
sudo apt update
sudo apt install ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

# Add the repository to Apt sources:
sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
```

Actual installation of Docker.

```bash
sudo apt-get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Check the status of Docker, it should be running at this point.

```bash
sudo systemctl status docker
```

### Git

The repository containing the project will need to be cloned.

Install git using apt.

```bash
sudo apt install git
```
# Set-up

Note: The setup script should be robust enough to handle different directory names. However, this was only tested inside the user's home directory.

## 1. Create directory for LNTest and the Bitcoin-Core files

Navigate to the Documents directory in your home directory.

```bash
cd ~/Documents
```

Create a directory for LNBot and Bitcoin core to live in. In our testing environment we named it “LNBot_research_project”.

```bash
mkdir LNBot_research_project  
cd LNBot_research_project
```

Note: If you name this directory something else, the setup script should catch and name the paths correctly. This can be double checked in config.env once setup is complete.

## 2. Install Bitcoin-core

Download the bitcoin core tar file from [https://bitcoincore.org/en/download/](https://bitcoincore.org/en/download/) and move this file into the LNBot_research_project directory.

Extract the bitcoin core tar file here.

```bash
tar -xvzf bitcoin-*
```

Rename the folder to bitcoin. After typing the initial bitcoin, press tab to autofill the bitcoin folder name.

```bash
mv bitcoin-[tab] bitcoin
```

Remove the tar file since it is no longer needed.

```bash
rm bitcoin-*
```

The directory should currently look like this.

![ln-research-bitcoin-only-dir](images/ln-research-bitcoin-only-dir.png)

Do not run bitcoin-core just yet.

## 3. Setting up LNTest

Using git, we will download the repo containing LNTest. Keep in mind that this is a self contained testing suite and will not communicate outside of the host machine, hence why we did not need to open up any ports in the previous steps. We will then run the setup.sh script to populate the required paths and rpc credentials.

The setup script will also check dependencies, create the python virtual environment, install pre-requisite packages, and create the config files in ```~/.lightning``` and ```~/.bitcoin``` directories.

It is important to note that the script uses paths in ```config.env``` to find the necessary files to run. This is because we use sudo to run the tester files (docker and shared memory management require root privileges) and so the tester file will look in “root”s home directory for those files unless a hard path is used instead.

### 3.1 Clone the repo using git

```bash
cd ~/Documents/LNBot_research_project  
git clone https://github.com/LN-Testbed/DSN2026.git LNTest
```

The final directory structure inside the LNBot_research_project should appear as follows:  
![lntest-dir](images/lntest-dir.png)

### 3.2 Add execute permission to bash files

```bash
chmod +x *.sh
```

Restore execute permissions to the bash files in case they were removed during the cloning process.

### 3.2 Run setup.sh

```bash
./setup.sh
```

The setup will ask for the rpc username and password to use for this installation. You can double check that the rpc username and password matches by checking ```config.env``` and the config files in ```~/.bitcoin``` and ```~/.lightning```.

## 4. Running lntest

The python script “lntest” is the main script for testing the Lightning Botnet. It is responsible for creating the containers, managing memory, sending the initial botnet commands for propagation and then tearing everything down for subsequent tests.

Because this script manages memory and deals with running docker files, it must be run as sudo, however doing so will use the root’s path for python, which means that we lose the dependencies we just installed. To use the correct interpreter, we need to run sudo with an absolute path to the correct python interpreter. Run lntest.

```bash
sudo venv/bin/python lntest.py small
```

This will start a small gamut of tests to see if everything is set up correctly. Progress can be monitored in the logs in the log directory, with the status of each node stored in the status directory.

Data collected will be in the “data/” directory.

Important: Remember to save your data to a separate directory after each run since the script will overwrite any data that is stored there.

# Commands and Scripts

An overview of the useful scripts contained in this setup. This section will be written as:

### Script

How to run script  
Description of what this script does.

# Running the Test Suite

The core of the simulation is managed by `lntest.py`. This script handles container orchestration, network topology generation, and data recording.

**Note:** Because the script manages Docker containers and system memory, it must be run with `sudo`. To ensure it uses the correct dependencies, point it to the python interpreter inside your virtual environment.

```bash
sudo venv/bin/python3 lntest.py <command> [options]
```

## Modes of Operation

The script operates in three main modes:

### 1. Small Check (`small`)

Runs a minimal test to verify that the environment, Docker containers, and Bitcoin regtest are configured correctly.

```bash
sudo venv/bin/python3 lntest.py small
```

### 2. Full Suite (`full`)

Runs the complete battery of default experiments (Test IDs 1 through 5).

```bash
sudo venv/bin/python3 lntest.py full
```

### 3. Specific Test Run (`run`)

Runs a specific experimental scenario. This mode allows for granular control over the test variable ranges and steps.

```bash
# Syntax: lntest.py run <TEST_ID> [options]
sudo venv/bin/python3 lntest.py run 1 --max-range 50 --step 5
```

---

## Test Scenarios (Test IDs)

When using the `run` command, you must specify one of the following Test IDs:

| ID | Description | Variable Tested | Default Behavior |
| --- | --- | --- | --- |
| **1** | **Scale C&C Nodes** | `num_cc` | Increases total C&C nodes from 10 to 100. |
| **2** | **Scale Active Nodes** | `active_nodes` | Increases the number of active channels per node from 1 to 6. |
| **3** | **Botmaster Connectivity** | `bm_cc` | Increases the number of initial entry nodes the Botmaster connects to. |
| **4** | **Botmaster Position** | `bm_pos` | Changes the Botmaster's injection point from oldest nodes (0%) to newest nodes (100%). |
| **5** | **Resilience (Takedown)** | `num_cc` (with takedown) | Randomly shuts down 10% of nodes during propagation to test resilience. |

---

## Customization & Flags

You can override the default network topology and simulation parameters for **any** mode (`small`, `full`, or `run`) using the flags below.

### Topology Parameters

* `--num-cc <INT>`: Set the starting number of Command & Control (C&C) nodes.
* `--active-nodes <INT>`: Set the number of active channels every node attempts to maintain.
* `--bm-cc <INT>`: Set how many C&C nodes the Botmaster creates channels with.
* `--bm-pos <INT>`: Set the position in the network where the Botmaster connects (0-100%).
* `0`: Oldest nodes.
* `50`: Middle of the network.
* `100`: Newest nodes.



### Simulation Control

* `--max-msg <INT>`: The number of distinct messages to propagate per test iteration.

### Takedown Simulation

Forcefully remove nodes during the test.

* `--takedown`: Enable the takedown mechanism.
* `--takedown-pct <FLOAT>`: The percentage of nodes to kill (e.g., `0.2` for 20%). Default is `0.1` (10%).

### Range Control (Only for `run` mode)

* `--max-range <INT>`: Override the upper limit of the test variable.
* `--step <INT>`: Override the increment step size for the test variable.

---

## Examples

**1. Run a custom "Scale C&C" test:**
Run Test ID 1, but go up to 200 nodes in steps of 20.

```bash
sudo venv/bin/python3 lntest.py run 1 --max-range 200 --step 20
```

**2. Test network resilience with higher churn:**
Run the "Small" sanity check, but kill 30% of the nodes.

```bash
sudo venv/bin/python3 lntest.py small --takedown --takedown-pct 0.3
```

**3. customized Full Run:**
Run the full suite, but force all tests to start with a highly connected topology (8 active nodes).

```bash
sudo venv/bin/python3 lntest.py full --active-nodes 8
```


### kill_nodes.sh

```bash
sudo ./kill_nodes.sh
```  
Stops and removes all docker nodes created during the test.  
Clears out shared memory.  
Removes the persistent docker directories so that no files interfere with further tests.  
Does not remove logs in the NodeManagerComms/logs directory.

### cleanup_lightning_nodes.sh
```bash
sudo ./cleanup_lightning_nodes.sh  
```
Kill nodes except it also clears out the logs.   
This is the script that the lntest script calls after recording each test.

### restart_bitcoin.sh
```bash
sudo ./restart_bitcoin.sh  
```
Stops bitcoin-core.  
Deletes regtest data so we start fresh.  
Creates a new wallet for the tests, since by default bitcoind does not create a wallet.  
Starts a mineBlocks bitcoin miner in the background.

Note: Does not kill any bitcoin miner that may be still running. That is done in the lntest script.

# Common Problems and Fixes

Some common problems that can pop up from time to time.

### Bitcoin error

If you run into bitcoin errors as the testing starts, usually with a description of loading wallet or some such. This is usually because bitcoin core was already running and the script couldn’t shut it down properly or the device was shutdown while bitcoin-core was still running.

You will need to find the pid of bitcoin-core and kill it, sometimes forcefully if it will not exit out with a normal kill command.
You may need to run pkill as sudo.

```bash
pkill -9 bitcoind
```

### Bitcoin lock error

If you start the tester and it states that it can’t get a lock on the regtest folder, that means bitcoin-core was not shutdown automatically by the scripts. Crtl+c to exit the tester and retry, it usually clears up immediately.
