#!/bin/bash
set -e

echo "=== VPS AUTO-SETUP START ==="

# 1. Setup 2GB Swap Memory (essential for 1GB RAM machines to run Playwright Chromium)
if [ ! -f /swapfile ]; then
    echo ">> Creating 2GB swap file..."
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo ">> Swap created successfully."
else
    echo ">> Swap file already exists."
fi

# 2. Update and Install System Dependencies
echo ">> Installing python3 and build-essential..."
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv python3-dev build-essential curl

# 3. Setup Virtual Environment
cd /home/cherish/nifty_intraday_backtester
echo ">> Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 4. Install requirements
echo ">> Installing requirements from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Install Playwright browser & system-level dependencies for Chromium
echo ">> Installing Playwright Chromium browser & dependencies..."
playwright install chromium
playwright install-deps chromium

# 6. Configure systemd Service Daemon
echo ">> Configuring systemd service nifty-bot.service..."
sudo tee /etc/systemd/system/nifty-bot.service > /dev/null <<EOF
[Unit]
Description=Nifty Trading Bot Service
After=network.target
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
User=cherish
WorkingDirectory=/home/cherish/nifty_intraday_backtester
ExecStart=/home/cherish/nifty_intraday_backtester/venv/bin/python3 app.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 7. Reload and Start service
echo ">> Reloading systemd and restarting service..."
sudo systemctl daemon-reload
sudo systemctl enable nifty-bot.service
sudo systemctl restart nifty-bot.service

echo "=== VPS AUTO-SETUP COMPLETE ==="
echo "Service status:"
sudo systemctl status nifty-bot.service --no-pager
