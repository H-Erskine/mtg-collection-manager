"""
Apply 'listed on cm' tag to matching cards in each Moxfield binder.

Reads card_tags for 'listed on cm', then searches every configured binder for
those card names and PUTs the tag via the Moxfield API. Does not require
for_sale_cards to be populated.

Usage:
    python -m scripts.tag_listed_on_cm
"""
import os
import sys
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

from mtg_manager.config import load_config
from mtg_manager.db import get_conn, get_card_moxfield_tags, add_moxfield_tag
from mtg_manager.moxfield import fetch_moxfield_card_ids
from mtg_manager.moxfield_write import fetch_deck_info, get_token, set_card_tags

TAG = "listed on cm"

cfg = load_config()
token = get_token()
if not token:
    print("ERROR: MOXFIELD_TOKEN not set")
    sys.exit(1)

# Collect all card names tagged 'listed on cm' locally
with get_conn(cfg.db_path) as conn:
    rows = conn.execute(
        "SELECT LOWER(name) AS name FROM card_tags WHERE LOWER(tag) = LOWER(?) GROUP BY LOWER(name)",
        (TAG,),
    ).fetchall()

if not rows:
    print(f"No cards tagged '{TAG}' in local DB.")
    sys.exit(0)

target_names: set[str] = {r["name"] for r in rows}
print(f"Targeting {len(target_names)} card name(s) tagged '{TAG}'")

tagged = failed = skipped = already = 0

for pkg in cfg.packages:
    binder_url = f"https://www.moxfield.com/decks/{pkg.public_id}"
    print(f"\n=== {pkg.color_group} ({pkg.public_id}) ===")

    try:
        card_ids = fetch_moxfield_card_ids(binder_url)
        binder_internal_id, binder_version = fetch_deck_info(pkg.public_id, token)
    except Exception as e:
        print(f"  ERROR fetching binder: {e}")
        continue

    # Only process cards that are both in this binder and in our target set
    matches = {name: cid for name, cid in card_ids.items() if name in target_names}
    if not matches:
        print(f"  None of the target cards found here")
        continue

    print(f"  {len(matches)} match(es) found in this binder")
    with get_conn(cfg.db_path) as conn:
        for name_lower, card_id in matches.items():
            existing = get_card_moxfield_tags(conn, card_id, pkg.public_id)
            if TAG in existing:
                print(f"  ALREADY: '{name_lower}'")
                already += 1
                continue
            new_tags = existing + [TAG]
            if set_card_tags(binder_internal_id, card_id, new_tags, token, binder_version, pkg.public_id):
                add_moxfield_tag(conn, card_id, pkg.public_id, TAG)
                print(f"  OK: '{name_lower}' -> {new_tags}")
                tagged += 1
            else:
                print(f"  FAIL: '{name_lower}'")
                failed += 1

print(f"\nDone — tagged: {tagged}, already had tag: {already}, failed: {failed}")
