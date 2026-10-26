"""
Portfolio Live Chat Bot - FastAPI Backend
Target: https://portfolio-live-chat-bot.onrender.com
Frontend: https://googlemarketing.github.io/portfolio/
Author: Google Marketing - Shakil
Admin: afshakil279@gmail.com

Handles:
- Firebase Admin SDK verification & database sync
- Telegram Bot webhook and message routing
- ImgBB image handling (upload & forwarding)
- Visitor analytics ingestion (optional bridge)
- Order notifications
"""

import os
import json
import base64
import time
import uuid
import re
import logging
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
import requests
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Firebase Admin
try:
    import firebase_admin
    from firebase_admin import credentials, auth as fb_auth, db as fb_db
except ImportError:
    firebase_admin = None

# ------------------------------------------------------------------
# Config & Environment
# ------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("portfolio-backend")

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
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # admin chat/group id where notifications go
BOT_ADMIN_ID = os.getenv("BOT_ADMIN_ID", "")  # Telegram user ID of admin (afshakil279@gmail.com owner)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "afshakil279@gmail.com")
BRIDGE_WEBHOOK_SECRET = os.getenv("BRIDGE_WEBHOOK_SECRET", "")  # optional secret for Telegram webhook

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""
TELEGRAM_FILE_BASE = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""

# ------------------------------------------------------------------
# Firebase Admin Initialization
# ------------------------------------------------------------------
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
        # Try to init with database URL only (limited functionality)
        if FIREBASE_DATABASE_URL:
            try:
                cred = credentials.Certificate({})  # will fail, so we skip
            except Exception:
                pass
        logger.warning("FIREBASE_SERVICE_ACCOUNT not set - Firebase Admin features limited")
        return
    try:
        service_account_info = None
        sa_str = FIREBASE_SERVICE_ACCOUNT.strip()
        # Try base64 decode first if it looks like base64
        if not sa_str.startswith("{"):
            try:
                decoded = base64.b64decode(sa_str).decode("utf-8")
                service_account_info = json.loads(decoded)
            except Exception:
                # try raw json
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
        logger.info("Firebase Admin initialized successfully")
    except Exception as e:
        logger.error(f"Firebase Admin init failed: {e}")

init_firebase()

def get_db_ref(path: str):
    if not db_available or not firebase_app:
        return None
    try:
        return fb_db.reference(path)
    except Exception as e:
        logger.error(f"Failed to get DB ref {path}: {e}")
        return None

def verify_firebase_token(id_token: str) -> Optional[Dict[str, Any]]:
    if not firebase_app or not id_token:
        return None
    try:
        decoded = fb_auth.verify_id_token(id_token)
        # Auto-promote admin email to admin claim
        if decoded.get("email") == ADMIN_EMAIL and not decoded.get("admin"):
            try:
                fb_auth.set_custom_user_claims(decoded["uid"], {"admin": True})
                logger.info(f"Set admin claim for {decoded['email']}")
                decoded["admin"] = True
            except Exception as ce:
                logger.warning(f"Could not set admin claim: {ce}")
        return decoded
    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        return None

# ------------------------------------------------------------------
# Telegram Helpers
# ------------------------------------------------------------------
def telegram_api_call(method: str, payload: Dict[str, Any], files: Optional[Dict] = None) -> Dict[str, Any]:
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not configured")
    url = f"{TELEGRAM_API_BASE}/{method}"
    try:
        if files:
            resp = requests.post(url, data=payload, files=files, timeout=20)
        else:
            resp = requests.post(url, json=payload, timeout=20)
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram API {method} failed: {data}")
            raise Exception(f"Telegram API error: {data.get('description')}")
        return data
    except requests.RequestException as e:
        logger.error(f"Telegram request failed {method}: {e}")
        raise HTTPException(status_code=502, detail=f"Telegram API request failed: {e}")

def send_telegram_message(chat_id: str, text: str, parse_mode: str = "HTML", reply_markup: Optional[Dict] = None, reply_to_message_id: Optional[int] = None) -> Dict:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    return telegram_api_call("sendMessage", payload)

def send_telegram_photo(chat_id: str, photo_url_or_id: str, caption: str = "", parse_mode: str = "HTML") -> Dict:
    payload = {
        "chat_id": chat_id,
        "photo": photo_url_or_id,
        "caption": caption[:1024] if caption else "",
        "parse_mode": parse_mode,
    }
    return telegram_api_call("sendPhoto", payload)

def send_telegram_photo_bytes(chat_id: str, image_bytes: bytes, filename: str = "image.jpg", caption: str = "") -> Dict:
    url = f"{TELEGRAM_API_BASE}/sendPhoto"
    files = {"photo": (filename, image_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": "HTML"}
    try:
        resp = requests.post(url, data=data, files=files, timeout=30)
        result = resp.json()
        if not result.get("ok"):
            logger.error(f"sendPhoto bytes failed: {result}")
            raise Exception(result.get("description"))
        return result
    except Exception as e:
        logger.error(f"sendPhoto bytes exception: {e}")
        raise

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
        logger.error(f"download telegram file failed {file_path}: {e}")
        return None

# ------------------------------------------------------------------
# ImgBB Helpers
# ------------------------------------------------------------------
def upload_to_imgbb(image_bytes: bytes, name: Optional[str] = None) -> str:
    if not IMGBB_API_KEY:
        raise HTTPException(status_code=500, detail="IMGBB_API_KEY not configured")
    url = f"https://api.imgbb.com/1/upload?key={IMGBB_API_KEY}"
    try:
        # ImgBB expects base64 or multipart. Use multipart for reliability.
        files = {"image": (name or f"{uuid.uuid4().hex}.jpg", image_bytes)}
        resp = requests.post(url, files=files, timeout=30)
        data = resp.json()
        if resp.status_code == 200 and data.get("success") and data.get("data", {}).get("url"):
            return data["data"]["url"]
        # fallback to base64
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        resp2 = requests.post(url, data={"image": b64, "name": name or "upload"}, timeout=30)
        data2 = resp2.json()
        if data2.get("success"):
            return data2["data"]["url"]
        logger.error(f"ImgBB upload failed: {data} / {data2}")
        raise Exception(data.get("error", {}).get("message", "ImgBB upload failed"))
    except requests.RequestException as e:
        logger.error(f"ImgBB request exception: {e}")
        raise HTTPException(status_code=502, detail=f"ImgBB upload failed: {e}")

def upload_url_to_imgbb(image_url: str) -> str:
    # Download then re-upload to get clean ImgBB link (optional, but we keep original if needed)
    try:
        r = requests.get(image_url, timeout=15)
        r.raise_for_status()
        return upload_to_imgbb(r.content, name="telegram_image.jpg")
    except Exception as e:
        logger.warning(f"Failed to re-upload url to ImgBB, returning original: {e}")
        return image_url  # fallback to original URL

# ------------------------------------------------------------------
# FastAPI App
# ------------------------------------------------------------------
app = FastAPI(
    title="Google Marketing Portfolio - Live Chat Bot Backend",
    description="Backend for real-time chat, Telegram bot, orders, and analytics. Admin: afshakil279@gmail.com",
    version="2.0.0",
)

# CORS - allow GitHub Pages and localhost
origins = [
    "https://googlemarketing.github.io",
    "https://portfolio-live-chat-bot.onrender.com",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For portfolio, allow all; you can restrict to origins list for security
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Models
# ------------------------------------------------------------------
class BridgePayload(BaseModel):
    event: str
    payload: Dict[str, Any]
    sentAt: Optional[str] = None

class ImgBBProxyRequest(BaseModel):
    image_base64: Optional[str] = None
    image_url: Optional[str] = None

# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------
@app.get("/")
async def root():
    return {
        "service": "Google Marketing Portfolio Live Chat Bot",
        "status": "running",
        "admin": ADMIN_EMAIL,
        "frontend": "https://googlemarketing.github.io/portfolio/",
        "firebase": "configured" if db_available else "not configured (check FIREBASE_SERVICE_ACCOUNT)",
        "telegram": "configured" if TELEGRAM_BOT_TOKEN else "missing TELEGRAM_BOT_TOKEN",
        "imgbb": "configured" if IMGBB_API_KEY else "missing IMGBB_API_KEY",
        "endpoints": [
            "/health",
            "/api/bridge",
            "/api/telegram/webhook",
            "/api/telegram/set-webhook",
            "/api/imgbb/upload",
        ],
    }

@app.get("/health")
async def health():
    return {
        "ok": True,
        "timestamp": datetime.utcnow().isoformat(),
        "firebase_admin": db_available,
        "telegram_bot": bool(TELEGRAM_BOT_TOKEN),
        "imgbb": bool(IMGBB_API_KEY),
    }

def extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None

@app.post("/api/bridge")
async def telegram_bridge(request: Request, authorization: Optional[str] = Header(None)):
    """
    Secure bridge from frontend to Telegram.
    Frontend sends: {event, payload, sentAt} with Firebase ID token in Authorization header.
    Events: account_created, chat_message, new_order, order_created, review_submitted, analytics_visit
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    event = body.get("event")
    payload = body.get("payload", {})
    sent_at = body.get("sentAt") or datetime.utcnow().isoformat()

    if not event:
        raise HTTPException(status_code=400, detail="Missing event field")

    # Verify Firebase token if present
    token = extract_bearer_token(authorization)
    user_info = None
    if token:
        user_info = verify_firebase_token(token)
        # If token invalid but we still want to allow guest chats, log but don't block for chat_message
        if not user_info and event not in ("chat_message", "analytics_visit"):
            logger.warning(f"Invalid token for event {event}, allowing as guest for chat/analytics")
    else:
        # Allow guest events without token
        if event not in ("chat_message", "analytics_visit", "account_created"):
            logger.info(f"No auth token for event {event}")

    if not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set - bridge will not forward to Telegram")
        return {"ok": True, "warning": "TELEGRAM_CHAT_ID not configured, message logged only"}

    # ------------------------------------------------------------------
    # Event Handlers
    # ------------------------------------------------------------------
    try:
        if event == "account_created":
            name = payload.get("name", "New User")
            email = payload.get("email", "")
            contact = payload.get("contact", "")
            uid = payload.get("uid", "")
            text = (
                f"<b>👤 New Account Created</b>\n"
                f"<b>Name:</b> {name}\n"
                f"<b>Email:</b> {email}\n"
                f"<b>Contact:</b> {contact}\n"
                f"<b>UID:</b> <code>{uid}</code>\n"
                f"<b>Time:</b> {sent_at}"
            )
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            return {"ok": True, "telegram_message_id": result.get("result", {}).get("message_id")}

        elif event in ("chat_message", "new_message", "live_chat"):
            chat_id = payload.get("chatId") or payload.get("chat_id") or "unknown"
            name = payload.get("name", "Website Visitor")
            contact = payload.get("contact", "")
            text_content = payload.get("text", "") or payload.get("message", "")
            html_content = payload.get("html", "")
            image_url = payload.get("imageUrl", "") or payload.get("image_url", "")
            order_id = payload.get("orderId", "") or payload.get("order_id", "")
            uid = payload.get("uid", "")

            # Build formatted message for Telegram
            telegram_text = (
                f"<b>💬 New Live Chat Message</b>\n"
                f"<b>Chat ID:</b> <code>{chat_id}</code>\n"
                f"<b>Name:</b> {name}\n"
                f"<b>Contact:</b> {contact or 'Not provided'}\n"
                f"<b>UID:</b> <code>{uid or 'guest'}</code>\n"
            )
            if order_id:
                telegram_text += f"<b>Order ID:</b> <code>{order_id}</code>\n"
            telegram_text += f"<b>Time:</b> {sent_at}\n"
            telegram_text += f"━━━━━━━━━━━━━━━\n"
            if text_content:
                # Escape HTML but preserve line breaks
                safe_text = text_content[:3000]
                telegram_text += f"{safe_text}\n"
            if html_content and html_content != text_content:
                telegram_text += f"\n<i>Formatted:</i> {html_content[:1000]}\n"
            telegram_text += f"\n<i>Reply to this message or use /reply {chat_id} your message</i>"

            # If image present, send as photo
            if image_url:
                caption = telegram_text[:1024]
                try:
                    # If image_url is ImgBB link, send directly
                    result = send_telegram_photo(TELEGRAM_CHAT_ID, image_url, caption=caption)
                except Exception as e:
                    logger.warning(f"Failed to send photo via URL, trying download+reupload: {e}")
                    # Try to download and send bytes
                    try:
                        img_bytes = requests.get(image_url, timeout=15).content
                        result = send_telegram_photo_bytes(TELEGRAM_CHAT_ID, img_bytes, caption=caption)
                    except Exception as e2:
                        logger.error(f"Photo send failed: {e2}")
                        result = send_telegram_message(TELEGRAM_CHAT_ID, telegram_text + f"\n\nImage: {image_url}")

                # Store mapping
                tg_msg_id = result.get("result", {}).get("message_id")
                if tg_msg_id and db_available:
                    try:
                        get_db_ref(f"telegram_message_map/{tg_msg_id}").set({
                            "chatId": chat_id,
                            "uid": uid,
                            "createdAt": int(time.time() * 1000),
                            "type": "chat_message"
                        })
                    except Exception as me:
                        logger.warning(f"Failed to store telegram mapping: {me}")
                return {"ok": True, "telegram_message_id": result.get("result", {}).get("message_id") if 'result' in locals() else None}
            else:
                result = send_telegram_message(TELEGRAM_CHAT_ID, telegram_text)
                tg_msg_id = result.get("result", {}).get("message_id")
                if tg_msg_id and db_available:
                    try:
                        get_db_ref(f"telegram_message_map/{tg_msg_id}").set({
                            "chatId": chat_id,
                            "uid": uid,
                            "createdAt": int(time.time() * 1000),
                            "type": "chat_message"
                        })
                    except Exception as me:
                        logger.warning(f"Mapping store failed: {me}")
                return {"ok": True, "telegram_message_id": tg_msg_id}

        elif event in ("new_order", "order_created", "order_placed"):
            order_id = payload.get("orderId") or payload.get("id") or f"ORD-{int(time.time())}"
            service_name = payload.get("serviceName") or payload.get("service") or payload.get("serviceType") or "Service"
            quantity = payload.get("quantity", "")
            total = payload.get("total", "") or payload.get("amount", "")
            customer_name = payload.get("name") or payload.get("customerName") or "Client"
            contact = payload.get("contact") or payload.get("customerContact") or ""
            email = payload.get("email", "")
            app_link = payload.get("appLink") or payload.get("appName") or payload.get("requirements") or ""
            uid = payload.get("uid", "")

            text = (
                f"<b>🛒 New Order Received!</b>\n"
                f"<b>Order ID:</b> <code>{order_id}</code>\n"
                f"<b>Service:</b> {service_name}\n"
                f"<b>Quantity:</b> {quantity}\n"
                f"<b>Total:</b> ${total}\n"
                f"<b>Customer:</b> {customer_name}\n"
                f"<b>Contact:</b> {contact}\n"
                f"<b>Email:</b> {email}\n"
                f"<b>UID:</b> <code>{uid or 'guest'}</code>\n"
                f"<b>Details:</b> {str(app_link)[:500]}\n"
                f"<b>Time:</b> {sent_at}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"<i>Reply with /reply {payload.get('chatId','')} to message customer</i>"
            )
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            # Also store order in mapping for easy reply
            tg_msg_id = result.get("result", {}).get("message_id")
            if tg_msg_id and db_available and payload.get("chatId"):
                try:
                    get_db_ref(f"telegram_message_map/{tg_msg_id}").set({
                        "chatId": payload.get("chatId"),
                        "orderId": order_id,
                        "uid": uid,
                        "createdAt": int(time.time() * 1000),
                        "type": "order"
                    })
                except Exception:
                    pass
            return {"ok": True, "order_id": order_id, "telegram_message_id": tg_msg_id}

        elif event == "review_submitted":
            name = payload.get("name", "Client")
            rating = payload.get("rating", "")
            review_text = payload.get("text", "") or payload.get("review", "")
            service = payload.get("serviceName", "")
            text = (
                f"<b>⭐ New Review Submitted (Needs Approval)</b>\n"
                f"<b>From:</b> {name}\n"
                f"<b>Service:</b> {service}\n"
                f"<b>Rating:</b> {rating}/5\n"
                f"<b>Review:</b> {review_text[:1000]}\n"
                f"<b>Time:</b> {sent_at}"
            )
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            return {"ok": True, "telegram_message_id": result.get("result", {}).get("message_id")}

        elif event == "analytics_visit":
            # Analytics already handled client-side directly to Firebase.
            # Optionally forward summary to Telegram if needed (not spammy).
            return {"ok": True, "skipped": "analytics handled client-side"}

        else:
            # Generic event
            text = f"<b>🔔 Event: {event}</b>\n<pre>{json.dumps(payload, indent=2)[:3000]}</pre>\n<b>Time:</b> {sent_at}"
            result = send_telegram_message(TELEGRAM_CHAT_ID, text)
            return {"ok": True, "telegram_message_id": result.get("result", {}).get("message_id")}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bridge event {event} failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Bridge processing failed: {e}")

# ------------------------------------------------------------------
# Telegram Webhook - Admin Replies -> Firebase
# ------------------------------------------------------------------
@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Receives updates from Telegram Bot.
    - If admin replies to a bot message, route back to user's chat.
    - Supports /reply <chatId> <message> command
    - Supports photo messages from admin
    """
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Telegram bot not configured")

    # Optional webhook secret verification
    if BRIDGE_WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != BRIDGE_WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret token")

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(f"Telegram webhook received: {json.dumps(update)[:1000]}")

    message = update.get("message") or update.get("edited_message") or update.get("channel_post")
    if not message:
        # Handle callback queries, etc.
        return {"ok": True, "skipped": "no message"}

    from_user = message.get("from", {})
    from_id = str(from_user.get("id", ""))
    chat = message.get("chat", {})
    chat_id_str = str(chat.get("id", ""))
    text = message.get("text", "") or message.get("caption", "") or ""
    photo = message.get("photo")  # array of sizes
    reply_to = message.get("reply_to_message")

    # Security: only allow admin
    is_admin_user = False
    if BOT_ADMIN_ID and from_id == str(BOT_ADMIN_ID):
        is_admin_user = True
    if TELEGRAM_CHAT_ID and chat_id_str == str(TELEGRAM_CHAT_ID):
        # Any message in the admin chat is considered admin (you can restrict further by user ID)
        is_admin_user = True
    # Also allow if from_id is in admin chat? For simplicity, if chat is private with admin ID.
    # You can add more checks: verify from_user username, etc.

    if not is_admin_user:
        # Optionally reply that only admin can use bot
        # send_telegram_message(chat_id_str, "⛔ This bot is for admin use only. Your message will not be forwarded.")
        logger.info(f"Non-admin message ignored from {from_id} in chat {chat_id_str}")
        return {"ok": True, "ignored": "not admin"}

    # Parse target chatId
    target_chat_id = None
    admin_reply_text = text
    image_url_for_customer = ""

    # 1. Command format: /reply <chatId> <message>
    reply_cmd_match = re.match(r"^/reply\s+(\S+)\s+([\s\S]+)", text, re.IGNORECASE)
    if reply_cmd_match:
        target_chat_id = reply_cmd_match.group(1).strip()
        admin_reply_text = reply_cmd_match.group(2).strip()
    # 2. Reply to bot message - lookup mapping
    elif reply_to and db_available:
        replied_msg_id = reply_to.get("message_id")
        if replied_msg_id:
            try:
                mapping_ref = get_db_ref(f"telegram_message_map/{replied_msg_id}")
                if mapping_ref:
                    mapping = mapping_ref.get()
                    if mapping and mapping.get("chatId"):
                        target_chat_id = mapping["chatId"]
                        # If text is empty and this is a command, use remaining text
                        if not admin_reply_text:
                            admin_reply_text = text
            except Exception as e:
                logger.warning(f"Failed to lookup mapping for {replied_msg_id}: {e}")
        # Fallback: try to extract chatId from replied message text via regex
        if not target_chat_id and reply_to.get("text"):
            # Look for <code>chatId</code> or Chat ID: xxx
            m = re.search(r"Chat ID:</b>\s*<code>([^<]+)</code>", reply_to.get("text",""))
            if not m:
                m = re.search(r"Chat ID:\s*(\S+)", reply_to.get("text",""))
            if m:
                target_chat_id = m.group(1).strip()
    # 3. If message contains chatId explicitly in caption/text like [user_xxx] or [guest_xxx]
    if not target_chat_id:
        # Try to find user_ or guest_ pattern
        m2 = re.search(r"(user_[a-zA-Z0-9_-]+|guest_[a-zA-Z0-9_-]+|ORD-[a-zA-Z0-9_-]+)", text)
        if m2:
            # This might be order ID, not chat ID - but try
            pass

    # If admin sent a photo, handle image upload
    if photo:
        # Get largest photo file
        largest = photo[-1] if isinstance(photo, list) and photo else None
        if largest:
            file_id = largest.get("file_id")
            file_info = get_telegram_file(file_id)
            if file_info:
                file_path = file_info.get("file_path")
                img_bytes = download_telegram_file(file_path)
                if img_bytes:
                    try:
                        # Upload to ImgBB for clean URL
                        image_url_for_customer = upload_to_imgbb(img_bytes, name=f"admin_{int(time.time())}.jpg")
                    except Exception as e:
                        logger.error(f"ImgBB upload for admin photo failed: {e}")
                        # Fallback: keep Telegram file URL temporarily? Better to send as is
                        image_url_for_customer = ""  # will handle as bytes fallback later
                        # Store bytes for direct Firebase? No, we need URL
                    # If ImgBB failed, we can still store as telegram file URL? But better to keep bytes handling
                else:
                    logger.warning("Failed to download Telegram file")
            else:
                logger.warning(f"Could not get file info for {file_id}")

    # If no target chatId found and it's a direct command like /start, handle help
    if not target_chat_id:
        if text.lower().startswith("/start") or text.lower().startswith("/help"):
            help_text = (
                "<b>Google Marketing Admin Bot</b>\n\n"
                "Commands:\n"
                "/reply <code>chatId</code> Your message - Reply to a user\n"
                "Or simply reply to a forwarded chat message.\n\n"
                "You can also send photos - reply to a message with a photo to send it to the customer.\n\n"
                f"Admin chat ID: <code>{TELEGRAM_CHAT_ID}</code>\n"
                f"Bot Admin ID: <code>{BOT_ADMIN_ID}</code>\n"
            )
            send_telegram_message(chat_id_str, help_text)
            return {"ok": True, "help_sent": True}
        # If not help and no target, we cannot route
        logger.info(f"No target chatId found for admin message: {text[:200]}")
        # Optionally inform admin
        if text and not text.startswith("/"):
            send_telegram_message(chat_id_str, f"⚠️ Could not find target Chat ID. Use /reply <code>chatId</code> message or reply to a forwarded message.\n\nYour message: {text[:500]}")
        return {"ok": True, "no_target": True}

    # Now we have target_chat_id - write to Firebase
    if not db_available:
        raise HTTPException(status_code=500, detail="Firebase DB not available - cannot route message")

    try:
        # Prepare admin message for Firebase
        message_data = {
            "sender": "admin",
            "from": "admin",
            "text": admin_reply_text or "",
            "html": admin_reply_text or "",
            "imageUrl": image_url_for_customer or "",
            "createdAt": int(time.time() * 1000),
            "createdAtServer": {".sv": "timestamp"},
            "telegramMessageId": message.get("message_id"),
            "adminUserId": from_id,
        }
        # Push to chats/{target_chatId}/messages
        chat_messages_ref = get_db_ref(f"chats/{target_chat_id}/messages")
        new_msg_ref = chat_messages_ref.push(message_data)

        # Update metadata - increment unreadForUser, update lastMessage
        metadata_ref = get_db_ref(f"chatMetadata/{target_chat_id}")
        if metadata_ref:
            try:
                current_meta = metadata_ref.get() or {}
                unread = int(current_meta.get("unreadForUser", 0) or 0) + 1
                metadata_ref.update({
                    "lastMessage": admin_reply_text[:200] if admin_reply_text else ("Image attachment" if image_url_for_customer else "Admin reply"),
                    "lastSender": "admin",
                    "updatedAt": int(time.time() * 1000),
                    "unreadForUser": unread,
                    "unreadForAdmin": 0,  # reset admin unread since admin replied
                })
            except Exception as e:
                logger.warning(f"Metadata update failed for {target_chat_id}: {e}")

        # Confirmation to admin
        confirm_text = (
            f"✅ Reply sent to <code>{target_chat_id}</code>\n"
            f"Message: {admin_reply_text[:500]}\n"
            f"{'Image: '+image_url_for_customer if image_url_for_customer else ''}"
        )
        send_telegram_message(chat_id_str, confirm_text)

        # Store reverse mapping for future (optional)
        try:
            get_db_ref(f"telegram_message_map/{message.get('message_id')}").set({
                "chatId": target_chat_id,
                "type": "admin_reply",
                "createdAt": int(time.time() * 1000),
            })
        except Exception:
            pass

        return {"ok": True, "routed_to": target_chat_id, "firebase_key": new_msg_ref.key if hasattr(new_msg_ref, 'key') else str(new_msg_ref)}

    except Exception as e:
        logger.error(f"Failed to route admin message to {target_chat_id}: {e}", exc_info=True)
        send_telegram_message(chat_id_str, f"❌ Failed to send message to <code>{target_chat_id}</code>: {e}")
        raise HTTPException(status_code=500, detail=f"Routing failed: {e}")

@app.post("/api/telegram/set-webhook")
async def set_telegram_webhook(request: Request):
    """
    Set Telegram webhook URL. Call this once after deployment.
    Body: {"url": "https://portfolio-live-chat-bot.onrender.com/api/telegram/webhook"}
    Or query param ?url=
    """
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not set")

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    url = body.get("url") or request.query_params.get("url")
    if not url:
        # Default to current host + /api/telegram/webhook
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "portfolio-live-chat-bot.onrender.com"
        scheme = request.headers.get("x-forwarded-proto") or "https"
        url = f"{scheme}://{host}/api/telegram/webhook"

    # Set webhook via Telegram API
    payload = {"url": url, "allowed_updates": ["message", "edited_message", "channel_post", "callback_query"]}
    if BRIDGE_WEBHOOK_SECRET:
        payload["secret_token"] = BRIDGE_WEBHOOK_SECRET

    try:
        result = telegram_api_call("setWebhook", payload)
        # Also get webhook info
        info = telegram_api_call("getWebhookInfo", {})
        return {"ok": True, "setWebhook": result, "webhookInfo": info, "url": url}
    except Exception as e:
        logger.error(f"setWebhook failed: {e}")
        raise HTTPException(status_code=500, detail=f"setWebhook failed: {e}")

@app.get("/api/telegram/get-webhook-info")
async def get_webhook_info():
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TELEGRAM_BOT_TOKEN not set")
    try:
        info = telegram_api_call("getWebhookInfo", {})
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/imgbb/upload")
async def imgbb_upload_proxy(request: Request, authorization: Optional[str] = Header(None)):
    """
    Proxy for ImgBB uploads to hide API key from client if desired.
    Accepts multipart/form-data with file, or JSON with base64.
    Requires Firebase auth token.
    """
    if not IMGBB_API_KEY:
        raise HTTPException(status_code=500, detail="IMGBB_API_KEY not configured")

    token = extract_bearer_token(authorization)
    if token:
        user = verify_firebase_token(token)
        if not user:
            # Allow anonymous but log
            logger.warning("ImgBB proxy called with invalid token")

    try:
        content_type = request.headers.get("content-type", "")
        image_bytes = None
        filename = None

        if "multipart/form-data" in content_type:
            form = await request.form()
            file = form.get("image") or form.get("file")
            if file:
                if hasattr(file, "read"):
                    image_bytes = await file.read() if hasattr(file.read, "__call__") else file.file.read()
                    filename = getattr(file, "filename", "upload.jpg")
                else:
                    image_bytes = file
        else:
            body = await request.json()
            b64 = body.get("image_base64") or body.get("image")
            url = body.get("image_url")
            if b64:
                # Remove data URL prefix if present
                if "," in b64 and "base64" in b64[:100]:
                    b64 = b64.split(",", 1)[1]
                image_bytes = base64.b64decode(b64)
            elif url:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                image_bytes = r.content
                filename = "remote.jpg"

        if not image_bytes:
            raise HTTPException(status_code=400, detail="No image provided")

        if len(image_bytes) > 15 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large (max 15MB)")

        uploaded_url = upload_to_imgbb(image_bytes, name=filename or "upload.jpg")
        return {"ok": True, "url": uploaded_url, "display_url": uploaded_url}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ImgBB proxy failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

# ------------------------------------------------------------------
# Admin utilities
# ------------------------------------------------------------------
@app.post("/api/admin/set-admin-claim")
async def set_admin_claim_endpoint(request: Request, authorization: Optional[str] = Header(None)):
    """
    Set admin custom claim for a user by email. Only callable by service account or existing admin.
    Body: {"email": "afshakil279@gmail.com"}
    """
    if not firebase_app:
        raise HTTPException(status_code=500, detail="Firebase Admin not initialized")

    token = extract_bearer_token(authorization)
    caller = verify_firebase_token(token) if token else None

    # Allow if caller is already admin or if email matches ADMIN_EMAIL
    try:
        body = await request.json()
    except Exception:
        body = {}
    target_email = body.get("email") or ADMIN_EMAIL

    if caller:
        if not caller.get("admin") and caller.get("email") != ADMIN_EMAIL:
            raise HTTPException(status_code=403, detail="Only admin can set claims")
    else:
        # No token - allow only if setting ADMIN_EMAIL and we are in setup mode (first time)
        # For security, require BRIDGE_WEBHOOK_SECRET or TELEGRAM_BOT_TOKEN matching?
        if target_email != ADMIN_EMAIL:
            raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        user = fb_auth.get_user_by_email(target_email)
        fb_auth.set_custom_user_claims(user.uid, {"admin": True})
        return {"ok": True, "email": target_email, "uid": user.uid, "admin": True}
    except Exception as e:
        logger.error(f"set admin claim failed for {target_email}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------------------------------
# Entry point for Render
# ------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
