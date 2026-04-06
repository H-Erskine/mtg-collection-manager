#!/usr/bin/env bash
# EC2 bootstrap script for MTG Manager API
# Run once as the ubuntu user after cloning the repo:
#   bash deploy/setup.sh
#
# Before running, set these at the top of the script or export them:
#   DOMAIN      — your domain pointing at this EC2 instance's Elastic IP

set -euo pipefail

DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN:-CHANGEME}"  # REPLACE or export before running
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="ubuntu"

echo "==> Installing system packages"
sudo apt-get update -q
sudo apt-get install -y -q python3-pip python3-venv nginx certbot python3-certbot-nginx git

echo "==> Creating Python virtualenv"
python3 -m venv "$REPO_DIR/.venv"
source "$REPO_DIR/.venv/bin/activate"

echo "==> Installing Python dependencies"
pip install --quiet -e "$REPO_DIR"
pip install --quiet -r "$REPO_DIR/requirements-api.txt"

echo "==> Creating database directory"
mkdir -p "$HOME/.mtg_manager"

if [ ! -f "$HOME/.mtg_manager/config.toml" ]; then
    echo ""
    echo "  NOTE: Copy your config.toml to $HOME/.mtg_manager/config.toml"
    echo "  The app looks for it at: ~/.mtg_manager/config.toml"
    echo "  (or wherever your config.toml points the db_path to)"
    echo ""
fi

echo "==> Writing systemd service"
sudo tee /etc/systemd/system/mtg-api.service > /dev/null <<EOF
[Unit]
Description=MTG Manager Discord Bot
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$REPO_DIR
Environment="DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN"
ExecStart=$REPO_DIR/.venv/bin/python -m api.bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mtg-api
sudo systemctl restart mtg-api

echo ""
echo "==> Done!"
echo ""
echo "Service status:"
sudo systemctl status mtg-api --no-pager
echo ""
echo "The Discord bot is now running. Invite it to your server if you haven't already:"
echo "  https://discord.com/developers/applications"
