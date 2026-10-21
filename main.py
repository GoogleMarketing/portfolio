import os
import json
from flask import Flask, request
import firebase_admin
from firebase_admin import credentials

app = Flask(__name__)

# Render-এর Environment থেকে ফায়ারবেস এডমিন কি নিয়ে কানেক্ট করা
service_account_info = json.loads(os.environ.get("FIREBASE_SERVICE_ACCOUNT"))
cred = credentials.Certificate(service_account_info)
firebase_admin.initialize_app(cred)

# Render-এর Environment থেকে টেলিগ্রাম টোকেন এবং চ্যাট আইডি নেওয়া
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

@app.route('/', methods=['GET'])
def home():
    return "Python Bot is running perfectly on Render!"

@app.route('/', methods=['POST'])
def webhook():
    # আপনার টেলিগ্রাম মেসেজ রিসিভ ও সেন্ড করার কোড এখানে থাকবে
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
