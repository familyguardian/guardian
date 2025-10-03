#!/bin/bash

# This script automates the process of deploying and testing the Guardian project on a remote test laptop.
# It reads configuration from testenv.conf, pushes the latest changes, and runs the installation script.

set -e # Exit immediately if a command exits with a non-zero status.

# Find the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/testenv.conf"

# Check if the config file exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Configuration file not found: $CONFIG_FILE"
    exit 1
fi

# Source the configuration file
source "$CONFIG_FILE"

# --- Validation ---
if [ "$TEST_HOST" == "<IP_ADDRESS_HERE>" ] || [ -z "$TEST_HOST" ]; then
    echo "Please fill in the TEST_HOST in $CONFIG_FILE"
    exit 1
fi

echo "--- Starting Deployment to $TEST_HOST ---"

# --- Step 1: Git Commit and Push ---
if [ -n "$1" ]; then
    echo "Committing changes with message: $1"
    git add .
    git commit -m "$1"
fi

# Ensure the current branch is pushed to the 'origin' remote.
# The remote machine is expected to have 'origin' pointing to this repository.
echo "Pushing latest changes to origin..."
git push origin main # Assuming 'main' is the branch to be tested

# --- Step 2: SSH and Run Installation ---
# Connect to the test machine and execute the installation script.
echo "Connecting to $TEST_HOST to run the installation..."

ssh -t "$TEST_USER@$TEST_HOST" "
    set -e
    echo '--- On test host: $TEST_HOST ---'

    # Navigate to the project directory
    cd '$PROJECT_DIR'

    # Pull the latest changes
    echo 'Pulling latest changes from git...'
    git fetch origin
    git pull origin main

    # Run the installation script with sudo
    echo 'Running installation script...'
    sudo python3 '$PROJECT_DIR/scripts/install_artifacts.py'

    # Wait a few seconds for the daemon to initialize
    echo 'Waiting for the daemon to start...'
    sleep 5

    # Check the daemon's logs
    echo 'Displaying daemon logs...'
    journalctl -u guardian_daemon.service --no-pager --since "1 minute ago"

    echo '--- Deployment and installation complete ---'
"

echo "--- Workflow finished successfully! ---"
