import html
import imaplib
import os
import re
import secrets
import smtplib
import ssl
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request, session
from flask_session import Session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

VALID_APP_MODES = {"showcase", "live"}
APP_MODE = os.getenv("APP_MODE", "showcase").strip().lower()
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

if APP_MODE not in VALID_APP_MODES:
    raise RuntimeError("APP_MODE must be either 'showcase' or 'live'.")

MAIL_DOMAIN = os.getenv("MAIL_DOMAIN", "").strip().lower()
IMAP_HOST = os.getenv("IMAP_HOST", "").strip()
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_SUBMISSION_PORT = int(os.getenv("SMTP_SUBMISSION_PORT", "587"))
SESSION_REDIS_URL = os.getenv("SESSION_REDIS_URL", "").strip()
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

secret_key = os.getenv("FLASK_SECRET_KEY", "")
cookie_secure_setting = os.getenv("SESSION_COOKIE_SECURE", "").strip().lower()
if IS_PRODUCTION:
    if len(secret_key) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must contain at least 32 characters in production.")
    if cookie_secure_setting != "true":
        raise RuntimeError("SESSION_COOKIE_SECURE=true is required in production.")
elif not secret_key:
    secret_key = secrets.token_urlsafe(48)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=secret_key,
    MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_TTL_SECONDS),
    SESSION_PERMANENT=True,
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=cookie_secure_setting == "true",
)

if SESSION_REDIS_URL:
    import redis

    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_REDIS"] = redis.from_url(SESSION_REDIS_URL)
else:
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.getenv(
        "SESSION_FILE_DIR", os.path.join(tempfile.gettempdir(), "email-client-sessions")
    )

Session(app)

UID_PATTERN = re.compile(r"^[1-9][0-9]{0,18}$")
ADDRESS_PATTERN = re.compile(r"^[^\s@<>,;:]+@[^\s@<>,;:]+\.[^\s@<>,;:]+$")


class MailClientError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def runtime_config_issues() -> list[str]:
    issues: list[str] = []
    if APP_MODE == "live":
        if not IMAP_HOST:
            issues.append("IMAP_HOST")
        if not SMTP_HOST:
            issues.append("SMTP_HOST")
        if IS_PRODUCTION and not SESSION_REDIS_URL:
            issues.append("SESSION_REDIS_URL")
    return issues


def get_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


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
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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


def validate_mailbox_name(value: str) -> str:
    mailbox = (value or "INBOX").strip() or "INBOX"
    if len(mailbox) > 160 or any(char in mailbox for char in ("\r", "\n", "\x00")):
        raise MailClientError("Invalid folder name.")
    return mailbox


def quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def validate_login_address(address: object) -> str:
    if not isinstance(address, str):
        raise MailClientError("Use a full mailbox address.")
    normalized = address.strip().lower()
    if len(normalized) > MAX_ADDRESS_CHARS or not ADDRESS_PATTERN.fullmatch(normalized):
        raise MailClientError("Use a valid mailbox address.")
    if MAIL_DOMAIN and not normalized.endswith(f"@{MAIL_DOMAIN}"):
        raise MailClientError(f"Only {MAIL_DOMAIN} mailboxes can sign in.", 403)
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
    if not subject:
        raise MailClientError("Subject is required.")
    if not body:
        raise MailClientError("Body is required.")
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
    if APP_MODE == "showcase":
        ensure_showcase_state()
        return True
    return bool(session.get("mail_user") and session.get("mail_password"))


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            raise MailClientError("Please sign in first.", 401)
        return view(*args, **kwargs)

    return wrapped


def demo_seed() -> list[dict]:
    return [
        {
            "uid": "104",
            "folder": "INBOX",
            "from": "Marta Ruiz <marta@clinic.invalid>",
            "to": "alex@demo.invalid",
            "subject": "Pilot review: patient intake workflow",
            "date": "2026-07-25T09:15:00+02:00",
            "body": (
                "The new intake flow reduced duplicate data entry in our synthetic pilot.\n\n"
                "Next step: review accessibility and audit logging before any real clinical use."
            ),
            "is_read": False,
        },
        {
            "uid": "103",
            "folder": "INBOX",
            "from": "Security Review <review@lab.invalid>",
            "to": "alex@demo.invalid",
            "subject": "Threat model notes for the showcase",
            "date": "2026-07-24T16:40:00+02:00",
            "body": (
                "Confirmed: the public showcase uses synthetic messages only. No mailbox password, "
                "SMTP connection, IMAP connection, or patient data is involved."
            ),
            "is_read": True,
        },
        {
            "uid": "102",
            "folder": "INBOX",
            "from": "Product Team <product@healthtech.invalid>",
            "to": "alex@demo.invalid",
            "subject": "Interview demo checklist",
            "date": "2026-07-23T11:05:00+02:00",
            "body": (
                "Show the mode banner, keyboard navigation, simulated compose flow, secure headers, "
                "and the separate liveness/readiness endpoints."
            ),
            "is_read": False,
        },
        {
            "uid": "101",
            "folder": "INBOX",
            "from": "BFAB Portfolio <hello@bfab.invalid>",
            "to": "alex@demo.invalid",
            "subject": "Welcome to the safe email showcase",
            "date": "2026-07-22T08:30:00+02:00",
            "body": (
                "Everything in this inbox is fictional and stored only in your temporary session. "
                "Use it to explore the interface without sharing credentials."
            ),
            "is_read": True,
        },
        {
            "uid": "201",
            "folder": "Sent",
            "from": "Alex Developer <alex@demo.invalid>",
            "to": "team@clinic.invalid",
            "subject": "Synthetic deployment handoff",
            "date": "2026-07-22T12:00:00+02:00",
            "body": "The public build is isolated from real mail infrastructure and ready for review.",
            "is_read": True,
        },
    ]


def ensure_showcase_state() -> None:
    if "demo_messages" not in session:
        session["demo_messages"] = demo_seed()
    session["mail_user"] = "alex@demo.invalid"
    session["showcase_initialized"] = True
    session.permanent = True


def showcase_folders() -> list[dict]:
    ensure_showcase_state()
    messages = session["demo_messages"]
    definitions = [
        ("INBOX", "Inbox", "inbox"),
        ("Sent", "Sent", "sent"),
        ("Trash", "Trash", "trash"),
    ]
    return [
        {
            "name": name,
            "display_name": label,
            "role": role,
            "count": sum(1 for message in messages if message["folder"] == name),
            "unread": sum(
                1
                for message in messages
                if message["folder"] == name and not message["is_read"]
            ),
        }
        for name, label, role in definitions
    ]


def showcase_messages(folder: str) -> list[dict]:
    mailbox = validate_mailbox_name(folder)
    if mailbox not in {"INBOX", "Sent", "Trash"}:
        raise MailClientError("Folder not found.", 404)
    ensure_showcase_state()
    messages = [
        {key: value for key, value in message.items() if key != "body"}
        for message in session["demo_messages"]
        if message["folder"] == mailbox
    ]
    return sorted(messages, key=lambda item: int(item["uid"]), reverse=True)


def find_showcase_message(folder: str, uid: str) -> tuple[list[dict], dict]:
    mailbox = validate_mailbox_name(folder)
    ensure_showcase_state()
    messages = session["demo_messages"]
    for message in messages:
        if message["uid"] == uid and message["folder"] == mailbox:
            return messages, message
    raise MailClientError("Message not found.", 404)


def showcase_message(folder: str, uid: str) -> dict:
    messages, message = find_showcase_message(folder, uid)
    message["is_read"] = True
    session["demo_messages"] = messages
    return dict(message)


def showcase_mark_read(folder: str, uid: str, is_read: bool) -> None:
    messages, message = find_showcase_message(folder, uid)
    message["is_read"] = is_read
    session["demo_messages"] = messages


def showcase_delete(folder: str, uid: str) -> None:
    messages, message = find_showcase_message(folder, uid)
    if message["folder"] == "Trash":
        messages.remove(message)
    else:
        message["folder"] = "Trash"
    session["demo_messages"] = messages


def showcase_send(recipients: list[str], subject: str, body: str) -> dict:
    ensure_showcase_state()
    enforce_sliding_limit(
        "send_timestamps", 3600, SEND_RATE_LIMIT_PER_HOUR, "Hourly send limit reached."
    )
    messages = session["demo_messages"]
    next_uid = str(max(int(message["uid"]) for message in messages) + 1)
    message = {
        "uid": next_uid,
        "folder": "Sent",
        "from": "Alex Developer <alex@demo.invalid>",
        "to": ", ".join(recipients),
        "subject": subject,
        "date": datetime.now(timezone.utc).isoformat(),
        "body": body,
        "is_read": True,
    }
    messages.append(message)
    session["demo_messages"] = messages
    return message


def connect_imap(user: str, password: str):
    if not IMAP_HOST:
        raise MailClientError("Missing mail configuration: IMAP_HOST.", 503)
    try:
        client = imaplib.IMAP4_SSL(
            IMAP_HOST, IMAP_PORT, ssl_context=get_tls_context(), timeout=25
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


def validate_live_credentials(user: str, password: str) -> None:
    client = connect_imap(user, password)
    safe_logout(client)


def get_credentials() -> tuple[str, str]:
    user = session.get("mail_user")
    password = session.get("mail_password")
    if not user or not password:
        raise MailClientError("Please sign in first.", 401)
    return user, password


def open_imap(mailbox: str | None = None, readonly: bool = False):
    user, password = get_credentials()
    client = connect_imap(user, password)
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
    message = EmailMessage()
    message["From"] = user
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=MAIL_DOMAIN or user.split("@")[-1])
    message.set_content(body)
    return message


def live_send(message: EmailMessage) -> None:
    if not SMTP_HOST:
        raise MailClientError("Missing mail configuration: SMTP_HOST.", 503)
    user, password = get_credentials()
    context = get_tls_context()
    try:
        if SMTP_SUBMISSION_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST, SMTP_SUBMISSION_PORT, timeout=30, context=context
            ) as smtp:
                smtp.login(user, password)
                smtp.send_message(message)
            return
        with smtplib.SMTP(SMTP_HOST, SMTP_SUBMISSION_PORT, timeout=30) as smtp:
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
    client = open_imap()
    try:
        sent_folder = os.getenv("SENT_FOLDER", "Sent")
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
    if APP_MODE == "showcase":
        ensure_showcase_state()
    user = session.get("mail_user")
    return {
        "mode": APP_MODE,
        "authenticated": is_authenticated(),
        "user": user,
        "domain": "demo.invalid" if APP_MODE == "showcase" else MAIL_DOMAIN,
        "csrf_token": ensure_csrf_token(),
        "simulated": APP_MODE == "showcase",
        "network_access": APP_MODE == "live",
    }


@app.get("/")
def home():
    return render_template("email_system.html", csp_nonce=g.csp_nonce)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "alive", "mode": APP_MODE})


@app.get("/readyz")
def readyz():
    issues = runtime_config_issues()
    return (
        jsonify({"status": "ready" if not issues else "not_ready", "mode": APP_MODE, "issues": issues}),
        200 if not issues else 503,
    )


@app.get("/health")
def health_compatibility():
    return readyz()


@app.get("/api/state")
def api_state():
    return jsonify(public_state())


@app.post("/api/login")
def api_login():
    if APP_MODE == "showcase":
        raise MailClientError("Password login is disabled in showcase mode.", 409)
    enforce_sliding_limit(
        "login_timestamps",
        900,
        LOGIN_RATE_LIMIT_PER_15_MINUTES,
        "Too many login attempts. Try again later.",
    )
    data = json_object()
    mailbox = validate_login_address(data.get("email", ""))
    password = data.get("password", "")
    if not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_CHARS:
        raise MailClientError("Password is required and must be within the allowed length.")
    validate_live_credentials(mailbox, password)
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


@app.post("/api/demo/reset")
def api_demo_reset():
    if APP_MODE != "showcase":
        raise MailClientError("Demo reset is only available in showcase mode.", 404)
    csrf_token = session.get("csrf_token")
    session.clear()
    session["csrf_token"] = csrf_token or secrets.token_urlsafe(32)
    ensure_showcase_state()
    rotate_session()
    return jsonify({"ok": True, "state": public_state()})


@app.get("/api/folders")
@require_login
def api_folders():
    folders = showcase_folders() if APP_MODE == "showcase" else live_folders()
    return jsonify({"folders": folders})


@app.get("/api/messages")
@require_login
def api_messages():
    folder = request.args.get("folder", "INBOX")
    messages = showcase_messages(folder) if APP_MODE == "showcase" else live_messages(folder)
    return jsonify({"folder": folder, "messages": messages})


@app.get("/api/messages/<uid>")
@require_login
def api_message(uid):
    valid_uid = validate_uid(uid)
    folder = request.args.get("folder", "INBOX")
    message = (
        showcase_message(folder, valid_uid)
        if APP_MODE == "showcase"
        else live_message(folder, valid_uid)
    )
    return jsonify({"message": message})


@app.post("/api/messages/<uid>/read")
@require_login
def api_mark_read(uid):
    valid_uid = validate_uid(uid)
    data = json_object()
    is_read = data.get("read", True)
    if not isinstance(is_read, bool):
        raise MailClientError("The read field must be a boolean.")
    folder = request.args.get("folder", "INBOX")
    if APP_MODE == "showcase":
        showcase_mark_read(folder, valid_uid, is_read)
    else:
        live_mark_read(folder, valid_uid, is_read)
    return jsonify({"ok": True, "read": is_read})


@app.delete("/api/messages/<uid>")
@require_login
def api_delete_message(uid):
    valid_uid = validate_uid(uid)
    folder = request.args.get("folder", "INBOX")
    if APP_MODE == "showcase":
        showcase_delete(folder, valid_uid)
    else:
        live_delete(folder, valid_uid)
    return jsonify({"ok": True})


@app.post("/api/send")
@require_login
def api_send():
    recipients, subject, body = validate_message_fields(json_object())
    if APP_MODE == "showcase":
        message = showcase_send(recipients, subject, body)
        return (
            jsonify(
                {
                    "ok": True,
                    "message": message,
                    "simulated": True,
                    "warning": "Simulation only: no email was delivered.",
                }
            ),
            201,
        )
    enforce_sliding_limit(
        "send_timestamps", 3600, SEND_RATE_LIMIT_PER_HOUR, "Hourly send limit reached."
    )
    message = build_message(recipients, subject, body)
    live_send(message)
    warning = append_to_sent(message)
    return jsonify({"ok": True, "simulated": False, "warning": warning}), 201


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, MailClientError):
        return jsonify({"error": error.message}), error.status_code
    if isinstance(error, HTTPException):
        return jsonify({"error": error.name, "message": error.description}), error.code
    app.logger.exception("Unhandled application error")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=not IS_PRODUCTION)
