#!/bin/bash
# Network lab startup script for user namespace environment
# Adapted from sdn_assignment2 approach

set -e

PROJECT_DIR="/home/nayan/Projects/sdn_project"
NETWORK_LAB="${PROJECT_DIR}/network-lab"
LOG_DIR="${PROJECT_DIR}/logs"

mkdir -p "${LOG_DIR}"

# Check if mininet is available
if [ ! -f "${PROJECT_DIR}/network-lab/scripts/mn-runner.py" ]; then
    echo "ERROR: mn-runner.py not found. Run setup first."
    exit 1
fi

echo "=== Starting network lab in user namespace ==="

# Pattern from sdn_assignment2: unshare -Urn for user namespace
# This allows running network tools without root

cd "${PROJECT_DIR}"

# Start Ryu + Mininet (in background for now)
unshare -Urn bash -c "
    echo 'In user namespace'
    id

    # Start OVS if available
    # ovs-vsctl init || true
    # ovs-vswitchd --pidfile --detach || true

    # Start Ryu controller (if installed)
    # ryu-manager network-lab/controller/ryu_controller.py &
" > "${LOG_DIR}/lab-startup.log" 2>&1

cat "${LOG_DIR}/lab-startup.log"

echo "=== Network lab startup complete ==="
echo "Check logs at: ${LOG_DIR}/lab-startup.log"