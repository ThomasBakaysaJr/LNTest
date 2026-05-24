# Setup

This testbed has been verified on fresh Ubuntu 24.04 LTS and 26.04 LTS installs. The instructions below assume a fresh Ubuntu system.

## 1. Install system dependencies

Update your system and install required packages:

```bash
sudo apt update && sudo apt upgrade
sudo apt install python3-venv git jq -y
```

Install Docker following the official guide for Ubuntu: [https://docs.docker.com/engine/install/ubuntu/](https://docs.docker.com/engine/install/ubuntu/)

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
Architectures: $(dpkg --print-architecture)
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

Pick a workspace directory — `LNTest/` (cloned in step 3) and `bitcoin/` (extracted below) just need to live as siblings under it. Example using `~/lntest`:

```bash
mkdir -p ~/lntest
cd ~/lntest
```

Download the latest Bitcoin Core release (currently 31.0) from [https://bitcoincore.org/en/download/](https://bitcoincore.org/en/download/), move the tarball into your workspace, then extract:

```bash
tar -xvzf bitcoin-*.tar.gz
mv bitcoin-*/ bitcoin
rm bitcoin-*.tar.gz
```

Do not start Bitcoin Core yet — `setup.sh` writes the regtest config in the next step and refuses to run if `bitcoind` is already up.

## 3. Clone and configure LNTest

```bash
cd ~/lntest
git clone https://github.com/ThomasBakaysaJr/LNTest.git LNTest
cd LNTest
./setup.sh
```

The setup script will:
* Check dependencies (Docker, jq, Python)
* Create the Python virtual environment and install packages
* Create config files in `~/.lightning` and `~/.bitcoin`
* Prompt for RPC username and password
* Build the LNTest Docker image, pinning the Core Lightning version at setup time

You can verify the RPC credentials by checking `config.env` and the config files in `~/.bitcoin` and `~/.lightning`.

The Core Lightning version is fixed when the image is built, so it stays constant across test runs. To rebuild against the latest release, re-run `./setup.sh` or `sudo venv/bin/python3 -m utils.docker_helpers`.

Note: `lntest.py` runs with `sudo` (required for Docker and shared memory management), so `config.env` uses absolute paths.

Once setup completes, run the sanity check from the [README](../README.md#quick-start) to confirm everything works end-to-end.

## Resetting the testbed

Between iterations the orchestrator automatically resets containers, shared memory, and per-run files (`scripts/cleanup.sh iter`). To reset everything to a clean-checkout + `setup.sh` state — stopping bitcoind, wiping the regtest chain, and removing all runtime artifacts — run:

```bash
sudo ./scripts/cleanup.sh fresh
```
