# Setup

This testbed has been verified on a fresh Ubuntu 24.04 LTS install. The instructions below assume a fresh Ubuntu system.

**Requirements:** Docker, Python 3.10+, Bitcoin Core v28+. Core Lightning v25.09 is pulled automatically via Docker during setup.

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
git clone https://github.com/ThomasBakaysaJr/LNTest.git LNTest
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
