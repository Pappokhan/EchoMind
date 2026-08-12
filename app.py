import base64
import logging
import mimetypes
import os
import re
import sqlite3
import uuid

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, flash,
    send_from_directory, abort,
)
from flask_login import (
    login_user, logout_user, login_required, current_user,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import generate_password_hash, check_password_hash

import config
import database
import digest_service
from auth import login_manager, User
from llm_service import analyze_entry, chat_reply, LLMServiceError
from mlops import metrics as mlops_metrics
from prompts import MODES, DEFAULT_MODE, public_modes
from streaks import compute_streak

logging.basicConfig(level=logging.INFO if not config.DEBUG else logging.DEBUG)
logger = logging.getLogger("echomind")

app = Flask(__name__)
app.config["SECRET_KEY"] = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
app.config["REMEMBER_COOKIE_HTTPONLY"] = True
app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"
app.config["REMEMBER_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE

database.init_db()

login_manager.init_app(app)

csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=config.RATELIMIT_STORAGE_URI,
    default_limits=["200 per hour"],
)

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.\-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_DUMMY_PASSWORD_HASH = generate_password_hash("not-a-real-password-just-for-timing")


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    flash("Please sign in to continue.", "info")
    return redirect(url_for("login", next=request.path))


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if config.SESSION_COOKIE_SECURE:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.errorhandler(CSRFError)
def csrf_error(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Your session expired or is invalid — please refresh and try again."}), 400
    flash("Your session expired — please try again.", "error")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(429)
def rate_limited(e):
    message = "Too many requests — please slow down and try again shortly."
    if request.path.startswith("/api/"):
        return jsonify({"error": message}), 429
    flash(message, "error")
    return redirect(request.referrer or url_for("index")), 429


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    return e, 404


@app.errorhandler(500)
def server_error(e):
    logger.exception("Unhandled server error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    return e, 500


@app.route("/")
def index():
    if not current_user.is_authenticated:
        return render_template(
            "cover.html",
            demo_mode=config.DEMO_MODE,
            model_name=config.MODEL_NAME,
            modes=public_modes(),
        )
    return render_template(
        "index.html",
        demo_mode=config.DEMO_MODE,
        model_name=config.MODEL_NAME,
        modes=public_modes(),
        default_mode=DEFAULT_MODE,
    )


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME)


@app.route("/chat")
@login_required
def chat_page():
    return render_template("chat.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME)


@app.route("/api/modes")
def api_modes():
    return jsonify({"modes": public_modes(), "default_mode": DEFAULT_MODE})


@app.route("/api/analyze", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def api_analyze():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    raw_text = payload.get("entry_text")
    raw_mode = payload.get("mode")
    entry_text = str(raw_text).strip() if raw_text is not None else ""
    mode_id = str(raw_mode).strip() if raw_mode else DEFAULT_MODE

    if not entry_text:
        return jsonify({"error": "entry_text is required"}), 400
    if len(entry_text) > 4000:
        return jsonify({"error": "entry_text too long (max 4000 characters)"}), 400
    if mode_id not in MODES:
        mode_id = DEFAULT_MODE

    try:
        analysis = analyze_entry(entry_text, mode_id, user_id=int(current_user.id))
    except LLMServiceError as e:
        return jsonify({"error": "The AI model is currently unavailable. Please try again.",
                         "detail": str(e)}), 502

    database.save_entry(
        entry_text=entry_text,
        analysis=analysis,
        latency_ms=analysis.get("_latency_ms"),
        demo_mode=analysis.get("_demo_mode", False),
        mode=mode_id,
        user_id=int(current_user.id),
    )

    return jsonify({
        "mode": mode_id,
        "mood": analysis.get("mood"),
        "sentiment_score": analysis.get("sentiment_score"),
        "themes": analysis.get("themes", []),
        "reflection": analysis.get("reflection"),
        "suggested_action": analysis.get("suggested_action"),
        "latency_ms": round(analysis.get("_latency_ms", 0), 1),
        "demo_mode": analysis.get("_demo_mode", False),
        "model_name": config.MODEL_NAME if not analysis.get("_demo_mode") else "rule-based-fallback",
        "tools_used": analysis.get("_tool_calls", []),
        "rag_entries_used": analysis.get("_rag_entries_used", 0),
        "memory_facts_used": analysis.get("_memory_facts_used", 0),
    })


@app.route("/api/entries")
@login_required
def api_entries():
    limit = request.args.get("limit", default=50, type=int)
    if limit is None:
        limit = 50
    limit = max(1, min(limit, 200))
    mode_id = request.args.get("mode", default=None, type=str)
    if mode_id and mode_id not in MODES:
        mode_id = None
    return jsonify(database.get_recent_entries(int(current_user.id), limit=limit, mode=mode_id))


@app.route("/api/metrics")
@login_required
def api_metrics():
    return jsonify(mlops_metrics.get_summary(int(current_user.id)))


@app.route("/api/streak")
@login_required
def api_streak():
    return jsonify(compute_streak(database.get_active_dates(int(current_user.id))))

class ChatAttachmentError(ValueError):
    """Raised for a bad/oversized/wrong-type attachment; message is safe
    to return to the client as-is."""


def _save_chat_attachment(user_id, file_storage):
    if not file_storage or not file_storage.filename:
        return None, None

    mime = (file_storage.mimetype or "").split(";")[0].strip().lower()
    if mime not in config.ALLOWED_ATTACHMENT_MIME_TYPES:
        raise ChatAttachmentError(
            "Attachments must be an image (PNG, JPEG, WEBP, GIF) or a PDF."
        )

    data = file_storage.read()
    if not data:
        raise ChatAttachmentError("The attached file is empty.")
    if len(data) > config.MAX_ATTACHMENT_SIZE_BYTES:
        max_mb = config.MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)
        raise ChatAttachmentError(f"Attachments are limited to {max_mb}MB.")

    kind = "pdf" if mime in config.ALLOWED_PDF_MIME_TYPES else "image"
    ext = mimetypes.guess_extension(mime) or ""
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", file_storage.filename)[:120] or "attachment"

    user_dir = os.path.join(config.CHAT_UPLOADS_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(user_dir, stored_name), "wb") as f:
        f.write(data)

    meta = {
        "name": safe_name,
        "mime": mime,
        "kind": kind,
        "path": f"{user_id}/{stored_name}",
    }
    return meta, data


@app.route("/api/chat/attachment/<int:message_id>")
@login_required
def api_chat_attachment(message_id):
    attachment = database.get_chat_attachment(int(current_user.id), message_id)
    if not attachment:
        abort(404)
    directory = os.path.join(config.CHAT_UPLOADS_DIR)
    return send_from_directory(
        directory,
        attachment["path"],
        mimetype=attachment["mime"],
        download_name=attachment["name"],
    )


@app.route("/api/chat/history")
@login_required
def api_chat_history():
    limit = request.args.get("limit", default=50, type=int)
    if limit is None:
        limit = 50
    limit = max(1, min(limit, 200))
    user_id = int(current_user.id)
    messages = database.get_chat_messages(user_id, limit=limit)
    for m in messages:
        if m.get("attachment"):
            m["attachment"]["url"] = url_for("api_chat_attachment", message_id=m["id"])
    return jsonify({
        "messages": messages,
        "day_count": database.get_chat_day_count(user_id),
        "total_count": database.get_chat_message_count(user_id),
        "first_message_at": database.get_chat_first_message_at(user_id),
    })


@app.route("/api/chat", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def api_chat():
    is_multipart = request.content_type and "multipart/form-data" in request.content_type

    if is_multipart:
        raw_text = request.form.get("message", "")
        file_storage = request.files.get("attachment")
    else:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400
        raw_text = payload.get("message")
        file_storage = None

    message_text = str(raw_text).strip() if raw_text is not None else ""

    if not message_text and not (file_storage and file_storage.filename):
        return jsonify({"error": "message or attachment is required"}), 400
    if len(message_text) > 2000:
        return jsonify({"error": "message too long (max 2000 characters)"}), 400

    user_id = int(current_user.id)

    try:
        attachment_meta, attachment_bytes = _save_chat_attachment(user_id, file_storage)
    except ChatAttachmentError as e:
        return jsonify({"error": str(e)}), 400

    user_message_id = database.save_chat_message(user_id, "user", message_text, attachment=attachment_meta)

    llm_attachment = None
    if attachment_meta and attachment_bytes:
        llm_attachment = {
            "mime": attachment_meta["mime"],
            "kind": attachment_meta["kind"],
            "name": attachment_meta["name"],
            "b64": base64.b64encode(attachment_bytes).decode("ascii"),
        }

    try:
        result = chat_reply(message_text, user_id=user_id, attachment=llm_attachment)
    except LLMServiceError as e:
        return jsonify({"error": "The AI model is currently unavailable. Please try again.",
                         "detail": str(e)}), 502

    database.save_chat_message(user_id, "assistant", result["reply"])

    return jsonify({
        "reply": result["reply"],
        "latency_ms": round(result.get("_latency_ms", 0), 1),
        "demo_mode": result.get("_demo_mode", False),
        "model_name": config.MODEL_NAME if not result.get("_demo_mode") else "rule-based-fallback",
        "tools_used": result.get("_tool_calls", []),
        "rag_entries_used": result.get("_rag_entries_used", 0),
        "memory_facts_used": result.get("_memory_facts_used", 0),
        "attachment": attachment_meta and {
            "name": attachment_meta["name"], "mime": attachment_meta["mime"], "kind": attachment_meta["kind"],
            "url": url_for("api_chat_attachment", message_id=user_message_id),
        },
    })


@app.route("/api/chat", methods=["DELETE"])
@login_required
@limiter.limit("10 per hour")
def api_chat_clear():
    database.clear_chat_messages(int(current_user.id))
    return jsonify({"cleared": True})

@app.route("/api/memory")
@login_required
def api_memory():
    return jsonify({"facts": database.get_memory_facts(int(current_user.id), limit=100)})


@app.route("/api/memory/<path:key>", methods=["DELETE"])
@login_required
@limiter.limit("30 per minute")
def api_memory_delete(key):
    deleted = database.delete_memory_fact(int(current_user.id), key)
    if not deleted:
        return jsonify({"error": "No saved fact with that key"}), 404
    return jsonify({"deleted": True, "key": key})


@app.route("/digest")
@login_required
def digest_page():
    return render_template(
        "digest.html",
        demo_mode=config.DEMO_MODE,
        model_name=config.MODEL_NAME,
        modes=public_modes(),
    )


@app.route("/api/digest", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def api_digest():
    payload = request.get_json(silent=True) or {}
    raw_days = payload.get("days", 7)
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        days = 7
    if days not in (7, 30):
        days = 7

    raw_mode = payload.get("mode")
    mode_id = str(raw_mode).strip() if raw_mode else None
    if mode_id and mode_id not in MODES:
        mode_id = None

    result = digest_service.build_digest(int(current_user.id), days=days, mode_id=mode_id)
    return jsonify(result)


@app.route("/api/digests")
@login_required
def api_digests():
    limit = request.args.get("limit", default=10, type=int)
    if limit is None:
        limit = 10
    limit = max(1, min(limit, 50))
    return jsonify(database.get_recent_digests(int(current_user.id), limit=limit))


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        display_name = (request.form.get("display_name") or "").strip()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        errors = []
        if not USERNAME_RE.match(username):
            errors.append("Username must be 3-32 characters: letters, numbers, '.', '_' or '-'.")
        if not EMAIL_RE.match(email):
            errors.append("Please enter a valid email address.")
        if not display_name:
            display_name = username
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords don't match.")

        if not errors:
            try:
                user_id = database.create_user(
                    username=username,
                    email=email,
                    display_name=display_name,
                    password_hash=generate_password_hash(password),
                )
            except sqlite3.IntegrityError:
                errors.append("That username or email is already taken.")
            else:
                login_user(User(database.get_user_by_id(user_id)))
                flash("Welcome to EchoMind — your account is ready.", "success")
                return redirect(url_for("index"))

        for e in errors:
            flash(e, "error")
        return render_template(
            "register.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME,
            form={"username": username, "email": email, "display_name": display_name},
        ), 400

    return render_template("register.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME, form={})


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "POST":
        identifier = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        remember = bool(request.form.get("remember"))

        row = database.get_user_by_username(identifier) or database.get_user_by_email(identifier)
        password_ok = check_password_hash(
            row["password_hash"] if row else _DUMMY_PASSWORD_HASH, password
        )
        if row and password_ok:
            login_user(User(row), remember=remember)
            flash(f"Welcome back, {row['display_name']}.", "success")
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("index"))

        flash("Incorrect username/email or password.", "error")
        return render_template(
            "login.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME,
            form={"username": identifier},
        ), 400

    return render_template("login.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME, form={})


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You've been signed out.", "info")
    return redirect(url_for("login"))


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        form_type = request.form.get("form_type", "profile")

        if form_type == "profile":
            display_name = (request.form.get("display_name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            errors = []
            if not display_name:
                errors.append("Display name can't be empty.")
            if not EMAIL_RE.match(email):
                errors.append("Please enter a valid email address.")

            if not errors:
                existing = database.get_user_by_email(email)
                if existing and str(existing["id"]) != current_user.id:
                    errors.append("That email is already in use by another account.")

            if not errors:
                try:
                    database.update_user_profile(int(current_user.id), display_name, email)
                except sqlite3.IntegrityError:
                    errors.append("That email is already in use by another account.")
                else:
                    flash("Profile updated.", "success")
                    return redirect(url_for("profile"))

            for e in errors:
                flash(e, "error")

        elif form_type == "password":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm = request.form.get("confirm_password") or ""
            errors = []
            if not check_password_hash(current_user.password_hash, current_password):
                errors.append("Current password is incorrect.")
            if len(new_password) < 8:
                errors.append("New password must be at least 8 characters.")
            if new_password != confirm:
                errors.append("New passwords don't match.")

            if not errors:
                database.update_user_password(int(current_user.id), generate_password_hash(new_password))
                flash("Password updated.", "success")
                return redirect(url_for("profile"))

            for e in errors:
                flash(e, "error")

    stats = database.get_user_stats(int(current_user.id))
    return render_template(
        "profile.html", demo_mode=config.DEMO_MODE, model_name=config.MODEL_NAME, stats=stats,
    )


@app.route("/health")
def health():
    status = {
        "app": "ok",
        "demo_mode": config.DEMO_MODE,
        "model_name": config.MODEL_NAME,
        "prompt_version": config.PROMPT_VERSION,
        "modes": list(MODES.keys()),
    }
    return jsonify(status)


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, debug=config.DEBUG)
