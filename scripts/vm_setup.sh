#!/bin/bash
# Run this once after provisioning a new VM to complete setup.
# SSH in, then: bash ~/mtg-manager/scripts/vm_setup.sh
set -euo pipefail

REPO="/home/ubuntu/mtg-manager"

echo "=== MTG Manager VM Setup ==="
echo

# ── GitHub deploy key ─────────────────────────────────────────────────────────
if [ ! -f ~/.ssh/github_ed25519 ]; then
    echo "Step 1: GitHub deploy key"
    echo "Generate a key and add the public half to the repo's Deploy Keys in GitHub:"
    ssh-keygen -t ed25519 -f ~/.ssh/github_ed25519 -N "" -C "mtg-manager-vm"
    echo
    echo "Add this public key to https://github.com/H-Erskine/mtg-collection-manager/settings/keys"
    echo
    cat ~/.ssh/github_ed25519.pub
    echo
    cat >> ~/.ssh/config <<'EOF'
Host github.com
    IdentityFile ~/.ssh/github_ed25519
    StrictHostKeyChecking no
EOF
    read -rp "Press Enter once you've added the deploy key to GitHub..."
    # Switch repo remote to SSH now that the key is set up
    git -C "$REPO" remote set-url origin git@github.com:H-Erskine/mtg-collection-manager.git
    git -C "$REPO" pull
else
    echo "Step 1: GitHub deploy key already exists — skipping"
fi

echo

# ── config.toml ───────────────────────────────────────────────────────────────
if [ ! -f ~/.mtg_manager/config.toml ]; then
    echo "Step 2: Creating config.toml — fill in your Moxfield package IDs"
    cat > ~/.mtg_manager/config.toml <<'EOF'
db_path = "/home/ubuntu/.mtg_manager/collection.db"
web_static_dir = "/home/ubuntu/mtg-manager/web/static"

# Add your Moxfield package public IDs below.
# Find them in the URL of each package on moxfield.com/users/<you>/collection
[packages]
# white  = "your-public-id-here"
# blue   = "your-public-id-here"
# black  = "your-public-id-here"
# red    = "your-public-id-here"
# green  = "your-public-id-here"
# multi  = "your-public-id-here"
# land   = "your-public-id-here"
# sale   = "your-public-id-here"
EOF
    echo "Edit ~/.mtg_manager/config.toml with your package IDs, then press Enter"
    read -rp "Press Enter when config.toml is ready..."
else
    echo "Step 2: config.toml already exists — skipping"
fi

echo

# ── .env ──────────────────────────────────────────────────────────────────────
if [ ! -f "$REPO/.env" ]; then
    echo "Step 3: Creating .env — add your Discord bot token"
    cat > "$REPO/.env" <<'EOF'
DISCORD_BOT_TOKEN=your-token-here
OWNER_DISCORD_ID=your-discord-id-here
EOF
    echo "Edit $REPO/.env with your Discord credentials, then press Enter"
    read -rp "Press Enter when .env is ready..."
else
    echo "Step 3: .env already exists — skipping"
fi

echo

# ── Enable and start the bot ──────────────────────────────────────────────────
echo "Step 4: Enabling and starting mtg-bot service..."
sudo systemctl enable mtg-bot
sudo systemctl start mtg-bot
sudo systemctl status mtg-bot --no-pager

echo
echo "=== Setup complete ==="
echo "Website: http://$(curl -s ifconfig.me)"
echo "Bot logs: sudo journalctl -u mtg-bot -f"
