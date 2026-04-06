import cloudscraper
import sys

url = sys.argv[1] if len(sys.argv) > 1 else "https://www.moxfield.com/decks/wm91pcraLES2rDaZjiEUeA"
public_id = url.split("/decks/")[-1].split("?")[0]
api_url = f"https://api2.moxfield.com/v3/decks/all/{public_id}"

headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://www.moxfield.com",
    "Referer": "https://www.moxfield.com/",
}

print(f"URL:       {url}")
print(f"Public ID: {public_id}")
print(f"API URL:   {api_url}")
print()

scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows"})
resp = scraper.get(api_url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
print()

data = resp.json()
print(f"Top-level keys: {list(data.keys())}")
print(f"Deck name:      {data.get('name')}")
print()

boards = data.get("boards", {})
print(f"Boards found: {list(boards.keys())}")
for board_name, board in boards.items():
    cards = board.get("cards", {})
    print(f"  {board_name}: {len(cards)} card entries")
    for card_id, item in list(cards.items())[:3]:
        name = item.get("card", {}).get("name", "?")
        qty = item.get("quantity", "?")
        print(f"    {qty}x {name}")
    if len(cards) > 3:
        print(f"    ... and {len(cards) - 3} more")
