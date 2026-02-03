#!/bin/bash

# setup.sh - Initial setup for LNTest environment

set -e

echo "WARNING: This setup script will OVERWRITE any existing lightning and bitcoin configuration files."
echo "Make sure to backup your existing configurations if necessary."

# 0. Check if bitcoind is running
if pgrep -x "bitcoind" > /dev/null; then
    echo "Error: Bitcoin Core (bitcoind) is currently running."
    echo "Please stop Bitcoin Core before running setup to avoid credential mismatches."
    echo "Warning: The suggested command below will FORCE KILL all 'bitcoind' processes."
    echo "Ensure no other important Bitcoin nodes are running!"
    echo "You can stop it using: ./kill_bitcoin.sh"
    exit 1
fi

# 1. Copy template to config.env if it doesn't exist
TEMPLATE_DIR="config_templates"

if [ ! -f config.env ]; then
    if [ -f "$TEMPLATE_DIR/config.env.template" ]; then
        echo "Creating config.env from template..."
        cp "$TEMPLATE_DIR/config.env.template" config.env
    else
        echo "Error: config.env.template not found in $TEMPLATE_DIR."
        exit 1
    fi
else
    echo "config.env already exists. Skipping copy."
fi

# 2. Detect directories
LNBOT_DIR=$(pwd)
BASE_DIR=$(dirname "$LNBOT_DIR")
USER_NAME=$(whoami)
HOME_DIR=$HOME

echo "Detected BASE_DIR: $BASE_DIR"
echo "Detected USER_NAME: $USER_NAME"
echo "Detected LNBOT_DIR: $LNBOT_DIR"

# 3. Prompt for RPC credentials
read -p "Enter RPC Username [user]: " RPC_USER_INPUT
RPC_USER=${RPC_USER_INPUT:-user}

read -s -p "Enter RPC Password [password]: " RPC_PASSWORD_INPUT
echo "" # Newline after password input
RPC_PASSWORD=${RPC_PASSWORD_INPUT:-password}

# 4. Update config.env with detected values
# Using | as a delimiter for sed to handle paths safely
sed -i "s|^USER_NAME=.*|USER_NAME=\"$USER_NAME\"|" config.env
sed -i "s|^BASE_DIR=.*|BASE_DIR=\"$BASE_DIR\"|" config.env
sed -i "s|^LNBOT_DIR=.*|LNBOT_DIR=\"$LNBOT_DIR\"|" config.env
sed -i "s|^RPC_USER=.*|RPC_USER=\"$RPC_USER\"|" config.env
sed -i "s|^RPC_PASSWORD=.*|RPC_PASSWORD=\"$RPC_PASSWORD\"|" config.env

# 5. Install Bitcoin and Lightning configs
install_config() {
    local src_template="$1"
    local dest_dir="$2"
    local dest_filename="$3"
    local dest="$dest_dir/$dest_filename"

    if [ ! -f "$src_template" ]; then
        echo "Error: Template $src_template not found."
        return 1
    fi

    if [ ! -d "$dest_dir" ]; then
        echo "Creating directory: $dest_dir"
        mkdir -p "$dest_dir"
    fi

    if [ -f "$dest" ]; then
        echo "Backing up existing $dest_filename to $dest_filename.bak"
        cp "$dest" "$dest.bak"
    fi

    echo "Installing $dest_filename to $dest_dir (substituting credentials)..."
    
    # Copy template to destination and perform substitutions
    cp "$src_template" "$dest"
    
    # Substitute values in the destination file
    # For bitcoin.conf: rpcuser, rpcpassword
    # For lightning.conf: bitcoin-rpcuser, bitcoin-rpcpassword
    
    # We use a generic approach: try to substitute both key styles.
    # sed will simply not find a match if the key doesn't exist in that specific file.
    
    # Bitcoin Core style
    sed -i "s|^rpcuser=.*|rpcuser=$RPC_USER|" "$dest"
    sed -i "s|^rpcpassword=.*|rpcpassword=$RPC_PASSWORD|" "$dest"
    
    # C-Lightning style
    sed -i "s|^bitcoin-rpcuser=.*|bitcoin-rpcuser=$RPC_USER|" "$dest"
    sed -i "s|^bitcoin-rpcpassword=.*|bitcoin-rpcpassword=$RPC_PASSWORD|" "$dest"
}

echo "Installing configuration files..."
install_config "$TEMPLATE_DIR/bitcoin.conf.template" "$HOME_DIR/.bitcoin" "bitcoin.conf"
install_config "$TEMPLATE_DIR/lightning.conf.template" "$HOME_DIR/.lightning" "lightning.conf"

# 6. Check System Dependencies
if [ -f "./check_dependencies.sh" ]; then
    ./check_dependencies.sh
else
    echo "Warning: check_dependencies.sh not found. Skipping dependency check."
fi

# 7. Setup Python Environment
echo "Setting up Python environment..."

# Copy requirements template if needed
if [ ! -f requirements.txt ]; then
    if [ -f "$TEMPLATE_DIR/requirements.txt.template" ]; then
         echo "Creating requirements.txt from template..."
         cp "$TEMPLATE_DIR/requirements.txt.template" requirements.txt
    else
         echo "Warning: requirements.txt.template not found. Skipping python setup."
    fi
fi

if [ -f requirements.txt ]; then
    if [ ! -d "venv" ]; then
        echo "Creating virtual environment (venv)..."
        python3 -m venv venv
    else
        echo "Virtual environment already exists."
    fi

    echo "Installing Python dependencies..."
    # We use the full path to pip to avoid needing to activate the venv in the script
    ./venv/bin/pip install -r requirements.txt
    
    if [ $? -eq 0 ]; then
         echo "Python dependencies installed successfully."
    else
         echo "Error installing Python dependencies."
         exit 1
    fi
else
    echo "No requirements.txt found. Skipping dependency install."
fi

echo "Setup complete. Please verify your settings in config.env"
echo ""
echo "Next Steps:"
echo "1. Test configuration by running: sudo venv/bin/python3 lntest.py small"
echo "For help on usage, run: sudo venv/bin/python3 lntest.py --help"
echo "use lntest.py --full -h to see all options."
echo "Refer to readme.md for further instructions."
