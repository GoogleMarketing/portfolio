"""
Portfolio Live Chat Bot - UPDATED FastAPI Backend
Target: https://portfolio-live-chat-bot.onrender.com
Frontend: https://googlemarketing.github.io/portfolio/
Author: Google Marketing - Shakil
Admin: afshakil279@gmail.com

UPDATES:
- Simplified Telegram notification: Name + Chat ID only
- One-click copy reply command via inline buttons + code block
- Pending reply state: /reply <chatId> -> bot asks for message, next message forwarded
- Welcome message auto-sent when account created
- Download all clients + single client via commands
- Full command list with / help
- Handles callback_query for inline buttons
"""

import os
import json
import base64
import time
import uuid
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
import tempfile

import httpx
import requests
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

try:
    import firebase_admin
    from firebase_admin import credentials, auth as fb_auth, db as fb_db
except ImportError:
    firebase_admin = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portfolio-backend-updated")

FIREBASE_API_KEY = os.getenv("FIREBASE_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.getenv("FIREBASE_AUTH_DOMAIN", "")
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL", "")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "")
FIREBASE_MESSAGING_SENDER_ID = os.getenv("FIREBASE_MESSAGING_SENDER_ID", "")
FIREBASE_APP_ID = os.getenv("FIREBASE_APP_ID", "")
FIREBASE_SERVICE_ACCOUNT = os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
IMGBB_API_KEY = os.getenv("IMGBB_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BOT_ADMIN_ID = os.getenv("BOT_ADMIN_ID", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "afshakil279@gmail.com")
BRIDGE_WEBHOOK_SECRET = os.getenv("BRIDGE_WEBHOOK_SECRET", "")

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""
TELEGRAM_FILE_BASE = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

# Pending replies: admin_telegram_chat_id -> target_client_chat_id
pending_replies: Dict[str, Dict[str, Any]] = {}

# --- Visit counting: only consented visits, no personal details stored ---
class VisitIn(BaseModel):
    consent: bool = False
    page: Optional[str] = ""
    referrerDomain: Optional[str] = ""
    timezone: Optional[str] = ""
    language: Optional[str] = ""
    timestamp: Optional[str] = ""

@app.post("/api/visit")
async def api_visit(payload: VisitIn):
    # Only consented visits are accepted. No personal details are read or stored here.
    if not payload.consent:
        return {"ok": False, "reason": "consent_required"}
    # Summary only: timezone + referrer domain + page.
    try:
        if TELEGRAM_CHAT_ID:
            page_val = payload.page or '/'
            from_val = payload.referrerDomain or 'direct'
            tz_val = payload.timezone or '-'
            lang_val = payload.language or '-'
            time_val = payload.timestamp or datetime.utcnow().isoformat()
            text = 'Visit counted (allowed)' + chr(10) + 'Page: ' + page_val + chr(10) + 'From: ' + from_val + chr(10) + 'Timezone: ' + tz_val + ' / Lang: ' + lang_val + chr(10) + 'Time: ' + time_val
            # Do not block the response on Telegram
            try:
                send_telegram_message(TELEGRAM_CHAT_ID, text)
            except Exception as e:
                logger.warning(f"visit telegram failed: {e}")
    except Exception as e:
        logger.warning(f"visit handler: {e}")
    return {"ok": True}


firebase_app = None
db_available = False

def init_firebase():
    global firebase_app, db_available
    if not firebase_admin:
        logger.warning("firebase-admin not installed")
        return
    if firebase_admin._apps:
        firebase_app = firebase_admin.get_app()
        db_available = True
        return
    if not FIREBASE_SERVICE_ACCOUNT:
        logger.warning("FIREBASE_SERVICE_ACCOUNT not set")
        return
    try:
        service_account_info = None
        sa_str = FIREBASE_SERVICE_ACCOUNT.strip()
        if not sa_str.startswith("{"):
            try:
                decoded = base64.b64decode(sa_str).decode("utf-8")
                service_account_info = json.loads(decoded)
            except Exception:
                service_account_info = json.loads(sa_str)
        else:
            service_account_info = json.loads(sa_str)
        cred = credentials.Certificate(service_account_info)
        options = {}
        if FIREBASE_DATABASE_URL:
            options["databaseURL"] = FIREBASE_DATABASE_URL
        if FIREBASE_STORAGE_BUCKET:
            options["storageBucket"] = FIREBASE_STORAGE_BUCKET
        firebase_app = firebase_admin.initialize_app(cred, options if options else None)
        db_available = True
        logger.info("Firebase Admin initialized")
    except Exception as e:
        logger.error(f"Firebase init failed: {e}")

init_firebase()

def get_db_ref(path: str):
    if not db_available or not firebase_app:
        return None
    try:
        return fb_db.reference(path)
    except Exception as e:
        logger.error(f"DB ref {path} failed: {e}")
        return None

def verify_firebase_token(id_token: str) -> Optional[Dict[str, Any]]:
    if not firebase_app or not id_token:
        return None
    try:
        decoded = fb_auth.verify_id_token(id_token)
        if decoded.get("email") == ADMIN_EMAIL and not decoded.get("admin"):
            try:
                fb_auth.set_custom_user_claims(decoded["uid"], {"admin": True})
                decoded["admin"] = True
            except Exception as ce:
                logger.warning(f"Set admin claim failed: {ce}")
        return decoded
    except Exception as e:
        logger.warning(f"Token verify failed: {e}")
        return None

# Telegram helpers
def telegram_api_call(method: str, payload: Dict[str, Any], files: Optional[Dict] = None) -> Dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured")
    url = f"{TELEGRAM_API_BASE}/{method}"
    try:
        if files:
            resp = requests.post(url, data=payload, files=files, timeout=30)
        else:
            resp = requests.post(url, json=payload, timeout=30)
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram {method} failed: {data}")
            raise Exception(data.get("description"))
        return data
    except requests.RequestException as e:
        logger.error(f"Telegram request {method} failed: {e}")
        raise HTTPException(status_code=502, detail=str(e))

def send_telegram_message(chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: Optional[Dict] = None, reply_to_message_id: Optional[int] = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return telegram_api_call("sendMessage", payload)

def send_telegram_photo(chat_id: str, photo_url_or_id: str, caption: str = "", parse_mode: str = "HTML", reply_markup: Optional[Dict] = None):
    payload = {"chat_id": chat_id, "photo": photo_url_or_id, "caption": caption[:1024], "parse_mode": parse_mode}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return telegram_api_call("sendPhoto", payload)

def send_telegram_photo_bytes(chat_id: str, image_bytes: bytes, filename: str = "image.jpg", caption: str = "", reply_markup: Optional[Dict] = None):
    url = f"{TELEGRAM_API_BASE}/sendPhoto"
    files = {"photo": (filename, image_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    resp = requests.post(url, data=data, files=files, timeout=30)
    result = resp.json()
    if not result.get("ok"):
        raise Exception(result.get("description"))
    return result

def send_telegram_document(chat_id: str, file_path: str, filename: str, caption: str = ""):
    url = f"{TELEGRAM_API_BASE}/sendDocument"
    with open(file_path, 'rb') as f:
        files = {"document": (filename, f)}
        data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
        resp = requests.post(url, data=data, files=files, timeout=60)
        result = resp.json()
        if not result.get("ok"):
            logger.error(f"sendDocument failed: {result}")
            raise Exception(result.get("description"))
        return result

def answer_callback_query(callback_query_id: str, text: str = "", show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id, "text": text[:200], "show_alert": show_alert}
    try:
        telegram_api_call("answerCallbackQuery", payload)
    except Exception as e:
        logger.warning(f"answerCallbackQuery failed: {e}")

def get_telegram_file(file_id: str) -> Optional[Dict]:
    try:
        resp = requests.get(f"{TELEGRAM_API_BASE}/getFile", params={"file_id": file_id}, timeout=15).json()
        if resp.get("ok"):
            return resp["result"]
    except Exception as e:
        logger.error(f"getFile failed: {e}")
    return None

def download_telegram_file(file_path: str) -> Optional[bytes]:
    if not file_path:
        return None
    url = f"{TELEGRAM_FILE_BASE}/{file_path}"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        logger.error(f"download file failed {file_path}: {e}")
        return None

def upload_to_imgbb(image_bytes: bytes, name: Optional[str] = None) -> str:
    if not IMGBB_API_KEY:
        raise HTTPException(status_code=500, detail="IMGBB_API_KEY not configured")
    url = f"https://api.imgbb.com/1/upload?key={IMGBB_API_KEY}"
    try:
        files = {"image": (name or f"{uuid.uuid4().hex}.jpg", image_bytes)}
        resp = requests.post(url, files=files, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and data.get("success") and data.get("data", {}).get("url"):
            return data["data"]["url"]
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp2 = requests.post(url, data={"image": b64, "name": name or "upload"}, timeout=30)
        data2 = resp2.json()
        if data2.get("success"):
            return data2["data"]["url"]
        raise Exception("ImgBB failed")
    except Exception as e:
        logger.error(f"ImgBB upload failed: {e}")
        raise

# App
app = FastAPI(title="Google Marketing Portfolio - Updated Bot", version="3.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class BridgePayload(BaseModel):
    event: str
    payload: Dict[str, Any]
    sentAt: Optional[str] = None

def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

def build_copy_keyboard(chat_id: str):
    return {
        "inline_keyboard": [
            [
                {"text": "📋 Copy Reply Command", "callback_data": f"copy:{chat_id}"},
                {"text": "✍️ Reply Now", "callback_data": f"quick:{chat_id}"}
            ],
            [
                {"text": "👤 Client Info", "callback_data": f"view:{chat_id}"},
                {"text": "📥 Download Data", "callback_data": f"dl:{chat_id}"}
            ]
        ]
    }

def get_welcome_message(name: str):
    return f"""🎉 <b>Welcome to Google Marketing, {name}!</b>

Thanks for creating your account. We're excited to work with you.

<b>What you can do now:</b>
✅ Chat here directly to discuss your requirements
✅ Share your personal contact info (WhatsApp / Telegram / Email) for faster communication
✅ Place an order or ask for a custom quote - we reply within minutes

Our team is available 24/7. Just tell us about your app or business and we'll guide you.

<i>You can also contact us directly via WhatsApp / Telegram from the contact section.</i>

- Team Google Marketing"""

@app.post("/api/bridge")
async def telegram_bridge(request: Request, authorization: Optional[str] = Header(None)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    event = body.get("event")
    payload = body.get("payload", {})
    sent_at = body.get("sentAt") or datetime.utcnow().isoformat()

    if not event:
        raise HTTPException(status_code=400, detail="Missing event")

    token = extract_bearer_token(authorization)
    user_info = None
    if token:
        user_info = verify_firebase_token(token)

    if not TELEGRAM_CHAT_ID:
        return {"ok": True, "warning": "TELEGRAM_CHAT_ID not configured"}

    try:
        if event == "account_created":
            name = payload.get("name", "New User")
            email = payload.get("email", "")
            contact = payload.get("contact", "")
            uid = payload.get("uid", "")
            chat_id = payload.get("chatId") or payload.get("chat_id") or (f"user_{uid}" if uid else f"guest_{uuid.uuid4().hex[:8]}")

            # 1. Notify admin - CLEAN format
            admin_text = (
                f"<b>👤 New Account</b>\n"
                f"<b>Name:</b> {name}\n"
                f"<b>Email:</b> {email}\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"<b>UID:</b> <code>{uid}</code>\n"
                f"<b>Time:</b> {sent_at}"
            )
            keyboard = build_copy_keyboard(chat_id)
            result = send_telegram_message(TELEGRAM_CHAT_ID, admin_text, reply_markup=keyboard)

            # 2. Auto welcome message to client in Firebase
            if db_available:
                try:
                    welcome = get_welcome_message(name)
                    msg_data = {
                        "sender": "admin",
                        "from": "admin",
                        "text": welcome,
                        "html": welcome,
                        "imageUrl": "",
                        "createdAt": int(time.time() * 1000),
                        "createdAtServer": {".sv": "timestamp"},
                        "type": "welcome_auto"
                    }
                    get_db_ref(f"chats/{chat_id}/messages").push(msg_data)
                    get_db_ref(f"chatMetadata/{chat_id}").update({
                        "lastMessage": "Welcome message sent",
                        "lastSender": "admin",
                        "updatedAt": int(time.time() * 1000),
                        "unreadForUser": 1,
                        "clientName": name,
                        "clientEmail": email,
                        "clientContact": contact
                    })
                except Exception as e:
                    logger.warning(f"Welcome msg failed: {e}")

            return {"ok": True, "telegram_message_id": result.get("result", {}).get("message_id")}

        elif event in ("chat_message", "new_message", "live_chat"):
            chat_id = payload.get("chatId") or payload.get("chat_id") or "unknown"
            name = payload.get("name", "Website Visitor")
            contact = payload.get("contact", "")
            text_content = payload.get("text", "") or payload.get("message", "")
            image_url = payload.get("imageUrl", "") or payload.get("image_url", "")
            uid = payload.get("uid", "")

            # Clean notification - ONLY name + chat ID + short preview
            safe_preview = text_content[:120].replace("<", "").replace(">", "") if text_content else ("📷 Image" if image_url else "New message")
            service_name = payload.get("serviceName") or payload.get("service") or payload.get("pendingChatService") or ""
            if service_name:
                safe_preview = f"[Order: {str(service_name)[:80]}] " + safe_preview
            telegram_text = (
                f"💬 <b>Chat</b> | <b>{name}</b>\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"💭 {safe_preview}"
            )
            keyboard = build_copy_keyboard(chat_id)

            if image_url:
                caption = telegram_text[:1024]
                try:
                    result = send_telegram_photo(TELEGRAM_CHAT_ID, image_url, caption=caption, reply_markup=keyboard)
                except Exception:
                    try:
                        img_bytes = requests.get(image_url, timeout=15).content
                        result = send_telegram_photo_bytes(TELEGRAM_CHAT_ID, img_bytes, caption=caption, reply_markup=keyboard)
                    except Exception:
                        result = send_telegram_message(TELEGRAM_CHAT_ID, telegram_text + f"\nImage: {image_url}", reply_markup=keyboard)
            else:
                result = send_telegram_message(TELEGRAM_CHAT_ID, telegram_text, reply_markup=keyboard)

            tg_msg_id = result.get("result", {}).get("message_id")
            if tg_msg_id and db_available:
                try:
                    get_db_ref(f"telegram_message_map/{tg_msg_id}").set({
                        "chatId": chat_id, "uid": uid, "createdAt": int(time.time()*1000), "type": "chat_message"
                    })
                except Exception:
                    pass
            return {"ok": True, "telegram_message_id": tg_msg_id}

        elif event in ("new_order", "order_created"):
            order_id = payload.get("orderId") or f"ORD-{int(time.time())}"
            service_name = payload.get("serviceName") or "Service"
            quantity = payload.get("quantity", "")
            total = payload.get("total", "")
            customer_name = payload.get("name") or "Client"
            contact = payload.get("contact") or ""
            uid = payload.get("uid", "")
            chat_id = payload.get("chatId") or f"user_{uid}"

            text = (
                f"<b>🛒 New Order</b> | <b>{customer_name}</b>\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"<b>Order:</b> <code>{order_id}</code> | {service_name} x{quantity} ${total}"
            )
            keyboard = build_copy_keyboard(chat_id)
            result = send_telegram_message(TELEGRAM_CHAT_ID, text, reply_markup=keyboard)
            return {"ok": True, "order_id": order_id}

        else:
            text = f"<b>🔔 {event}</b>\n<pre>{json.dumps(payload, indent=2)[:2000]}</pre>"
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bridge {event} failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Bot not configured")
    if BRIDGE_WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret and secret != BRIDGE_WEBHOOK_SECRET:
            logger.warning("Invalid secret")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Webhook: {json.dumps(update)[:1500]}")

    # Handle callback_query first
    if "callback_query" in update:
        cq = update["callback_query"]
        cq_id = cq.get("id")
        data = cq.get("data", "")
        from_user = cq.get("from", {})
        from_id = str(from_user.get("id", ""))
        chat = cq.get("message", {}).get("chat", {})
        chat_id_str = str(chat.get("id", ""))

        is_admin = False
        if BOT_ADMIN_ID and from_id == str(BOT_ADMIN_ID):
            is_admin = True
        if TELEGRAM_CHAT_ID and chat_id_str == str(TELEGRAM_CHAT_ID):
            is_admin = True
        if not is_admin:
            answer_callback_query(cq_id, "⛔ Admin only")
            return {"ok": True, "ignored": "not admin"}

        if data.startswith("copy:"):
            target = data[5:]
            answer_callback_query(cq_id, f"Copied {target}")
            # Send copy-ready command
            copy_text = f"<code>/reply {target}</code>\n\n👆 Tap to copy, then paste and send.\nAfter that, send your message/photo/link - it will go to client."
            send_telegram_message(chat_id_str, copy_text)
            # Also set pending so next message can be sent directly if admin wants
            pending_replies[chat_id_str] = {"target": target, "ts": time.time()}
            return {"ok": True, "copy_sent": True}

        elif data.startswith("quick:"):
            target = data[6:]
            pending_replies[chat_id_str] = {"target": target, "ts": time.time()}
            answer_callback_query(cq_id, "Reply mode on")
            send_telegram_message(chat_id_str, f"✅ <b>Reply mode ON</b> for <code>{target}</code>\n\nNow send your message, photo, link or file. It will be delivered to client instantly.\n\nSend /cancel to cancel.")
            return {"ok": True, "quick_reply_set": True}

        elif data.startswith("view:"):
            target = data[5:]
            # Fetch client info
            info_text = f"<b>👤 Client:</b> <code>{target}</code>\n"
            if db_available:
                try:
                    meta = get_db_ref(f"chatMetadata/{target}").get()
                    if meta:
                        info_text += f"<b>Name:</b> {meta.get('clientName','-')}\n<b>Email:</b> {meta.get('clientEmail','-')}\n<b>Contact:</b> {meta.get('clientContact','-')}\n<b>Last:</b> {meta.get('lastMessage','-')}"
                except Exception as e:
                    info_text += f"Error: {e}"
            answer_callback_query(cq_id, "Client info")
            send_telegram_message(chat_id_str, info_text)
            return {"ok": True}

        elif data.startswith("dl:"):
            target = data[3:]
            answer_callback_query(cq_id, "Preparing download...")
            # Trigger download single
            await handle_download_user(chat_id_str, target)
            return {"ok": True}

        answer_callback_query(cq_id, "Done")
        return {"ok": True}

    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True, "skipped": "no message"}

    from_user = message.get("from", {})
    from_id = str(from_user.get("id", ""))
    chat = message.get("chat", {})
    chat_id_str = str(chat.get("id", ""))
    text = message.get("text", "") or message.get("caption", "") or ""
    photo = message.get("photo")
    document = message.get("document")
    reply_to = message.get("reply_to_message")

    is_admin = False
    if BOT_ADMIN_ID and from_id == str(BOT_ADMIN_ID):
        is_admin = True
    if TELEGRAM_CHAT_ID and chat_id_str == str(TELEGRAM_CHAT_ID):
        is_admin = True
    if not is_admin:
        logger.info(f"Non-admin ignored {from_id}")
        return {"ok": True, "ignored": "not admin"}

    # Commands
    if text.lower().startswith("/start") or text.lower().startswith("/help"):
        help_text = (
            "<b>🤖 Google Marketing Admin Bot - Commands</b>\n\n"
            "<b>💬 Reply System:</b>\n"
            "/reply <code>chatId</code> <i>message</i> - Reply directly with message\n"
            "/reply <code>chatId</code> - Enter reply mode, then send anything\n"
            "/cancel - Cancel pending reply\n\n"
            "<b>👥 Clients:</b>\n"
            "/clients or /users - List all clients\n"
            "/chats - List active chats\n"
            "/stats - Show statistics\n\n"
            "<b>📥 Download:</b>\n"
            "/download_all - Download ALL clients data (JSON)\n"
            "/download_user <code>chatId</code> - Download single client data\n"
            "/export_csv - Export clients as CSV\n\n"
            "<b>How reply works:</b>\n"
            "1. Client chats -> you get notification with Chat ID\n"
            "2. Tap 📋 Copy Reply Command -> copy /reply chatId\n"
            "3. Paste & send -> bot asks for your message\n"
            "4. Send text / link / photo -> delivered to client instantly\n\n"
            f"Admin Chat: <code>{TELEGRAM_CHAT_ID}</code>"
        )
        send_telegram_message(chat_id_str, help_text)
        return {"ok": True}

    if text.lower().startswith("/cancel"):
        if chat_id_str in pending_replies:
            del pending_replies[chat_id_str]
            send_telegram_message(chat_id_str, "❌ Reply mode cancelled.")
        else:
            send_telegram_message(chat_id_str, "No pending reply to cancel.")
        return {"ok": True}

    if text.lower().startswith("/stats"):
        await handle_stats(chat_id_str)
        return {"ok": True}

    if text.lower().startswith("/clients") or text.lower().startswith("/users"):
        await handle_list_clients(chat_id_str)
        return {"ok": True}

    if text.lower().startswith("/chats"):
        await handle_list_chats(chat_id_str)
        return {"ok": True}

    if text.lower().startswith("/download_all") or text.lower().startswith("/export_all"):
        await handle_download_all(chat_id_str)
        return {"ok": True}

    if text.lower().startswith("/download_user"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_telegram_message(chat_id_str, "Usage: /download_user <code>chatId</code>\nExample: /download_user <code>user_abc123</code>")
        else:
            target = parts[1].strip()
            await handle_download_user(chat_id_str, target)
        return {"ok": True}

    if text.lower().startswith("/export_csv"):
        await handle_export_csv(chat_id_str)
        return {"ok": True}

    # Check pending reply
    if chat_id_str in pending_replies and not text.startswith("/"):
        pending = pending_replies[chat_id_str]
        target_chat_id = pending["target"]
        # This message should be forwarded to target_chat_id
        image_url = ""
        if photo:
            largest = photo[-1]
            file_id = largest.get("file_id")
            file_info = get_telegram_file(file_id)
            if file_info:
                img_bytes = download_telegram_file(file_info.get("file_path"))
                if img_bytes:
                    try:
                        image_url = upload_to_imgbb(img_bytes, name=f"admin_{int(time.time())}.jpg")
                    except Exception as e:
                        logger.error(f"ImgBB failed: {e}")

        # Handle document as well
        if document:
            file_id = document.get("file_id")
            file_info = get_telegram_file(file_id)
            if file_info:
                img_bytes = download_telegram_file(file_info.get("file_path"))
                if img_bytes:
                    try:
                        image_url = upload_to_imgbb(img_bytes, name=document.get("file_name", "file.jpg"))
                    except Exception:
                        pass
                # If it's not image, still try to send as file? For now send link text
                if not image_url and text:
                    text = f"{text}\n[File: {document.get('file_name')}]"

        # Forward to Firebase
        if db_available:
            try:
                msg_data = {
                    "sender": "admin",
                    "from": "admin",
                    "text": text or "",
                    "html": text or "",
                    "imageUrl": image_url or "",
                    "createdAt": int(time.time()*1000),
                    "createdAtServer": {".sv": "timestamp"},
                    "type": "admin_reply_pending"
                }
                get_db_ref(f"chats/{target_chat_id}/messages").push(msg_data)
                get_db_ref(f"chatMetadata/{target_chat_id}").update({
                    "lastMessage": text[:200] if text else "Image",
                    "lastSender": "admin",
                    "updatedAt": int(time.time()*1000),
                    "unreadForUser": 1,
                    "unreadForAdmin": 0
                })
                send_telegram_message(chat_id_str, f"✅ Sent to <code>{target_chat_id}</code>: {text[:300] if text else 'Image'}")
                del pending_replies[chat_id_str]
                return {"ok": True, "routed_via_pending": target_chat_id}
            except Exception as e:
                logger.error(f"Pending forward failed: {e}")
                send_telegram_message(chat_id_str, f"❌ Failed: {e}")
                return {"ok": False}

    # Parse /reply command
    target_chat_id = None
    admin_reply_text = text
    image_url_for_customer = ""

    reply_cmd_match = re.match(r"^/reply\s+(\S+)(?:\s+([\s\S]+))?", text, re.IGNORECASE)
    if reply_cmd_match:
        target_chat_id = reply_cmd_match.group(1).strip()
        msg_part = reply_cmd_match.group(2)
        if msg_part:
            admin_reply_text = msg_part.strip()
        else:
            # Only /reply chatId -> enter pending mode
            pending_replies[chat_id_str] = {"target": target_chat_id, "ts": time.time()}
            send_telegram_message(chat_id_str, f"✅ <b>Reply mode ON</b> for <code>{target_chat_id}</code>\n\nNow send your message, photo, link or file.\nIt will be delivered exactly as you send it (text/link/image).\n\n/cancel to cancel.")
            return {"ok": True, "pending_set": target_chat_id}

    elif reply_to and db_available:
        replied_msg_id = reply_to.get("message_id")
        if replied_msg_id:
            try:
                mapping = get_db_ref(f"telegram_message_map/{replied_msg_id}").get()
                if mapping and mapping.get("chatId"):
                    target_chat_id = mapping["chatId"]
            except Exception:
                pass
        if not target_chat_id and reply_to.get("text"):
            m = re.search(r"Chat ID:</b>\s*<code>([^<]+)</code>", reply_to.get("text",""))
            if not m:
                m = re.search(r"Chat ID:\s*(\S+)", reply_to.get("text",""))
            if m:
                target_chat_id = m.group(1).strip()

    if photo:
        largest = photo[-1]
        file_id = largest.get("file_id")
        file_info = get_telegram_file(file_id)
        if file_info:
            img_bytes = download_telegram_file(file_info.get("file_path"))
            if img_bytes:
                try:
                    image_url_for_customer = upload_to_imgbb(img_bytes, name=f"admin_{int(time.time())}.jpg")
                except Exception as e:
                    logger.error(f"ImgBB admin photo failed: {e}")

    if not target_chat_id:
        if text and not text.startswith("/"):
            send_telegram_message(chat_id_str, f"⚠️ No target Chat ID. Use /reply <code>chatId</code> message or tap Copy button on client message.\n\nYour message: {text[:500]}")
        return {"ok": True, "no_target": True}

    if not db_available:
        raise HTTPException(status_code=500, detail="Firebase DB not available")

    try:
        msg_data = {
            "sender": "admin",
            "from": "admin",
            "text": admin_reply_text or "",
            "html": admin_reply_text or "",
            "imageUrl": image_url_for_customer or "",
            "createdAt": int(time.time()*1000),
            "createdAtServer": {".sv": "timestamp"},
            "telegramMessageId": message.get("message_id"),
            "adminUserId": from_id,
        }
        chat_messages_ref = get_db_ref(f"chats/{target_chat_id}/messages")
        new_msg_ref = chat_messages_ref.push(msg_data)
        get_db_ref(f"chatMetadata/{target_chat_id}").update({
            "lastMessage": admin_reply_text[:200] if admin_reply_text else "Image",
            "lastSender": "admin",
            "updatedAt": int(time.time()*1000),
            "unreadForUser": 1,
            "unreadForAdmin": 0
        })
        confirm_text = f"✅ Sent to <code>{target_chat_id}</code>\n{admin_reply_text[:500]}"
        send_telegram_message(chat_id_str, confirm_text)
        return {"ok": True, "routed_to": target_chat_id}
    except Exception as e:
        logger.error(f"Route failed: {e}", exc_info=True)
        send_telegram_message(chat_id_str, f"❌ Failed to send to <code>{target_chat_id}</code>: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Download handlers
async def handle_stats(admin_chat_id: str):
    try:
        users = get_db_ref("users").get() if db_available else {}
        chats_meta = get_db_ref("chatMetadata").get() if db_available else {}
        orders = get_db_ref("orders").get() if db_available else {}
        text = (
            f"<b>📊 Stats</b>\n"
            f"👥 Users: {len(users) if isinstance(users, dict) else 0}\n"
            f"💬 Active Chats: {len(chats_meta) if isinstance(chats_meta, dict) else 0}\n"
            f"🛒 Orders: {len(orders) if isinstance(orders, dict) else 0}\n"
        )
        send_telegram_message(admin_chat_id, text)
    except Exception as e:
        send_telegram_message(admin_chat_id, f"Stats failed: {e}")

async def handle_list_clients(admin_chat_id: str):
    try:
        users_ref = get_db_ref("users")
        users = users_ref.get() if users_ref else {}
        if not users:
            send_telegram_message(admin_chat_id, "No clients found.")
            return
        text = f"<b>👥 Clients ({len(users)})</b>\n\n"
        count = 0
        for uid, data in list(users.items())[:50]:
            if not isinstance(data, dict):
                continue
            name = data.get("name", "No name")
            email = data.get("email", "")
            chat_id = f"user_{uid}"
            text += f"{count+1}. {name} | {email} | <code>{chat_id}</code>\n"
            count += 1
        text += "\nUse /download_user <code>chatId</code> to download single client."
        send_telegram_message(admin_chat_id, text)
    except Exception as e:
        send_telegram_message(admin_chat_id, f"List failed: {e}")

async def handle_list_chats(admin_chat_id: str):
    try:
        meta_ref = get_db_ref("chatMetadata")
        metas = meta_ref.get() if meta_ref else {}
        if not metas:
            send_telegram_message(admin_chat_id, "No chats.")
            return
        text = f"<b>💬 Active Chats ({len(metas)})</b>\n\n"
        for chat_id, meta in list(metas.items())[:50]:
            if not isinstance(meta, dict):
                continue
            name = meta.get("clientName", "-")
            last = meta.get("lastMessage", "")[:50]
            text += f"• <code>{chat_id}</code> | {name} | {last}\n"
        send_telegram_message(admin_chat_id, text)
    except Exception as e:
        send_telegram_message(admin_chat_id, f"Chats list failed: {e}")

async def handle_download_all(admin_chat_id: str):
    try:
        send_telegram_message(admin_chat_id, "⏳ Preparing all clients data...")
        users = get_db_ref("users").get() if get_db_ref("users") else {}
        chats_meta = get_db_ref("chatMetadata").get() if get_db_ref("chatMetadata") else {}
        orders = get_db_ref("orders").get() if get_db_ref("orders") else {}
        # For privacy, limit chats messages not included in all download, only metadata
        data = {
            "exportedAt": datetime.utcnow().isoformat(),
            "totalUsers": len(users) if isinstance(users, dict) else 0,
            "totalChats": len(chats_meta) if isinstance(chats_meta, dict) else 0,
            "totalOrders": len(orders) if isinstance(orders, dict) else 0,
            "users": users,
            "chatMetadata": chats_meta,
            "orders": orders
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path = f.name
        filename = f"all_clients_{int(time.time())}.json"
        send_telegram_document(admin_chat_id, temp_path, filename, f"📦 All clients export - {len(users)} users")
        os.unlink(temp_path)
    except Exception as e:
        logger.error(f"Download all failed: {e}")
        send_telegram_message(admin_chat_id, f"❌ Download all failed: {e}")

async def handle_download_user(admin_chat_id: str, target_chat_id: str):
    try:
        send_telegram_message(admin_chat_id, f"⏳ Preparing data for <code>{target_chat_id}</code>...")
        # Try to get uid from chat_id
        uid = None
        if target_chat_id.startswith("user_"):
            uid = target_chat_id[5:]
        # Fetch
        user_data = None
        if uid:
            user_data = get_db_ref(f"users/{uid}").get()
        chat_messages = get_db_ref(f"chats/{target_chat_id}/messages").get()
        chat_meta = get_db_ref(f"chatMetadata/{target_chat_id}").get()
        user_orders = None
        if uid:
            # orders filtered by uid
            all_orders = get_db_ref("orders").get()
            if isinstance(all_orders, dict):
                user_orders = {k: v for k, v in all_orders.items() if isinstance(v, dict) and v.get("uid") == uid}

        data = {
            "chatId": target_chat_id,
            "uid": uid,
            "exportedAt": datetime.utcnow().isoformat(),
            "user": user_data,
            "chatMetadata": chat_meta,
            "messages": chat_messages,
            "orders": user_orders
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path = f.name
        filename = f"client_{target_chat_id}_{int(time.time())}.json"
        send_telegram_document(admin_chat_id, temp_path, filename, f"👤 Client data - {target_chat_id}")
        os.unlink(temp_path)
    except Exception as e:
        logger.error(f"Download user {target_chat_id} failed: {e}")
        send_telegram_message(admin_chat_id, f"❌ Download failed for {target_chat_id}: {e}")

async def handle_export_csv(admin_chat_id: str):
    try:
        users = get_db_ref("users").get() if get_db_ref("users") else {}
        if not users or not isinstance(users, dict):
            send_telegram_message(admin_chat_id, "No users to export.")
            return
        import csv
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["uid", "name", "email", "contact", "createdAt", "chatId"])
            for uid, data in users.items():
                if not isinstance(data, dict):
                    continue
                writer.writerow([
                    uid,
                    data.get("name",""),
                    data.get("email",""),
                    data.get("contact",""),
                    data.get("createdAt",""),
                    f"user_{uid}"
                ])
            temp_path = f.name
        filename = f"clients_{int(time.time())}.csv"
        send_telegram_document(admin_chat_id, temp_path, filename, "📊 Clients CSV export")
        os.unlink(temp_path)
    except Exception as e:
        send_telegram_message(admin_chat_id, f"CSV export failed: {e}")

@app.get("/")
async def root():
    return {
        "service": "Google Marketing Portfolio Live Chat Bot - UPDATED",
        "status": "running",
        "admin": ADMIN_EMAIL,
        "frontend": "https://googlemarketing.github.io/portfolio/",
        "firebase": "configured" if db_available else "not configured",
        "telegram": "configured" if TELEGRAM_BOT_TOKEN else "missing",
        "imgbb": "configured" if IMGBB_API_KEY else "missing",
        "features": ["copy reply command", "pending reply state", "welcome auto", "download all/single", "inline buttons", "callback handling"],
        "endpoints": ["/health", "/api/bridge", "/api/telegram/webhook", "/api/telegram/set-webhook"]
    }

@app.get("/health")
async def health():
    return {"ok": True, "timestamp": datetime.utcnow().isoformat(), "firebase": db_available, "telegram": bool(TELEGRAM_BOT_TOKEN), "pending": len(pending_replies)}

@app.api_route("/api/telegram/set-webhook", methods=["GET", "POST"])
async def set_telegram_webhook(request: Request):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Token not set")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    url = body.get("url") or request.query_params.get("url")
    if not url:
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "portfolio-live-chat-bot.onrender.com"
        scheme = request.headers.get("x-forwarded-proto") or "https"
        url = f"{scheme}://{host}/api/telegram/webhook"
    payload = {"url": url, "allowed_updates": ["message", "edited_message", "channel_post", "callback_query"]}
    if BRIDGE_WEBHOOK_SECRET:
        payload["secret_token"] = BRIDGE_WEBHOOK_SECRET
    try:
        result = telegram_api_call("setWebhook", payload)
        info = telegram_api_call("getWebhookInfo", {})
        return {"ok": True, "setWebhook": result, "webhookInfo": info, "url": url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/telegram/get-webhook-info")
async def get_webhook_info():
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Token not set")
    info = telegram_api_call("getWebhookInfo", {})
    return info
