#!/usr/bin/env bash
# One-time setup of the 24x7 live job alerter on an always-on Ubuntu machine (e.g. a free cloud VM).
# Usage:  bash setup_live.sh <your-ntfy-topic>
set -euo pipefail
TOPIC="${1:?give your ntfy topic, e.g. bash setup_live.sh sahil-cyberjobs-xxxx}"
sudo apt-get update -y && sudo apt-get install -y git python3 python3-venv
cd "$HOME"
[ -d linkedin-job-alerts ] || git clone https://github.com/potatoomann/linkedin-job-alerts.git
cd linkedin-job-alerts && git pull
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
mkdir -p live-data
sudo tee /etc/systemd/system/job-alerts.service >/dev/null <<UNIT
[Unit]
Description=Live cyber job alerts (24x7)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/linkedin-job-alerts
Environment=LIVE=1
Environment=NTFY_TOPIC=$TOPIC
Environment=DATA_DIR=$HOME/linkedin-job-alerts/live-data
ExecStartPre=-/usr/bin/git pull --ff-only
ExecStart=$HOME/linkedin-job-alerts/.venv/bin/python scraper.py
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now job-alerts
echo "Live alerter is running. Watch it with:  journalctl -u job-alerts -f"
