#!/usr/bin/env bash
# One-command Team Talk update: pull the latest code AND restart the service.
# Usage:  sudo /opt/team-talk/update.sh
set -e

cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo:  sudo $0"
    exit 1
fi

APP_USER="$(stat -c '%U' .)"
echo "Pulling latest code (as $APP_USER)..."
sudo -u "$APP_USER" git pull

# Install any new dependencies — updates sometimes add packages, and
# restarting without them crash-loops the service (connection refused)
if [ -x ".venv/bin/pip" ]; then
    echo "Installing dependencies..."
    sudo -u "$APP_USER" .venv/bin/pip install -q -r requirements.txt
elif command -v pip3 >/dev/null 2>&1; then
    echo "Installing dependencies (no .venv found, using pip3)..."
    sudo -u "$APP_USER" pip3 install -q -r requirements.txt || true
fi

if systemctl list-unit-files team-talk.service >/dev/null 2>&1 \
    && systemctl is-enabled team-talk >/dev/null 2>&1; then
    echo "Restarting team-talk service..."
    systemctl restart team-talk
    systemctl --no-pager --lines=0 status team-talk || true
else
    echo "No team-talk systemd service found — restart the app manually."
fi

echo "Done. Refresh the page in your browser."
