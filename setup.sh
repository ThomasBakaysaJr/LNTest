#!/bin/bash
set -e


echo "LNTest Setup"

# Check for Template
if [ ! -f "config.env.template" ]; then
    echo "Error: config.env.template not found."
    exit 1
fi

# Gather Credentials
# Set defaults safely
DEFAULT_RPC_USER="lnbot"
DEFAULT_RPC_PASS="lnbotpassword"

read -p "Enter RPC Username [default: $DEFAULT_RPC_USER]: " RPC_USER
RPC_USER=${RPC_USER:-$DEFAULT_RPC_USER}

read -p "Enter RPC Password [default: $DEFAULT_RPC_PASS]: " RPC_PASS
RPC_PASS=${RPC_PASS:-$DEFAULT_RPC_PASS}

# Create config.env from Template
echo "Creating config.env..."
cp config.env.template config.env

# Replace Credentials
# We use | as a delimiter for sed to avoid issues with slashes
sed -i "s|USER_NAME=\"username\"|USER_NAME=\"$USER\"|g" config.env
sed -i "s|RPC_USER=\"user\"|RPC_USER=\"$RPC_USER\"|g" config.env
sed -i "s|RPC_PASSWORD=\"password\"|RPC_PASSWORD=\"$RPC_PASS\"|g" config.env

echo "Finished with config.env"

# Generate Bitcoin/Lightning Configs

echo "writing ~/.bitcoin/bitcoin.conf..."
mkdir -p "$HOME/.bitcoin"
cat <<EOF > "$HOME/.bitcoin/bitcoin.conf"
regtest=1
server=1
daemon=1
txindex=1
rpcworkqueue=512
rpcthreads=64
prune=0
rpcuser=$RPC_USER
rpcpassword=$RPC_PASS
[regtest]
rpcport=8332
rpcallowip=0.0.0.0/0
whitelist=127.0.0.1
fallbackfee=0.00001
EOF

echo "writing ~/.lightning/lightning.conf..."
mkdir -p "$HOME/.lightning"
cat <<EOF > "$HOME/.lightning/lightning.conf"
network=regtest
log-level=debug
bitcoin-rpcuser=$RPC_USER
bitcoin-rpcpassword=$RPC_PASS
bitcoin-rpcconnect=127.0.0.1
bitcoin-rpcport=8332
EOF

# Final Python/Data Setup
echo "Setting up Directories & Python..."

if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install -r requirements.txt > /dev/null

echo "Success! Setup complete."