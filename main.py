const express = require('express');
const admin = require('firebase-admin');

const app = express();
app.use(express.json());

// Render-এর Environment থেকে ফায়ারবেস এডমিন কি নিয়ে কানেক্ট করা
const serviceAccount = JSON.parse(process.env.FIREBASE_SERVICE_ACCOUNT);
admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});

// Render-এর Environment থেকে টেলিগ্রাম টোকেন এবং চ্যাট আইডি নেওয়া
const BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const CHAT_ID = process.env.TELEGRAM_CHAT_ID;

// বটের আসল লজিক (API/Webhook)
app.get('/', (req, res) => {
    res.send("Bot is running perfectly on Render!");
});

app.post('/', (req, res) => {
    // আপনার টেলিগ্রাম মেসেজ রিসিভ ও সেন্ড করার কোড এখানে থাকবে
    res.status(200).send("OK");
});

// Render-এর জন্য পোর্ট সেটআপ করে সার্ভার চালু করা
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
    console.log(`Bot server is successfully running on port ${PORT}`);
});
