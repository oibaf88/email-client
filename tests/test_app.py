import os
import re
import subprocess
import sys

import pytest

import app as app_module


@pytest.fixture
def client():
    app_module.app.config.update(TESTING=True)
    with app_module.app.test_client() as test_client:
        yield test_client


def get_state(client):
    response = client.get("/api/state")
    assert response.status_code == 200
    return response.get_json()


def write_headers(state):
    return {"X-CSRF-Token": state["csrf_token"]}


def test_showcase_is_automatic_and_explicit(client):
    state = get_state(client)

    assert state["mode"] == "showcase"
    assert state["release"] == "1.2.0"
    assert state["authenticated"] is True
    assert state["simulated"] is True
    assert state["network_access"] is False
    assert state["user"].endswith("@demo.invalid")
    assert "imap_host" not in state
    assert "smtp_host" not in state

    page = client.get("/")
    assert page.status_code == 200
    assert b"SAFE SHOWCASE" in page.data
    assert b"NO PASSWORDS" in page.data


def test_showcase_never_opens_mail_connections(client, monkeypatch):
    def unexpected_network(*args, **kwargs):
        raise AssertionError("showcase attempted a mail network connection")

    monkeypatch.setattr(app_module.imaplib, "IMAP4_SSL", unexpected_network)
    monkeypatch.setattr(app_module.smtplib, "SMTP", unexpected_network)
    monkeypatch.setattr(app_module.smtplib, "SMTP_SSL", unexpected_network)

    state = get_state(client)
    headers = write_headers(state)

    login = client.post(
        "/api/login",
        json={"email": "real@example.com", "password": "must-not-be-read"},
        headers=headers,
    )
    assert login.status_code == 409
    assert "disabled" in login.get_json()["error"].lower()

    folders = client.get("/api/folders")
    assert folders.status_code == 200
    assert {folder["name"] for folder in folders.get_json()["folders"]} == {
        "INBOX",
        "Sent",
        "Trash",
    }

    inbox = client.get("/api/messages?folder=INBOX").get_json()["messages"]
    assert len(inbox) == 4
    assert all("body" not in message for message in inbox)

    message = client.get("/api/messages/104?folder=INBOX")
    assert message.status_code == 200
    assert "synthetic pilot" in message.get_json()["message"]["body"]

    unread = client.post(
        "/api/messages/104/read?folder=INBOX",
        json={"read": False},
        headers=headers,
    )
    assert unread.status_code == 200
    assert unread.get_json()["read"] is False

    deleted = client.delete("/api/messages/104?folder=INBOX", headers=headers)
    assert deleted.status_code == 200
    trash = client.get("/api/messages?folder=Trash").get_json()["messages"]
    assert any(item["uid"] == "104" for item in trash)

    sent = client.post(
        "/api/send",
        json={
            "to": "reviewer@example.invalid",
            "subject": "Synthetic review",
            "body": "This message must remain inside the temporary session.",
        },
        headers=headers,
    )
    assert sent.status_code == 201
    assert sent.get_json()["simulated"] is True
    assert "no email was delivered" in sent.get_json()["warning"].lower()
    sent_items = client.get("/api/messages?folder=Sent").get_json()["messages"]
    assert any(item["subject"] == "Synthetic review" for item in sent_items)


def test_showcase_reset_restores_seed(client):
    state = get_state(client)
    headers = write_headers(state)
    client.post(
        "/api/send",
        json={"to": "a@example.invalid", "subject": "Temporary", "body": "Temporary"},
        headers=headers,
    )

    response = client.post("/api/demo/reset", json={}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()["state"]["mode"] == "showcase"
    sent = client.get("/api/messages?folder=Sent").get_json()["messages"]
    assert [item["subject"] for item in sent] == ["Synthetic deployment handoff"]


def test_csrf_is_required_for_every_write(client):
    response = client.post(
        "/api/send",
        json={"to": "a@example.invalid", "subject": "No token", "body": "Blocked"},
    )
    assert response.status_code == 403
    assert "token" in response.get_json()["error"].lower()


def test_message_validation_limits(client):
    state = get_state(client)
    headers = write_headers(state)

    too_many = ", ".join(f"user{index}@example.invalid" for index in range(11))
    cases = [
        ({"to": too_many, "subject": "Valid", "body": "Valid"}, "maximum"),
        (
            {
                "to": "a@example.invalid",
                "subject": "x" * (app_module.MAX_SUBJECT_CHARS + 1),
                "body": "Valid",
            },
            "subject",
        ),
        (
            {
                "to": "a@example.invalid",
                "subject": "Valid",
                "body": "x" * (app_module.MAX_BODY_CHARS + 1),
            },
            "body",
        ),
    ]
    for payload, expected in cases:
        response = client.post("/api/send", json=payload, headers=headers)
        assert response.status_code == 400
        assert expected in response.get_json()["error"].lower()

    invalid_uid = client.get("/api/messages/not-a-number?folder=INBOX")
    assert invalid_uid.status_code == 400

    oversized = client.post(
        "/api/send",
        data='{"padding":"' + ("x" * app_module.MAX_REQUEST_BYTES) + '"}',
        content_type="application/json",
        headers=headers,
    )
    assert oversized.status_code == 413


def test_health_and_security_headers(client):
    live = client.get("/healthz")
    ready = client.get("/readyz")
    page = client.get("/")

    assert live.status_code == 200
    assert live.get_json() == {"mode": "showcase", "release": "1.2.0", "status": "alive"}
    assert ready.status_code == 200
    assert ready.get_json()["status"] == "ready"
    assert ready.get_json()["release"] == "1.2.0"
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Content-Type-Options"] == "nosniff"
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]

    match = re.search(r'nonce="([^"]+)"', page.get_data(as_text=True))
    assert match
    assert f"'nonce-{match.group(1)}'" in page.headers["Content-Security-Policy"]


def test_live_login_regenerates_session(client, monkeypatch):
    monkeypatch.setattr(app_module, "APP_MODE", "live")
    monkeypatch.setattr(app_module, "MAIL_DOMAIN", "")
    validated = []
    regenerated = []

    monkeypatch.setattr(
        app_module,
        "validate_live_credentials",
        lambda email, password: validated.append((email, password)),
    )
    monkeypatch.setattr(
        app_module.app.session_interface,
        "regenerate",
        lambda session_object: regenerated.append(session_object.get("mail_user")),
    )

    state = get_state(client)
    response = client.post(
        "/api/login",
        json={"email": "owner@example.com", "password": "private-test-value"},
        headers=write_headers(state),
    )

    assert response.status_code == 200
    assert response.get_json()["authenticated"] is True
    assert response.get_json()["user"] == "owner@example.com"
    assert validated == [("owner@example.com", "private-test-value")]
    assert regenerated == ["owner@example.com"]

    with client.session_transaction() as live_session:
        assert live_session["mail_user"] == "owner@example.com"
        assert live_session["mail_password"] == "private-test-value"
        assert live_session["csrf_token"] != state["csrf_token"]


def test_live_readiness_reports_missing_dependencies(monkeypatch):
    monkeypatch.setattr(app_module, "APP_MODE", "live")
    monkeypatch.setattr(app_module, "IS_PRODUCTION", True)
    monkeypatch.setattr(app_module, "IMAP_HOST", "")
    monkeypatch.setattr(app_module, "SMTP_HOST", "")
    monkeypatch.setattr(app_module, "SESSION_REDIS_URL", "")

    assert app_module.runtime_config_issues() == [
        "IMAP_HOST",
        "SMTP_HOST",
        "SESSION_REDIS_URL",
    ]


def test_production_secret_and_cookie_fail_closed():
    environment = os.environ.copy()
    environment.update(
        {
            "APP_MODE": "showcase",
            "APP_ENV": "production",
            "FLASK_SECRET_KEY": "short",
            "SESSION_COOKIE_SECURE": "true",
        }
    )
    weak_secret = subprocess.run(
        [sys.executable, "-c", "import app"],
        capture_output=True,
        cwd=os.getcwd(),
        env=environment,
        text=True,
        check=False,
    )
    assert weak_secret.returncode != 0
    assert "at least 32 characters" in weak_secret.stderr

    environment["FLASK_SECRET_KEY"] = "x" * 48
    environment["SESSION_COOKIE_SECURE"] = "false"
    insecure_cookie = subprocess.run(
        [sys.executable, "-c", "import app"],
        capture_output=True,
        cwd=os.getcwd(),
        env=environment,
        text=True,
        check=False,
    )
    assert insecure_cookie.returncode != 0
    assert "SESSION_COOKIE_SECURE=true" in insecure_cookie.stderr
