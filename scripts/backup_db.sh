#!/bin/bash
# Daily backup of all MTG Manager SQLite databases to OCI Object Storage.
# Uses instance principal auth — no credentials needed on the VM.
# Retention is enforced by the bucket's 7-day retention rule (set in Terraform).
set -euo pipefail

OCI="/home/ubuntu/mtg-manager/.venv/bin/oci"
BUCKET="mtg-db-backup"
DATE=$(date +%Y%m%d)

backup_file() {
    local src="$1"
    local name="$2"
    if [ ! -f "$src" ]; then
        echo "Skipping $name (not found)"
        return
    fi
    echo "Backing up $name..."
    "$OCI" os object put \
        --bucket-name "$BUCKET" \
        --file "$src" \
        --name "${name}-${DATE}.db" \
        --auth instance_principal \
        --force
}

# Owner collection DB
backup_file "$HOME/.mtg_manager/collection.db" "collection"

# Multi-user registry
backup_file "$HOME/mtg_data/registry.sqlite" "registry"

# Per-user DBs
if [ -d "$HOME/mtg_data/users" ]; then
    for db in "$HOME/mtg_data/users"/*.sqlite; do
        [ -f "$db" ] || continue
        base=$(basename "$db" .sqlite)
        backup_file "$db" "user-${base}"
    done
fi

echo "Backup complete for $DATE"
