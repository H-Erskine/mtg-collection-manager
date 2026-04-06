"""
Debug script: show raw Moxfield API data for a specific card across all packages.
Usage: python3 debug_package.py [card_name]
Default card: Bloodstained Mire
"""
import json
import sys
import tomllib
from pathlib import Path

import cloudscraper

card_search = sys.argv[1].lower() if len(sys.argv) > 1 else "bloodstained mire"

config_path = Path.home() / ".mtg_manager" / "config.toml"
with open(config_path, "rb") as f:
    config = tomllib.load(f)

packages = config["moxfield"]["packages"]

headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://www.moxfield.com",
    "Referer": "https://www.moxfield.com/",
}

scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})

for pkg in packages:
    color_group = pkg["color_group"]
    public_id = pkg["public_id"]
    print(f"\n=== {color_group} (id: {public_id}) ===")

    url = f"https://api2.moxfield.com/v3/decks/all/{public_id}"
    resp = scraper.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        print(f"  HTTP {resp.status_code} — skipping")
        continue

    data = resp.json()
    found = False
    for board_name, board in data.get("boards", {}).items():
        for item in board.get("cards", {}).values():
            name = item.get("card", {}).get("name", "")
            if card_search in name.lower():
                found = True
                card = item.get("card", {})
                print(f"  name={name}")
                print(f"  set={card.get('set')}  cn={card.get('cn')}")
                print(f"  quantity={item.get('quantity')}  isFoil={item.get('isFoil')}  finish={item.get('finish')}")
                print(f"  Raw card keys: {list(card.keys())}")
    if not found:
        print(f"  (not found)")
