# Google Marketing Portfolio - Real-Time Live Chat, Workspace, Telegram Bot & Admin Control

**Live Portfolio:** https://googlemarketing.github.io/portfolio/  
**Backend API:** https://portfolio-live-chat-bot.onrender.com  
**Admin Email:** afshakil279@gmail.com

Production-ready implementation of the Master Project Specification.

## Architecture Overview

| Layer | Technology | Purpose |
|-------|------------|---------|
| Frontend | `index.html` (Static, Vanilla JS + Firebase SDK 10) | Portfolio, live chat widget, auth, workspace, calculator, reviews ticker |
| Backend | Python FastAPI `backend/main.py` | Firebase Admin verification, Telegram Bot webhook, ImgBB image handling, bridge |
| Database & Auth | Firebase Realtime Database + Firebase Auth | Users, services, orders, chats, reviews, notifications, analytics, telegram mappings |
| Image Hosting | ImgBB API `https://api.imgbb.com/1/upload` | Chat images, profile photos, admin replies |
| Deployment | GitHub Actions `.github/workflows/deploy.yml` + Render | Auto-deploy frontend to GitHub Pages, backend to Render |
| Bot | Telegram Bot API | Real-time admin notifications and reply routing |

### Data Flow

```
Visitor -> index.html
  -> Firebase Auth (Google / Email)
  -> Firebase RTDB (chat, orders, reviews, analytics)
  -> Bridge /api/bridge (with ID token) -> Telegram Bot -> Admin Telegram (TELEGRAM_CHAT_ID)
Admin Reply in Telegram -> /api/telegram/webhook -> Firebase RTDB chats/{chatId}/messages -> Live update in user chat box
Image upload -> ImgBB -> URL stored in Firebase -> Rendered in both chat UIs and Telegram
```

## 1. Frontend - index.html Features

### Core Preservation
- Original dark premium theme, Outfit + Plus Jakarta Sans, ambient glow, navbar, hero, trust counter, services, guarantee, reviews gallery, FAQ, contact, footer preserved 1:1. No layout shift.

### Live Chat Widget
- Floating button `gm-chat-launcher` bottom-right, accessible to guest and logged-in users.
- **Guest:** Firebase anonymous auth `signInAnonymously`. Red dot badge `gmChatBadge` shows when `chatMetadata/{chatId}/unreadForUser > 0` while away.
- **Registered:** Notifications pushed to `users/{uid}/notifications` and shown in workspace notification bar with unread count.
- Formatting toolbar: bold, italic, underline, strike, link. Image attach via ImgBB.

### Moving Reviews (Play Store Style)
- Below service cards: `gm-live-catalog` with infinite horizontal marquee `gm-catalog-track` (42s linear infinite, pauses on hover).
- Each service card shows Play Store style review marquee `gm-review-marquee` with approved reviews only.
- Ticker duplicates DOM nodes to ensure seamless loop.

### Authentication & Profile
- Redesigned modal `gmAuthOverlay` with kicker "Secure Client Portal", modern clean UI, 46px min-height inputs, focus outline.
- **Google Auth:** `signInWithPopup` + `linkWithPopup` for anonymous upgrade. Auto-fills name, photoURL, email into profile and contact fields. Editable later.
- **Manual:** Name, Contact (Email/WhatsApp/Telegram), optional photo upload via ImgBB.
- Menu: Sign In / Sign Up / Sign Out / Edit Profile (Save Profile).

### User Workspace / Dashboard
- **Direct Entry:** On `onAuthStateChanged` if user exists and not anonymous, `openWorkspace()` immediately. No redundant "Open My Workspace" button.
- **Top Bar:** Discreet profile avatar + menu, adaptive: `gm-work-top` with search, menu, profile, portfolio buttons. Mobile collapses to icons.
- **Back/Close:** `gmWorkspaceBackButton` and `workspaceBack()` returns to overview or portfolio. `closeWorkspace()` always available.
- **Search:** Search icon expands into `gmWorkspaceSearch` absolute panel with live filtering of tabs, actions, services, admin sections (18 results max).
- **Calculator:** Top widget in `calculator` tab: select service, quantity, unit price, total `$` real-time `updateCalculator()`. `prepareOrderFromCalculator()` pre-fills order form.
- **Service Cards:** Professional green checkmarks `li i {color: #10b981}` next to features. Managed via Firebase `services`.
- **Ordering:** Quantity, service type, contact details -> submit -> generates unique Order ID `GM-YYYY-XXXX` / `ORD_timestamp_random`. Triggers bridge `new_order` -> Telegram notification with Order ID + contact info.
- **Order Tracking:** Each order has unique ID, status `pending`, `in_progress`, `completed`.

### Review System
- Logged-in users: rate (1-5 stars) + written review -> `reviews` with `approved: false`.
- **Admin Moderation:** Only user with email `afshakil279@gmail.com` gets custom claim `admin: true`. Admin panel `Review Moderation` can approve/edit/publish. Published reviews appear in ticker and dashboard Play Store section.

### Visitor Analytics
- `trackVisit()` on load: calls `https://ipapi.co/json/` (3.5s timeout) to get country/region, captures browser, device, referrer, page, screen size, sessionId. Writes to `analytics/visits` via `push(ref(db,'analytics/visits'), {...})`. Raw IP not stored.
- Admin panel shows total visits, countries, sessions, today. Export CSV.

### Responsive
- Media queries for 900px and 640px: compact top bar, 2-col stats, 1-col service grid, chat window `calc(100vw - 16px)`, no overflow.

## 2. Backend - main.py

### Endpoints
- `GET /` and `GET /health` - status
- `POST /api/bridge` - secure bridge, requires Firebase ID token in `Authorization: Bearer <token>`. Events: `account_created`, `chat_message`, `new_order`, `review_submitted`
- `POST /api/telegram/webhook` - Telegram bot webhook. Routes admin replies back to Firebase.
- `POST /api/telegram/set-webhook` - set webhook URL to `https://portfolio-live-chat-bot.onrender.com/api/telegram/webhook`
- `POST /api/imgbb/upload` - proxy upload to hide key if needed (optional, frontend can also upload directly)
- `POST /api/admin/set-admin-claim` - set admin claim for `afshakil279@gmail.com`

### Telegram Flow
**User -> Admin:**
Frontend writes to Firebase `chats/{chatId}/messages` and calls bridge. Bridge sends formatted HTML message to `TELEGRAM_CHAT_ID`:
```
💬 New Live Chat Message
Chat ID: <code>user_xxx</code>
Name: ...
Order ID: ...
Message: ...
```
If imageUrl present, `sendPhoto` with caption. Stores `telegram_message_map/{telegram_message_id} = {chatId}` for reply routing.

**Admin -> User:**
Admin can:
1. Reply directly to forwarded message in Telegram -> webhook looks up `telegram_message_map` for replied message ID -> finds chatId
2. Use command `/reply <chatId> Your message here`

If admin sends photo:
- `getFile` -> download via `https://api.telegram.org/file/bot<token>/<file_path>` -> upload to ImgBB -> get clean URL -> write to Firebase `chats/{chatId}/messages` with `sender: admin`, `imageUrl`

Then updates `chatMetadata/{chatId}`: `unreadForUser +=1`, `lastMessage`, `lastSender: admin`.

### ImgBB Integration
- Frontend `uploadImage(file)`: `FormData` POST to `https://api.imgbb.com/1/upload?key=...`
- Backend `upload_to_imgbb(bytes)`: multipart upload, fallback base64.
- Admin Telegram images also go through ImgBB to get clean URL for chat box rendering.

### Firebase Admin
- `FIREBASE_SERVICE_ACCOUNT` env var: JSON string or base64-encoded JSON.
- Verifies ID tokens, auto-sets admin claim for `ADMIN_EMAIL`.
- Database access via `firebase_admin.db`.

## 3. Environment Variables (Render & GitHub Secrets)

Set in Render dashboard and GitHub repo Settings -> Secrets and variables -> Actions:

```
FIREBASE_API_KEY
FIREBASE_AUTH_DOMAIN
FIREBASE_DATABASE_URL
FIREBASE_PROJECT_ID
FIREBASE_STORAGE_BUCKET
FIREBASE_MESSAGING_SENDER_ID
FIREBASE_APP_ID
FIREBASE_SERVICE_ACCOUNT  # full JSON or base64
IMGBB_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID          # e.g. -1001234567890 or your user ID
BOT_ADMIN_ID              # Telegram user ID of admin (get via @userinfobot)
ADMIN_EMAIL=afshakil279@gmail.com
TELEGRAM_BRIDGE_URL=https://portfolio-live-chat-bot.onrender.com/api/bridge
BRIDGE_WEBHOOK_SECRET     # optional secret for webhook verification
```

## 4. Deployment

### Frontend - GitHub Pages
`.github/workflows/deploy.yml`:
- On push to `main`, replaces placeholders `__FIREBASE_API_KEY__` etc in `index.html` with secrets.
- Uses `actions/configure-pages` and `actions/deploy-pages`.
- Notifies admin via Telegram on success.

Enable Pages: Repo Settings -> Pages -> Source: GitHub Actions.

### Backend - Render
1. Create new Web Service on Render, connect GitHub repo, root dir `backend` or `portfolio-project/backend`.
2. Runtime: Python 3.11
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add env vars above.
6. After deploy, set Telegram webhook:
```bash
curl -X POST https://portfolio-live-chat-bot.onrender.com/api/telegram/set-webhook \
  -H "Content-Type: application/json" \
  -d '{"url":"https://portfolio-live-chat-bot.onrender.com/api/telegram/webhook"}'
```
Verify:
```bash
curl https://portfolio-live-chat-bot.onrender.com/api/telegram/get-webhook-info
```

### Firebase Rules (Realtime Database)
Set in Firebase Console -> Realtime Database -> Rules:

```json
{
  "rules": {
    "users": {
      "$uid": {
        ".read": "auth != null && (auth.uid == $uid || auth.token.admin == true)",
        ".write": "auth != null && (auth.uid == $uid || auth.token.admin == true)"
      }
    },
    "services": {
      ".read": true,
      ".write": "auth != null && auth.token.admin == true"
    },
    "orders": {
      ".read": "auth != null && (data.child('uid').val() == auth.uid || auth.token.admin == true)",
      ".write": "auth != null",
      ".indexOn": ["uid", "status"]
    },
    "reviews": {
      ".read": true,
      ".write": "auth != null",
      ".indexOn": ["uid", "approved"]
    },
    "chats": {
      "$chatId": {
        "messages": {
          ".read": "auth != null || $chatId.startsWith('guest_')",
          ".write": "auth != null || $chatId.startsWith('guest_')",
          ".indexOn": ["createdAt"]
        }
      }
    },
    "chatMetadata": {
      "$chatId": {
        ".read": "auth != null || $chatId.startsWith('guest_')",
        ".write": "auth != null || $chatId.startsWith('guest_')"
      }
    },
    "analytics": {
      "visits": {
        ".read": "auth != null && auth.token.admin == true",
        ".write": true
      }
    },
    "telegram_message_map": {
      ".read": "auth != null && auth.token.admin == true",
      ".write": "auth != null && auth.token.admin == true"
    },
    "notifications": {
      "$uid": {
        ".read": "auth != null && auth.uid == $uid",
        ".write": "auth != null && auth.token.admin == true"
      }
    }
  }
}
```

Set admin claim once:
```bash
curl -X POST https://portfolio-live-chat-bot.onrender.com/api/admin/set-admin-claim \
  -H "Content-Type: application/json" \
  -d '{"email":"afshakil279@gmail.com"}'
```
Or via Firebase Admin SDK in Python console:
```python
import firebase_admin
from firebase_admin import credentials, auth
cred = credentials.Certificate("serviceAccount.json")
firebase_admin.initialize_app(cred)
user = auth.get_user_by_email("afshakil279@gmail.com")
auth.set_custom_user_claims(user.uid, {"admin": True})
```

## 5. Testing Checklist

- [ ] Guest chat: open site, click chat icon, send message, check Telegram receives with chatId, reply in Telegram via reply, check guest sees admin reply and red dot appears when chat closed.
- [ ] Google Sign-Up: click Profile -> Continue with Google, check name/photo/email auto-filled, workspace opens directly.
- [ ] Manual Sign-Up: create account, upload photo (ImgBB), save profile.
- [ ] Calculator: select service, change quantity, total updates instantly in dollars.
- [ ] Order: submit order, check Order ID generated, Telegram receives Order ID + contact, order appears in My Orders.
- [ ] Reviews: submit review as user, appears pending, login as admin `afshakil279@gmail.com`, approve, check ticker on portfolio page and dashboard.
- [ ] Image: send image in chat, check ImgBB URL rendered in chat and forwarded as image to Telegram.
- [ ] Admin image reply: send image in Telegram as reply to user message, check it appears in user chat box.
- [ ] Analytics: visit site from different country (VPN), check admin panel Visit Analytics.
- [ ] Responsive: test on desktop 1920px and mobile 375px, no overflow, compact layout.
- [ ] Search: in workspace click search icon, type "calculator", click result, jumps to section.

## 6. File Structure

```
portfolio-project/
├── index.html                         # Production frontend (deploy to GitHub Pages)
├── backend/
│   ├── main.py                        # FastAPI backend for Render
│   └── requirements.txt
├── .github/
│   └── workflows/
│       └── deploy.yml                 # GitHub Actions deploy to Pages
├── firebase-database-rules.json
├── render.yaml                        # Render blueprint (optional)
├── .env.example
└── README.md
```

## 7. Security Notes

- Never put `TELEGRAM_BOT_TOKEN` or `FIREBASE_SERVICE_ACCOUNT` in `index.html`. Only public Firebase web config and ImgBB client key go to frontend.
- ImgBB API key is public client key; restrict domain in ImgBB dashboard and monitor usage.
- Firebase rules enforce admin-only writes for services, analytics read.
- Bridge verifies Firebase ID token before forwarding to Telegram.
- Telegram webhook checks admin ID (`BOT_ADMIN_ID` or `TELEGRAM_CHAT_ID`).

## 8. Future Improvements

- Add rate limiting to `/api/bridge` (e.g., 10 messages/min per IP).
- Add email notifications via Firebase Functions.
- Add Stripe/Bkash payment integration for orders.
- Add PWA support for workspace.

---

Built for Google Marketing - Authentic Review Provider & Google Map SEO Specialist.
Admin: afshakil279@gmail.com
