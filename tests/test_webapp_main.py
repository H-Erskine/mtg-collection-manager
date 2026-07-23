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

    from webapp.main import app
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


def test_whoami_redirects_when_not_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/whoami", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_whoami_returns_email_when_logged_in(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    from api.users import ensure_user
    ensure_user("google:alice@example.com")

    with client as c:
        with c.session_transaction() as session:
            session["user_id"] = "google:alice@example.com"
        response = c.get("/api/whoami")

    assert response.status_code == 200
    assert response.json() == {"email": "alice@example.com"}


def test_root_redirects_to_login_when_not_authenticated(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


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
    assert matching[0]["status"] in (302, 307)


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
