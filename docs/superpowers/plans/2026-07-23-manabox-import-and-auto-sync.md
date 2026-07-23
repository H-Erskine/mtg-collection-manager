# ManaBox Import, Card Cache & Auto-Sync Throttle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user import a ManaBox CSV export as their collection (alternative to Moxfield packages), backed by a new shared Scryfall-ID → CMC cache, and auto-trigger a sync whenever a user edits their collection source (packages or CSV), throttled to once per 60 seconds.

**Architecture:** Three new/extended pieces, each independently testable: (1) `mtg_manager/card_cache.py` — a new global SQLite cache (`~/mtg_data/cards_cache.sqlite`) mapping `scryfall_id → cmc`, checked before any Scryfall network call; (2) `mtg_manager/manabox.py` — pure CSV parsing/validation with no I/O side effects, producing `OwnedCard` instances via the cache; (3) extensions to `api/users.py` (new throttle columns/functions) and `webapp/config.py` (new upload route, auto-sync wiring on package add/remove) that wire the above into the existing `/config` self-service flow.

**Tech Stack:** Python 3.11+, FastAPI, `sqlite3` (stdlib), `csv` (stdlib), `urllib.request` (stdlib, matching the existing Scryfall-calling convention in `web/export_meta.py` — no new HTTP dependency), pytest + `unittest.mock`.

## Global Constraints

- No new third-party dependencies — use stdlib `csv`/`urllib`/`sqlite3` throughout, matching existing conventions (`web/export_meta.py` uses `urllib`, not `requests`/`httpx`, for Scryfall).
- New global DB files live under `~/mtg_data/`, matching the existing `_REGISTRY_PATH`/`_USERS_DIR` convention in `api/users.py`.
- Every new SQLite module follows the `mtg_manager/db.py` / `api/users.py` pattern: a module-level `SCHEMA` string, a `@contextmanager` connection function that runs `executescript(SCHEMA)` + any migrations, commits on success, rolls back on exception.
- ManaBox-imported cards always use the fixed `color_group = 'manabox'` — never overlaps with Moxfield color groups, which are user-chosen strings.
- The auto-sync throttle (60 seconds) is entirely separate from the existing manual `/api/config/sync` throttle (`SYNC_THROTTLE_MINUTES = 60` minutes, `webapp/config.py:27`) — different column, different function, different constant. Do not touch `SYNC_THROTTLE_MINUTES` or `minutes_since_last_sync`.
- Per the design spec (`docs/superpowers/specs/2026-07-23-manabox-groups-meta-design.md` §5), the auto-sync trigger fires on **Moxfield package add/remove**. A ManaBox CSV upload does not additionally call `handle_sync` (which is Moxfield-specific — it fetches from the Moxfield API): the upload endpoint already writes directly to `owned_cards`, so the import *is* the up-to-date state; there is nothing further to sync. The upload route still records `mark_auto_synced`/`mark_synced` so the throttle state stays consistent for any subsequent package edit in the same 60s window.

---

### Task 1: Card cache module — schema, read, write (no network)

**Files:**
- Create: `mtg_manager/card_cache.py`
- Test: `tests/test_card_cache.py`

**Interfaces:**
- Produces: `_CACHE_PATH: Path` (module global, monkeypatchable exactly like `api.users._REGISTRY_PATH`), `get_cached_cmc(scryfall_ids: list[str]) -> dict[str, float]`, `cache_printings(printings: list[dict]) -> None` where each dict has keys `scryfall_id, name, set_code, collector_number, cmc`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_card_cache.py
import pytest


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    import mtg_manager.card_cache as cc
    monkeypatch.setattr(cc, "_CACHE_PATH", tmp_path / "cards_cache.sqlite")
    yield


def test_get_cached_cmc_empty_when_nothing_cached():
    from mtg_manager.card_cache import get_cached_cmc

    result = get_cached_cmc(["abc-123", "def-456"])

    assert result == {}


def test_cache_printings_then_get_cached_cmc_returns_them():
    from mtg_manager.card_cache import cache_printings, get_cached_cmc

    cache_printings([
        {"scryfall_id": "abc-123", "name": "Lightning Bolt", "set_code": "lea", "collector_number": "161", "cmc": 1.0},
        {"scryfall_id": "def-456", "name": "Counterspell", "set_code": "lea", "collector_number": "54", "cmc": 2.0},
    ])

    result = get_cached_cmc(["abc-123", "def-456", "not-cached"])

    assert result == {"abc-123": 1.0, "def-456": 2.0}


def test_cache_printings_upserts_on_conflict():
    from mtg_manager.card_cache import cache_printings, get_cached_cmc

    cache_printings([{"scryfall_id": "abc-123", "name": "Old Name", "set_code": "lea", "collector_number": "161", "cmc": 1.0}])
    cache_printings([{"scryfall_id": "abc-123", "name": "Old Name", "set_code": "lea", "collector_number": "161", "cmc": 99.0}])

    result = get_cached_cmc(["abc-123"])

    assert result == {"abc-123": 99.0}


def test_get_cached_cmc_empty_list_input_returns_empty_dict():
    from mtg_manager.card_cache import get_cached_cmc

    assert get_cached_cmc([]) == {}


def test_cache_printings_empty_list_is_a_noop():
    from mtg_manager.card_cache import cache_printings

    cache_printings([])  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_card_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_manager.card_cache'`

- [ ] **Step 3: Write the implementation**

```python
# mtg_manager/card_cache.py
"""Shared, cross-user cache of Scryfall printing data (scryfall_id -> cmc, etc).

Lives at ~/mtg_data/cards_cache.sqlite, separate from the per-user registry
and collection databases, since it holds bulk card-printing facts rather
than account/user metadata. Any lookup that needs a card's CMC by Scryfall
ID (e.g. ManaBox import) should check this cache first via get_cached_cmc,
falling back to resolve_cmc (added in a later task) for cache misses.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_CACHE_PATH = Path("~/mtg_data/cards_cache.sqlite").expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_printings (
    scryfall_id      TEXT NOT NULL PRIMARY KEY,
    name             TEXT NOT NULL,
    set_code         TEXT NOT NULL,
    collector_number TEXT NOT NULL,
    cmc              REAL NOT NULL DEFAULT 0,
    cached_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def _cache_conn():
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_CACHE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_cached_cmc(scryfall_ids: list[str]) -> dict[str, float]:
    """Return {scryfall_id: cmc} for whichever of the given ids are cached."""
    if not scryfall_ids:
        return {}
    with _cache_conn() as conn:
        placeholders = ",".join("?" * len(scryfall_ids))
        rows = conn.execute(
            f"SELECT scryfall_id, cmc FROM card_printings WHERE scryfall_id IN ({placeholders})",
            scryfall_ids,
        ).fetchall()
        return {r["scryfall_id"]: r["cmc"] for r in rows}


def cache_printings(printings: list[dict]) -> None:
    """Insert or update printing rows. Each dict needs scryfall_id, name, set_code, collector_number, cmc."""
    if not printings:
        return
    with _cache_conn() as conn:
        conn.executemany(
            """
            INSERT INTO card_printings (scryfall_id, name, set_code, collector_number, cmc, cached_at)
            VALUES (:scryfall_id, :name, :set_code, :collector_number, :cmc, datetime('now'))
            ON CONFLICT (scryfall_id) DO UPDATE SET
                name = excluded.name,
                set_code = excluded.set_code,
                collector_number = excluded.collector_number,
                cmc = excluded.cmc,
                cached_at = excluded.cached_at
            """,
            printings,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_card_cache.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg_manager/card_cache.py tests/test_card_cache.py
git commit -m "feat: add shared card-printing cache for scryfall_id -> cmc lookups"
```

---

### Task 2: Resolve CMC via cache + batched Scryfall lookup

**Files:**
- Modify: `mtg_manager/card_cache.py`
- Test: `tests/test_card_cache.py`

**Interfaces:**
- Consumes: `get_cached_cmc`, `cache_printings` (Task 1).
- Produces: `resolve_cmc(scryfall_ids: list[str]) -> dict[str, float]` — the function ManaBox import (Task 4) calls.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_card_cache.py
from unittest.mock import patch


def test_resolve_cmc_uses_cache_without_network_call():
    from mtg_manager.card_cache import cache_printings, resolve_cmc

    cache_printings([{"scryfall_id": "abc-123", "name": "Lightning Bolt", "set_code": "lea", "collector_number": "161", "cmc": 1.0}])

    with patch("mtg_manager.card_cache._scryfall_collection_by_id") as mock_fetch:
        result = resolve_cmc(["abc-123"])

    assert result == {"abc-123": 1.0}
    mock_fetch.assert_not_called()


def test_resolve_cmc_fetches_and_caches_misses():
    from mtg_manager.card_cache import get_cached_cmc, resolve_cmc

    fake_cards = [
        {"id": "new-1", "name": "Brainstorm", "set": "ice", "collector_number": "48", "cmc": 1.0},
    ]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=fake_cards) as mock_fetch:
        result = resolve_cmc(["new-1"])

    assert result == {"new-1": 1.0}
    mock_fetch.assert_called_once_with(["new-1"])
    # Second call must hit the cache, not the network again.
    with patch("mtg_manager.card_cache._scryfall_collection_by_id") as mock_fetch_2:
        result2 = resolve_cmc(["new-1"])
    assert result2 == {"new-1": 1.0}
    mock_fetch_2.assert_not_called()


def test_resolve_cmc_batches_in_groups_of_75():
    from mtg_manager.card_cache import resolve_cmc

    ids = [f"id-{i}" for i in range(150)]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=[]) as mock_fetch, \
         patch("mtg_manager.card_cache.time.sleep"):
        resolve_cmc(ids)

    assert mock_fetch.call_count == 2
    assert len(mock_fetch.call_args_list[0].args[0]) == 75
    assert len(mock_fetch.call_args_list[1].args[0]) == 75


def test_resolve_cmc_ignores_blank_ids_and_deduplicates():
    from mtg_manager.card_cache import resolve_cmc

    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=[]) as mock_fetch:
        resolve_cmc(["dup-1", "", "dup-1", None])

    mock_fetch.assert_called_once_with(["dup-1"])


def test_resolve_cmc_defaults_missing_cmc_field_to_zero():
    from mtg_manager.card_cache import resolve_cmc

    fake_cards = [{"id": "no-cmc", "name": "Weird Card", "set": "xyz", "collector_number": "1"}]
    with patch("mtg_manager.card_cache._scryfall_collection_by_id", return_value=fake_cards):
        result = resolve_cmc(["no-cmc"])

    assert result == {"no-cmc": 0.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_card_cache.py -v -k resolve_cmc`
Expected: FAIL with `AttributeError: module 'mtg_manager.card_cache' has no attribute 'resolve_cmc'`

- [ ] **Step 3: Write the implementation**

```python
# add to mtg_manager/card_cache.py, after the existing imports:
import json
import time
import urllib.request

# add after cache_printings():

def _scryfall_collection_by_id(scryfall_ids: list[str]) -> list[dict]:
    """POST a batch (<=75) of Scryfall IDs to /cards/collection, return matched card objects."""
    identifiers = [{"id": sid} for sid in scryfall_ids]
    payload = json.dumps({"identifiers": identifiers}).encode()
    req = urllib.request.Request(
        "https://api.scryfall.com/cards/collection",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mtg-manager/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data.get("data", [])


def resolve_cmc(scryfall_ids: list[str]) -> dict[str, float]:
    """Return {scryfall_id: cmc} for every non-blank id, using the cache first.

    Cache misses are batched to Scryfall's /cards/collection (75 ids per
    request, matching web/export_meta.py's existing batching convention) and
    the results are written back to the cache for every future caller.
    """
    unique_ids = list(dict.fromkeys(sid for sid in scryfall_ids if sid))
    result = get_cached_cmc(unique_ids)
    missing = [sid for sid in unique_ids if sid not in result]

    batch_size = 75
    for i in range(0, len(missing), batch_size):
        batch = missing[i:i + batch_size]
        cards = _scryfall_collection_by_id(batch)
        printings = []
        for card in cards:
            sid = card.get("id")
            if not sid:
                continue
            cmc = float(card.get("cmc", 0) or 0)
            result[sid] = cmc
            printings.append({
                "scryfall_id": sid,
                "name": card.get("name", ""),
                "set_code": card.get("set", ""),
                "collector_number": card.get("collector_number", ""),
                "cmc": cmc,
            })
        cache_printings(printings)
        if i + batch_size < len(missing):
            time.sleep(0.15)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_card_cache.py -v`
Expected: PASS (10 tests total)

- [ ] **Step 5: Commit**

```bash
git add mtg_manager/card_cache.py tests/test_card_cache.py
git commit -m "feat: resolve CMC from cache with batched Scryfall fallback"
```

---

### Task 3: ManaBox CSV parser and validator

**Files:**
- Create: `mtg_manager/manabox.py`
- Test: `tests/test_manabox.py`

**Interfaces:**
- Produces: `ManaboxImportError(ValueError)`, `ManaboxRow` dataclass (`name, set_code, collector_number, foil, quantity, scryfall_id`), `parse_manabox_csv(csv_text: str) -> list[ManaboxRow]`.
- No dependency on Task 1/2 — pure text parsing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_manabox.py
import pytest

from mtg_manager.manabox import ManaboxImportError, parse_manabox_csv

HEADER = "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added"


def _row(name="Eldritch Evolution", set_code="inr", collector_number="195", foil="normal", quantity="1", scryfall_id="606caf13-c0d3-4a61-9a1a-32f13b6448ab"):
    return f"{name},{set_code},Innistrad Remastered,{collector_number},{foil},rare,{quantity},102565,{scryfall_id},1.85,false,false,near_mint,en,EUR,2026-07-23T11:28:00.035386Z"


def test_parse_valid_csv_returns_rows():
    csv_text = HEADER + "\n" + _row() + "\n"

    rows = parse_manabox_csv(csv_text)

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Eldritch Evolution"
    assert row.set_code == "inr"
    assert row.collector_number == "195"
    assert row.foil is False
    assert row.quantity == 1
    assert row.scryfall_id == "606caf13-c0d3-4a61-9a1a-32f13b6448ab"


def test_parse_ignores_unused_columns():
    # Purchase price / rarity / condition etc. must not surface on ManaboxRow at all.
    csv_text = HEADER + "\n" + _row() + "\n"

    rows = parse_manabox_csv(csv_text)

    assert not hasattr(rows[0], "purchase_price")
    assert not hasattr(rows[0], "rarity")
    assert not hasattr(rows[0], "condition")


def test_parse_foil_value_sets_foil_true():
    csv_text = HEADER + "\n" + _row(foil="foil") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert rows[0].foil is True


def test_parse_etched_value_sets_foil_true():
    csv_text = HEADER + "\n" + _row(foil="etched") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert rows[0].foil is True


def test_parse_multiple_rows():
    csv_text = HEADER + "\n" + _row(name="Card A") + "\n" + _row(name="Card B", scryfall_id="other-id") + "\n"

    rows = parse_manabox_csv(csv_text)

    assert [r.name for r in rows] == ["Card A", "Card B"]


def test_parse_missing_required_column_raises():
    csv_text = "Name,Quantity\nBrainstorm,1\n"

    with pytest.raises(ManaboxImportError, match="missing required column"):
        parse_manabox_csv(csv_text)


def test_parse_empty_name_raises():
    csv_text = HEADER + "\n" + _row(name="") + "\n"

    with pytest.raises(ManaboxImportError, match="Name is required"):
        parse_manabox_csv(csv_text)


def test_parse_non_integer_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="abc") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be an integer"):
        parse_manabox_csv(csv_text)


def test_parse_zero_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="0") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        parse_manabox_csv(csv_text)


def test_parse_negative_quantity_raises():
    csv_text = HEADER + "\n" + _row(quantity="-1") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        parse_manabox_csv(csv_text)


def test_parse_no_header_raises():
    with pytest.raises(ManaboxImportError, match="no header row"):
        parse_manabox_csv("")


def test_parse_oversized_file_raises():
    csv_text = HEADER + "\n" + _row() + "\n"
    with pytest.raises(ManaboxImportError, match="exceeds maximum size"):
        parse_manabox_csv(csv_text, max_bytes=10)


def test_parse_too_many_rows_raises():
    csv_text = HEADER + "\n" + "".join(_row(scryfall_id=f"id-{i}") + "\n" for i in range(5))
    with pytest.raises(ManaboxImportError, match="exceeds maximum of"):
        parse_manabox_csv(csv_text, max_rows=3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manabox.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_manager.manabox'`

- [ ] **Step 3: Write the implementation**

```python
# mtg_manager/manabox.py
"""Parse and validate ManaBox collection-export CSVs.

ManaBox's export has 16 columns; only 6 map onto our owned_cards schema
(Name, Set code, Collector number, Foil, Quantity, Scryfall ID). Everything
else (Set name, Rarity, ManaBox ID, Purchase price, Misprint, Altered,
Condition, Language, Purchase price currency, Added) is read from the file
and deliberately discarded -- it is never written anywhere.
"""

import csv
import io
from dataclasses import dataclass

REQUIRED_COLUMNS = {"Name", "Set code", "Collector number", "Foil", "Quantity", "Scryfall ID"}
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_ROWS = 20_000


class ManaboxImportError(ValueError):
    """Raised when a ManaBox CSV fails validation."""


@dataclass
class ManaboxRow:
    name: str
    set_code: str
    collector_number: str
    foil: bool
    quantity: int
    scryfall_id: str


def parse_manabox_csv(
    csv_text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> list[ManaboxRow]:
    """Parse a ManaBox CSV export into validated rows, or raise ManaboxImportError."""
    if len(csv_text.encode("utf-8")) > max_bytes:
        raise ManaboxImportError(f"CSV exceeds maximum size of {max_bytes} bytes")

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise ManaboxImportError("CSV has no header row")

    missing = REQUIRED_COLUMNS - set(reader.fieldnames)
    if missing:
        raise ManaboxImportError(f"CSV missing required column(s): {', '.join(sorted(missing))}")

    rows: list[ManaboxRow] = []
    for line_num, raw in enumerate(reader, start=2):  # line 1 is the header
        if line_num - 1 > max_rows:
            raise ManaboxImportError(f"CSV exceeds maximum of {max_rows} rows")

        name = (raw.get("Name") or "").strip()
        if not name:
            raise ManaboxImportError(f"Row {line_num}: Name is required")

        quantity_raw = (raw.get("Quantity") or "").strip()
        try:
            quantity = int(quantity_raw)
        except ValueError:
            raise ManaboxImportError(f"Row {line_num}: Quantity must be an integer, got {quantity_raw!r}")
        if quantity <= 0:
            raise ManaboxImportError(f"Row {line_num}: Quantity must be positive, got {quantity}")

        foil_raw = (raw.get("Foil") or "").strip().lower()
        foil = foil_raw not in ("", "normal")

        rows.append(ManaboxRow(
            name=name,
            set_code=(raw.get("Set code") or "").strip().lower(),
            collector_number=(raw.get("Collector number") or "").strip(),
            foil=foil,
            quantity=quantity,
            scryfall_id=(raw.get("Scryfall ID") or "").strip(),
        ))

    return rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manabox.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg_manager/manabox.py tests/test_manabox.py
git commit -m "feat: add ManaBox CSV parser with column and row validation"
```

---

### Task 4: ManaBox rows -> OwnedCard, with CMC resolution

**Files:**
- Modify: `mtg_manager/manabox.py`
- Test: `tests/test_manabox.py`

**Interfaces:**
- Consumes: `ManaboxRow`, `parse_manabox_csv` (Task 3); `mtg_manager.models.OwnedCard` (existing: `name, quantity, color_group, set_code='', collector_number='', foil=False, cmc=0.0, any_version=False`); `resolve_cmc` (Task 2, injected as a parameter for testability).
- Produces: `import_manabox_csv(csv_text: str, resolve_cmc_fn=resolve_cmc) -> list[OwnedCard]` — called directly by the upload route in Task 7.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_manabox.py
from mtg_manager.manabox import import_manabox_csv
from mtg_manager.models import OwnedCard


def test_import_manabox_csv_builds_owned_cards_with_manabox_color_group():
    csv_text = HEADER + "\n" + _row(name="Eldritch Evolution", set_code="inr", collector_number="195", quantity="2", scryfall_id="scry-1") + "\n"

    def fake_resolve_cmc(scryfall_ids):
        assert scryfall_ids == ["scry-1"]
        return {"scry-1": 3.0}

    cards = import_manabox_csv(csv_text, resolve_cmc_fn=fake_resolve_cmc)

    assert cards == [
        OwnedCard(
            name="Eldritch Evolution",
            quantity=2,
            color_group="manabox",
            set_code="inr",
            collector_number="195",
            foil=False,
            cmc=3.0,
        )
    ]


def test_import_manabox_csv_defaults_cmc_to_zero_when_unresolved():
    csv_text = HEADER + "\n" + _row(scryfall_id="unknown-id") + "\n"

    cards = import_manabox_csv(csv_text, resolve_cmc_fn=lambda ids: {})

    assert cards[0].cmc == 0.0


def test_import_manabox_csv_propagates_validation_errors():
    csv_text = HEADER + "\n" + _row(quantity="0") + "\n"

    with pytest.raises(ManaboxImportError, match="Quantity must be positive"):
        import_manabox_csv(csv_text, resolve_cmc_fn=lambda ids: {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_manabox.py -v -k import_manabox_csv`
Expected: FAIL with `ImportError: cannot import name 'import_manabox_csv'`

- [ ] **Step 3: Write the implementation**

```python
# add to mtg_manager/manabox.py, at the top:
from mtg_manager.card_cache import resolve_cmc
from mtg_manager.models import OwnedCard

# add at the end of the file:

def import_manabox_csv(csv_text: str, resolve_cmc_fn=resolve_cmc) -> list[OwnedCard]:
    """Parse a ManaBox CSV and return OwnedCard rows ready for db.upsert_cards.

    resolve_cmc_fn defaults to card_cache.resolve_cmc but is overridable so
    callers (and tests) can avoid the cache/network dependency entirely.
    """
    rows = parse_manabox_csv(csv_text)
    cmc_by_id = resolve_cmc_fn([r.scryfall_id for r in rows])
    return [
        OwnedCard(
            name=r.name,
            quantity=r.quantity,
            color_group="manabox",
            set_code=r.set_code,
            collector_number=r.collector_number,
            foil=r.foil,
            cmc=cmc_by_id.get(r.scryfall_id, 0.0),
        )
        for r in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_manabox.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg_manager/manabox.py tests/test_manabox.py
git commit -m "feat: convert ManaBox rows to OwnedCard with cached CMC lookup"
```

---

### Task 5: Registry — auto-sync throttle column and functions

**Files:**
- Modify: `api/users.py`
- Test: `tests/test_users.py`

**Interfaces:**
- Produces: `seconds_since_last_auto_sync(user_id: str) -> float | None`, `mark_auto_synced(user_id: str) -> None`, migration `_migrate_auto_sync_column(conn)` wired into `_registry_conn()`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_users.py
def test_seconds_since_last_auto_sync_none_when_never_synced():
    from api.users import ensure_user, seconds_since_last_auto_sync

    ensure_user("google:alice@example.com")

    assert seconds_since_last_auto_sync("google:alice@example.com") is None


def test_mark_auto_synced_then_seconds_since_is_near_zero():
    from api.users import ensure_user, mark_auto_synced, seconds_since_last_auto_sync

    ensure_user("google:alice@example.com")
    mark_auto_synced("google:alice@example.com")

    seconds = seconds_since_last_auto_sync("google:alice@example.com")

    assert seconds is not None
    assert 0 <= seconds < 5


def test_seconds_since_last_auto_sync_unknown_user_returns_none():
    from api.users import seconds_since_last_auto_sync

    assert seconds_since_last_auto_sync("google:nobody@example.com") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_users.py -v -k auto_sync`
Expected: FAIL with `ImportError: cannot import name 'seconds_since_last_auto_sync'`

- [ ] **Step 3: Write the implementation**

```python
# in api/users.py: add a new migration function after _migrate_onboarding_column (line 122)

def _migrate_auto_sync_column(conn: sqlite3.Connection) -> None:
    """One-time addition of the last_auto_synced_at column for pre-existing registries."""
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "users" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "last_auto_synced_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN last_auto_synced_at TEXT")


# in _registry_conn() (line 125-142), add the call alongside the other migrations:
@contextmanager
def _registry_conn():
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate_legacy_discord_id(conn)
        conn.executescript(REGISTRY_SCHEMA)
        _migrate_profile_columns(conn)
        _migrate_onboarding_column(conn)
        _migrate_auto_sync_column(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# add near mark_synced() / minutes_since_last_sync() (around line 298-323):

def mark_auto_synced(user_id: str) -> None:
    with _registry_conn() as conn:
        conn.execute(
            "UPDATE users SET last_auto_synced_at = datetime('now') WHERE user_id = ?",
            (user_id,),
        )


def seconds_since_last_auto_sync(user_id: str) -> float | None:
    """Return seconds since the last auto-triggered sync, or None if never auto-synced."""
    with _registry_conn() as conn:
        row = conn.execute(
            """
            SELECT
                CASE
                    WHEN last_auto_synced_at IS NULL THEN NULL
                    ELSE (julianday('now') - julianday(last_auto_synced_at)) * 24 * 60 * 60
                END AS seconds_ago
            FROM users WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return row["seconds_ago"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: PASS (all existing tests plus the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add api/users.py tests/test_users.py
git commit -m "feat: add per-user auto-sync throttle timestamp to registry"
```

---

### Task 6: Wire auto-sync into package add/remove routes

**Files:**
- Modify: `webapp/config.py`
- Test: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `seconds_since_last_auto_sync`, `mark_auto_synced` (Task 5); existing `handle_sync`, `mark_synced`, `is_owner`, `get_user_config`.
- Produces: response bodies for `POST /api/config/packages` and `DELETE /api/config/packages/{color_group}` gain an `"auto_sync"` key: `{"synced": true, "message": "..."}` or `{"synced": false, "retry_after_seconds": N}`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_config.py
from unittest.mock import patch


def test_add_package_triggers_auto_sync(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/packages", json={"color_group": "Red", "public_id": "some-id"})

    assert response.status_code == 200
    assert response.json()["auto_sync"] == {"synced": True, "message": "Synced."}
    mock_sync.assert_called_once()


def test_add_package_auto_sync_throttled_within_60_seconds(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, mark_auto_synced
    ensure_user("google:alice@example.com")
    mark_auto_synced("google:alice@example.com")

    with patch.object(config_mod, "handle_sync") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post("/api/config/packages", json={"color_group": "Blue", "public_id": "another-id"})

    assert response.status_code == 200
    body = response.json()["auto_sync"]
    assert body["synced"] is False
    assert 0 < body["retry_after_seconds"] <= 60
    mock_sync.assert_not_called()


def test_remove_package_triggers_auto_sync(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_package, ensure_user
    ensure_user("google:alice@example.com")
    add_package("google:alice@example.com", "Red", "some-id")

    with patch.object(config_mod, "handle_sync", return_value="Synced.") as mock_sync:
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.delete("/api/config/packages/Red")

    assert response.status_code == 200
    assert response.json()["auto_sync"]["synced"] is True
    mock_sync.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k auto_sync`
Expected: FAIL — `KeyError: 'auto_sync'` (route doesn't return that key yet)

- [ ] **Step 3: Write the implementation**

```python
# in webapp/config.py: update the imports (line 7-20) to add:
from api.users import (
    add_package,
    is_owner,
    is_whitelist_admin,
    list_packages,
    list_profiles,
    mark_auto_synced,
    mark_onboarded,
    mark_synced,
    minutes_since_last_sync,
    remove_package,
    seconds_since_last_auto_sync,
    set_formats,
    set_profile,
    set_sort,
)
from api.users import get_user_config

# add module-level constant near SYNC_THROTTLE_MINUTES (line 27):
AUTO_SYNC_THROTTLE_SECONDS = 60


# add helper function after _is_admin (line 48-49):
def _trigger_auto_sync(user_id: str) -> dict:
    """Fire an auto-sync for this user's Moxfield packages, throttled to once per
    AUTO_SYNC_THROTTLE_SECONDS. Reloads Config fresh so a just-added/removed
    package is reflected (the cfg passed into the route was fetched before the
    mutation)."""
    seconds = seconds_since_last_auto_sync(user_id)
    if seconds is not None and seconds < AUTO_SYNC_THROTTLE_SECONDS:
        return {"synced": False, "retry_after_seconds": int(AUTO_SYNC_THROTTLE_SECONDS - seconds) or 1}

    fresh_cfg = get_user_config(user_id)
    if fresh_cfg is None or not fresh_cfg.packages:
        return {"synced": False, "retry_after_seconds": 0}

    message = handle_sync(fresh_cfg, is_owner=is_owner(user_id))
    mark_auto_synced(user_id)
    mark_synced(user_id)
    return {"synced": True, "message": message}


# replace add_config_package (line 68-73):
@router.post("/api/config/packages")
async def add_config_package(request: Request, body: PackageIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    public_id = public_id_from_url(body.public_id) or body.public_id.strip()
    add_package(user_id, body.color_group, public_id)
    return {"ok": True, "auto_sync": _trigger_auto_sync(user_id)}


# replace remove_config_package (line 76-80):
@router.delete("/api/config/packages/{color_group}")
async def remove_config_package(request: Request, color_group: str, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_package(user_id, color_group)
    return {"ok": True, "auto_sync": _trigger_auto_sync(user_id)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: PASS (all existing config tests plus the 3 new ones)

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: auto-trigger a throttled sync on package add/remove"
```

---

### Task 7: ManaBox CSV upload route

**Files:**
- Modify: `webapp/config.py`
- Test: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `import_manabox_csv`, `ManaboxImportError` (Task 4); `mtg_manager.db.get_conn`, `mtg_manager.db.clear_color_group`, `mtg_manager.db.upsert_cards` (existing); `_trigger_auto_sync` (Task 6, but per the Global Constraints note, the CSV route does **not** call `_trigger_auto_sync`/`handle_sync` — the import itself is the up-to-date state. It does call `mark_auto_synced`/`mark_synced` directly so the throttle stays consistent).
- Produces: `POST /api/config/manabox` — `{"ok": true, "imported": N}` on success, `400` with `{"detail": "..."}` on validation failure.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_webapp_config.py
def test_upload_manabox_csv_imports_cards(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config
    ensure_user("google:alice@example.com")

    csv_text = (
        "Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,ManaBox ID,Scryfall ID,"
        "Purchase price,Misprint,Altered,Condition,Language,Purchase price currency,Added\n"
        "Brainstorm,ice,Ice Age,48,normal,rare,3,1,scry-1,1.00,false,false,near_mint,en,EUR,2026-07-23T00:00:00Z\n"
    )

    with patch.object(config_mod, "import_manabox_csv") as mock_import:
        from mtg_manager.models import OwnedCard
        mock_import.return_value = [
            OwnedCard(name="Brainstorm", quantity=3, color_group="manabox", set_code="ice", collector_number="48", foil=False, cmc=1.0)
        ]
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            response = c.post(
                "/api/config/manabox",
                files={"file": ("collection.csv", csv_text, "text/csv")},
            )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "imported": 1}

    cfg = get_user_config("google:alice@example.com")
    from mtg_manager.db import get_conn
    with get_conn(cfg.db_path) as conn:
        row = conn.execute("SELECT quantity, color_group FROM owned_cards WHERE name = 'Brainstorm'").fetchone()
    assert row["quantity"] == 3
    assert row["color_group"] == "manabox"


def test_upload_manabox_csv_rejects_invalid_csv(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post(
            "/api/config/manabox",
            files={"file": ("bad.csv", "Name,Quantity\nBrainstorm,1\n", "text/csv")},
        )

    assert response.status_code == 400
    assert "missing required column" in response.json()["detail"]


def test_upload_manabox_csv_replaces_previous_manabox_import(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import OwnedCard
    ensure_user("google:alice@example.com")
    cfg = get_user_config("google:alice@example.com")
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Old Card", quantity=1, color_group="manabox", set_code="xxx", collector_number="1")])

    with patch.object(config_mod, "import_manabox_csv") as mock_import:
        mock_import.return_value = [
            OwnedCard(name="New Card", quantity=1, color_group="manabox", set_code="yyy", collector_number="2")
        ]
        with client as c:
            with c.session_transaction() as session:
                session["user_id"] = "google:alice@example.com"
            c.post("/api/config/manabox", files={"file": ("c.csv", "irrelevant", "text/csv")})

    with get_conn(cfg.db_path) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM owned_cards WHERE color_group = 'manabox'")}
    assert names == {"New Card"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_webapp_config.py -v -k manabox`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

```python
# in webapp/config.py: add to imports at top:
from fastapi import File, UploadFile

from mtg_manager.db import clear_color_group, get_conn, upsert_cards
from mtg_manager.manabox import ManaboxImportError, import_manabox_csv

# add new route after remove_config_package (after the block added in Task 6):

@router.post("/api/config/manabox")
async def upload_manabox_csv(request: Request, file: UploadFile = File(...), cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    try:
        cards = import_manabox_csv(text)
    except ManaboxImportError as e:
        raise HTTPException(status_code=400, detail=str(e))

    with get_conn(cfg.db_path) as conn:
        clear_color_group(conn, "manabox")
        upsert_cards(conn, cards)

    mark_auto_synced(user_id)
    mark_synced(user_id)
    return {"ok": True, "imported": len(cards)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: PASS (all config tests, including the 3 new manabox ones)

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: add ManaBox CSV upload endpoint to /config"
```

---

### Task 8: Config page UI — source toggle, CSV upload, auto-sync feedback

**Files:**
- Modify: `webapp/static/config.html:79-114` (Moxfield Packages / Sync sections) and its inline `<script>` block (`addPackage`, `removePackage`, `syncNow` functions).

**Interfaces:**
- Consumes: `POST /api/config/packages` and `DELETE /api/config/packages/{color_group}` (now returning `auto_sync`, Task 6), `POST /api/config/manabox` (Task 7).
- No automated test — this repo has no JS test harness. Verified manually per Step 3 below (matches this project's existing pattern of manual browser verification for `/config` UI changes).

- [ ] **Step 1: Read the current section to modify**

Read `webapp/static/config.html` in full to get exact current line numbers and surrounding JS function bodies for `addPackage`, `removePackage`, `syncNow` before editing (line numbers shift as the file evolves, so re-check them here rather than trusting the numbers noted during planning).

- [ ] **Step 2: Add a "ManaBox Import" section to the HTML, right after the Moxfield Packages `<section>`**

```html
<section>
  <h2>ManaBox Import</h2>
  <p class="muted">Upload a ManaBox collection export (CSV) as an alternative to Moxfield packages. Re-uploading replaces your previous ManaBox import.</p>
  <input type="file" id="manabox-file" accept=".csv">
  <button class="btn" onclick="uploadManaboxCsv()">Import CSV</button>
  <div id="manabox-msg" class="msg"></div>
</section>
```

- [ ] **Step 3: Add the corresponding JS, following the same fetch/message-div pattern as `addPackage()`/`syncNow()`**

```javascript
async function uploadManaboxCsv() {
  const input = document.getElementById('manabox-file');
  const msgEl = document.getElementById('manabox-msg');
  if (!input.files.length) {
    msgEl.textContent = 'Choose a CSV file first.';
    return;
  }
  const formData = new FormData();
  formData.append('file', input.files[0]);
  const response = await fetch('/api/config/manabox', { method: 'POST', body: formData });
  const data = await response.json();
  if (!response.ok) {
    msgEl.textContent = data.detail || 'Import failed.';
    return;
  }
  msgEl.textContent = `Imported ${data.imported} card(s).`;
  input.value = '';
}

function showAutoSyncStatus(autoSync) {
  const msgEl = document.getElementById('pkg-msg');
  if (!autoSync) return;
  if (autoSync.synced) {
    msgEl.textContent = `Auto-synced: ${autoSync.message}`;
  } else if (autoSync.retry_after_seconds) {
    msgEl.textContent = `Auto-syncing shortly (throttled, ${autoSync.retry_after_seconds}s remaining).`;
  }
}
```

- [ ] **Step 4: Wire `showAutoSyncStatus` into the existing `addPackage()`/`removePackage()` response handling**

Update the `.then(data => ...)` (or `await` equivalent) bodies of `addPackage()` and `removePackage()` to call `showAutoSyncStatus(data.auto_sync)` after their existing success handling (e.g. after `renderPackages()` is re-called), so the "auto-syncing shortly / auto-synced" message shows in the same `#pkg-msg` element already used for package add/remove feedback.

- [ ] **Step 5: Manually verify in the browser**

Run the app locally (see the project's `run` skill / existing dev-server instructions), log in, go to `/config`:
- Add a Moxfield package → confirm the "Auto-synced: ..." message appears (or "Auto-syncing shortly" if within 60s of a previous sync).
- Upload the sample file at `d:\Harry shit\Downloads\manabox-scan-2026-07-23.csv` → confirm "Imported N card(s)." appears and the imported count is sane (83 lines of sample data minus header).
- Upload a CSV missing required columns → confirm the error message renders instead of a silent failure.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/config.html
git commit -m "feat: add ManaBox import UI and auto-sync feedback to /config page"
```

---

## Plan Self-Review Notes

- **Spec coverage:** ManaBox CSV import (§1) → Tasks 1-4, 7, 8. Auto-sync throttle (§5, auto-sync half) → Tasks 5-6. Shared card cache (§5, cache half) → Tasks 1-2. The design spec's groups/permissions/Meta-tab sections (§2-4) are intentionally out of scope for this plan — they're covered by the separate groups/permissions/Meta-tab plan per the earlier scope-split decision.
- **Type consistency:** `OwnedCard` field names (`name, quantity, color_group, set_code, collector_number, foil, cmc`) are used identically across Tasks 3, 4, and 7 — matches the existing dataclass in `mtg_manager/models.py:4-13` verbatim, no new fields added.
- **Function name consistency:** `resolve_cmc` (Task 2) is the exact name consumed by `import_manabox_csv`'s default parameter (Task 4); `import_manabox_csv` and `ManaboxImportError` (Task 4) are the exact names imported by `webapp/config.py` (Task 7); `seconds_since_last_auto_sync`/`mark_auto_synced` (Task 5) are the exact names imported by `webapp/config.py` (Tasks 6-7).
