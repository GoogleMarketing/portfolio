const functions = require('firebase-functions');
const admin = require('firebase-admin');
const axios = require('axios');
admin.initializeApp();

// Telegram token is NEVER hardcoded - comes from Secrets
// Set via: firebase functions:config:set telegram.token="xxx" telegram.chat_id="yyy"

exports.onNewLead = functions.firestore.document('leads/{leadId}').onCreate(async (snap) => {
  const lead = snap.data();
  const botToken = functions.config().telegram?.token;
  const chatId = functions.config().telegram?.chat_id;
  
  if(!botToken || !chatId) {
    console.log('Telegram config missing');
    return;
  }

  const text = `🚀 New Lead\nService: ${lead.service || lead.appName}\nContact: ${lead.contact}\nMessage: ${lead.message}`;

  await axios.post(`https://api.telegram.org/bot${botToken}/sendMessage`, {
    chat_id: chatId,
    text: text
  });
});

exports.telegramWebhook = functions.https.onRequest(async (req, res) => {
  const message = req.body.message;
  
  if(message && message.text && message.text.startsWith('/reply')){
    const parts = message.text.split(' ');
    const userId = parts[1];
    const replyText = parts.slice(2).join(' ');
    await admin.database().ref(`chats/${userId}/messages`).push({
      from: 'admin',
      text: replyText,
      time: Date.now()
    });
  }
  res.send('ok');
});
