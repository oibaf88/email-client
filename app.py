import html
import imaplib
import os
import re
import smtplib
import ssl
import time
from datetime import timedelta
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
from functools import wraps
from secrets import token_urlsafe

from flask import Flask, jsonify, render_template, request, session
from flask_session import Session
from werkzeug.exceptions import HTTPException


MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "").strip().lower()
IMAP_HOST = os.environ.get("IMAP_HOST", "").strip()
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_SUBMISSION_PORT = int(os.environ.get("SMTP_SUBMISSION_PORT", "587"))
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
SESSION_REDIS_URL = os.environ.get("SESSION_REDIS_URL", "").strip()
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", "28800"))
SEND_RATE_LIMIT_PER_HOUR = int(os.environ.get("SEND_RATE_LIMIT_PER_HOUR", "60"))
MAX_MESSAGE_LIST_SIZE = int(os.environ.get("MAX_MESSAGE_LIST_SIZE", "50"))


app = Flask(__name__)
app.config.update(
    SECRET_KEY=FLASK_SECRET_KEY,
    PERMANENT_SESSION_LIFETIME=timedelta(seconds=SESSION_TTL_SECONDS),
    SESSION_PERMANENT=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower()
    == "true",
)

if SESSION_REDIS_URL:
    import redis

    app.config["SESSION_TYPE"] = "redis"
    app.config["SESSION_REDIS"] = redis.from_url(SESSION_REDIS_URL)
else:
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = os.environ.get(
        "SESSION_FILE_DIR", os.path.join(os.getcwd(), "flask_session")
    )

Session(app)


class MailClientError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def get_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
    except ssl.SSLError:
        app.logger.warning("Could not restrict TLS 1.2 ciphers to ECDHE suites.")
    return context


def require_mail_config() -> None:
    missing = [
        key
        for key, value in {
            "IMAP_HOST": IMAP_HOST,
            "SMTP_HOST": SMTP_HOST,
        }.items()
        if not value
    ]
    if missing:
        raise MailClientError(
            f"Missing mail configuration: {', '.join(missing)}.", 503
        )


def require_login(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("mail_user") or not session.get("mail_password"):
            raise MailClientError("Please sign in first.", 401)
        return view(*args, **kwargs)

    return wrapped


def require_csrf() -> None:
    expected = session.get("csrf_token")
    provided = request.headers.get("X-CSRF-Token")
    if not expected or provided != expected:
        raise MailClientError("Invalid request token.", 403)


@app.before_request
def protect_write_requests():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.endpoint not in {"api_login"}:
            require_csrf()


def rotate_csrf_token() -> str:
    session["csrf_token"] = token_urlsafe(32)
    return session["csrf_token"]


def get_credentials() -> tuple[str, str]:
    user = session.get("mail_user")
    password = session.get("mail_password")
    if not user or not password:
        raise MailClientError("Please sign in first.", 401)
    return user, password


def validate_login_address(address: str) -> str:
    normalized = address.strip().lower()
    if not normalized or "@" not in normalized:
        raise MailClientError("Use a full mailbox address.")
    if MAIL_DOMAIN and not normalized.endswith(f"@{MAIL_DOMAIN}"):
        raise MailClientError(f"Only {MAIL_DOMAIN} mailboxes can sign in.", 403)
    return normalized


def open_imap(mailbox: str | None = None, readonly: bool = False):
    require_mail_config()
    user, password = get_credentials()
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
        client.logout()
        raise MailClientError("IMAP authentication failed.", 401)

    if mailbox:
        safe_mailbox = validate_mailbox_name(mailbox)
        status, data = client.select(quote_mailbox(safe_mailbox), readonly=readonly)
        if status != "OK":
            client.logout()
            detail = decode_bytes(data[0]) if data else safe_mailbox
            raise MailClientError(f"Could not open folder: {detail}", 404)

    return client


def close_imap(client, selected: bool = False) -> None:
    try:
        if selected:
            client.close()
    except imaplib.IMAP4.error:
        pass
    finally:
        try:
            client.logout()
        except imaplib.IMAP4.error:
            pass


def validate_mailbox_name(value: str) -> str:
    mailbox = (value or "INBOX").strip() or "INBOX"
    if any(char in mailbox for char in ("\r", "\n", "\x00")):
        raise MailClientError("Invalid folder name.")
    if len(mailbox) > 160:
        raise MailClientError("Folder name is too long.")
    return mailbox


def quote_mailbox(mailbox: str) -> str:
    escaped = mailbox.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


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


def parse_list_response(line: bytes) -> dict | None:
    text = decode_bytes(line)
    match = re.match(r"\((?P<flags>.*?)\)\s+\".*?\"\s+(?P<name>.+)$", text)
    if not match:
        return None

    raw_flags = match.group("flags")
    raw_name = match.group("name").strip()
    if raw_name.startswith('"') and raw_name.endswith('"'):
        raw_name = raw_name[1:-1].replace('\\"', '"').replace("\\\\", "\\")

    flags = [flag.strip("\\").lower() for flag in raw_flags.split() if flag]
    role = classify_folder(raw_name, flags)
    return {
        "name": raw_name,
        "display_name": raw_name,
        "role": role,
        "flags": flags,
    }


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


def list_folders() -> list[dict]:
    client = open_imap()
    try:
        status, data = client.list()
        if status != "OK":
            raise MailClientError("Could not list folders.", 503)

        folders = []
        for line in data or []:
            parsed = parse_list_response(line)
            if parsed:
                folders.append(parsed)

        if not folders:
            folders = [{"name": "INBOX", "display_name": "INBOX", "role": "inbox"}]

        preferred_order = {"inbox": 0, "sent": 1, "drafts": 2, "trash": 3, "junk": 4}
        return sorted(
            folders, key=lambda item: (preferred_order.get(item["role"], 99), item["name"].lower())
        )
    finally:
        close_imap(client)


def find_sent_folder(client) -> str:
    status, data = client.list()
    if status == "OK":
        for line in data or []:
            parsed = parse_list_response(line)
            if parsed and parsed["role"] == "sent":
                return parsed["name"]
    return os.environ.get("SENT_FOLDER", "Sent")


def parse_flags(raw: bytes) -> list[str]:
    match = re.search(rb"FLAGS \((?P<flags>[^)]*)\)", raw or b"", re.IGNORECASE)
    if not match:
        return []
    return [decode_bytes(flag) for flag in match.group("flags").split()]


def extract_payload(fetch_data) -> tuple[bytes, list[str]]:
    payload = b""
    flags: list[str] = []
    for item in fetch_data or []:
        if isinstance(item, tuple):
            response_part = item[0] or b""
            payload = item[1] or b""
            flags = parse_flags(response_part)
    return payload, flags


def normalize_address_header(value) -> str:
    addresses = getaddresses([str(value or "")])
    if not addresses:
        return ""
    return ", ".join(address for _, address in addresses if address)


def message_summary_from_headers(uid: str, raw_headers: bytes, flags: list[str]) -> dict:
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


def list_messages(folder: str) -> list[dict]:
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox=mailbox, readonly=True)
    selected = True
    try:
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise MailClientError("Could not search messages.", 503)

        uids = (data[0] or b"").split()
        selected_uids = list(reversed(uids))[:MAX_MESSAGE_LIST_SIZE]
        messages = []

        for raw_uid in selected_uids:
            uid = decode_bytes(raw_uid)
            status, fetch_data = client.uid(
                "FETCH",
                uid,
                "(BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE MESSAGE-ID)] FLAGS)",
            )
            if status != "OK":
                continue
            raw_headers, flags = extract_payload(fetch_data)
            messages.append(message_summary_from_headers(uid, raw_headers, flags))

        return messages
    finally:
        close_imap(client, selected=selected)


def extract_body(message) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def read_part(part) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    if message.is_multipart():
        for part in message.walk():
            disposition = part.get_content_disposition()
            if disposition == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(read_part(part))
            elif content_type == "text/html":
                html_parts.append(read_part(part))
    else:
        content_type = message.get_content_type()
        if content_type == "text/html":
            html_parts.append(read_part(message))
        else:
            plain_parts.append(read_part(message))

    if plain_parts:
        return "\n\n".join(part.strip() for part in plain_parts if part.strip())

    html_body = "\n\n".join(part.strip() for part in html_parts if part.strip())
    without_tags = re.sub(r"<[^>]+>", " ", html_body)
    return html.unescape(re.sub(r"\s+", " ", without_tags)).strip()


def get_message(folder: str, uid: str) -> dict:
    if not uid.isdigit():
        raise MailClientError("Invalid message id.")

    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox=mailbox, readonly=True)
    selected = True
    try:
        status, fetch_data = client.uid("FETCH", uid, "(BODY.PEEK[] FLAGS)")
        if status != "OK":
            raise MailClientError("Could not fetch message.", 404)

        raw_message, flags = extract_payload(fetch_data)
        if not raw_message:
            raise MailClientError("Message not found.", 404)

        message = BytesParser(policy=policy.default).parsebytes(raw_message)
        summary = message_summary_from_headers(uid, raw_message, flags)
        summary["body"] = extract_body(message)
        summary["attachments"] = [
            {
                "filename": decode_header_value(part.get_filename()),
                "content_type": part.get_content_type(),
            }
            for part in message.walk()
            if part.get_content_disposition() == "attachment"
        ]
        return summary
    finally:
        close_imap(client, selected=selected)


def mark_message_read(folder: str, uid: str) -> None:
    if not uid.isdigit():
        raise MailClientError("Invalid message id.")
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox=mailbox)
    selected = True
    try:
        status, _ = client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        if status != "OK":
            raise MailClientError("Could not mark message as read.", 503)
    finally:
        close_imap(client, selected=selected)


def delete_message(folder: str, uid: str) -> None:
    if not uid.isdigit():
        raise MailClientError("Invalid message id.")
    mailbox = validate_mailbox_name(folder)
    client = open_imap(mailbox=mailbox)
    selected = True
    try:
        status, _ = client.uid("STORE", uid, "+FLAGS", "(\\Deleted)")
        if status != "OK":
            raise MailClientError("Could not delete message.", 503)
        client.expunge()
    finally:
        close_imap(client, selected=selected)


def parse_recipients(value: str) -> list[str]:
    recipients = [address for _, address in getaddresses([value or ""]) if address]
    if not recipients:
        raise MailClientError("At least one recipient is required.")
    invalid = [address for address in recipients if "@" not in address]
    if invalid:
        raise MailClientError("One or more recipients are invalid.")
    return recipients


def enforce_send_rate_limit() -> None:
    now = int(time.time())
    window_start = now - 3600
    timestamps = [
        timestamp
        for timestamp in session.get("send_timestamps", [])
        if timestamp >= window_start
    ]
    if len(timestamps) >= SEND_RATE_LIMIT_PER_HOUR:
        raise MailClientError("Hourly send limit reached.", 429)
    timestamps.append(now)
    session["send_timestamps"] = timestamps


def build_message(to_addresses: list[str], subject: str, body: str) -> EmailMessage:
    user, _ = get_credentials()
    message = EmailMessage()
    message["From"] = user
    message["To"] = ", ".join(to_addresses)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=MAIL_DOMAIN or user.split("@")[-1])
    message.set_content(body)
    return message


def send_message(message: EmailMessage) -> None:
    require_mail_config()
    user, password = get_credentials()
    context = get_tls_context()

    try:
        if SMTP_SUBMISSION_PORT == 465:
            with smtplib.SMTP_SSL(
                SMTP_HOST, SMTP_SUBMISSION_PORT, timeout=30, context=context
            ) as smtp:
                smtp.ehlo()
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
        sent_folder = find_sent_folder(client)
        status, _ = client.append(
            quote_mailbox(sent_folder),
            "\\Seen",
            imaplib.Time2Internaldate(time.time()),
            message.as_bytes(),
        )
        if status != "OK":
            return "Message sent, but could not save a copy in Sent."
        return None
    finally:
        close_imap(client)


def public_state() -> dict:
    csrf_token = session.get("csrf_token") or rotate_csrf_token()
    user = session.get("mail_user")
    return {
        "authenticated": bool(user),
        "user": user,
        "domain": MAIL_DOMAIN,
        "imap_host": IMAP_HOST,
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_SUBMISSION_PORT,
        "csrf_token": csrf_token,
    }


@app.get("/")
def home():
    return render_template("email_system.html")


@app.get("/health")
@app.get("/healthz")
def health():
    missing_required = [
        key
        for key, value in {
            "IMAP_HOST": IMAP_HOST,
            "SMTP_HOST": SMTP_HOST,
        }.items()
        if not value
    ]
    missing_recommended = []
    if not SESSION_REDIS_URL:
        missing_recommended.append("SESSION_REDIS_URL")
    return (
        jsonify(
            {
                "status": "ok",
                "missing_required": missing_required,
                "missing_recommended": missing_recommended,
            }
        ),
        200,
    )


@app.get("/api/state")
def api_state():
    return jsonify(public_state())


@app.post("/api/login")
def api_login():
    data = request.get_json(silent=True) or {}
    mailbox = validate_login_address(data.get("email", ""))
    password = data.get("password", "")
    if not password:
        raise MailClientError("Password is required.")

    session.clear()
    session.permanent = True
    session["mail_user"] = mailbox
    session["mail_password"] = password
    rotate_csrf_token()

    try:
        client = open_imap()
    except Exception:
        session.clear()
        rotate_csrf_token()
        raise
    close_imap(client)
    return jsonify(public_state())


@app.post("/api/logout")
@require_login
def api_logout():
    session.clear()
    rotate_csrf_token()
    return jsonify(public_state())


@app.get("/api/folders")
@require_login
def api_folders():
    return jsonify({"folders": list_folders()})


@app.get("/api/messages")
@require_login
def api_messages():
    folder = request.args.get("folder", "INBOX")
    return jsonify({"folder": folder, "messages": list_messages(folder)})


@app.get("/api/messages/<uid>")
@require_login
def api_message(uid):
    folder = request.args.get("folder", "INBOX")
    return jsonify({"message": get_message(folder, uid)})


@app.post("/api/messages/<uid>/read")
@require_login
def api_mark_read(uid):
    folder = request.args.get("folder", "INBOX")
    mark_message_read(folder, uid)
    return jsonify({"ok": True})


@app.delete("/api/messages/<uid>")
@require_login
def api_delete_message(uid):
    folder = request.args.get("folder", "INBOX")
    delete_message(folder, uid)
    return jsonify({"ok": True})


@app.post("/api/send")
@require_login
def api_send():
    data = request.get_json(silent=True) or {}
    recipients = parse_recipients(data.get("to", ""))
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()

    if not subject:
        raise MailClientError("Subject is required.")
    if not body:
        raise MailClientError("Body is required.")

    enforce_send_rate_limit()
    message = build_message(recipients, subject, body)
    send_message(message)
    warning = append_to_sent(message)
    return jsonify({"ok": True, "warning": warning}), 201


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, MailClientError):
        return jsonify({"error": error.message}), error.status_code

    if isinstance(error, HTTPException):
        return jsonify({"error": error.name, "message": error.description}), error.code

    app.logger.exception("Unhandled application error")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=True)
