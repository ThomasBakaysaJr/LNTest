#!/bin/bash
# bootstrap.sh

set -eux  # Exit on errors

# Update and install required packages
echo "starting bootstrap"
apt update -y -qq
apt-get install -y -qq python3-pip
# going to just install it globally; no reason for a virtual env here
pip3 install --upgrade -qq pip  --break-system-packages

pip3 install requests -qq --break-system-packages --resume-retries 5
pip3 install "fastapi[all]" -qq --break-system-packages --resume-retries 5

echo "Innocent_node bootstrapping done"