import sqlite3

import pytest

import app as app_module


@pytest.fixture
def client(tmp_path):
    app_module.app.config.update(
        TESTING=True,
        DATABASE_PATH=str(tmp_path / "test-email-client.db"),
    )
    with app_module.app.test_client() as test_client:
        yield test_client


def state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    return response.get_json()


def headers(client):
    return {"X-CSRF-Token": state(client)["csrf_token"]}


def save_config(client):
    response = client.post(
        "/api/config",
        json={
            "mail_domain": "example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "sent_folder": "Sent",
        },
        headers=headers(client),
    )
    assert response.status_code == 200
    return response.get_json()["config"]


def test_local_state_and_database(client, tmp_path):
    current = state(client)
    assert current["mode"] == "local"
    assert current["release"] == "2.1.0"
    assert current["configured"] is False
    assert current["authenticated"] is False
    assert current["storage"] == "sqlite"
    assert current["runtime"] == "localhost"
    assert current["network_access"] == "imap-smtp-only"

    path = tmp_path / "test-email-client.db"
    assert path.exists()
    with sqlite3.connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "mail_config" in tables


def test_config_is_saved_locally_without_password(client, tmp_path):
    config = save_config(client)
    assert config["imap_host"] == "imap.example.com"
    assert config["smtp_port"] == 587

    with sqlite3.connect(tmp_path / "test-email-client.db") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(mail_config)")]
        row = connection.execute("SELECT imap_host, smtp_host FROM mail_config WHERE id=1").fetchone()
    assert "password" not in columns
    assert row == ("imap.example.com", "smtp.example.com")


def test_login_uses_imap_and_server_side_session(client, monkeypatch):
    config = save_config(client)
    calls = []
    monkeypatch.setattr(
        app_module,
        "validate_live_credentials",
        lambda user, password, saved: calls.append((user, password, saved)),
    )

    response = client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "app-password"},
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.get_json()["authenticated"] is True
    assert calls == [("owner@example.com", "app-password", config)]

    with client.session_transaction() as current_session:
        assert current_session["mail_user"] == "owner@example.com"
        assert current_session["mail_password"] == "app-password"


def test_config_change_clears_mail_credentials(client, monkeypatch):
    save_config(client)
    monkeypatch.setattr(app_module, "validate_live_credentials", lambda *_: None)
    client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "secret"},
        headers=headers(client),
    )
    save_config(client)
    assert state(client)["authenticated"] is False


def test_mail_routes_delegate_to_imap(client, monkeypatch):
    save_config(client)
    monkeypatch.setattr(app_module, "validate_live_credentials", lambda *_: None)
    client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "secret"},
        headers=headers(client),
    )
    monkeypatch.setattr(
        app_module,
        "live_folders",
        lambda: [{"name": "INBOX", "display_name": "INBOX", "role": "inbox"}],
    )
    monkeypatch.setattr(
        app_module,
        "live_messages",
        lambda folder: [{"uid": "1", "subject": "Hello", "from": "a@example.com", "to": "owner@example.com", "date": "now", "is_read": False}],
    )
    monkeypatch.setattr(
        app_module,
        "live_message",
        lambda folder, uid: {"uid": uid, "subject": "Hello", "from": "a@example.com", "to": "owner@example.com", "date": "now", "is_read": False, "body": "Body", "attachments": [], "truncated": False},
    )

    assert client.get("/api/folders").status_code == 200
    assert client.get("/api/messages?folder=INBOX").get_json()["messages"][0]["uid"] == "1"
    assert client.get("/api/messages/1?folder=INBOX").get_json()["message"]["body"] == "Body"


def test_send_uses_real_smtp_path(client, monkeypatch):
    save_config(client)
    monkeypatch.setattr(app_module, "validate_live_credentials", lambda *_: None)
    client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "secret"},
        headers=headers(client),
    )
    sent = []
    monkeypatch.setattr(app_module, "live_send", lambda message: sent.append(message))
    monkeypatch.setattr(app_module, "append_to_sent", lambda message: None)

    response = client.post(
        "/api/send",
        json={"to": "recipient@example.net", "subject": "Real path", "body": "Body"},
        headers=headers(client),
    )
    assert response.status_code == 201
    assert response.get_json()["delivered"] is True
    assert len(sent) == 1
    assert sent[0]["To"] == "recipient@example.net"


def test_csrf_and_auth_are_required(client):
    assert client.post("/api/config", json={}).status_code == 403
    assert client.get("/api/folders").status_code == 401


def test_invalid_config_is_rejected(client):
    response = client.post(
        "/api/config",
        json={"imap_host": "bad host", "imap_port": 993, "smtp_host": "smtp.example.com", "smtp_port": 587},
        headers=headers(client),
    )
    assert response.status_code == 400


def test_health_ready_and_security_headers(client):
    health = client.get("/healthz")
    ready = client.get("/readyz")
    page = client.get("/")

    assert health.get_json() == {"mode": "local", "release": "2.1.0", "status": "alive"}
    assert ready.status_code == 200
    assert ready.get_json()["storage"] == "sqlite"
    assert ready.get_json()["mail_configured"] is False
    assert page.status_code == 200
    assert b"real IMAP/SMTP mail" in page.data
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
