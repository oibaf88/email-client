import os
import re
import secrets
import sqlite3
from datetime import UTC, datetime
from email.utils import getaddresses
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

load_dotenv()

APP_RELEASE = os.getenv("APP_RELEASE", "2.0.0").strip() or "2.0.0"
APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/email-client.db").strip() or "data/email-client.db"
LOCAL_ADDRESS = os.getenv("LOCAL_ADDRESS", "local@localhost.invalid").strip().lower()
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "65536"))
MAX_SUBJECT_CHARS = int(os.getenv("MAX_SUBJECT_CHARS", "200"))
MAX_BODY_CHARS = int(os.getenv("MAX_BODY_CHARS", "20000"))
MAX_RECIPIENTS = int(os.getenv("MAX_RECIPIENTS", "10"))
MAX_ADDRESS_CHARS = int(os.getenv("MAX_ADDRESS_CHARS", "320"))
MAX_MESSAGE_LIST_SIZE = int(os.getenv("MAX_MESSAGE_LIST_SIZE", "100"))

ADDRESS_PATTERN = re.compile(r"^[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+$")
VALID_FOLDERS = ("INBOX", "Sent", "Trash")

secret_key = os.getenv("FLASK_SECRET_KEY", "").strip() or secrets.token_urlsafe(48)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secret_key,
    DATABASE_PATH=DATABASE_PATH,
    LOCAL_ADDRESS=LOCAL_ADDRESS,
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=False,
)

SEED_MESSAGES = (
    {
        "folder": "INBOX",
        "sender": "welcome@local.invalid",
        "recipient": LOCAL_ADDRESS,
        "subject": "Welcome to BFAB Local Mail",
        "body": (
            "This mailbox is stored only in your local SQLite database.\n\n"
            "There are no IMAP, SMTP, Redis, Supabase, Render, or cloud dependencies."
        ),
        "is_read": 0,
    },
    {
        "folder": "INBOX",
        "sender": "sqlite@local.invalid",
        "recipient": LOCAL_ADDRESS,
        "subject": "Your database is a normal SQLite file",
        "body": (
            "The default database is data/email-client.db.\n\n"
            "You can inspect it with DB Browser for SQLite while the app is stopped."
        ),
        "is_read": 0,
    },
    {
        "folder": "INBOX",
        "sender": "docker@local.invalid",
        "recipient": LOCAL_ADDRESS,
        "subject": "Docker is optional",
        "body": (
            "Run with Docker Compose for the easiest setup, or use a Python virtual "
            "environment and run Flask directly."
        ),
        "is_read": 1,
    },
    {
        "folder": "Sent",
        "sender": LOCAL_ADDRESS,
        "recipient": "example@local.invalid",
        "subject": "Local-only example",
        "body": "This example message was stored locally and was never sent over a network.",
        "is_read": 1,
    },
)


class LocalMailError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def database_path() -> Path:
    configured = str(app.config.get("DATABASE_PATH", DATABASE_PATH))
    return Path(configured).expanduser()


def connect_database() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder TEXT NOT NULL CHECK (folder IN ('INBOX', 'Sent', 'Trash')),
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0 CHECK (is_read IN (0, 1))
        );

        CREATE INDEX IF NOT EXISTS idx_messages_folder_created
            ON messages(folder, created_at DESC, id DESC);
        """
    )
    count = connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    if count == 0:
        seed_database(connection)
    connection.commit()


def seed_database(connection: sqlite3.Connection) -> None:
    base_time = datetime.now(UTC)
    local_address = app.config["LOCAL_ADDRESS"]
    for message in SEED_MESSAGES:
        created_at = base_time.replace(microsecond=0).isoformat()
        connection.execute(
            """
            INSERT INTO messages
                (folder, sender, recipient, subject, body, created_at, is_read)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["folder"],
                local_address if message["sender"] == LOCAL_ADDRESS else message["sender"],
                local_address if message["recipient"] == LOCAL_ADDRESS else message["recipient"],
                message["subject"],
                message["body"],
                created_at,
                message["is_read"],
            ),
        )


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_database()
        ensure_schema(g.db)
    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def reset_database() -> None:
    connection = get_db()
    connection.execute("DELETE FROM messages")
    connection.execute("DELETE FROM sqlite_sequence WHERE name = 'messages'")
    seed_database(connection)
    connection.commit()


def ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def prepare_request():
    ensure_csrf_token()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        provided = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            raise LocalMailError("Invalid request token.", 403)


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; "
        "object-src 'none'; form-action 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    return response


def json_object() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise LocalMailError("A JSON object is required.")
    return data


def validate_folder(value: str) -> str:
    folder = (value or "INBOX").strip()
    if folder not in VALID_FOLDERS:
        raise LocalMailError("Invalid folder.")
    return folder


def validate_uid(value: str) -> int:
    try:
        uid = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalMailError("Invalid message id.") from exc
    if uid < 1:
        raise LocalMailError("Invalid message id.")
    return uid


def parse_recipients(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_ADDRESS_CHARS * MAX_RECIPIENTS:
        raise LocalMailError("Recipient list is invalid.")
    addresses = [address.strip().lower() for _, address in getaddresses([value]) if address]
    addresses = list(dict.fromkeys(addresses))
    if not addresses:
        raise LocalMailError("At least one recipient is required.")
    if len(addresses) > MAX_RECIPIENTS:
        raise LocalMailError(f"A maximum of {MAX_RECIPIENTS} recipients is allowed.")
    if any(
        len(address) > MAX_ADDRESS_CHARS or not ADDRESS_PATTERN.fullmatch(address)
        for address in addresses
    ):
        raise LocalMailError("One or more recipients are invalid.")
    return ", ".join(addresses)


def validate_message_fields(data: dict) -> tuple[str, str, str]:
    recipients = parse_recipients(data.get("to", ""))
    subject = data.get("subject", "")
    body = data.get("body", "")
    if not isinstance(subject, str) or len(subject) > MAX_SUBJECT_CHARS:
        raise LocalMailError(f"Subject must contain at most {MAX_SUBJECT_CHARS} characters.")
    if not isinstance(body, str) or len(body) > MAX_BODY_CHARS:
        raise LocalMailError(f"Body must contain at most {MAX_BODY_CHARS} characters.")
    return recipients, subject.strip(), body


def row_to_message(row: sqlite3.Row, include_body: bool = False) -> dict:
    message = {
        "uid": str(row["id"]),
        "folder": row["folder"],
        "from": row["sender"],
        "to": row["recipient"],
        "subject": row["subject"],
        "date": row["created_at"],
        "read": bool(row["is_read"]),
    }
    if include_body:
        message["body"] = row["body"]
    return message


@app.route("/")
def index():
    return render_template("email_system.html", release=APP_RELEASE)


@app.get("/healthz")
def healthz():
    return jsonify(status="alive", mode="local", release=APP_RELEASE)


@app.get("/readyz")
def readyz():
    try:
        get_db().execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        return jsonify(status="not-ready", mode="local", release=APP_RELEASE, error=str(exc)), 503
    return jsonify(
        status="ready",
        mode="local",
        release=APP_RELEASE,
        storage="sqlite",
        database=database_path().name,
    )


@app.get("/api/state")
def api_state():
    return jsonify(
        mode="local",
        release=APP_RELEASE,
        authenticated=True,
        local_only=True,
        network_access=False,
        storage="sqlite",
        user=app.config["LOCAL_ADDRESS"],
        csrf_token=ensure_csrf_token(),
    )


@app.post("/api/login")
def api_login_disabled():
    raise LocalMailError("Login is not used in local-only mode.", 409)


@app.post("/api/logout")
def api_logout_disabled():
    raise LocalMailError("Logout is not used in local-only mode.", 409)


@app.get("/api/folders")
def api_folders():
    connection = get_db()
    counts = {
        row["folder"]: row["count"]
        for row in connection.execute(
            "SELECT folder, COUNT(*) AS count FROM messages GROUP BY folder"
        ).fetchall()
    }
    folders = [
        {"name": folder, "count": counts.get(folder, 0)}
        for folder in VALID_FOLDERS
    ]
    return jsonify(folders=folders)


@app.get("/api/messages")
def api_messages():
    folder = validate_folder(request.args.get("folder", "INBOX"))
    rows = get_db().execute(
        """
        SELECT id, folder, sender, recipient, subject, body, created_at, is_read
        FROM messages
        WHERE folder = ?
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (folder, MAX_MESSAGE_LIST_SIZE),
    ).fetchall()
    return jsonify(messages=[row_to_message(row) for row in rows])


@app.get("/api/messages/<uid>")
def api_message(uid: str):
    message_id = validate_uid(uid)
    folder = validate_folder(request.args.get("folder", "INBOX"))
    row = get_db().execute(
        """
        SELECT id, folder, sender, recipient, subject, body, created_at, is_read
        FROM messages
        WHERE id = ? AND folder = ?
        """,
        (message_id, folder),
    ).fetchone()
    if row is None:
        raise LocalMailError("Message not found.", 404)
    return jsonify(message=row_to_message(row, include_body=True))


@app.post("/api/messages/<uid>/read")
def api_message_read(uid: str):
    message_id = validate_uid(uid)
    folder = validate_folder(request.args.get("folder", "INBOX"))
    data = json_object()
    read = data.get("read")
    if not isinstance(read, bool):
        raise LocalMailError("'read' must be true or false.")
    connection = get_db()
    result = connection.execute(
        "UPDATE messages SET is_read = ? WHERE id = ? AND folder = ?",
        (int(read), message_id, folder),
    )
    if result.rowcount == 0:
        raise LocalMailError("Message not found.", 404)
    connection.commit()
    return jsonify(uid=str(message_id), read=read)


@app.delete("/api/messages/<uid>")
def api_message_delete(uid: str):
    message_id = validate_uid(uid)
    folder = validate_folder(request.args.get("folder", "INBOX"))
    connection = get_db()
    if folder == "Trash":
        result = connection.execute(
            "DELETE FROM messages WHERE id = ? AND folder = 'Trash'",
            (message_id,),
        )
        action = "deleted"
    else:
        result = connection.execute(
            "UPDATE messages SET folder = 'Trash' WHERE id = ? AND folder = ?",
            (message_id, folder),
        )
        action = "moved-to-trash"
    if result.rowcount == 0:
        raise LocalMailError("Message not found.", 404)
    connection.commit()
    return jsonify(uid=str(message_id), action=action)


@app.post("/api/send")
def api_send():
    recipients, subject, body = validate_message_fields(json_object())
    connection = get_db()
    cursor = connection.execute(
        """
        INSERT INTO messages
            (folder, sender, recipient, subject, body, created_at, is_read)
        VALUES ('Sent', ?, ?, ?, ?, ?, 1)
        """,
        (app.config["LOCAL_ADDRESS"], recipients, subject, body, utc_now()),
    )
    connection.commit()
    return (
        jsonify(
            uid=str(cursor.lastrowid),
            stored=True,
            delivered=False,
            warning="Stored locally only; no email was delivered over the network.",
        ),
        201,
    )


@app.post("/api/reset")
@app.post("/api/demo/reset")
def api_reset():
    reset_database()
    return jsonify(status="reset", mode="local")


@app.errorhandler(LocalMailError)
def handle_local_mail_error(error):
    return jsonify(error=error.message), error.status_code


@app.errorhandler(HTTPException)
def handle_http_error(error):
    return jsonify(error=error.description), error.code


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if app.config.get("TESTING"):
        raise error
    return jsonify(error="Unexpected local application error."), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=APP_ENV == "development")
