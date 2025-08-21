#!/bin/bash
# bootstrap.sh

set -eux  # Exit on errors

# Update and install required packages
echo "starting bootstrap"
apt update -y -qq
apt install git -y -q > /dev/null 2>&1
apt-get install -y -qq python3-pip
# going to just install it globally; no reason for a virtual env here
pip3 install --upgrade pip -qq --break-system-packages

# Clone and set up the plugin
echo "Installing noise plugin"
git clone -qq https://github.com/lightningd/plugins.git
cd plugins/archived/noise
pip install -r requirements.txt -qq --break-system-packages --resume-retries 5
pip install -r requirements-dev.txt -qq --break-system-packages --resume-retries 5

# pip3 install "fastapi[standard]" -q --break-system-packages --resume-retries 5

# Start the plugin
lightning-cli --regtest plugin start /plugins/archived/noise/noise.py > /dev/null 2>&1
echo "bootstrapping done"



