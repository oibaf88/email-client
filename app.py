import html
import imaplib
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import tempfile
import time
from datetime import timedelta
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request, session
from flask_session import Session
from werkzeug.exceptions import HTTPException

load_dotenv()

APP_RELEASE = os.getenv("APP_RELEASE", "2.1.0").strip() or "2.1.0"
APP_ENV = os.getenv("APP_ENV", "local").strip().lower()
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/email-client.db").strip() or "data/email-client.db"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "7200"))
SEND_RATE_LIMIT_PER_HOUR = int(os.getenv("SEND_RATE_LIMIT_PER_HOUR", "30"))
LOGIN_RATE_LIMIT_PER_15_MINUTES = int(os.getenv("LOGIN_RATE_LIMIT_PER_15_MINUTES", "10"))
MAX_MESSAGE_LIST_SIZE = int(os.getenv("MAX_MESSAGE_LIST_SIZE", "50"))
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "65536"))
MAX_SUBJECT_CHARS = int(os.getenv("MAX_SUBJECT_CHARS", "200"))
MAX_BODY_CHARS = int(os.getenv("MAX_BODY_CHARS", "20000"))
MAX_RECIPIENTS = int(os.getenv("MAX_RECIPIENTS", "10"))
MAX_ADDRESS_CHARS = int(os.getenv("MAX_ADDRESS_CHARS", "320"))
MAX_PASSWORD_CHARS = int(os.getenv("MAX_PASSWORD_CHARS", "512"))
MAX_READ_MESSAGE_BYTES = int(os.getenv("MAX_READ_MESSAGE_BYTES", "2000000"))

UID_PATTERN = re.compile(r"^[1-9][0-9]{0,18}$")
ADDRESS_PATTERN = re.compile(r"^[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
DOMAIN_PATTERN = re.compile(r"^[A-Za-z0-9.-]{1,253}$")

secret_key = os.getenv("FLASK_SECRET_KEY", "").strip() or secrets.token_urlsafe(48)
session_dir = os.getenv(
    "SESSION_FILE_DIR", os.path.join(tempfile.gettempdir(), "email-client-sessions")
)
Path(session_dir).mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=secret_key,
    DATABASE_PATH=DATABASE_PATH,
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_TTL_SECONDS),
    SESSION_PERMANENT=True,
    SESSION_USE_SIGNER=True,
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=session_dir,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=False,
)
Session(app)


class MailClientError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def database_path() -> Path:
    return Path(str(app.config.get("DATABASE_PATH", DATABASE_PATH))).expanduser()


def connect_database() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mail_domain TEXT NOT NULL DEFAULT '',
            imap_host TEXT NOT NULL,
            imap_port INTEGER NOT NULL CHECK (imap_port BETWEEN 1 AND 65535),
            smtp_host TEXT NOT NULL,
            smtp_port INTEGER NOT NULL CHECK (smtp_port BETWEEN 1 AND 65535),
            sent_folder TEXT NOT NULL DEFAULT 'Sent',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    connection.commit()


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


def get_mail_config() -> dict | None:
    row = get_db().execute(
        """
        SELECT mail_domain, imap_host, imap_port, smtp_host, smtp_port, sent_folder
        FROM mail_config WHERE id = 1
        """
    ).fetchone()
    return dict(row) if row else None


def validate_host(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise MailClientError(f"{field} is required.")
    host = value.strip().lower()
    if not HOST_PATTERN.fullmatch(host) or ".." in host or host.startswith(".") or host.endswith("."):
        raise MailClientError(f"{field} is not a valid hostname.")
    return host


def validate_port(value: object, field: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise MailClientError(f"{field} must be a port number.") from exc
    if not 1 <= port <= 65535:
        raise MailClientError(f"{field} must be between 1 and 65535.")
    return port


def validate_domain(value: object) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise MailClientError("Mail domain must be text.")
    domain = value.strip().lower()
    if not DOMAIN_PATTERN.fullmatch(domain) or ".." in domain:
        raise MailClientError("Mail domain is invalid.")
    return domain


def validate_mailbox_name(value: object) -> str:
    if not isinstance(value, str):
        raise MailClientError("Invalid folder name.")
    mailbox = value.strip() or "INBOX"
    if len(mailbox) > 160 or any(char in mailbox for char in ("\r", "\n", "\x00")):
        raise MailClientError("Invalid folder name.")
    return mailbox


def validate_config_payload(data: dict) -> dict:
    sent_folder = validate_mailbox_name(data.get("sent_folder", "Sent"))
    return {
        "mail_domain": validate_domain(data.get("mail_domain", "")),
        "imap_host": validate_host(data.get("imap_host"), "IMAP host"),
        "imap_port": validate_port(data.get("imap_port", 993), "IMAP port"),
        "smtp_host": validate_host(data.get("smtp_host"), "SMTP host"),
        "smtp_port": validate_port(data.get("smtp_port", 587), "SMTP port"),
        "sent_folder": sent_folder,
    }


def save_mail_config(config: dict) -> None:
    connection = get_db()
    connection.execute(
        """
        INSERT INTO mail_config
            (id, mail_domain, imap_host, imap_port, smtp_host, smtp_port, sent_folder, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            mail_domain = excluded.mail_domain,
            imap_host = excluded.imap_host,
            imap_port = excluded.imap_port,
            smtp_host = excluded.smtp_host,
            smtp_port = excluded.smtp_port,
            sent_folder = excluded.sent_folder,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            config["mail_domain"],
            config["imap_host"],
            config["imap_port"],
            config["smtp_host"],
            config["smtp_port"],
            config["sent_folder"],
        ),
    )
    connection.commit()


def ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def rotate_session() -> None:
    regenerate = getattr(app.session_interface, "regenerate", None)
    if callable(regenerate):
        regenerate(session)
    session.modified = True


@app.before_request
def prepare_request():
    g.csp_nonce = secrets.token_urlsafe(18)
    ensure_csrf_token()
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        provided = request.headers.get("X-CSRF-Token", "")
        if not expected or not secrets.compare_digest(expected, provided):
            raise MailClientError("Invalid request token.", 403)


@app.after_request
def add_security_headers(response):
    nonce = getattr(g, "csp_nonce", "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"style-src 'self' 'nonce-{nonce}'; "
        f"script-src 'self' 'nonce-{nonce}'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
        "base-uri 'none'; object-src 'none'; form-action 'self'; frame-ancestors 'none'"
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
        raise MailClientError("A JSON object is required.")
    return data


def validate_uid(value: str) -> str:
    if not UID_PATTERN.fullmatch(value or ""):
        raise MailClientError("Invalid message id.")
    return value


def quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_login_address(address: object, config: dict) -> str:
    if not isinstance(address, str):
        raise MailClientError("Use a full mailbox address.")
    normalized = address.strip().lower()
    if len(normalized) > MAX_ADDRESS_CHARS or not ADDRESS_PATTERN.fullmatch(normalized):
        raise MailClientError("Use a valid mailbox address.")
    domain = config.get("mail_domain", "")
    if domain and not normalized.endswith(f"@{domain}"):
        raise MailClientError(f"Only {domain} mailboxes can sign in.", 403)
    return normalized


def parse_recipients(value: object) -> list[str]:
    if not isinstance(value, str) or len(value) > MAX_ADDRESS_CHARS * MAX_RECIPIENTS:
        raise MailClientError("Recipient list is invalid.")
    parsed = [address.strip().lower() for _, address in getaddresses([value]) if address]
    recipients = list(dict.fromkeys(parsed))
    if not recipients:
        raise MailClientError("At least one recipient is required.")
    if len(recipients) > MAX_RECIPIENTS:
        raise MailClientError(f"A maximum of {MAX_RECIPIENTS} recipients is allowed.")
    if any(
        len(address) > MAX_ADDRESS_CHARS or not ADDRESS_PATTERN.fullmatch(address)
        for address in recipients
    ):
        raise MailClientError("One or more recipients are invalid.")
    return recipients


def validate_message_fields(data: dict) -> tuple[list[str], str, str]:
    recipients = parse_recipients(data.get("to", ""))
    subject_value = data.get("subject", "")
    body_value = data.get("body", "")
    if not isinstance(subject_value, str) or not isinstance(body_value, str):
        raise MailClientError("Subject and body must be text.")
    subject = subject_value.strip()
    body = body_value.strip()
    if not subject or not body:
        raise MailClientError("Subject and body are required.")
    if len(subject) > MAX_SUBJECT_CHARS or any(char in subject for char in ("\r", "\n")):
        raise MailClientError(f"Subject must contain at most {MAX_SUBJECT_CHARS} characters.")
    if len(body) > MAX_BODY_CHARS:
        raise MailClientError(f"Body must contain at most {MAX_BODY_CHARS} characters.")
    return recipients, subject, body


def enforce_sliding_limit(key: str, seconds: int, maximum: int, message: str) -> None:
    now = int(time.time())
    timestamps = [stamp for stamp in session.get(key, []) if int(stamp) >= now - seconds]
    if len(timestamps) >= maximum:
        raise MailClientError(message, 429)
    timestamps.append(now)
    session[key] = timestamps


def is_authenticated() -> bool:
    return bool(session.get("mail_user") and session.get("mail_password"))


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            raise MailClientError("Please sign in first.", 401)
        return view(*args, **kwargs)

    return wrapped


def get_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def get_credentials() -> tuple[str, str]:
    user = session.get("mail_user")
    password = session.get("mail_password")
    if not user or not password:
        raise MailClientError("Please sign in first.", 401)
    return user, password


def connect_imap(user: str, password: str, config: dict):
    try:
        client = imaplib.IMAP4_SSL(
            config["imap_host"],
            config["imap_port"],
            ssl_context=get_tls_context(),
            timeout=25,
        )
        status, _ = client.login(user, password)
    except imaplib.IMAP4.error as error:
        raise MailClientError("IMAP authentication failed.", 401) from error
    except OSError as error:
        raise MailClientError("Could not connect to the IMAP server.", 503) from error
    if status != "OK":
        safe_logout(client)
        raise MailClientError("IMAP authentication failed.", 401)
    return client


def validate_live_credentials(user: str, password: str, config: dict) -> None:
    client = connect_imap(user, password, config)
    safe_logout(client)


def open_imap(mailbox: str | None = None, readonly: bool = False):
    config = get_mail_config()
    if not config:
        raise MailClientError("Configure IMAP/SMTP first.", 409)
    user, password = get_credentials()
    client = connect_imap(user, password, config)
    if mailbox:
        safe_mailbox = validate_mailbox_name(mailbox)
        status, data = client.select(quote_mailbox(safe_mailbox), readonly=readonly)
        if status != "OK":
            safe_logout(client)
            detail = decode_bytes(data[0]) if data else safe_mailbox
            raise MailClientError(f"Could not open folder: {detail}", 404)
    return client


def safe_logout(client, selected: bool = False) -> None:
    try:
        if selected:
            client.close()
    except (imaplib.IMAP4.error, OSError):
        pass
    try:
        client.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def decode_bytes(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def decode_header_value(value) -> str:
    if not value:
        return ""
    parts = []
    for payload, charset in decode_header(str(value)):
        if isinstance(payload, bytes):
            parts.append(payload.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(payload)
    return "".join(parts)


def classify_folder(name: str, flags: list[str]) -> str:
    normalized = name.lower()
    joined_flags = " ".join(flags)
    if normalized == "inbox":
        return "inbox"
    if "sent" in joined_flags or normalized in {"sent", "sent items", "sent messages"}:
        return "sent"
    if "trash" in joined_flags or normalized in {"trash", "deleted messages"}:
        return "trash"
    if "drafts" in joined_flags or normalized == "drafts":
        return "drafts"
    if "junk" in joined_flags or normalized in {"junk", "spam"}:
        return "junk"
    return "folder"


def parse_list_response(line: bytes) -> dict | None:
    text = decode_bytes(line)
    match = re.match(r"\((?P<flags>.*?)\)\s+\".*?\"\s+(?P<name>.+)$", text)
    if not match:
        return None
    raw_name = match.group("name").strip()
    if raw_name.startswith('"') and raw_name.endswith('"'):
        raw_name = raw_name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    flags = [flag.strip("\\").lower() for flag in match.group("flags").split() if flag]
    return {
        "name": raw_name,
        "display_name": raw_name,
        "role": classify_folder(raw_name, flags),
        "flags": flags,
    }


def live_folders() -> list[dict]:
    client = open_imap()
    try:
        status, data = client.list()
        if status != "OK":
            raise MailClientError("Could not list folders.", 503)
        folders = [parsed for line in data or [] if (parsed := parse_list_response(line))]
        if not folders:
            folders = [{"name": "INBOX", "display_name": "INBOX", "role": "inbox"}]
        order = {"inbox": 0, "sent": 1, "drafts": 2, "trash": 3, "junk": 4}
        return sorted(folders, key=lambda item: (order.get(item["role"], 99), item["name"]))
    finally:
        safe_logout(client)


def parse_flags(raw: bytes) -> list[str]:
    match = re.search(rb"FLAGS \((?P<flags>[^)]*)\)", raw or b"", re.IGNORECASE)
    if not match:
        return []
    return [decode_bytes(flag) for flag in match.group("flags").split()]


def normalize_address_header(value) -> str:
    return ", ".join(address for _, address in getaddresses([str(value or "")]) if address)


def message_summary(uid: str, raw_headers: bytes, flags: list[str]) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(raw_headers or b"")
    return {
        "uid": uid,
        "subject": decode_header_value(message.get("subject")) or "(no subject)",
        "from": normalize_address_header(message.get("from")),
        "to": normalize_address_header(message.get("to")),
        "date": decode_header_value(message.get("date")),
        "message_id": decode_header_value(message.get("message-id")),
        "is_read": "\\Seen" in flags,
    }


def live_messages(folder: str) -> list[dict]:
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox, readonly=True)
    try:
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise MailClientError("Could not search messages.", 503)
        selected_uids = list(reversed((data[0] or b"").split()))[:MAX_MESSAGE_LIST_SIZE]
        if not selected_uids:
            return []
        uid_set = ",".join(validate_uid(decode_bytes(uid)) for uid in selected_uids)
        status, fetch_data = client.uid(
            "FETCH",
            uid_set,
            "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)] FLAGS)",
        )
        if status != "OK":
            raise MailClientError("Could not fetch message summaries.", 503)
        results: dict[str, tuple[bytes, list[str]]] = {}
        for item in fetch_data or []:
            if not isinstance(item, tuple):
                continue
            response_part, payload = item[0] or b"", item[1] or b""
            uid_match = re.search(rb"UID\s+(?P<uid>\d+)", response_part, re.IGNORECASE)
            if uid_match:
                parsed_uid = validate_uid(decode_bytes(uid_match.group("uid")))
                results[parsed_uid] = (payload, parse_flags(response_part))
        return [
            message_summary(uid, *results[uid])
            for uid in map(decode_bytes, selected_uids)
            if uid in results
        ]
    finally:
        safe_logout(client, selected=True)


def extract_fetch_payload(fetch_data) -> tuple[bytes, list[str]]:
    for item in fetch_data or []:
        if isinstance(item, tuple):
            return item[1] or b"", parse_flags(item[0] or b"")
    return b"", []


def extract_body(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def read_part(part) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(part.get_content_charset() or "utf-8", errors="replace")

    for part in message.walk() if message.is_multipart() else [message]:
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() == "text/plain":
            plain_parts.append(read_part(part))
        elif part.get_content_type() == "text/html":
            html_parts.append(read_part(part))
    if plain_parts:
        return "\n\n".join(part.strip() for part in plain_parts if part.strip())
    raw_html = "\n\n".join(part.strip() for part in html_parts if part.strip())
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_html))).strip()


def live_message(folder: str, uid: str) -> dict:
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox, readonly=True)
    try:
        query = f"(BODY.PEEK[]<0.{MAX_READ_MESSAGE_BYTES}> FLAGS)"
        status, data = client.uid("FETCH", uid, query)
        if status != "OK":
            raise MailClientError("Could not fetch message.", 404)
        raw_message, flags = extract_fetch_payload(data)
        if not raw_message:
            raise MailClientError("Message not found.", 404)
        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        result = message_summary(uid, raw_message, flags)
        result["body"] = extract_body(message)
        result["attachments"] = [
            {
                "filename": decode_header_value(part.get_filename()),
                "content_type": part.get_content_type(),
            }
            for part in message.walk()
            if part.get_content_disposition() == "attachment"
        ]
        result["truncated"] = len(raw_message) >= MAX_READ_MESSAGE_BYTES
        return result
    finally:
        safe_logout(client, selected=True)


def live_mark_read(folder: str, uid: str, is_read: bool) -> None:
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox)
    try:
        operation = "+FLAGS.SILENT" if is_read else "-FLAGS.SILENT"
        status, _ = client.uid("STORE", uid, operation, "(\\Seen)")
        if status != "OK":
            raise MailClientError("Could not update message state.", 503)
    finally:
        safe_logout(client, selected=True)


def live_delete(folder: str, uid: str) -> None:
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox)
    try:
        status, _ = client.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
        if status != "OK":
            raise MailClientError("Could not delete message.", 503)
        client.expunge()
    finally:
        safe_logout(client, selected=True)


def build_message(recipients: list[str], subject: str, body: str) -> EmailMessage:
    user, _ = get_credentials()
    config = get_mail_config() or {}
    message = EmailMessage()
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=config.get("mail_domain") or user.split("@")[-1])
    message.set_content(body)
    return message


def live_send(message: EmailMessage) -> None:
    config = get_mail_config()
    if not config:
        raise MailClientError("Configure IMAP/SMTP first.", 409)
    user, password = get_credentials()
    context = get_tls_context()
    try:
        if config["smtp_port"] == 465:
            with smtplib.SMTP_SSL(
                config["smtp_host"], config["smtp_port"], timeout=30, context=context
            ) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as smtp:
            smtp.ehlo()
            if not smtp.has_extn("starttls"):
                raise MailClientError("SMTP server does not offer STARTTLS.", 503)
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise MailClientError("SMTP authentication failed.", 401) from error
    except smtplib.SMTPException as error:
        raise MailClientError("Could not send message through SMTP.", 503) from error
    except OSError as error:
        raise MailClientError("Could not connect to the SMTP server.", 503) from error


def append_to_sent(message: EmailMessage) -> str | None:
    config = get_mail_config()
    if not config:
        return "Message sent, but no Sent folder configuration is available."
    client = open_imap()
    try:
        sent_folder = config["sent_folder"]
        status, data = client.list()
        if status == "OK":
            for line in data or []:
                parsed = parse_list_response(line)
                if parsed and parsed["role"] == "sent":
                    sent_folder = parsed["name"]
                    break
        status, _ = client.append(
            quote_mailbox(sent_folder),
            "\\Seen",
            imaplib.Time2Internaldate(time.time()),
            message.as_bytes(),
        )
        if status != "OK":
            return "Message sent, but a copy could not be saved in Sent."
        return None
    finally:
        safe_logout(client)


def public_state() -> dict:
    config = get_mail_config()
    return {
        "mode": "local",
        "release": APP_RELEASE,
        "authenticated": is_authenticated(),
        "configured": bool(config),
        "user": session.get("mail_user"),
        "csrf_token": ensure_csrf_token(),
        "storage": "sqlite",
        "runtime": "localhost",
        "network_access": "imap-smtp-only",
        "config": config,
    }


@app.get("/")
def home():
    return render_template("email_system.html", csp_nonce=g.csp_nonce, release=APP_RELEASE)


@app.get("/healthz")
def healthz():
    return jsonify(status="alive", mode="local", release=APP_RELEASE)


@app.get("/readyz")
def readyz():
    try:
        get_db().execute("SELECT 1").fetchone()
        configured = bool(get_mail_config())
    except sqlite3.Error as error:
        return jsonify(status="not_ready", mode="local", release=APP_RELEASE, error=str(error)), 503
    return jsonify(
        status="ready",
        mode="local",
        release=APP_RELEASE,
        storage="sqlite",
        database=database_path().name,
        mail_configured=configured,
    )


@app.get("/api/state")
def api_state():
    return jsonify(public_state())


@app.get("/api/config")
def api_config():
    return jsonify(config=get_mail_config())


@app.post("/api/config")
def api_config_save():
    config = validate_config_payload(json_object())
    save_mail_config(config)
    session.pop("mail_user", None)
    session.pop("mail_password", None)
    rotate_session()
    return jsonify(config=config, authenticated=False)


@app.delete("/api/config")
def api_config_delete():
    get_db().execute("DELETE FROM mail_config WHERE id = 1")
    get_db().commit()
    session.clear()
    session["csrf_token"] = secrets.token_urlsafe(32)
    rotate_session()
    return jsonify(ok=True)


@app.post("/api/login")
def api_login():
    config = get_mail_config()
    if not config:
        raise MailClientError("Configure IMAP/SMTP before signing in.", 409)
    enforce_sliding_limit(
        "login_timestamps",
        900,
        LOGIN_RATE_LIMIT_PER_15_MINUTES,
        "Too many login attempts. Try again later.",
    )
    data = json_object()
    mailbox = validate_login_address(data.get("email", ""), config)
    password = data.get("password", "")
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_CHARS:
        raise MailClientError("Password is required and must be within the allowed length.")
    validate_live_credentials(mailbox, password, config)
    session.clear()
    session.permanent = True
    session["mail_user"] = mailbox
    session["mail_password"] = password
    session["csrf_token"] = secrets.token_urlsafe(32)
    rotate_session()
    return jsonify(public_state())


@app.post("/api/logout")
@require_login
def api_logout():
    session.clear()
    session["csrf_token"] = secrets.token_urlsafe(32)
    rotate_session()
    return jsonify(public_state())


@app.get("/api/folders")
@require_login
def api_folders():
    return jsonify(folders=live_folders())


@app.get("/api/messages")
@require_login
def api_messages():
    folder = request.args.get("folder", "INBOX")
    return jsonify(folder=folder, messages=live_messages(folder))


@app.get("/api/messages/<uid>")
@require_login
def api_message(uid):
    valid_uid = validate_uid(uid)
    folder = request.args.get("folder", "INBOX")
    return jsonify(message=live_message(folder, valid_uid))


@app.post("/api/messages/<uid>/read")
@require_login
def api_mark_read(uid):
    valid_uid = validate_uid(uid)
    data = json_object()
    is_read = data.get("read", True)
    if not isinstance(is_read, bool):
        raise MailClientError("The read field must be a boolean.")
    folder = request.args.get("folder", "INBOX")
    live_mark_read(folder, valid_uid, is_read)
    return jsonify(ok=True, read=is_read)


@app.delete("/api/messages/<uid>")
@require_login
def api_delete_message(uid):
    valid_uid = validate_uid(uid)
    folder = request.args.get("folder", "INBOX")
    live_delete(folder, valid_uid)
    return jsonify(ok=True)


@app.post("/api/send")
@require_login
def api_send():
    recipients, subject, body = validate_message_fields(json_object())
    enforce_sliding_limit(
        "send_timestamps", 3600, SEND_RATE_LIMIT_PER_HOUR, "Hourly send limit reached."
    )
    message = build_message(recipients, subject, body)
    live_send(message)
    warning = append_to_sent(message)
    return jsonify(ok=True, delivered=True, warning=warning), 201


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, MailClientError):
        return jsonify(error=error.message), error.status_code
    if isinstance(error, HTTPException):
        return jsonify(error=error.name, message=error.description), error.code
    if app.config.get("TESTING"):
        raise error
    app.logger.exception("Unhandled application error")
    return jsonify(error="Internal server error"), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=APP_ENV == "development")
