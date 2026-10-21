import os
import json
import requests
import re
import time
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials, db

app = Flask(__name__)

# Render Environment থেকে ফায়ারবেস এডমিন কানেক্ট
service_account_info = json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT"))
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred, {
    'databaseURL': os.environ.get("FIREBASE_DATABASE_URL")
})

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text})

@app.route('/', methods=['GET'])
def home():
    return "Backend is running!"

@app.route('/', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if not data:
            return "No data", 400

        # ওয়েবসাইট থেকে মেসেজ আসলে (টেলিগ্রামে পাঠানো)
        if "website_message" in data:
            user_msg = data.get("website_message")
            uid = data.get("uid", "unknown")
            name = data.get("name", "Guest")
            email = data.get("email", "No Email")
            
            alert_text = f"🌐 Website Msg from {name}\nEmail: {email}\nUID: `{uid}`\n\n💬 {user_msg}\n\n(রিপ্লাই দিতে এই মেসেজটি Reply করুন অথবা লিখুন: /reply {uid} আপনার_মেসেজ)"
            send_telegram_message(CHAT_ID, alert_text)
            return {"status": "success"}, 200

        # টেলিগ্রাম থেকে মেসেজ আসলে (ওয়েবসাইটে পাঠানো)
        if "message" in data:
            chat_id = str(data["message"]["chat"]["id"])
            text = data["message"].get("text", "")
            
            if chat_id != str(CHAT_ID):
                return "OK", 200

            if text.startswith("/start"):
                send_telegram_message(chat_id, "বট প্রস্তুত! ওয়েবসাইট থেকে চ্যাট আসলে এখানেই পাবেন।")
                return "OK", 200

            # কমান্ড দিয়ে রিপ্লাই (/reply UID Message)
            if text.startswith("/reply "):
                parts = text.split(" ", 2)
                if len(parts) >= 3:
                    target_uid = parts[1]
                    reply_msg = parts[2]
                    
                    ref = db.reference(f"chats/{target_uid}/messages")
                    ref.push({
                        "from": "admin",
                        "text": reply_msg,
                        "time": int(time.time() * 1000),
                        "name": "Google Marketing"
                    })
                    send_telegram_message(chat_id, "✅ ওয়েবসাইটে রিয়েল-টাইম রিপ্লাই পাঠানো হয়েছে!")
                else:
                    send_telegram_message(chat_id, "⚠️ নিয়ম: /reply UserID Message")
            
            # সরাসরি Reply অপশন ব্যবহার করলে
            elif "reply_to_message" in data["message"]:
                reply_text = data["message"]["reply_to_message"].get("text", "")
                match = re.search(r"UID:\s*`?([a-zA-Z0-9_]+)`?", reply_text)
                if match:
                    target_uid = match.group(1)
                    ref = db.reference(f"chats/{target_uid}/messages")
                    ref.push({
                        "from": "admin",
                        "text": text,
                        "time": int(time.time() * 1000),
                        "name": "Google Marketing"
                    })
                    send_telegram_message(chat_id, "✅ ওয়েবসাইটে রিয়েল-টাইম রিপ্লাই পাঠানো হয়েছে!")

        return "OK", 200
    except Exception as e:
        print(f"Error: {e}")
        return str(e), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
