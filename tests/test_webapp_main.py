import contextlib
import json
from base64 import b64encode

import itsdangerous
from fastapi.testclient import TestClient

# NOTE: Starlette's TestClient (httpx-based, installed version 1.0.0) has no
# `session_transaction()` method — that API is a Flask test-client feature,
# not a Starlette/FastAPI one. To keep the test bodies exactly as specified
# (interacting with a `session_transaction()` context manager), we attach a
# real, equivalent implementation here: it signs a session cookie the same
# way `starlette.middleware.sessions.SessionMiddleware` does (itsdangerous
# `TimestampSigner` over a base64-encoded JSON payload) and sets it on the
# client's cookie jar. This exercises the real signed-cookie mechanism the
# app uses in production — it is not a mock of authentication.


def _install_session_transaction(client, secret_key):
    @contextlib.contextmanager
    def session_transaction():
        session: dict = {}
        yield session
        signer = itsdangerous.TimestampSigner(secret_key)
        data = signer.sign(b64encode(json.dumps(session).encode("utf-8")))
        client.cookies.set("session", data.decode("utf-8"))

    client.session_transaction = session_transaction


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_SECRET_KEY", "test-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")

    import api.users as u
    monkeypatch.setattr(u, "_REGISTRY_PATH", tmp_path / "registry.sqlite")
    monkeypatch.setattr(u, "_USERS_DIR", tmp_path / "users")

    from webapp.main import app  # noqa: F401 (imported for its side effect: load_dotenv())

    # The real .env on this machine sets these to the actual site owner's
    # identity, which routes straight to the real ~/.mtg_manager/config.toml
    # DB (bypassing the tmp registry above). load_dotenv() (triggered by the
    # import above, only on the first call across the whole test session)
    # would otherwise repopulate them, so clear them AFTER importing —
    # tests that need owner behavior set OWNER_GOOGLE_EMAIL explicitly.
    monkeypatch.delenv("OWNER_DISCORD_ID", raising=False)
    monkeypatch.delenv("OWNER_GOOGLE_EMAIL", raising=False)

    client = TestClient(app)
    _install_session_transaction(client, "test-secret")
    return client


def test_api_collection_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_api_collection_returns_data_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection")

    assert response.status_code == 200
    assert response.json()["cards"] == []


def test_api_decks_returns_data_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/decks")

    assert response.status_code == 200
    assert response.json()["decks"] == []


def test_api_sale_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/sale", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_api_sale_returns_data_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/sale")

    assert response.status_code == 200
    data = response.json()
    assert data["for_sale"] == []
    assert data["extras"] == []
    assert data["wants"] == []


def _configure_fake_owner(tmp_path, monkeypatch):
    """Point the Google-owner shortcut at a throwaway config.toml/db, per the
    isolation pattern in tests/test_users.py — never let tests touch the real
    ~/.mtg_manager/config.toml or its collection.db."""
    monkeypatch.setenv("OWNER_GOOGLE_EMAIL", "owner@example.com")
    toml = tmp_path / "owner_config.toml"
    real_db = tmp_path / "owner_collection.db"
    toml.write_text(
        "[moxfield]\npackages = []\nrequest_delay_seconds = 1.0\n"
        "[mtgtop8]\nrequest_delay_seconds = 1.5\ncache_ttl_hours = 24\n"
        f"[database]\npath = '{real_db.as_posix()}'\n"
    )
    monkeypatch.setattr("mtg_manager.config.DEFAULT_CONFIG", toml)

    from api.users import ensure_user, get_user_config
    ensure_user("google:owner@example.com")
    return get_user_config("google:owner@example.com")


def test_api_collection_falls_back_to_owner_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    owner_cfg = _configure_fake_owner(tmp_path, monkeypatch)

    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import OwnedCard
    with get_conn(owner_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Brainstorm", quantity=2, color_group="Blue")])

    response = client.get("/api/collection")

    assert response.status_code == 200
    assert response.json()["cards"][0]["name"] == "Brainstorm"


def test_api_sale_falls_back_to_owner_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _configure_fake_owner(tmp_path, monkeypatch)

    response = client.get("/api/sale")

    assert response.status_code == 200
    data = response.json()
    assert data["for_sale"] == []
    assert data["extras"] == []
    assert data["wants"] == []


def test_app_serves_page_when_not_logged_in_and_owner_configured(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _configure_fake_owner(tmp_path, monkeypatch)

    response = client.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_api_sale_all_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/sale/all", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_sale_all_returns_combined_data(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/sale/all")

    assert response.status_code == 200
    data = response.json()
    assert "people" in data
    assert "for_sale" in data
    assert "extras" in data
    assert "wants" in data


def test_api_collection_all_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection/all", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_collection_all_requires_admin_now(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user
    ensure_user("google:alice@example.com")  # not whitelisted as admin

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection/all")

    assert response.status_code == 403


def test_api_collection_all_returns_combined_data_for_admin(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_whitelisted_email, ensure_user
    ensure_user("google:admin@example.com")
    add_whitelisted_email("admin@example.com", is_admin=True)

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:admin@example.com"
        response = c.get("/api/collection/all")

    assert response.status_code == 200
    data = response.json()
    assert "people" in data
    assert "cards" in data


def test_api_collection_group_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/collection/group", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_collection_group_scoped_to_caller_and_group_members(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    ensure_user("google:carol@example.com")  # not in alice's group
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/collection/group")

    assert response.status_code == 200
    data = response.json()
    people_ids = {p["user_id"] for p in data["people"]}
    assert people_ids == {"google:alice@example.com", "google:bob@example.com"}


def test_whoami_reports_unauthenticated_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/whoami", follow_redirects=False)
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


def test_whoami_returns_email_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/whoami")

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "email": "alice@example.com"}


def test_root_redirects_to_app(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app"


def test_root_redirects_to_app_when_authenticated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app"


def test_app_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/app", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_app_serves_page_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user, mark_onboarded
    ensure_user("google:alice@example.com")
    mark_onboarded("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "MTG Collection" in response.text


def test_app_redirects_to_onboarding_when_not_onboarded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/app", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/onboarding"


def test_onboarding_page_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/onboarding", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_onboarding_page_serves_when_not_onboarded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/onboarding")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_onboarding_page_redirects_to_app_when_already_onboarded(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user, mark_onboarded
    ensure_user("google:alice@example.com")
    mark_onboarded("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/onboarding", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app"


def test_activity_is_logged_for_authenticated_requests(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, list_request_log
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        c.get("/api/whoami")

    rows = list_request_log()
    matching = [r for r in rows if r["path"] == "/api/whoami"]
    assert len(matching) == 1
    assert matching[0]["method"] == "GET"
    assert matching[0]["status"] == 200
    assert matching[0]["user_id"] == "google:alice@example.com"


def test_activity_log_records_anonymous_requests(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import list_request_log

    client.get("/api/whoami", follow_redirects=False)

    rows = list_request_log()
    matching = [r for r in rows if r["path"] == "/api/whoami"]
    assert len(matching) == 1
    assert matching[0]["user_id"] is None
    assert matching[0]["status"] == 200


def test_image_route_requests_are_not_logged(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import list_request_log

    client.get("/images/badset/1")  # invalid collector_number-ish but exercises the route

    rows = list_request_log()
    assert all(not r["path"].startswith("/images/") for r in rows)


def test_group_check_reports_which_members_own_missing_cards(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user, get_user_config, set_profile
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import OwnedCard

    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    set_profile("google:bob@example.com", "Bob", "🐉")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    bob_cfg = get_user_config("google:bob@example.com")
    with get_conn(bob_cfg.db_path) as conn:
        upsert_cards(conn, [OwnedCard(name="Brainstorm", quantity=2, color_group="Blue")])

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/collection/group-check", json={"cards": [{"name": "Brainstorm", "quantity": 1}]})

    assert response.status_code == 200
    ownership = response.json()["ownership"]
    assert ownership["Brainstorm"] == [{"owner_user_id": "google:bob@example.com", "owner_display_name": "Bob", "owner_icon": "🐉", "owned": 2}]


def test_group_check_omits_cards_no_group_member_owns(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import add_group_member, ensure_user
    ensure_user("google:alice@example.com")
    ensure_user("google:bob@example.com")
    add_group_member("google:alice@example.com", "google:bob@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.post("/api/collection/group-check", json={"cards": [{"name": "Nonexistent Card", "quantity": 1}]})

    assert response.status_code == 200
    assert response.json()["ownership"] == {}


def test_group_check_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.post("/api/collection/group-check", json={"cards": []}, follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_meta_requires_auth(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/meta", follow_redirects=False)
    assert response.status_code in (302, 307)


def test_api_meta_compares_saved_decklists_against_own_collection(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config, set_formats
    from mtg_manager.db import get_conn, upsert_cards
    from mtg_manager.models import DeckCard, Decklist, OwnedCard
    from mtg_manager.db import replace_meta_decks

    ensure_user("google:alice@example.com")
    set_formats("google:alice@example.com", ["modern"])
    cfg = get_user_config("google:alice@example.com")

    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [
            OwnedCard(name="Brainstorm", quantity=1, color_group="Blue"),
            OwnedCard(name="Ponder", quantity=1, color_group="Blue"),
        ])
        replace_meta_decks(conn, "modern", [
            Decklist(deck_id="d1", name="Mono Blue", url="https://example.com/d1", meta_share=10.0,
                      cards=[DeckCard(name="Brainstorm", quantity=4), DeckCard(name="Force of Will", quantity=4),
                             DeckCard(name="Ponder", quantity=1)])
        ])

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/meta")

    assert response.status_code == 200
    data = response.json()
    assert data["formats"][0]["format"] == "modern"
    deck = data["formats"][0]["decks"][0]
    assert deck["name"] == "Mono Blue"
    assert deck["total_slots"] == 9
    assert deck["owned_slots"] == 2
    card_names_missing_first = [c["name"] for c in deck["cards"]]
    # Brainstorm (1/4) and Force of Will (0/4) are both still missing, so
    # within that group they sort alphabetically; Ponder (1/1) is fully
    # owned and must sort last, after both missing cards, regardless of
    # its own alphabetical position (it would otherwise sort before both).
    assert card_names_missing_first == ["Brainstorm", "Force of Will", "Ponder"]


def test_api_meta_never_includes_set_code_and_collector_number(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, get_user_config, set_formats
    from mtg_manager.db import get_conn, replace_meta_decks, upsert_cards
    from mtg_manager.models import DeckCard, Decklist, OwnedCard

    ensure_user("google:alice@example.com")
    set_formats("google:alice@example.com", ["modern"])
    cfg = get_user_config("google:alice@example.com")

    with get_conn(cfg.db_path) as conn:
        upsert_cards(conn, [
            OwnedCard(name="Brainstorm", quantity=1, color_group="Blue", set_code="ice", collector_number="48"),
        ])
        replace_meta_decks(conn, "modern", [
            Decklist(deck_id="d1", name="Mono Blue", url="https://example.com/d1", meta_share=10.0,
                      cards=[DeckCard(name="Brainstorm", quantity=1), DeckCard(name="Force of Will", quantity=4)])
        ])

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/meta")

    deck = response.json()["formats"][0]["decks"][0]
    brainstorm = next(c for c in deck["cards"] if c["name"] == "Brainstorm")
    force_of_will = next(c for c in deck["cards"] if c["name"] == "Force of Will")

    # set_code/collector_number are always None from /api/meta now (no
    # per-card printing lookup); the frontend fetches art by name instead.
    assert brainstorm["set_code"] is None
    assert brainstorm["collector_number"] is None
    assert force_of_will["set_code"] is None
    assert force_of_will["collector_number"] is None


def test_api_meta_skips_formats_with_no_saved_decklists(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    from api.users import ensure_user, set_formats
    ensure_user("google:alice@example.com")
    set_formats("google:alice@example.com", ["standard"])  # no saved meta_decks for this format

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["formats"] == []
