#!/bin/bash

# check_dependencies.sh - Verifies required system tools are installed

DEPENDENCIES=("docker" "jq" "python3")
MISSING_DEPS=0

echo "Checking system dependencies..."

for dep in "${DEPENDENCIES[@]}"; do
    if ! command -v "$dep" &> /dev/null; then
        echo "Error: '$dep' is not installed."
        MISSING_DEPS=1
    else
        echo "  - $dep: Found"
    fi
done

if [ $MISSING_DEPS -eq 1 ]; then
    echo "Please install missing dependencies and run setup.sh again."
    exit 1
fi

echo "All system dependencies found."
