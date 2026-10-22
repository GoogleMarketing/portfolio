"""Google Marketing website <-> Firebase <-> Telegram bridge.

Required Render environment variables:
  FIREBASE_SERVICE_ACCOUNT, FIREBASE_DATABASE_URL, TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID, BOT_ADMIN_ID, IMGBB_API_KEY, TELEGRAM_WEBHOOK_SECRET
Optional:
  WEBSITE_ORIGINS (comma-separated allowed browser origins)

The browser never receives the Telegram bot token or Firebase service account.
"""
from __future__ import annotations

import html
import json
import logging
import mimetypes
import os
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS
import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials, db

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("google-marketing-bridge")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
origins_setting = os.getenv("WEBSITE_ORIGINS", "").strip()
allowed_origins = [x.strip() for x in origins_setting.split(",") if x.strip()] if origins_setting else ["*"]
CORS(app, resources={r"/api/*": {"origins": allowed_origins}, r"/telegram/*": {"origins": []}})

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BOT_ADMIN_ID = os.getenv("BOT_ADMIN_ID", "").strip()
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "").strip()
SERVICE_ACCOUNT_RAW = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()

firebase_ready = False
firebase_error = ""
webhook_configured = False
webhook_lock = threading.Lock()
bridge_rate_lock = threading.Lock()
bridge_chat_events: dict[str, list[float]] = {}

try:
    if SERVICE_ACCOUNT_RAW and DATABASE_URL:
        try:
            firebase_admin.get_app()
        except ValueError:
            service_account_info = json.loads(SERVICE_ACCOUNT_RAW)
            firebase_admin.initialize_app(
                credentials.Certificate(service_account_info),
                {"databaseURL": DATABASE_URL},
            )
        firebase_ready = True
    else:
        firebase_error = "FIREBASE_SERVICE_ACCOUNT and FIREBASE_DATABASE_URL must be configured."
except Exception as exc:  # keep health route available so Render logs show the configuration issue
    firebase_error = f"Firebase initialization failed: {exc}"
    log.exception(firebase_error)


def require_ready() -> None:
    if not firebase_ready:
        raise RuntimeError(firebase_error or "Firebase Admin is not configured.")
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    if not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_CHAT_ID is not configured.")


def telegram_api(method: str, payload: dict[str, Any] | None = None, *, timeout: int = 20) -> dict[str, Any]:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload or {},
        timeout=timeout,
    )
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(body.get("description", f"Telegram {method} failed"))
    return body.get("result") or {}


def configure_webhook_once() -> None:
    """Register the Telegram webhook once per process after Render supplies its public URL."""
    global webhook_configured
    if webhook_configured or not BOT_TOKEN:
        return
    external_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not external_url:
        return
    with webhook_lock:
        if webhook_configured:
            return
        if not WEBHOOK_SECRET:
            log.warning("TELEGRAM_WEBHOOK_SECRET is not set; webhook registration was skipped for safety.")
            return
        try:
            payload = {
                "url": f"{external_url}/telegram/webhook",
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            }
            telegram_api("setWebhook", payload, timeout=15)
            webhook_configured = True
            log.info("Telegram webhook registered.")
        except Exception:
            log.exception("Telegram webhook setup failed; check Render logs and webhook settings.")


@app.before_request
def before_request() -> None:
    configure_webhook_once()


@app.get("/")
def home():
    return "Google Marketing chat bridge is running.", 200


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "firebase_ready": firebase_ready,
        "telegram_configured": bool(BOT_TOKEN and TELEGRAM_CHAT_ID and BOT_ADMIN_ID),
        "webhook_configured": webhook_configured,
        "firebase_error": firebase_error if not firebase_ready else None,
    }), 200


def verify_browser_token() -> dict[str, Any]:
    require_ready()
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise PermissionError("A Firebase ID token is required.")
    token = header[7:].strip()
    if not token:
        raise PermissionError("A Firebase ID token is required.")
    return firebase_auth.verify_id_token(token, check_revoked=True)


def assert_event_permission(event: str, payload: dict[str, Any], claims: dict[str, Any]) -> None:
    uid = str(claims.get("uid", ""))
    is_admin = claims.get("admin") is True
    if event in {"chat_message", "new_order", "new_review", "account_created"}:
        if not uid or str(payload.get("uid", "")) != uid:
            raise PermissionError("This event does not match the signed-in user.")
        if event == "chat_message":
            chat_id = str(payload.get("chatId", ""))
            # Website chat IDs are bound to the authenticated user, including anonymous Firebase users.
            if chat_id != f"user_{uid}":
                raise PermissionError("Invalid chat ID for the signed-in user.")
            meta = db.reference(f"chatMetadata/{chat_id}").get() or {}
            if str(meta.get("uid") or "") != uid:
                raise PermissionError("The chat does not belong to this signed-in user.")
            message_id = str(payload.get("messageId") or "")
            if not message_id or "/" in message_id:
                raise ValueError("A valid message ID is required.")
            message = db.reference(f"chats/{chat_id}/messages/{message_id}").get() or {}
            if str(message.get("senderUid") or "") != uid or message.get("sender") != "user":
                raise PermissionError("The message is not a user-authored message in this chat.")
        elif event == "new_order":
            order_id = str(payload.get("orderId") or "")
            if not order_id or "/" in order_id:
                raise ValueError("A valid order ID is required.")
            record = db.reference(f"orders/{order_id}").get() or {}
            if str(record.get("uid") or "") != uid:
                raise PermissionError("The order does not belong to this signed-in user.")
        elif event == "new_review":
            review_id = str(payload.get("reviewId") or "")
            if not review_id or "/" in review_id:
                raise ValueError("A valid review ID is required.")
            record = db.reference(f"reviews/{review_id}").get() or {}
            if str(record.get("uid") or "") != uid:
                raise PermissionError("The review does not belong to this signed-in user.")
        elif event == "account_created":
            record = db.reference(f"users/{uid}/profile").get() or {}
            if str(record.get("uid") or "") != uid:
                raise PermissionError("The account profile is not available yet.")
    elif event in {"admin_chat_reply", "order_status_updated"}:
        if not is_admin:
            raise PermissionError("Administrator permission is required for this event.")
        if event == "admin_chat_reply":
            chat_id = str(payload.get("chatId") or "")
            if not chat_id or "/" in chat_id or not db.reference(f"chatMetadata/{chat_id}").get():
                raise ValueError("A valid existing chat ID is required.")
        else:
            order_id = str(payload.get("orderId") or "")
            if not order_id or "/" in order_id or not db.reference(f"orders/{order_id}").get():
                raise ValueError("A valid existing order ID is required.")
    else:
        raise ValueError("Unsupported bridge event.")


def html_to_safe_telegram(raw: str) -> str:
    """Keep a small set of formatting tags and safe HTTPS/HTTP links for Telegram HTML."""
    allowed = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "br", "tg-spoiler", "a"}
    block_tags = {"p", "div", "li"}

    class SafeParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.link_stack: list[bool] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            tag = tag.lower()
            if tag in block_tags:
                self.parts.append("\n")
                return
            if tag in {"ul", "ol"}:
                return
            if tag not in allowed:
                return
            if tag == "a":
                href = dict(attrs).get("href", "") or ""
                is_safe_link = bool(re.match(r"^https?://", href, re.I))
                self.link_stack.append(is_safe_link)
                if is_safe_link:
                    self.parts.append(f'<a href="{html.escape(href, quote=True)}">')
            elif tag in {"br"}:
                self.parts.append("<br>")
            else:
                self.parts.append(f"<{tag}>")

        def handle_endtag(self, tag: str) -> None:
            tag = tag.lower()
            if tag in block_tags:
                self.parts.append("\n")
            elif tag == "a":
                if self.link_stack and self.link_stack.pop():
                    self.parts.append("</a>")
            elif tag in allowed and tag != "br":
                self.parts.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            self.parts.append(html.escape(data))

    parser = SafeParser()
    parser.feed(str(raw or ""))
    return "".join(parser.parts)[:3500]


def text_entities_to_html(text_value: str, entities: list[dict[str, Any]] | None) -> str:
    """Convert common Telegram message entities to HTML while honoring UTF-16 offsets."""
    text_value = str(text_value or "")
    entities = entities or []
    tag_map = {
        "bold": ("<b>", "</b>"),
        "italic": ("<i>", "</i>"),
        "underline": ("<u>", "</u>"),
        "strikethrough": ("<s>", "</s>"),
        "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
        "code": ("<code>", "</code>"),
        "pre": ("<pre>", "</pre>"),
        "blockquote": ("<blockquote>", "</blockquote>"),
    }

    def utf16_index(offset: int) -> int:
        target = max(0, int(offset))
        units = 0
        for index, char in enumerate(text_value):
            if units >= target:
                return index
            units += 2 if ord(char) > 0xFFFF else 1
        return len(text_value)

    starts: dict[int, list[tuple[int, str]]] = {}
    ends: dict[int, list[tuple[int, str]]] = {}
    for entity in entities:
        kind = entity.get("type")
        start = utf16_index(entity.get("offset", 0))
        end = utf16_index(int(entity.get("offset", 0)) + int(entity.get("length", 0)))
        if end <= start:
            continue
        if kind in tag_map:
            open_tag, close_tag = tag_map[kind]
        elif kind == "text_link":
            url = str(entity.get("url", ""))
            if not re.match(r"^https?://", url, re.I):
                continue
            open_tag, close_tag = f'<a href="{html.escape(url, quote=True)}">', "</a>"
        else:
            continue
        starts.setdefault(start, []).append((end, open_tag))
        ends.setdefault(end, []).append((start, close_tag))

    result: list[str] = []
    for index in range(len(text_value) + 1):
        for _start, tag in sorted(ends.get(index, []), key=lambda item: item[0], reverse=True):
            result.append(tag)
        for end, tag in sorted(starts.get(index, []), key=lambda item: item[0], reverse=True):
            result.append(tag)
        if index < len(text_value):
            result.append(html.escape(text_value[index]))
    return "".join(result)[:3500]


def telegram_send_admin_message(text: str, *, chat_id: str | None = None, reply_markup: dict[str, Any] | None = None) -> None:
    telegram_api("sendMessage", {
        "chat_id": chat_id or TELEGRAM_CHAT_ID,
        "text": text[:3900],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        **({"reply_markup": reply_markup} if reply_markup else {}),
    })


def send_admin_alert(event: str, payload: dict[str, Any]) -> None:
    name = html.escape(str(payload.get("name") or payload.get("customerName") or "Website Visitor"))
    contact = html.escape(str(payload.get("contact") or payload.get("email") or payload.get("customerEmail") or "Not provided"))
    text_value = html.escape(str(payload.get("text") or ""))
    formatted_html = html_to_safe_telegram(str(payload.get("html") or ""))
    body_html = formatted_html or text_value
    image_url = str(payload.get("imageUrl") or "")
    chat_id = str(payload.get("chatId") or "")
    order_id = html.escape(str(payload.get("orderId") or ""))
    if event == "chat_message":
        message = (
            f"<b>🌐 Website Chat</b>\n<b>From:</b> {name}\n<b>Contact:</b> {contact}\n"
            f"<b>Chat ID:</b> <code>{html.escape(chat_id)}</code>\n"
            f"<b>Message:</b> {body_html or '(Image / attachment)'}"
        )
        if order_id:
            message += f"\n<b>Order:</b> <code>{order_id}</code>"
        message += f"\n\nReply: <code>/reply {html.escape(chat_id)} your message</code>\nOr select it: <code>/select {html.escape(chat_id)}</code>"
        markup = {"inline_keyboard": [[{"text": "Copy Chat ID", "copy_text": {"text": chat_id}}]]} if chat_id else None
        telegram_send_admin_message(message, reply_markup=markup)
        if image_url and re.match(r"^https://", image_url, re.I):
            telegram_api("sendPhoto", {"chat_id": TELEGRAM_CHAT_ID, "photo": image_url, "caption": f"Attachment from {name} · <code>{html.escape(chat_id)}</code>", "parse_mode": "HTML"})
    elif event == "new_order":
        uid = str(payload.get("uid") or "")
        chat_ref = f"user_{uid}" if uid else ""
        message = (
            f"<b>🛒 New Order</b>\n<b>Order ID:</b> <code>{html.escape(str(payload.get('orderId') or ''))}</code>\n"
            f"<b>Customer:</b> {name}\n<b>Contact:</b> {contact}\n<b>Service:</b> {html.escape(str(payload.get('serviceName') or ''))}\n"
            f"<b>Quantity:</b> {html.escape(str(payload.get('quantity') or ''))}\n<b>Total:</b> ${html.escape(str(payload.get('total') or '0'))}\n"
            f"<b>Details:</b> {html.escape(str(payload.get('details') or ''))}"
        )
        if chat_ref:
            message += f"\n\nReply to customer: <code>/reply {html.escape(chat_ref)} your message</code>"
        telegram_send_admin_message(message)
    elif event == "new_review":
        telegram_send_admin_message(
            f"<b>⭐ New Review Submitted</b>\n<b>Service:</b> {html.escape(str(payload.get('serviceName') or ''))}\n"
            f"<b>Rating:</b> {html.escape(str(payload.get('rating') or ''))}/5\n<b>Title:</b> {html.escape(str(payload.get('title') or ''))}\n"
            f"<b>Review:</b> {html.escape(str(payload.get('text') or ''))}\n<b>Review ID:</b> <code>{html.escape(str(payload.get('reviewId') or ''))}</code>"
        )
    elif event == "account_created":
        telegram_send_admin_message(
            f"<b>👤 New Account</b>\n<b>Name:</b> {name}\n<b>Email:</b> {html.escape(str(payload.get('email') or 'Not provided'))}\n"
            f"<b>Contact:</b> {contact}\n<b>User ID:</b> <code>{html.escape(str(payload.get('uid') or ''))}</code>"
        )
    elif event == "order_status_updated":
        telegram_send_admin_message(
            f"<b>ℹ️ Order Status Updated</b>\n<b>Order ID:</b> <code>{html.escape(str(payload.get('orderId') or ''))}</code>\n"
            f"<b>Status:</b> {html.escape(str(payload.get('status') or ''))}"
        )
    elif event == "admin_chat_reply":
        telegram_send_admin_message(
            f"<b>↩️ Admin replied from website</b>\n<b>Customer:</b> {name}\n<b>Chat ID:</b> <code>{html.escape(chat_id)}</code>\n"
            f"<b>Message:</b> {body_html or 'Image / attachment'}"
        )
        if image_url and re.match(r"^https://", image_url, re.I):
            telegram_api("sendPhoto", {"chat_id": TELEGRAM_CHAT_ID, "photo": image_url, "caption": f"Admin attachment for <code>{html.escape(chat_id)}</code>", "parse_mode": "HTML"})


def _read_telegram_image(message: dict[str, Any]) -> str:
    if not IMGBB_API_KEY:
        raise RuntimeError("IMGBB_API_KEY is required to forward Telegram photos to the website.")
    photos = message.get("photo") or []
    if not photos:
        return ""
    file_id = photos[-1].get("file_id")
    file_info = telegram_api("getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram did not return a file path.")
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{quote(file_path)}"
    downloaded = requests.get(download_url, timeout=30)
    downloaded.raise_for_status()
    filename = file_path.rsplit("/", 1)[-1] or "telegram-image.jpg"
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    upload = requests.post(
        "https://api.imgbb.com/1/upload",
        params={"key": IMGBB_API_KEY},
        files={"image": (filename, downloaded.content, content_type)},
        timeout=45,
    )
    upload.raise_for_status()
    result = upload.json()
    if not result.get("success") or not (result.get("data") or {}).get("url"):
        raise RuntimeError("ImgBB could not store the Telegram photo.")
    return str(result["data"]["url"])


def _ensure_chat_record(chat_id: str, uid: str, name: str = "Website Visitor", contact: str = "") -> dict[str, Any]:
    chat_ref = db.reference(f"chatMetadata/{chat_id}")
    existing = chat_ref.get() or {}
    now = int(time.time() * 1000)
    if not existing:
        profile = {}
        if uid and str(chat_id).startswith("user_"):
            try:
                profile = db.reference(f"users/{uid}/profile").get() or {}
            except Exception:
                profile = {}
        existing = {
            "chatId": chat_id,
            "uid": uid or None,
            "name": name or profile.get("name") or "Website Visitor",
            "contact": contact or profile.get("contact") or profile.get("email") or "",
            "isGuest": not str(chat_id).startswith("user_"),
            "createdAt": now,
            "updatedAt": now,
            "unreadForAdmin": 0,
            "unreadForUser": 0,
        }
        chat_ref.set(existing)
    return existing


def _send_telegram_reply_to_website(chat_id: str, message: dict[str, Any], sender_id: str) -> None:
    require_ready()
    uid = str(chat_id)[5:] if str(chat_id).startswith("user_") else ""
    if not uid:
        # Guest IDs are deliberately not guessed; their Firebase UID is saved in the chat metadata.
        current = db.reference(f"chatMetadata/{chat_id}").get() or {}
        uid = str(current.get("uid") or "")
    if not uid:
        raise ValueError("That chat ID has no linked Firebase user yet. Ask the visitor to open Live Chat again.")
    record = _ensure_chat_record(chat_id, uid)
    text_value = str(message.get("text") or message.get("caption") or "")
    entities = message.get("entities") or message.get("caption_entities") or []
    safe_html = text_entities_to_html(text_value, entities) if entities else html.escape(text_value).replace("\n", "<br>")
    photo_url = _read_telegram_image(message) if message.get("photo") else ""
    now = int(time.time() * 1000)
    message_ref = db.reference(f"chats/{chat_id}/messages").push()
    message_ref.set({
        "sender": "admin",
        "senderUid": "telegram_admin",
        "text": text_value,
        "html": safe_html,
        "imageUrl": photo_url,
        "createdAt": now,
        "source": "telegram",
        "telegramAdminId": str(sender_id),
    })
    db.reference(f"chatMetadata/{chat_id}").update({
        "chatId": chat_id,
        "uid": uid,
        "lastMessage": text_value or ("Image attachment" if photo_url else "New reply"),
        "lastSender": "admin",
        "updatedAt": now,
        "unreadForUser": int(record.get("unreadForUser") or 0) + 1,
    })
    if uid and not bool(record.get("isGuest", False)):
        db.reference(f"notifications/{uid}").push({
            "title": "New support reply",
            "body": "Google Marketing replied to your live chat.",
            "createdAt": now,
            "read": False,
        })
    telegram_send_admin_message(f"✅ Reply delivered to <code>{html.escape(chat_id)}</code>.")


def handle_telegram_message(message: dict[str, Any]) -> None:
    sender = message.get("from") or {}
    sender_id = str(sender.get("id") or "")
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if not sender_id or sender_id != BOT_ADMIN_ID:
        log.warning("Ignored a Telegram message from a non-admin sender.")
        return

    text_value = str(message.get("text") or message.get("caption") or "").strip()
    parts = text_value.split(maxsplit=2)
    command = parts[0].split("@", 1)[0].lower() if parts else ""

    if command == "/start":
        telegram_send_admin_message(
            "<b>Google Marketing Support Bot</b>\n"
            "Use <code>/reply CHAT_ID your message</code> to reply to a customer.\n"
            "Use <code>/select CHAT_ID</code> to select a conversation, then send text or photos.\n"
            "Use <code>/chats</code> to list recent conversations.",
            chat_id=chat_id,
        )
        return
    if command == "/chats":
        chats = db.reference("chatMetadata").get() or {}
        rows = sorted(chats.items(), key=lambda item: int((item[1] or {}).get("updatedAt") or 0), reverse=True)[:20]
        if not rows:
            telegram_send_admin_message("No conversations yet.", chat_id=chat_id)
            return
        lines = ["<b>Recent conversations</b>"]
        for key, info in rows:
            info = info or {}
            lines.append(f"<code>{html.escape(key)}</code> · {html.escape(str(info.get('name') or 'Visitor'))} · {html.escape(str(info.get('lastMessage') or ''))[:100]}")
        telegram_send_admin_message("\n".join(lines), chat_id=chat_id)
        return
    if command == "/select":
        if len(parts) < 2:
            telegram_send_admin_message("Usage: <code>/select CHAT_ID</code>", chat_id=chat_id)
            return
        selected = parts[1].strip()
        info = db.reference(f"chatMetadata/{selected}").get()
        if not info:
            # User chats may not have been opened yet, but only user_UID IDs can be initialized safely.
            if not selected.startswith("user_"):
                telegram_send_admin_message("That chat does not exist yet. Ask the visitor to open Live Chat first.", chat_id=chat_id)
                return
            uid = selected[5:]
            info = _ensure_chat_record(selected, uid)
        db.reference(f"telegram_admin_state/{sender_id}/activeChatId").set(selected)
        telegram_send_admin_message(f"Selected chat <code>{html.escape(selected)}</code>. Send your next message or photo here.", chat_id=chat_id)
        return
    if command == "/reply":
        if len(parts) < 3:
            telegram_send_admin_message("Usage: <code>/reply CHAT_ID your message</code>", chat_id=chat_id)
            return
        target = parts[1].strip()
        # Keep the command's text/caption as the body without the command prefix.
        message_for_user = dict(message)
        prefix = text_value[:len(text_value) - len(parts[2])]
        prefix_utf16_length = len(prefix.encode("utf-16-le")) // 2
        if message.get("text"):
            message_for_user["text"] = parts[2]
            message_for_user["entities"] = [
                {**entity, "offset": int(entity.get("offset", 0)) - prefix_utf16_length}
                for entity in (message.get("entities") or [])
                if int(entity.get("offset", 0)) >= prefix_utf16_length
            ]
        if message.get("caption"):
            message_for_user["caption"] = parts[2]
            message_for_user["caption_entities"] = [
                {**entity, "offset": int(entity.get("offset", 0)) - prefix_utf16_length}
                for entity in (message.get("caption_entities") or [])
                if int(entity.get("offset", 0)) >= prefix_utf16_length
            ]
        _send_telegram_reply_to_website(target, message_for_user, sender_id)
        return

    if message.get("photo") or text_value:
        state_ref = db.reference(f"telegram_admin_state/{sender_id}/activeChatId")
        selected = state_ref.get()
        if not selected:
            telegram_send_admin_message("Choose a customer first with <code>/select CHAT_ID</code>, or use <code>/reply CHAT_ID your message</code>.", chat_id=chat_id)
            return
        _send_telegram_reply_to_website(str(selected), message, sender_id)


@app.post("/api/bridge")
def bridge():
    try:
        claims = verify_browser_token()
        data = request.get_json(silent=True) or {}
        event = str(data.get("event") or "")
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "payload must be an object"}), 400
        assert_event_permission(event, payload, claims)
        if event == "chat_message":
            uid = str(claims.get("uid", ""))
            now = time.time()
            with bridge_rate_lock:
                history = [stamp for stamp in bridge_chat_events.get(uid, []) if now - stamp < 60]
                if len(history) >= 12:
                    return jsonify({"ok": False, "error": "Please slow down and try sending again shortly."}), 429
                history.append(now)
                bridge_chat_events[uid] = history
        require_ready()
        # Alert from the authoritative Realtime Database record, not arbitrary client-supplied values.
        if event == "chat_message":
            chat_id = str(payload["chatId"])
            meta = db.reference(f"chatMetadata/{chat_id}").get() or {}
            message = db.reference(f"chats/{chat_id}/messages/{payload['messageId']}").get() or {}
            payload = {**message, "chatId": chat_id, "messageId": payload["messageId"], "uid": str(claims.get("uid", "")), "name": meta.get("name"), "contact": meta.get("contact")}
        elif event == "new_order":
            payload = db.reference(f"orders/{payload['orderId']}").get() or {}
        elif event == "new_review":
            payload = db.reference(f"reviews/{payload['reviewId']}").get() or {}
        elif event == "account_created":
            uid = str(claims.get("uid", ""))
            payload = db.reference(f"users/{uid}/profile").get() or {}
            payload["email"] = claims.get("email") or payload.get("email") or ""
        elif event == "order_status_updated":
            order_id = str(payload["orderId"])
            payload = db.reference(f"orders/{order_id}").get() or {}
            payload["orderId"] = order_id
        send_admin_alert(event, payload)
        return jsonify({"ok": True}), 200
    except PermissionError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 403
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        log.exception("Bridge request failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/telegram/webhook")
def telegram_webhook():
    if not WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "Telegram webhook secret is not configured."}), 503
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if supplied != WEBHOOK_SECRET:
        return jsonify({"ok": False}), 403
    try:
        require_ready()
        update_data = request.get_json(silent=True) or {}
        message = update_data.get("message")
        if message:
            handle_telegram_message(message)
        return jsonify({"ok": True}), 200
    except Exception as exc:
        log.exception("Telegram webhook handling failed")
        # Return 200 to avoid Telegram sending duplicate updates indefinitely; the error is visible in Render logs.
        return jsonify({"ok": False, "error": str(exc)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
