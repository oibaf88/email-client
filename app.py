import os
import uuid
from flask import Flask, jsonify, render_template, request, session
from supabase import create_client, Client
from werkzeug.exceptions import HTTPException


SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

if not SUPABASE_URL:
    raise RuntimeError("Missing SUPABASE_URL environment variable.")

if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY environment variable.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY


def get_session_key() -> str:
    if "email_demo_session_key" not in session:
        session["email_demo_session_key"] = str(uuid.uuid4())
    return session["email_demo_session_key"]


def get_or_create_demo_session() -> dict:
    session_key = get_session_key()

    existing = (
        supabase.table("email_demo_sessions")
        .select("*")
        .eq("session_key", session_key)
        .limit(1)
        .execute()
    )

    if existing.data:
        return existing.data[0]

    created = (
        supabase.table("email_demo_sessions")
        .insert(
            {
                "session_key": session_key,
                "user1_name": "User1",
                "user2_name": "User2",
            }
        )
        .execute()
    )

    return created.data[0]


def get_user_name(demo_session: dict, slot: int) -> str:
    if slot == 1:
        return demo_session["user1_name"]
    if slot == 2:
        return demo_session["user2_name"]
    return "Unknown"


def serialize_email(email: dict, demo_session: dict) -> dict:
    sender_slot = int(email["sender_slot"])
    receiver_slot = int(email["receiver_slot"])

    return {
        "id": email["id"],
        "sender_slot": sender_slot,
        "receiver_slot": receiver_slot,
        "sender_name": get_user_name(demo_session, sender_slot),
        "receiver_name": get_user_name(demo_session, receiver_slot),
        "subject": email["subject"],
        "body": email["body"],
        "is_read": email["is_read"],
        "created_at": email["created_at"],
    }


def build_state() -> dict:
    demo_session = get_or_create_demo_session()

    emails_response = (
        supabase.table("email_demo_emails")
        .select("*")
        .eq("session_id", demo_session["id"])
        .order("created_at", desc=False)
        .execute()
    )

    emails = emails_response.data or []

    user1_inbox = [
        serialize_email(email, demo_session)
        for email in emails
        if int(email["receiver_slot"]) == 1
    ]

    user2_inbox = [
        serialize_email(email, demo_session)
        for email in emails
        if int(email["receiver_slot"]) == 2
    ]

    return {
        "session_id": demo_session["id"],
        "user1_name": demo_session["user1_name"],
        "user2_name": demo_session["user2_name"],
        "inboxes": {
            "1": user1_inbox,
            "2": user2_inbox,
        },
    }


@app.get("/")
def home():
    return render_template("email_system.html")


@app.get("/health")
@app.get("/healthz")
def health():
    return jsonify({"status": "ok"}), 200


@app.get("/api/state")
def api_state():
    return jsonify(build_state())


@app.post("/api/users")
def api_users():
    data = request.get_json(silent=True) or {}

    user1_name = (data.get("user1_name") or "User1").strip()
    user2_name = (data.get("user2_name") or "User2").strip()

    demo_session = get_or_create_demo_session()

    updated = (
        supabase.table("email_demo_sessions")
        .update(
            {
                "user1_name": user1_name,
                "user2_name": user2_name,
            }
        )
        .eq("id", demo_session["id"])
        .execute()
    )

    if not updated.data:
        return jsonify({"error": "Could not update users."}), 500

    return jsonify(build_state())


@app.post("/api/send")
def api_send():
    data = request.get_json(silent=True) or {}

    try:
        sender_slot = int(data.get("sender_slot"))
    except (TypeError, ValueError):
        return jsonify({"error": "sender_slot must be 1 or 2."}), 400

    if sender_slot not in (1, 2):
        return jsonify({"error": "sender_slot must be 1 or 2."}), 400

    receiver_slot = 2 if sender_slot == 1 else 1

    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()

    if not subject:
        return jsonify({"error": "Subject is required."}), 400

    if not body:
        return jsonify({"error": "Body is required."}), 400

    demo_session = get_or_create_demo_session()

    inserted = (
        supabase.table("email_demo_emails")
        .insert(
            {
                "session_id": demo_session["id"],
                "sender_slot": sender_slot,
                "receiver_slot": receiver_slot,
                "subject": subject,
                "body": body,
                "is_read": False,
            }
        )
        .execute()
    )

    if not inserted.data:
        return jsonify({"error": "Could not send email."}), 500

    return jsonify(build_state()), 201


@app.post("/api/read/<email_id>")
def api_read(email_id):
    demo_session = get_or_create_demo_session()

    found = (
        supabase.table("email_demo_emails")
        .select("*")
        .eq("id", email_id)
        .eq("session_id", demo_session["id"])
        .limit(1)
        .execute()
    )

    if not found.data:
        return jsonify({"error": "Email not found."}), 404

    updated = (
        supabase.table("email_demo_emails")
        .update({"is_read": True})
        .eq("id", email_id)
        .eq("session_id", demo_session["id"])
        .execute()
    )

    if not updated.data:
        return jsonify({"error": "Could not mark email as read."}), 500

    return jsonify(
        {
            "email": serialize_email(updated.data[0], demo_session),
            "state": build_state(),
        }
    )


@app.delete("/api/email/<email_id>")
def api_delete_email(email_id):
    demo_session = get_or_create_demo_session()

    deleted = (
        supabase.table("email_demo_emails")
        .delete()
        .eq("id", email_id)
        .eq("session_id", demo_session["id"])
        .execute()
    )

    if not deleted.data:
        return jsonify({"error": "Email not found or already deleted."}), 404

    return jsonify(build_state())


@app.post("/api/reset")
def api_reset():
    demo_session = get_or_create_demo_session()

    supabase.table("email_demo_emails").delete().eq(
        "session_id", demo_session["id"]
    ).execute()

    supabase.table("email_demo_sessions").update(
        {
            "user1_name": "User1",
            "user2_name": "User2",
        }
    ).eq("id", demo_session["id"]).execute()

    return jsonify(build_state())


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, HTTPException):
        return jsonify(
            {
                "error": error.name,
                "message": error.description,
            }
        ), error.code

    app.logger.exception("Unhandled application error")
    return jsonify({"error": "Internal server error"}), 500


if __name__ == "__main__":
    app.run(debug=True)
