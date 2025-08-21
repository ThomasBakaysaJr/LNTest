#!/bin/bash
# bootstrap.sh

set -e  # Exit on errors

# Update and install required packages
apt update
apt install git -y
apt-get install -y python3-venv python3-pip
pip3 install --upgrade pip

# Clone and set up the plugin
git clone https://github.com/lightningd/plugins.git
cd plugins/archived/noise
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Start the plugin
lightning-cli --regtest plugin start /plugins/archived/noise/noise.py




