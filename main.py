import os
import json
import requests
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# ১. ফায়ারবেস এডমিন কানেক্ট করা
service_account_info = json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT"))
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ২. টেলিগ্রাম টোকেন এবং চ্যাট আইডি নেওয়া
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram_message(chat_id, text):
    """টেলিগ্রামে মেসেজ পাঠানোর ফাংশন"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    requests.post(url, json=payload)

@app.route('/', methods=['GET'])
def home():
    return "Portfolio Live Chat Bot is running!"

@app.route('/', methods=['POST'])
def webhook():
    """ওয়েবসাইট বা টেলিগ্রাম থেকে আসা ডেটা হ্যান্ডেল করার মূল লজিক"""
    try:
        data = request.get_json()
        
        if not data:
            return "No data", 400

        # কেইস ১: যদি মেসেজটি সরাসরি ওয়েবসাইট থেকে আসে (পোর্টফোলিও চ্যাট ফর্ম)
        if "website_message" in data:
            user_msg = data.get("website_message")
            user_email = data.get("email", "Unknown")
            
            # ওয়েবসাইটের মেসেজটি সরাসরি আপনার টেলিগ্রাম চ্যাট আইডিতে পাঠিয়ে দেবে
            alert_text = f"🌐 নতুন ওয়েবসাইট মেসেজ!\n\nইমেইল: {user_email}\nমেসেজ: {user_msg}"
            send_telegram_message(CHAT_ID, alert_text)
            
            # চাইলে ফায়ারবেসেও সেভ করে রাখতে পারেন
            db.collection("messages").add({
                "email": user_email,
                "message": user_msg,
                "source": "website"
            })
            
            return {"status": "success", "message": "Sent to Telegram"}, 200

        # কেইস ২: যদি মেসেজটি টেলিগ্রাম থেকে আসে (বট চ্যাট)
        if "message" in data:
            chat_id = data["message"]["chat"]["id"]
            text = data["message"].get("text", "")
            
            if text == "/start":
                send_telegram_message(chat_id, "বট সফলভাবে চালু আছে! আপনার পোর্টফোলিও চ্যাট রিসিভ করার জন্য প্রস্তুত।")
            else:
                send_telegram_message(chat_id, f"আপনার মেসেজটি পাওয়া গেছে: {text}")

        return "OK", 200

    except Exception as e:
        print(f"Error: {e}")
        return str(e), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
