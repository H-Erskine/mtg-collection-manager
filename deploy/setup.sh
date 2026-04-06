#!/usr/bin/env bash
# EC2 bootstrap script for MTG Manager API
# Run once as the ubuntu user after cloning the repo:
#   bash deploy/setup.sh
#
# Before running, set these at the top of the script or export them:
#   DOMAIN      — your domain pointing at this EC2 instance's Elastic IP
#   AUTH_TOKEN  — your Twilio Auth Token (from console.twilio.com)

set -euo pipefail

DOMAIN="${DOMAIN:-yourdomain.com}"       # REPLACE or export before running
AUTH_TOKEN="${AUTH_TOKEN:-CHANGEME}"     # REPLACE or export before running
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
Description=MTG Manager WhatsApp API
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$REPO_DIR
Environment="TWILIO_AUTH_TOKEN=$AUTH_TOKEN"
ExecStart=$REPO_DIR/.venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 --timeout 30 api.app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable mtg-api
sudo systemctl restart mtg-api

echo "==> Configuring Nginx"
sudo cp "$REPO_DIR/deploy/nginx.conf" /etc/nginx/sites-available/mtg-manager
# Substitute the placeholder domain
sudo sed -i "s/yourdomain.com/$DOMAIN/g" /etc/nginx/sites-available/mtg-manager

sudo ln -sf /etc/nginx/sites-available/mtg-manager /etc/nginx/sites-enabled/mtg-manager
# Remove default site if present
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl reload nginx

echo "==> Obtaining SSL certificate via Certbot"
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@$DOMAIN"

echo ""
echo "==> Done!"
echo ""
echo "Service status:"
sudo systemctl status mtg-api --no-pager
echo ""
echo "Your webhook URL is: https://$DOMAIN/webhook"
echo "Set this as the webhook in your Twilio WhatsApp Sandbox:"
echo "  https://console.twilio.com/us1/develop/sms/settings/whatsapp-sandbox"
echo ""
echo "Health check: curl https://$DOMAIN/health"
