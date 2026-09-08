import sqlite3

import pytest

import app as app_module


@pytest.fixture
def client(tmp_path):
    app_module.app.config.update(
        TESTING=True,
        DATABASE_PATH=str(tmp_path / "test-email-client.db"),
        LOCAL_ADDRESS="local@test.invalid",
    )
    with app_module.app.test_client() as test_client:
        yield test_client


def get_state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    return response.get_json()


def headers(client):
    return {"X-CSRF-Token": get_state(client)["csrf_token"]}


def test_local_state_has_no_external_services(client):
    state = get_state(client)
    assert state["mode"] == "local"
    assert state["release"] == "2.0.0"
    assert state["authenticated"] is True
    assert state["local_only"] is True
    assert state["network_access"] is False
    assert state["storage"] == "sqlite"
    assert state["user"] == "local@test.invalid"


def test_database_is_created_and_seeded(client, tmp_path):
    folders = client.get("/api/folders")
    assert folders.status_code == 200
    counts = {item["name"]: item["count"] for item in folders.get_json()["folders"]}
    assert counts == {"INBOX": 3, "Sent": 1, "Trash": 0}

    path = tmp_path / "test-email-client.db"
    assert path.exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 4


def test_messages_persist_in_sqlite(client):
    state = get_state(client)
    response = client.post(
        "/api/send",
        json={
            "to": "recipient@example.invalid",
            "subject": "Persist me",
            "body": "Stored in SQLite.",
        },
        headers={"X-CSRF-Token": state["csrf_token"]},
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["stored"] is True
    assert payload["delivered"] is False

    sent = client.get("/api/messages?folder=Sent").get_json()["messages"]
    assert any(message["subject"] == "Persist me" for message in sent)


def test_read_and_delete_flow(client):
    inbox = client.get("/api/messages?folder=INBOX").get_json()["messages"]
    uid = inbox[0]["uid"]

    response = client.post(
        f"/api/messages/{uid}/read?folder=INBOX",
        json={"read": True},
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.get_json()["read"] is True

    response = client.delete(
        f"/api/messages/{uid}?folder=INBOX",
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.get_json()["action"] == "moved-to-trash"

    trash = client.get("/api/messages?folder=Trash").get_json()["messages"]
    assert any(message["uid"] == uid for message in trash)

    response = client.delete(
        f"/api/messages/{uid}?folder=Trash",
        headers=headers(client),
    )
    assert response.status_code == 200
    assert response.get_json()["action"] == "deleted"


def test_reset_restores_seed(client):
    client.post(
        "/api/send",
        json={"to": "a@example.invalid", "subject": "Temporary", "body": "Temporary"},
        headers=headers(client),
    )
    response = client.post("/api/reset", json={}, headers=headers(client))
    assert response.status_code == 200

    counts = {
        item["name"]: item["count"]
        for item in client.get("/api/folders").get_json()["folders"]
    }
    assert counts == {"INBOX": 3, "Sent": 1, "Trash": 0}


def test_csrf_required_for_writes(client):
    response = client.post(
        "/api/send",
        json={"to": "a@example.invalid", "subject": "No token", "body": "Blocked"},
    )
    assert response.status_code == 403


def test_validation_limits(client):
    token_headers = headers(client)
    too_many = ", ".join(f"user{i}@example.invalid" for i in range(11))
    response = client.post(
        "/api/send",
        json={"to": too_many, "subject": "Valid", "body": "Valid"},
        headers=token_headers,
    )
    assert response.status_code == 400

    response = client.post(
        "/api/send",
        json={
            "to": "a@example.invalid",
            "subject": "x" * (app_module.MAX_SUBJECT_CHARS + 1),
            "body": "Valid",
        },
        headers=token_headers,
    )
    assert response.status_code == 400

    response = client.get("/api/messages/not-a-number?folder=INBOX")
    assert response.status_code == 400


def test_login_and_logout_are_disabled(client):
    token_headers = headers(client)
    assert client.post("/api/login", json={}, headers=token_headers).status_code == 409
    assert client.post("/api/logout", json={}, headers=token_headers).status_code == 409


def test_health_ready_and_security_headers(client):
    health = client.get("/healthz")
    ready = client.get("/readyz")
    page = client.get("/")

    assert health.get_json() == {"mode": "local", "release": "2.0.0", "status": "alive"}
    assert ready.status_code == 200
    assert ready.get_json()["storage"] == "sqlite"
    assert ready.get_json()["database"] == "test-email-client.db"

    assert page.status_code == 200
    assert b"LOCAL ONLY" in page.data
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
