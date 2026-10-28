#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: ./deploy.sh <VM_EXTERNAL_IP> [SSH_KEY_PATH]"
    exit 1
fi

IP=$1
KEY=$2
SSH_OPTS="-o StrictHostKeyChecking=no"

if [ -n "$KEY" ]; then
    SSH_OPTS="$SSH_OPTS -i $KEY"
fi

echo "=== STARTING DEPLOYMENT TO $IP ==="

# 1. Create a clean archive of the codebase
echo ">> Creating clean code archive..."
tar --exclude='venv' --exclude='__pycache__' --exclude='.git' --exclude='*.log' --exclude='nifty_intraday_backtester.tar.gz' --exclude='live_trades.json' --exclude='live_state.json*' --exclude='scratch' -czf nifty_intraday_backtester.tar.gz .

# 2. Prepare directory and upload archive to VPS
echo ">> Uploading archive to server..."
ssh $SSH_OPTS cherish@$IP "mkdir -p /home/cherish/nifty_intraday_backtester"
scp $SSH_OPTS nifty_intraday_backtester.tar.gz cherish@$IP:/home/cherish/nifty_intraday_backtester/

# 3. Extract and Run Setup
echo ">> Extracting and running vps_setup.sh on VPS..."
ssh $SSH_OPTS cherish@$IP "cd /home/cherish/nifty_intraday_backtester && tar -xzf nifty_intraday_backtester.tar.gz && chmod +x deploy/vps_setup.sh && ./deploy/vps_setup.sh"

# Clean up local archive
rm nifty_intraday_backtester.tar.gz

echo "=== DEPLOYMENT COMPLETED SUCCESSFULLY ==="
echo "You can access the dashboard at: http://$IP:5001"
echo "To view live logs, run:"
echo "ssh $SSH_OPTS cherish@$IP \"sudo journalctl -u nifty-bot.service -f\""
