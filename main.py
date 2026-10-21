import os
import json
import requests
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials, firestore

app = Flask(__name__)

# ১. Render-এর Environment থেকে ফায়ারবেস এডমিন কি নিয়ে কানেক্ট করা
service_account_info = json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT"))
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred)

# ফায়ারস্টোর ডাটাবেস ইনিশিয়ালাইজ করা (পোর্টফোলিও ডেটা বা চ্যাট হিস্ট্রি সেভ করার জন্য)
db = firestore.client()

# ২. Render-এর Environment থেকে টেলিগ্রাম টোকেন এবং চ্যাট আইডি নেওয়া
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
    return "Portfolio Live Chat Bot is running perfectly on Render!"

@app.route('/', methods=['POST'])
def webhook():
    """টেলিগ্রাম থেকে ওয়েবহুক রিসিভ করে প্রসেস করার মূল লজিক"""
    try:
        update = request.get_json()
        
        if update and "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")
            user_name = update["message"]["from"].get("first_name", "User")
            
            # কেউ /start লিখলে ওয়েলকাম মেসেজ পাঠানো
            if text == "/start":
                welcome_msg = (
                    f"হ্যালো {user_name}! পোর্টফোলিও লাইভ চ্যাট বটে আপনাকে স্বাগতম। "
                    "আপনার যেকোনো জিজ্ঞাসা বা মেসেজ এখানে পাঠাতে পারেন, এটি সরাসরি আমাদের কাছে পৌঁছে যাবে।"
                )
                send_telegram_message(chat_id, welcome_msg)
            else:
                # সাধারণ মেসেজ আসলে তা ফায়ারবেসে সেভ করা বা অটো-রেসপন্স দেওয়া
                # চাইলে এখানে ফায়ারবেসে ডেটা সংরক্ষণের কোড রাখতে পারেন
                reply_msg = "আপনার মেসেজটি সফলভাবে পাওয়া গেছে! খুব শীঘ্রই আপনার সাথে যোগাযোগ করা হবে।"
                send_telegram_message(chat_id, reply_msg)
                
        return "OK", 200
    except Exception as e:
        print(f"Error handling webhook: {e}")
        return "Error", 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
