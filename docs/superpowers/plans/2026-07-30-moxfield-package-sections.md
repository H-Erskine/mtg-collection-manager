# Moxfield Package Sections — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single flat, name-sniffed Moxfield package list in the web multi-user registry with four explicit sections — Collection, Sale, Wants, Decks — each holding any number of packages, with correct sync semantics per section.

**Architecture:** `user_packages` (registry SQLite) gains an `id` primary key, a `section` column, and a nullable `price` column. `Config` (shared dataclass) gains `sale_packages`/`wants_packages`/`deck_packages` alongside the existing `packages` (now specifically meaning collection). The web sync path (`api/handlers.py`) is rewritten to clear each destination table once per run (not once per package) and to process each section explicitly instead of sniffing `$`/`"Wants"` out of the Moxfield deck's own title. A new deck-auto-build path reuses the existing allocation logic from `handle_build`.

**Tech Stack:** Python 3.11+, FastAPI, SQLite (stdlib `sqlite3`), pytest, vanilla JS/HTML for the config page.

## Global Constraints

- The CLI (`mtg_manager/cli.py`, `mtg_manager/config.py`'s `load_config`, `config.toml`) is **not touched** — it is being decommissioned and keeps its existing `$`/`"Wants"` name-sniffing behavior exactly as-is.
- `MoxfieldPackage` keeps its existing `color_group`/`public_id` attribute names (not renamed to "label") — this matches the spec's intent but keeps the huge existing `.color_group` call-site surface (moxfield.py, handlers.py, scripts, tests) untouched for the collection section.
- Any destination table (`for_sale_cards`, `wants_cards`) is cleared **once per sync run**, never once per package.
- Sale packages populate both `owned_cards` (collection) and `for_sale_cards`. Wants packages populate only `wants_cards`. Deck packages populate only `built_decks`/`allocated_cards`.
- `built_decks.box_name` for auto-built deck packages is a freshly generated UUID string — no user-facing box concept for this flow.
- Every schema/behavior change must keep existing non-package registry tests green unless the test is explicitly being updated in this plan because its assertions target the old contract.

---

### Task 1: Registry schema — sectioned `user_packages` table + CRUD

**Files:**
- Modify: `api/users.py` (schema string, `_registry_conn`, `add_package`, `remove_package`, `list_packages`, `get_user_config`)
- Test: `tests/test_users.py`

**Interfaces:**
- Produces:
  - `VALID_PACKAGE_SECTIONS: tuple[str, ...] = ("collection", "sale", "wants", "decks")`
  - `add_package(user_id: str, section: str, color_group: str, public_id: str, price: float | None = None) -> int` — returns new row id, raises `ValueError` for an invalid section
  - `remove_package(user_id: str, package_id: int) -> bool`
  - `list_packages(user_id: str, section: str | None = None) -> list[dict]` — each dict is `{"id": int, "section": str, "color_group": str, "public_id": str, "price": float | None}`
  - `get_user_config(user_id)` returns `Config` with `packages` (section="collection"), `sale_packages` (section="sale", `SalePackage` from Task 2), `wants_packages` (section="wants", `MoxfieldPackage`), `deck_packages` (section="decks", `MoxfieldPackage`)

- [ ] **Step 1: Write failing tests for the new CRUD contract**

Replace the package-related tests in `tests/test_users.py` (the old 3-arg `add_package`/tuple-`list_packages` contract is being replaced, not kept alongside). Replace these existing tests:

```python
def test_get_user_config_returns_config_for_known(tmp_path):
    users_mod.ensure_user("discord:222")
    users_mod.add_package("discord:222", "collection", "Red", "abc123")
    cfg = users_mod.get_user_config("discord:222")
    assert cfg is not None
    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert cfg.packages[0].public_id == "abc123"


def test_add_and_list_packages():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "collection", "White", "w1")
    users_mod.add_package("discord:333", "collection", "Blue", "u1")
    pkgs = users_mod.list_packages("discord:333")
    color_groups = {p["color_group"] for p in pkgs}
    assert color_groups == {"White", "Blue"}


def test_add_package_allows_duplicate_labels_in_same_section():
    """Any number of packages per section — duplicate labels must not collide or overwrite."""
    users_mod.ensure_user("discord:333")
    id1 = users_mod.add_package("discord:333", "collection", "White", "w1")
    id2 = users_mod.add_package("discord:333", "collection", "White", "w2")
    assert id1 != id2
    pkgs = users_mod.list_packages("discord:333", section="collection")
    assert {p["public_id"] for p in pkgs} == {"w1", "w2"}


def test_add_package_rejects_invalid_section():
    users_mod.ensure_user("discord:333")
    with pytest.raises(ValueError):
        users_mod.add_package("discord:333", "bogus", "White", "w1")


def test_add_sale_package_stores_price():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "sale", "Binder A", "s1", price=5.0)
    pkgs = users_mod.list_packages("discord:333", section="sale")
    assert pkgs[0]["price"] == 5.0


def test_list_packages_filters_by_section():
    users_mod.ensure_user("discord:333")
    users_mod.add_package("discord:333", "collection", "White", "w1")
    users_mod.add_package("discord:333", "sale", "Binder A", "s1", price=5.0)
    users_mod.add_package("discord:333", "wants", "Wants", "n1")
    users_mod.add_package("discord:333", "decks", "Burn", "d1")

    assert len(users_mod.list_packages("discord:333", section="collection")) == 1
    assert len(users_mod.list_packages("discord:333", section="sale")) == 1
    assert len(users_mod.list_packages("discord:333", section="wants")) == 1
    assert len(users_mod.list_packages("discord:333", section="decks")) == 1
    assert len(users_mod.list_packages("discord:333")) == 4


def test_remove_package():
    users_mod.ensure_user("discord:444")
    pkg_id = users_mod.add_package("discord:444", "collection", "Green", "g1")
    removed = users_mod.remove_package("discord:444", pkg_id)
    assert removed
    assert users_mod.list_packages("discord:444") == []


def test_remove_nonexistent_package_returns_false():
    users_mod.ensure_user("discord:444")
    assert not users_mod.remove_package("discord:444", 999999)


def test_get_user_config_splits_packages_by_section():
    users_mod.ensure_user("discord:222")
    users_mod.add_package("discord:222", "collection", "Red", "c1")
    users_mod.add_package("discord:222", "sale", "Binder A", "s1", price=7.5)
    users_mod.add_package("discord:222", "wants", "Wants", "n1")
    users_mod.add_package("discord:222", "decks", "Burn", "d1")

    cfg = users_mod.get_user_config("discord:222")

    assert len(cfg.packages) == 1
    assert cfg.packages[0].color_group == "Red"
    assert len(cfg.sale_packages) == 1
    assert cfg.sale_packages[0].color_group == "Binder A"
    assert cfg.sale_packages[0].price == 7.5
    assert len(cfg.wants_packages) == 1
    assert cfg.wants_packages[0].color_group == "Wants"
    assert len(cfg.deck_packages) == 1
    assert cfg.deck_packages[0].color_group == "Burn"
```

Delete the old `test_add_package_upserts` and `test_remove_package_case_insensitive` tests — upsert-by-label and case-insensitive-label removal no longer apply now that packages are keyed by `id`.

Update every other existing call site in `tests/test_users.py` that calls `add_package(user_id, color_group, public_id)` (3-arg form) to the new `add_package(user_id, "collection", color_group, public_id)` form, and every `cfg.packages[0].color_group` assertion stays as-is (attribute name unchanged). Specifically fix:
- `test_get_user_config_google_owner_uses_registry_packages`
- `test_remove_whitelisted_user_deletes_everything`

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_users.py -v -k "package"`
Expected: FAIL — `add_package()` doesn't accept a `section` argument yet, `list_packages()` doesn't return dicts yet, `Config` has no `sale_packages`/`wants_packages`/`deck_packages`.

- [ ] **Step 3: Update the schema and migration**

In `api/users.py`, replace the `user_packages` table in `REGISTRY_SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS user_packages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    section     TEXT NOT NULL CHECK (section IN ('collection','sale','wants','decks')),
    color_group TEXT NOT NULL,
    public_id   TEXT NOT NULL,
    price       REAL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Add a migration function, following the existing `_migrate_*` pattern, and call it in `_registry_conn()` in the same early slot as `_migrate_legacy_discord_id` (i.e. **before** `conn.executescript(REGISTRY_SCHEMA)` runs, since `CREATE TABLE IF NOT EXISTS` is a no-op against an existing old-shape table):

```python
def _migrate_package_sections(conn: sqlite3.Connection) -> None:
    """One-time migration of the old (user_id, color_group) keyed user_packages
    table to the new id-keyed, sectioned schema. Classifies each existing row
    into collection/sale/wants using the same name-sniffing rules the old
    runtime code used ($-prefixed name = sale, name == 'Wants' = wants,
    otherwise collection), so existing users see no behavior change until
    they manually re-file a package into a different section.
    """
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "user_packages" not in tables:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_packages)").fetchall()}
    if "section" in cols:
        return  # already migrated

    old_rows = conn.execute(
        "SELECT user_id, color_group, public_id FROM user_packages"
    ).fetchall()

    conn.execute("ALTER TABLE user_packages RENAME TO user_packages_old")
    conn.execute("""
        CREATE TABLE user_packages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            section     TEXT NOT NULL CHECK (section IN ('collection','sale','wants','decks')),
            color_group TEXT NOT NULL,
            public_id   TEXT NOT NULL,
            price       REAL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    from mtg_manager.moxfield import fetch_package_cards
    from mtg_manager.config import MoxfieldPackage
    import re as _re
    price_re = _re.compile(r"^\$(\d+(?:\.\d+)?)")

    for row in old_rows:
        section = "collection"
        price = None
        try:
            _cards, pkg_name = fetch_package_cards(
                MoxfieldPackage(color_group=row["color_group"], public_id=row["public_id"])
            )
            if pkg_name.startswith("$"):
                section = "sale"
                m = price_re.match(pkg_name)
                price = float(m.group(1)) if m else 0.0
            elif pkg_name.strip() == "Wants":
                section = "wants"
        except Exception:
            pass  # network/API failure during migration — default to collection, same as an unrecognized name
        conn.execute(
            "INSERT INTO user_packages (user_id, section, color_group, public_id, price) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["user_id"], section, row["color_group"], row["public_id"], price),
        )

    conn.execute("DROP TABLE user_packages_old")
```

Wire it into `_registry_conn()`:

```python
    conn = sqlite3.connect(_REGISTRY_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        _migrate_legacy_discord_id(conn)
        _migrate_package_sections(conn)
        conn.executescript(REGISTRY_SCHEMA)
```

- [ ] **Step 4: Rewrite `add_package`/`remove_package`/`list_packages`**

```python
VALID_PACKAGE_SECTIONS = ("collection", "sale", "wants", "decks")


def add_package(
    user_id: str,
    section: str,
    color_group: str,
    public_id: str,
    price: float | None = None,
) -> int:
    """Add a new Moxfield package to one of this user's sections. Returns the new row's id.

    Unlike the old color_group-keyed table, this never overwrites an existing
    row — any number of packages per section is supported.
    """
    if section not in VALID_PACKAGE_SECTIONS:
        raise ValueError(
            f"Invalid section '{section}'. Must be one of: {', '.join(VALID_PACKAGE_SECTIONS)}"
        )
    with _registry_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO user_packages (user_id, section, color_group, public_id, price)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, section, color_group.strip(), public_id.strip(), price),
        )
        return cur.lastrowid


def remove_package(user_id: str, package_id: int) -> bool:
    """Remove a package by id. Returns True if a row was deleted."""
    with _registry_conn() as conn:
        conn.execute(
            "DELETE FROM user_packages WHERE user_id = ? AND id = ?",
            (user_id, package_id),
        )
        return conn.total_changes > 0


def list_packages(user_id: str, section: str | None = None) -> list[dict]:
    """Return [{"id", "section", "color_group", "public_id", "price"}, ...],
    optionally filtered to one section, ordered by section then color_group."""
    with _registry_conn() as conn:
        query = (
            "SELECT id, section, color_group, public_id, price FROM user_packages "
            "WHERE user_id = ?"
        )
        params: list = [user_id]
        if section is not None:
            query += " AND section = ?"
            params.append(section)
        query += " ORDER BY section, color_group"
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": r["id"],
                "section": r["section"],
                "color_group": r["color_group"],
                "public_id": r["public_id"],
                "price": r["price"],
            }
            for r in rows
        ]
```

- [ ] **Step 5: Add `SalePackage` and wire `get_user_config`**

In `mtg_manager/config.py`, add alongside `MoxfieldPackage` (this is Task 2's dataclass, but `get_user_config` needs it now — add the minimal dataclass here and finish Task 2's other changes there):

```python
@dataclass
class SalePackage:
    color_group: str
    public_id: str
    price: float
```

In `api/users.py`, update the imports and `get_user_config` body:

```python
from mtg_manager.config import Config, MoxfieldPackage, SalePackage, load_config
```

Replace the package-loading block inside `get_user_config`:

```python
    with _registry_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not user:
            return None

        pkg_rows = conn.execute(
            "SELECT section, color_group, public_id, price FROM user_packages "
            "WHERE user_id = ? ORDER BY section, color_group",
            (user_id,),
        ).fetchall()

    packages = [
        MoxfieldPackage(color_group=r["color_group"], public_id=r["public_id"])
        for r in pkg_rows if r["section"] == "collection"
    ]
    sale_packages = [
        SalePackage(color_group=r["color_group"], public_id=r["public_id"], price=r["price"] or 0.0)
        for r in pkg_rows if r["section"] == "sale"
    ]
    wants_packages = [
        MoxfieldPackage(color_group=r["color_group"], public_id=r["public_id"])
        for r in pkg_rows if r["section"] == "wants"
    ]
    deck_packages = [
        MoxfieldPackage(color_group=r["color_group"], public_id=r["public_id"])
        for r in pkg_rows if r["section"] == "decks"
    ]
```

And update the `Config(...)` construction at the end of `get_user_config` to pass all four lists:

```python
    return Config(
        packages=packages,
        sale_packages=sale_packages,
        wants_packages=wants_packages,
        deck_packages=deck_packages,
        moxfield_delay=1.0,
        mtgtop8_delay=1.5,
        mtgtop8_cache_ttl=24,
        db_path=db_path,
        pick_list_sort=user["pick_list_sort"],
        formats=formats,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_users.py -v`
Expected: PASS — all package tests green, all pre-existing non-package tests still green.

- [ ] **Step 7: Commit**

```bash
git add api/users.py mtg_manager/config.py tests/test_users.py
git commit -m "feat: split user_packages into explicit collection/sale/wants/decks sections"
```

---

### Task 2: `Config` dataclass — new package-list fields

**Files:**
- Modify: `mtg_manager/config.py`
- Test: none new (covered by Task 1's `test_get_user_config_splits_packages_by_section` and Task 1's CLI-compat check below)

**Interfaces:**
- Consumes: `SalePackage` (added in Task 1, Step 5)
- Produces: `Config.sale_packages: list[SalePackage]`, `Config.wants_packages: list[MoxfieldPackage]`, `Config.deck_packages: list[MoxfieldPackage]`, all defaulting to `[]` so `load_config()` (CLI path) needs no changes.

- [ ] **Step 1: Write a failing test that the CLI's `load_config` still works untouched**

Add to `tests/test_users.py` (or a new small test near the top of the file — either is fine, this just guards Task 2's "no CLI changes" constraint):

```python
def test_load_config_cli_path_unaffected_by_new_package_sections(tmp_path, monkeypatch):
    """The CLI's load_config() must keep working with zero changes to config.toml —
    the new section fields just default to empty lists."""
    from mtg_manager.config import load_config

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[moxfield]\n"
        "packages = [{color_group = 'Red', public_id = 'toml-package'}]\n"
        "request_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{(tmp_path / 'collection.db').as_posix()}'\n"
    )

    cfg = load_config(toml)

    assert len(cfg.packages) == 1
    assert cfg.sale_packages == []
    assert cfg.wants_packages == []
    assert cfg.deck_packages == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_users.py -v -k load_config_cli_path`
Expected: FAIL — `Config.__init__()` doesn't accept/default `sale_packages` etc. yet (or the fields don't exist, causing an `AttributeError`).

- [ ] **Step 3: Add the fields to `Config`**

In `mtg_manager/config.py`, update the `Config` dataclass (note `SalePackage` was already added in Task 1, Step 5 — don't duplicate it):

```python
@dataclass
class Config:
    packages: list[MoxfieldPackage]
    moxfield_delay: float
    mtgtop8_delay: float
    mtgtop8_cache_ttl: int
    db_path: Path
    pick_list_sort: str = "colour"
    formats: list[str] = field(default_factory=list)
    web_static_dir: Path | None = None
    sale_packages: list[SalePackage] = field(default_factory=list)
    wants_packages: list[MoxfieldPackage] = field(default_factory=list)
    deck_packages: list[MoxfieldPackage] = field(default_factory=list)
```

(Fields with defaults must stay after fields without defaults — the three new ones are appended at the end, after the pre-existing `web_static_dir`, so this is already valid ordering.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_users.py -v -k load_config_cli_path`
Expected: PASS

- [ ] **Step 5: Run the full existing config/CLI test suite to confirm no regression**

Run: `pytest tests/ -v -k "config"`
Expected: PASS — no CLI-facing test should have changed behavior.

- [ ] **Step 6: Commit**

```bash
git add mtg_manager/config.py tests/test_users.py
git commit -m "feat: add sale/wants/deck package lists to Config"
```

---

### Task 3: Sync rewrite — collection/sale/wants, cleared once per run

**Files:**
- Modify: `api/handlers.py` (`_auto_sync`, `handle_sync`)
- Test: `tests/test_handlers.py` if it exists, otherwise create `tests/test_handlers_sync.py`

**Interfaces:**
- Consumes: `Config.packages` / `.sale_packages` / `.wants_packages` (Task 2), `fetch_package_cards(pkg, delay) -> (list[OwnedCard], str)` (existing, unchanged), `upsert_cards`, `upsert_for_sale_cards`, `clear_color_group`, `clear_all_for_sale_cards`, `clear_wants_cards`, `upsert_wants_cards` (all existing `mtg_manager.db` functions, unchanged)
- Produces: rewritten `_auto_sync(cfg, conn) -> list[str]` and `handle_sync(cfg, is_owner=False, color_group=None) -> str` with the section-based clear-once/upsert-per-package pattern; deck packages handled separately by Task 4 and simply not touched here — `handle_sync` calls `_auto_build_deck_packages` (Task 4) as a final step after collection/sale/wants sync.

First, check whether `tests/test_handlers.py` (or similar) exists:

- [ ] **Step 0: Check for an existing handlers test file**

Run: `ls tests/ | grep -i handler` (or `Get-ChildItem tests | Select-String handler` on PowerShell)
If a file exists, add the new tests to it, matching its existing fixture style (look at how it constructs a `Config` and temp DB). If none exists, create `tests/test_handlers_sync.py` using the pattern below (mirroring `tests/test_web_export.py`'s use of a temp `get_conn`).

- [ ] **Step 1: Write failing tests for section-based sync**

```python
"""Tests for api.handlers sync semantics across collection/sale/wants sections."""
from unittest.mock import patch

from mtg_manager.config import Config, MoxfieldPackage, SalePackage
from mtg_manager.db import get_conn, list_for_sale_cards, list_wants_cards, get_owned_quantity
from mtg_manager.models import OwnedCard


def _cfg(tmp_path, **package_lists) -> Config:
    return Config(
        packages=package_lists.get("packages", []),
        sale_packages=package_lists.get("sale_packages", []),
        wants_packages=package_lists.get("wants_packages", []),
        deck_packages=package_lists.get("deck_packages", []),
        moxfield_delay=0.0,
        mtgtop8_delay=0.0,
        mtgtop8_cache_ttl=24,
        db_path=tmp_path / "collection.db",
    )


def _card(name="Lightning Bolt", qty=4, color_group="Red") -> OwnedCard:
    return OwnedCard(
        name=name, quantity=qty, color_group=color_group,
        set_code="m10", collector_number="146", foil=False, cmc=1.0,
    )


def test_multiple_sale_packages_do_not_wipe_each_other(tmp_path):
    from api.handlers import handle_sync

    pkg_a = SalePackage(color_group="Binder A", public_id="a1", price=5.0)
    pkg_b = SalePackage(color_group="Binder B", public_id="b1", price=10.0)
    cfg = _cfg(tmp_path, sale_packages=[pkg_a, pkg_b])

    def fake_fetch(pkg, delay=1.0):
        if pkg.public_id == "a1":
            return [_card("Lightning Bolt")], "$5"
        return [_card("Brainstorm", color_group="Blue")], "$10"

    with patch("api.handlers.fetch_package_cards", side_effect=fake_fetch):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        rows = list_for_sale_cards(conn)

    names = {r["name"] for r in rows}
    assert names == {"Lightning Bolt", "Brainstorm"}


def test_sale_cards_also_populate_collection(tmp_path):
    from api.handlers import handle_sync

    pkg = SalePackage(color_group="Binder A", public_id="a1", price=5.0)
    cfg = _cfg(tmp_path, sale_packages=[pkg])

    with patch("api.handlers.fetch_package_cards", return_value=([_card("Lightning Bolt")], "$5")):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        owned = get_owned_quantity(conn, "Lightning Bolt")
        for_sale = list_for_sale_cards(conn)

    assert owned == 4
    assert for_sale[0]["price"] == 5.0


def test_multiple_wants_packages_do_not_wipe_each_other(tmp_path):
    from api.handlers import handle_sync

    pkg_a = MoxfieldPackage(color_group="Wants A", public_id="wa")
    pkg_b = MoxfieldPackage(color_group="Wants B", public_id="wb")
    cfg = _cfg(tmp_path, wants_packages=[pkg_a, pkg_b])

    def fake_fetch(pkg, delay=1.0):
        if pkg.public_id == "wa":
            return [_card("Lightning Bolt")], "Wants A"
        return [_card("Brainstorm", color_group="Blue")], "Wants B"

    with patch("api.handlers.fetch_package_cards", side_effect=fake_fetch):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        rows = list_wants_cards(conn)

    names = {r["name"] for r in rows}
    assert names == {"Lightning Bolt", "Brainstorm"}


def test_wants_cards_do_not_populate_collection(tmp_path):
    from api.handlers import handle_sync

    pkg = MoxfieldPackage(color_group="Wants", public_id="w1")
    cfg = _cfg(tmp_path, wants_packages=[pkg])

    with patch("api.handlers.fetch_package_cards", return_value=([_card("Lightning Bolt")], "Wants")):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        owned = get_owned_quantity(conn, "Lightning Bolt")

    assert owned == 0
```

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_handlers_sync.py -v`
Expected: FAIL — `handle_sync` still reads `cfg.packages` only and name-sniffs; sale/wants packages passed via the new `Config` fields are never processed, and the second sale/wants package wipes the first (the collision the old code has).

- [ ] **Step 3: Rewrite `_auto_sync` and `handle_sync`**

In `api/handlers.py`, delete `_is_sale_package`, `_is_wants_package`, `_parse_sale_price`, and `_SALE_PRICE_RE` (no longer needed — section membership now comes from `Config`, not the Moxfield deck's title), then replace `_auto_sync`:

```python
def _auto_sync(cfg: Config, conn) -> list[str]:
    """Silently re-fetch every configured package across all sections.
    Only touches owned_cards/for_sale_cards/wants_cards — never box tables.
    Returns a list of warning strings."""
    warnings = []

    clear_all_for_sale_cards(conn)
    clear_wants_cards(conn)

    for pkg in cfg.packages:
        try:
            clear_color_group(conn, pkg.color_group)
            cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            upsert_cards(conn, cards)
        except Exception as e:
            warnings.append(f"Sync warning ({pkg.color_group}): {e}")

    for pkg in cfg.sale_packages:
        try:
            clear_color_group(conn, pkg.color_group)
            cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            upsert_cards(conn, cards)
            upsert_for_sale_cards(conn, cards, pkg.price)
        except Exception as e:
            warnings.append(f"Sync warning ({pkg.color_group}): {e}")

    for pkg in cfg.wants_packages:
        try:
            cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            upsert_wants_cards(conn, cards)
        except Exception as e:
            warnings.append(f"Sync warning ({pkg.color_group}): {e}")

    try:
        sale_rows = list_for_sale_cards(conn)
        if sale_rows:
            _sync_sale_prices(conn, sale_rows)
    except Exception as e:
        warnings.append(f"Price fetch warning: {e}")
    return warnings
```

Replace `handle_sync`'s body (keep the function signature — `color_group` is still an optional collection-only filter, applied only to `cfg.packages` since sale/wants/decks aren't filtered by color group):

```python
def handle_sync(cfg: Config, is_owner: bool = False, color_group: str | None = None) -> str:
    packages = cfg.packages
    if color_group:
        packages = [p for p in packages if p.color_group.lower() == color_group.lower()]
        if not packages:
            return f"No package found for color group '{color_group}'."

    lines = []
    with get_conn(cfg.db_path) as conn:
        if not color_group:
            clear_all_for_sale_cards(conn)
            clear_wants_cards(conn)

        for pkg in packages:
            try:
                cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
            except Exception as e:
                lines.append(f"Failed to fetch {pkg.color_group}: {e}")
                continue
            clear_color_group(conn, pkg.color_group)
            upsert_cards(conn, cards)
            lines.append(f"{pkg.color_group}: {sum(c.quantity for c in cards)} cards ({len(cards)} unique)")

        if not color_group:
            for pkg in cfg.sale_packages:
                try:
                    cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
                except Exception as e:
                    lines.append(f"Failed to fetch {pkg.color_group}: {e}")
                    continue
                clear_color_group(conn, pkg.color_group)
                upsert_cards(conn, cards)
                upsert_for_sale_cards(conn, cards, pkg.price)
                lines.append(f"{pkg.color_group} [for sale]: {sum(c.quantity for c in cards)} cards ({len(cards)} unique)")

            for pkg in cfg.wants_packages:
                try:
                    cards, _name = fetch_package_cards(pkg, delay=cfg.moxfield_delay)
                except Exception as e:
                    lines.append(f"Failed to fetch {pkg.color_group}: {e}")
                    continue
                upsert_wants_cards(conn, cards)
                lines.append(f"{pkg.color_group} [wants]: {sum(c.quantity for c in cards)} cards ({len(cards)} unique)")

            deck_lines = _auto_build_deck_packages(cfg, conn)
            lines.extend(deck_lines)

        if cfg.sale_packages and not color_group:
            try:
                sale_rows = list_for_sale_cards(conn)
                price_map = fetch_cardmarket_prices(sale_rows)
                updates = []
                for row in sale_rows:
                    key = (row["set_code"].lower(), row["collector_number"], int(row["foil"]))
                    if key in price_map:
                        updates.append((row["name"], row["set_code"], row["collector_number"], bool(row["foil"]), price_map[key]))
                if updates:
                    update_sale_prices(conn, updates)
                    lines.append(f"CardMarket prices updated: {len(updates)}/{len(sale_rows)} card(s).")
            except Exception as e:
                lines.append(f"Price fetch failed: {e}")

        if cfg.formats:
            names = get_names_missing_legality(conn, cfg.formats)
            if names:
                try:
                    legality_map = fetch_legalities(names, cfg.formats)
                    upsert_legalities(conn, legality_map)
                    lines.append(f"Legality: {len(legality_map)}/{len(names)} card(s) updated.")
                except Exception as e:
                    lines.append(f"Legality fetch failed: {e}")

        total = card_count(conn)

    lines.append(f"\nCollection total: {total} cards")

    try:
        from web.export import export_static
        export_static(cfg)
    except Exception as e:
        lines.append(f"Web export failed: {e}")

    if cfg.formats:
        try:
            from web.export_meta import export_meta_static
            export_meta_static(cfg, cfg.formats)
        except Exception as e:
            lines.append(f"Meta web export failed: {e}")

    return "\n".join(lines)
```

`_auto_build_deck_packages` is fully implemented in Task 4. For this task to be independently testable, add this exact stub in `api/handlers.py` right before `handle_sync`, and commit it as part of this task — Task 4 replaces the body, it does not change the call site in `handle_sync`:

```python
def _auto_build_deck_packages(cfg: Config, conn) -> list[str]:
    """Placeholder — real implementation lands in Task 4 of the package-sections plan."""
    return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_handlers_sync.py -v`
Expected: PASS

- [ ] **Step 5: Run the full handlers/webapp test suite to check for regressions**

Run: `pytest tests/ -v -k "handler or webapp_config or webapp_admin"`
Expected: Some `test_webapp_config.py`/`test_webapp_admin.py` failures are expected here — they call the old `add_package(user_id, color_group, public_id)` 2-arg form and are fixed in Task 5/6. Confirm the *only* failures are those known call-site mismatches, not new logic errors.

- [ ] **Step 6: Commit**

```bash
git add api/handlers.py tests/test_handlers_sync.py
git commit -m "feat: sync collection/sale/wants sections without cross-package data loss"
```

---

### Task 4: Deck packages — auto-build into a UUID-boxed deck

**Files:**
- Modify: `api/handlers.py` (add `_auto_build_deck_packages`, replacing the Task 3 stub)
- Test: `tests/test_handlers_sync.py` (same file as Task 3)

**Interfaces:**
- Consumes: `Config.deck_packages: list[MoxfieldPackage]` (Task 2), `fetch_decklists(url, delay) -> list[Decklist]` (existing, `mtg_manager.sources`), `get_deck`, `get_deck_by_url`, `get_available_quantity`, `insert_built_deck(conn, deck_id, deck_name, deck_url, box_name, cards)` (all existing `mtg_manager.db`, unchanged)
- Produces: `_auto_build_deck_packages(cfg: Config, conn) -> list[str]` — one summary line per deck package processed (built or skipped-already-built), used by `handle_sync`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_handlers_sync.py`:

```python
def test_deck_package_builds_deck_and_allocates_cards(tmp_path):
    from api.handlers import handle_sync
    from mtg_manager.db import get_conn, upsert_cards, list_built_decks, get_available_quantity
    from mtg_manager.models import Decklist, DeckCard

    pkg = MoxfieldPackage(color_group="Burn", public_id="d1")
    cfg = _cfg(tmp_path, deck_packages=[pkg])

    # Pre-populate the collection so the deck can be fully allocated (not proxied).
    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [_card("Lightning Bolt", qty=4)])

    decklist = Decklist(
        deck_id="d1", name="Mono Red Burn",
        url="https://www.moxfield.com/decks/d1",
        cards=[DeckCard(name="Lightning Bolt", quantity=4, is_sideboard=False)],
    )

    with patch("api.handlers.fetch_decklists", return_value=[decklist]):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        decks = list_built_decks(conn)
        remaining = get_available_quantity(conn, "Lightning Bolt")

    assert len(decks) == 1
    assert decks[0]["deck_name"] == "Mono Red Burn"
    assert decks[0]["deck_id"] == "d1"
    assert remaining == 0  # all 4 copies allocated to the deck


def test_deck_package_only_populates_decks_not_collection_or_wants(tmp_path):
    from api.handlers import handle_sync
    from mtg_manager.db import get_conn, list_wants_cards, get_owned_quantity
    from mtg_manager.models import Decklist, DeckCard

    pkg = MoxfieldPackage(color_group="Burn", public_id="d1")
    cfg = _cfg(tmp_path, deck_packages=[pkg])

    decklist = Decklist(
        deck_id="d1", name="Mono Red Burn",
        url="https://www.moxfield.com/decks/d1",
        cards=[DeckCard(name="Lightning Bolt", quantity=4, is_sideboard=False)],
    )

    with patch("api.handlers.fetch_decklists", return_value=[decklist]):
        handle_sync(cfg)

    with get_conn(cfg.db_path) as conn:
        owned = get_owned_quantity(conn, "Lightning Bolt")
        wants = list_wants_cards(conn)

    assert owned == 0  # proxied, not owned — deck packages never write owned_cards
    assert wants == []


def test_deck_package_is_idempotent_across_syncs(tmp_path):
    from api.handlers import handle_sync
    from mtg_manager.db import get_conn, list_built_decks
    from mtg_manager.models import Decklist, DeckCard

    pkg = MoxfieldPackage(color_group="Burn", public_id="d1")
    cfg = _cfg(tmp_path, deck_packages=[pkg])

    decklist = Decklist(
        deck_id="d1", name="Mono Red Burn",
        url="https://www.moxfield.com/decks/d1",
        cards=[DeckCard(name="Lightning Bolt", quantity=4, is_sideboard=False)],
    )

    with patch("api.handlers.fetch_decklists", return_value=[decklist]):
        handle_sync(cfg)
        handle_sync(cfg)  # second sync must not rebuild or duplicate

    with get_conn(cfg.db_path) as conn:
        decks = list_built_decks(conn)

    assert len(decks) == 1
```

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_handlers_sync.py -v -k deck_package`
Expected: FAIL — `_auto_build_deck_packages` is still the no-op stub from Task 3.

- [ ] **Step 3: Implement `_auto_build_deck_packages`**

Add near the top of `api/handlers.py`, alongside the other imports, `from uuid import uuid4`. Then add the function (replacing the Task 3 stub) right before `handle_sync`:

```python
def _auto_build_deck_packages(cfg: Config, conn) -> list[str]:
    """Auto-build every configured deck package into a freshly UUID-boxed deck.
    Idempotent by deck_id — a deck already built is skipped. Only touches
    built_decks/allocated_cards, never owned_cards/for_sale_cards/wants_cards."""
    lines = []
    for pkg in cfg.deck_packages:
        url = f"https://www.moxfield.com/decks/{pkg.public_id}"
        try:
            decklists = fetch_decklists(url, delay=cfg.moxfield_delay)
        except Exception as e:
            lines.append(f"Deck package '{pkg.color_group}' failed to fetch: {e}")
            continue
        if not decklists:
            lines.append(f"Deck package '{pkg.color_group}': no deck found.")
            continue

        dl = decklists[0]
        existing = get_deck_by_url(conn, url) or get_deck(conn, dl.deck_id)
        if existing:
            lines.append(f"Deck package '{pkg.color_group}' ({dl.name}): already built, skipped.")
            continue

        needed: dict[str, int] = defaultdict(int)
        for card in dl.cards:
            needed[card.name] += card.quantity

        card_entries: list[tuple[str, int, bool]] = []
        for name, qty in sorted(needed.items()):
            available = get_available_quantity(conn, name)
            is_proxy = available < qty
            card_entries.append((name, qty, is_proxy))

        insert_built_deck(
            conn,
            deck_id=dl.deck_id,
            deck_name=dl.name,
            deck_url=url,
            box_name=str(uuid4()),
            cards=card_entries,
        )
        proxy_count = sum(1 for _, _, p in card_entries if p)
        lines.append(
            f"Deck package '{pkg.color_group}': built '{dl.name}'"
            + (f" ({proxy_count} card(s) proxied)" if proxy_count else "")
        )
    return lines
```

Remove the Task 3 stub (`def _auto_build_deck_packages(cfg, conn): return []`) since this real implementation replaces it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_handlers_sync.py -v`
Expected: PASS — all Task 3 and Task 4 tests green.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: only the known Task 5/6 call-site failures in `test_webapp_config.py`/`test_webapp_admin.py` remain (same set as Task 3 Step 5).

- [ ] **Step 6: Commit**

```bash
git add api/handlers.py tests/test_handlers_sync.py
git commit -m "feat: auto-build deck packages into UUID-boxed decks during sync"
```

---

### Task 5: `webapp/config.py` — section-aware API

**Files:**
- Modify: `webapp/config.py`
- Test: `tests/test_webapp_config.py`

**Interfaces:**
- Consumes: `add_package(user_id, section, color_group, public_id, price=None) -> int`, `remove_package(user_id, package_id) -> bool`, `list_packages(user_id, section=None) -> list[dict]` (Task 1)
- Produces: `GET /api/config` response `packages` key becomes `{"collection": [...], "sale": [...], "wants": [...], "decks": [...]}` (each entry `{"id", "color_group", "public_id", "price"}`); `POST /api/config/packages` body gains required `section` and conditionally-required `price`; `DELETE /api/config/packages/{package_id}` (int, was `{color_group}` str).

- [ ] **Step 1: Update failing/changed tests in `tests/test_webapp_config.py`**

Replace `test_get_config_returns_defaults_for_new_user`'s package assertion:

```python
    assert data["packages"] == {"collection": [], "sale": [], "wants": [], "decks": []}
```

Replace `test_add_and_list_package`:

```python
def test_add_and_list_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post(
            "/api/config/packages",
            json={"section": "collection", "color_group": "Red", "public_id": "abc123"},
        )
        get_response = c.get("/api/config")

    assert add_response.status_code == 200
    collection = get_response.json()["packages"]["collection"]
    assert len(collection) == 1
    assert collection[0]["color_group"] == "Red"
    assert collection[0]["public_id"] == "abc123"
```

Replace `test_add_package_extracts_slug_from_full_url` the same way (add `"section": "collection"` to the body, read `data["packages"]["collection"]`).

Add a new test for the sale price requirement:

```python
def test_add_sale_package_requires_price(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post(
            "/api/config/packages",
            json={"section": "sale", "color_group": "Binder A", "public_id": "s1"},
        )

    assert response.status_code == 400


def test_add_sale_package_with_price(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        c.post(
            "/api/config/packages",
            json={"section": "sale", "color_group": "Binder A", "public_id": "s1", "price": 5.0},
        )
        get_response = c.get("/api/config")

    sale = get_response.json()["packages"]["sale"]
    assert sale[0]["price"] == 5.0
```

Replace `test_remove_package`:

```python
def test_remove_package(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        add_response = c.post(
            "/api/config/packages",
            json={"section": "collection", "color_group": "Red", "public_id": "abc123"},
        )
        package_id = add_response.json()["id"]
        remove_response = c.delete(f"/api/config/packages/{package_id}")
        get_response = c.get("/api/config")

    assert remove_response.status_code == 200
    assert get_response.json()["packages"]["collection"] == []
```

Update every remaining `add_package("google:...@example.com", "Red", "some-package-id")` call in this file (in `test_sync_calls_handle_sync_and_marks_synced`, `test_sync_rejects_when_no_packages_configured`'s sibling tests, `test_sync_throttled_returns_429`, `test_sync_skips_throttle_for_owner`) to the new 4-positional-arg form: `add_package("google:...@example.com", "collection", "Red", "some-package-id")`.

- [ ] **Step 2: Run to verify these fail**

Run: `pytest tests/test_webapp_config.py -v`
Expected: FAIL — route still uses the old `PackageIn`/label-keyed delete route.

- [ ] **Step 3: Update `webapp/config.py`**

```python
class PackageIn(BaseModel):
    section: str
    color_group: str
    public_id: str
    price: float | None = None
```

```python
@router.get("/api/config")
async def get_config(request: Request, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    all_pkgs = list_packages(user_id)
    by_section: dict[str, list[dict]] = {"collection": [], "sale": [], "wants": [], "decks": []}
    for p in all_pkgs:
        by_section[p["section"]].append(
            {"id": p["id"], "color_group": p["color_group"], "public_id": p["public_id"], "price": p["price"]}
        )
    own = get_profiles_by_ids({user_id})
    profile = own[0] if own else {"display_name": user_id, "icon": "🂠"}
    return {
        "packages": by_section,
        "formats": cfg.formats,
        "pick_list_sort": cfg.pick_list_sort,
        "minutes_since_last_sync": minutes_since_last_sync(user_id),
        "is_admin": is_admin_user(user_id),
        "display_name": profile["display_name"],
        "icon": profile["icon"],
        "is_private": is_private(user_id),
        "cardmarket_url": get_cardmarket_url(user_id),
        "groups": list_groups(user_id),
    }


@router.post("/api/config/packages")
def add_config_package(request: Request, body: PackageIn, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    if body.section == "sale" and body.price is None:
        raise HTTPException(status_code=400, detail="Sale packages require a price.")
    public_id = public_id_from_url(body.public_id) or body.public_id.strip()
    try:
        package_id = add_package(user_id, body.section, body.color_group, public_id, body.price)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "id": package_id, "auto_sync": _trigger_auto_sync(user_id)}


@router.delete("/api/config/packages/{package_id}")
def remove_config_package(request: Request, package_id: int, cfg: Config = Depends(require_user)):
    user_id = request.session["user_id"]
    remove_package(user_id, package_id)
    return {"ok": True, "auto_sync": _trigger_auto_sync(user_id)}
```

`sync_now`'s `if not cfg.packages:` guard should widen to cover all four sections, since a user with only sale/wants/deck packages configured should still be allowed to sync:

```python
    if not (cfg.packages or cfg.sale_packages or cfg.wants_packages or cfg.deck_packages):
        raise HTTPException(
            status_code=400,
            detail="No Moxfield packages added yet. Add at least one package before syncing.",
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webapp_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webapp/config.py tests/test_webapp_config.py
git commit -m "feat: section-aware /api/config package endpoints"
```

---

### Task 6: `webapp/admin.py` — admin user-detail endpoint

**Files:**
- Modify: `webapp/admin.py`
- Test: `tests/test_webapp_admin.py`

**Interfaces:**
- Consumes: `list_packages(user_id) -> list[dict]` (Task 1)
- Produces: `GET /api/admin/users/{user_id}` response `packages` field becomes a flat list of `{"id", "section", "color_group", "public_id", "price"}` dicts (admin view shows everything across sections at once, unlike the self-service `/api/config` which groups by section).

- [ ] **Step 1: Update the failing test**

Find the relevant assertion in `tests/test_webapp_admin.py` (around the `add_package("google:friend@example.com", "red", "abc123")` calls). Update those calls to the new signature — `add_package("google:friend@example.com", "collection", "red", "abc123")` — and update the response assertion:

```python
    data = response.json()
    assert data["packages"] == [
        {"id": data["packages"][0]["id"], "section": "collection", "color_group": "red", "public_id": "abc123", "price": None}
    ]
```

(Read the exact surrounding test body first — match its existing assertion style rather than replacing wholesale; only the shape of the `packages` value changes.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_webapp_admin.py -v`
Expected: FAIL — `add_package` call signature mismatch, and/or the response shape assertion mismatch.

- [ ] **Step 3: Update `webapp/admin.py`**

```python
@router.get("/api/admin/users/{user_id}")
async def get_user_detail(user_id: str, cfg: Config = Depends(require_admin)):
    if not is_registered(user_id):
        raise HTTPException(status_code=404, detail="User not found")
    packages = list_packages(user_id)
    return {
        "user_id": user_id,
        "packages": packages,
        "activity": list_request_log(limit=200, user_id=user_id),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_webapp_admin.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add webapp/admin.py tests/test_webapp_admin.py
git commit -m "feat: admin user-detail endpoint returns sectioned package list"
```

---

### Task 7: Discord bot commands — section-aware `/addpackage`

**Files:**
- Modify: `api/bot.py`

**Interfaces:**
- Consumes: `add_package(user_id, section, color_group, public_id, price=None)`, `remove_package(user_id, package_id)`, `list_packages(user_id, section=None) -> list[dict]` (Task 1)
- Produces: `/addpackage` gains a required `section` choice parameter and an optional `price` parameter (required by Discord's client-side validation only when the user picks `sale` — enforced server-side same as the web route); `/removepackage` takes a package id instead of a color_group label; `/listpackages` groups its display by section.

There is no existing bot test file to extend (confirmed no `tests/test_bot.py` in the repo) — this task is manual-review-only, no automated test. Treat each step as a direct code change; verify by re-reading the diff carefully rather than running pytest.

- [ ] **Step 1: Update `/addpackage`**

```python
@tree.command(name="addpackage", description="Add a Moxfield package to your account")
@app_commands.describe(
    section="Which section this package belongs to",
    color_group="Label for this package (e.g. White, Blue, Multicolour)",
    public_id="The slug at the end of your Moxfield deck URL",
    price="Sale price for all cards in this package (required for section=sale)",
)
@app_commands.choices(section=[
    app_commands.Choice(name="Collection", value="collection"),
    app_commands.Choice(name="Sale", value="sale"),
    app_commands.Choice(name="Wants", value="wants"),
    app_commands.Choice(name="Decks", value="decks"),
])
async def cmd_addpackage(
    interaction: discord.Interaction,
    section: app_commands.Choice[str],
    color_group: str,
    public_id: str,
    price: float = None,
):
    await interaction.response.defer(ephemeral=True)
    discord_id = f"discord:{interaction.user.id}"

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    if section.value == "sale" and price is None:
        await _send_embed(interaction, "Sale packages require a `price`.", title="Missing Price", color=COLOR_ERROR)
        return

    await asyncio.get_event_loop().run_in_executor(
        None, users.add_package, discord_id, section.value, color_group, public_id, price
    )
    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    pkg_lines = "\n".join(f"  [{p['section']}] {p['color_group']} → `{p['public_id']}`" for p in pkgs)
    await _send_embed(
        interaction,
        f"Added **{color_group}** (`{public_id}`) to **{section.value}**.\n\nYour packages:\n{pkg_lines}\n\nRun `/sync` to fetch your collection.",
        title="Package Added",
        color=COLOR_SUCCESS,
    )
```

- [ ] **Step 2: Update `/removepackage`**

```python
@tree.command(name="removepackage", description="Remove a Moxfield package from your account")
@app_commands.describe(package_id="The package id shown by /listpackages")
async def cmd_removepackage(interaction: discord.Interaction, package_id: int):
    await interaction.response.defer(ephemeral=True)
    discord_id = f"discord:{interaction.user.id}"

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    removed = await asyncio.get_event_loop().run_in_executor(
        None, users.remove_package, discord_id, package_id
    )
    if not removed:
        await _send_embed(interaction, f"No package with id {package_id} found.", title="Not Found", color=COLOR_WARNING)
        return

    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    remaining = "\n".join(f"  [{p['section']}] {p['color_group']} → `{p['public_id']}`" for p in pkgs) if pkgs else "  (none)"
    await _send_embed(
        interaction,
        f"Removed package {package_id}.\n\nRemaining packages:\n{remaining}",
        title="Package Removed",
        color=COLOR_SUCCESS,
    )
```

- [ ] **Step 3: Update `/listpackages`**

```python
@tree.command(name="listpackages", description="Show your registered Moxfield packages")
async def cmd_listpackages(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    discord_id = f"discord:{interaction.user.id}"

    if users.is_owner(discord_id):
        await _send_embed(interaction, _OWNER_MSG, title="Bot Operator", color=COLOR_INFO)
        return

    if not users.is_registered(discord_id):
        await _send_embed(interaction, "Run `/setup` first.", title="Not Registered", color=COLOR_ERROR)
        return

    pkgs = await asyncio.get_event_loop().run_in_executor(None, users.list_packages, discord_id)
    if not pkgs:
        body = "No packages yet. Use `/addpackage` to add one."
    else:
        by_section: dict[str, list[str]] = {}
        for p in pkgs:
            by_section.setdefault(p["section"], []).append(f"  (id {p['id']}) {p['color_group']} → `{p['public_id']}`")
        body = "\n\n".join(f"**{section}**\n" + "\n".join(lines) for section, lines in by_section.items())
    await _send_embed(interaction, body, title="Your Packages", color=COLOR_INFO)
```

- [ ] **Step 4: Fix the two help-text references to the old `/addpackage <color_group> <public_id>` signature**

In `_resolve_user` (around line 175) and `cmd_setup` (around line 206), update the copy to mention `section`:

```python
            "No Moxfield packages added yet.\nUse `/addpackage` to add your collection, then `/sync`.",
```

```python
        "1. `/addpackage` — add each Moxfield package (choose a section: collection, sale, wants, or decks)\n"
```

- [ ] **Step 5: Manual verification**

Since there's no automated bot test harness in this repo, verify by reading the full diff of `api/bot.py` end-to-end and confirming: every remaining call site of `add_package`/`remove_package`/`list_packages` in this file matches the Task 1 signatures, and no other command in the file destructures `list_packages`'s return value as `(color_group, public_id)` tuples (grep for `for cg, pid in` and `for cg, pid` across the file to be sure).

Run: `grep -n "for cg, pid" api/bot.py` (or PowerShell `Select-String "for cg, pid" api/bot.py`)
Expected: no matches remain.

- [ ] **Step 6: Commit**

```bash
git add api/bot.py
git commit -m "feat: section-aware Discord package commands"
```

---

### Task 8: `webapp/static/config.html` — four-section UI

**Files:**
- Modify: `webapp/static/config.html`

**Interfaces:**
- Consumes: `GET /api/config` (`packages: {collection, sale, wants, decks}`, Task 5), `POST /api/config/packages` (`{section, color_group, public_id, price?}`), `DELETE /api/config/packages/{id}`

No automated test exists for this file (it's plain JS in a static HTML page with no test harness in the repo) — verify manually by starting the dev server and exercising the page in a browser per this task's Step 3.

- [ ] **Step 1: Replace the single package list with four sections**

Locate the existing package list block (around line 61-71, `<div class="flush-section">` containing `packages-list`) and replace it with four blocks, one per section:

```html
  <div class="flush-section">
    <h3>Collection</h3>
    <div id="packages-list-collection"></div>
    <div class="add-row">
      <input id="pkg-collection-color" placeholder="Label (e.g. White)">
      <input id="pkg-collection-id" placeholder="Moxfield URL or public_id">
      <button onclick="addPackage('collection')">Add</button>
    </div>
  </div>

  <div class="flush-section">
    <h3>Sale</h3>
    <div id="packages-list-sale"></div>
    <div class="add-row">
      <input id="pkg-sale-color" placeholder="Label (e.g. Binder A)">
      <input id="pkg-sale-id" placeholder="Moxfield URL or public_id">
      <input id="pkg-sale-price" type="number" step="0.01" placeholder="Price">
      <button onclick="addPackage('sale')">Add</button>
    </div>
  </div>

  <div class="flush-section">
    <h3>Wants</h3>
    <div id="packages-list-wants"></div>
    <div class="add-row">
      <input id="pkg-wants-color" placeholder="Label (e.g. Wants)">
      <input id="pkg-wants-id" placeholder="Moxfield URL or public_id">
      <button onclick="addPackage('wants')">Add</button>
    </div>
  </div>

  <div class="flush-section">
    <h3>Decks</h3>
    <div id="packages-list-decks"></div>
    <div class="add-row">
      <input id="pkg-decks-id" placeholder="Moxfield deck URL or public_id">
      <button onclick="addPackage('decks')">Add</button>
    </div>
  </div>
```

- [ ] **Step 2: Rewrite the JS render/add/remove functions**

Replace `renderPackages` and the surrounding add/remove JS (around lines 156-225):

```javascript
  renderAllPackages(data.packages);
```

```javascript
function renderAllPackages(packagesBySection) {
  for (const section of ['collection', 'sale', 'wants', 'decks']) {
    renderPackageSection(section, packagesBySection[section] || []);
  }
}

function renderPackageSection(section, packages) {
  const list = document.getElementById(`packages-list-${section}`);
  list.innerHTML = '';
  if (!packages.length) {
    list.innerHTML = '<p class="muted">No packages yet.</p>';
    return;
  }
  for (const p of packages) {
    const row = document.createElement('div');
    row.className = 'package-row';
    const priceLabel = section === 'sale' ? ` — €${p.price ?? 0}` : '';
    row.innerHTML = `
      <span><strong>${p.color_group}</strong>${priceLabel}</span>
      <span>
        <a class="moxfield-link" href="https://www.moxfield.com/decks/${encodeURIComponent(p.public_id)}" target="_blank" rel="noopener">View on Moxfield ↗</a>
      </span>
    `;
    const btn = document.createElement('button');
    btn.textContent = 'Remove';
    btn.onclick = () => removePackage(p.id);
    row.appendChild(btn);
    list.appendChild(row);
  }
}

async function addPackage(section) {
  const colorInput = document.getElementById(`pkg-${section}-color`);
  const color_group = section === 'decks' ? 'Deck' : (colorInput ? colorInput.value.trim() : '');
  const public_id = document.getElementById(`pkg-${section}-id`).value.trim();
  if ((section !== 'decks' && !color_group) || !public_id) return;

  const body = { section, color_group, public_id };
  if (section === 'sale') {
    const priceInput = document.getElementById('pkg-sale-price');
    body.price = parseFloat(priceInput.value);
    if (!Number.isFinite(body.price)) return;
  }

  const res = await fetch('/api/config/packages', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const msg = document.getElementById('config-msg');
  msg.textContent = res.ok ? 'Added.' : 'Failed to add package.';
  if (res.ok) loadConfig();
}

async function removePackage(packageId) {
  const res = await fetch(`/api/config/packages/${packageId}`, { method: 'DELETE' });
  const msg = document.getElementById('config-msg');
  msg.textContent = res.ok ? 'Removed.' : 'Failed to remove package.';
  if (res.ok) loadConfig();
}
```

`loadConfig()` already exists in the file (it's what calls `renderPackages(data.packages)` today) — confirm its name by reading the file before editing, and update its call site from `renderPackages(data.packages)` to `renderAllPackages(data.packages)`.

- [ ] **Step 3: Manual browser verification**

Use the `run` skill (or start the dev server directly per this repo's existing dev-server instructions) and open the config page in a browser. Verify:
- Adding a Collection package appears under Collection only.
- Adding a Sale package requires a price and appears under Sale with the price shown.
- Adding two packages to the same section (e.g. two Collection packages) both appear — this is the core "any number of packages" requirement.
- Removing a package removes only that row.
- Adding a Decks package with just a Moxfield deck URL works without requiring a label.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/config.html
git commit -m "feat: four-section package UI in self-service config page"
```

---

## Post-plan check

After Task 8, run the full suite once more to confirm the whole feature is green end-to-end:

Run: `pytest tests/ -v`
Expected: PASS, no regressions in any file. (`cli.py`-related tests, if any exist, were never touched by this plan and must show zero diff in behavior.)
