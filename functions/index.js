const functions = require("firebase-functions");
const admin = require("firebase-admin");
const axios = require("axios");

admin.initializeApp();

const db = admin.database();

// 1. When new chat message from client, send to Telegram
exports.onNewChatMessage = functions.database.ref("/chats/{uid}/messages/{msgId}").onCreate(async (snap, context) => {
  const msg = snap.val();
  if (msg.from !== 'client') return null; // Only notify for client messages

  try {
    const token = functions.config().telegram?.token || process.env.TELEGRAM_BOT_TOKEN;
    const chatId = functions.config().telegram?.chat_id || functions.config().telegram?.admin_id || process.env.TELEGRAM_CHAT_ID;

    if (!token || !chatId) {
      console.log("Telegram config missing - token or chat_id not set");
      return null;
    }

    const uid = context.params.uid;
    const name = msg.name || msg.email || 'Guest';
    const email = msg.email || 'guest';
    const text = msg.text || '';

    const message = `📩 *New Live Chat Message*\n\n👤 *Name:* ${name}\n📧 *Email:* ${email}\n🆔 *User ID:* \`${uid}\`\n\n💬 *Message:*\n${text}\n\n---\nReply quickly with:\n\`/reply ${uid} Your reply here\``;

    await axios.post(`https://api.telegram.org/bot${token}/sendMessage`, {
      chat_id: chatId,
      text: message,
      parse_mode: "Markdown"
    });

    console.log(`Telegram notification sent for ${uid}`);
    return null;
  } catch (error) {
    console.error("Error sending Telegram:", error.message);
    return null;
  }
});

// 2. Telegram Webhook - receive /reply from Telegram and push to Realtime DB
exports.telegramWebhook = functions.https.onRequest(async (req, res) => {
  try {
    const body = req.body;
    if (!body.message || !body.message.text) {
      return res.status(200).send("ok - no message");
    }

    const message = body.message;
    const text = message.text.trim();

    // Handle /reply command
    if (text.startsWith("/reply")) {
      const parts = text.split(" ");
      if (parts.length < 3) {
        await sendTelegramReply(message.chat.id, "Usage: /reply USER_ID your_message\nExample: /reply guest_123456 Hello! How can I help?");
        return res.status(200).send("ok");
      }

      const uid = parts[1];
      const replyText = parts.slice(2).join(" ");

      // Push admin reply to Realtime DB
      await db.ref(`chats/${uid}/messages`).push({
        from: "admin",
        text: replyText,
        time: Date.now(),
        email: "admin",
        name: "Support Team"
      });

      await sendTelegramReply(message.chat.id, `✅ Reply sent to ${uid}:\n\n${replyText}`);

      // Log reply
      await db.ref(`chats/${uid}/lastAdminReply`).set({
        text: replyText,
        time: Date.now()
      });

      return res.status(200).send("ok");
    }

    // Handle /start
    if (text === "/start") {
      await sendTelegramReply(message.chat.id, 
        `👋 *Welcome to Google Marketing Bot*\n\n` +
        `You will receive live chat messages from your website here.\n\n` +
        `*Commands:*\n` +
        `/reply USER_ID message - Reply to a user\n` +
        `/users - List recent chats\n` +
        `/help - Show help`
      );
      return res.status(200).send("ok");
    }

    // Handle /users
    if (text === "/users") {
      const chatsSnap = await db.ref('chats').once('value');
      const chats = chatsSnap.val();
      if (!chats) {
        await sendTelegramReply(message.chat.id, "No chats yet");
        return res.status(200).send("ok");
      }

      let list = "📋 *Recent Chats:*\n\n";
      Object.entries(chats).slice(-10).forEach(([uid, chatObj]) => {
        const msgs = chatObj.messages ? Object.values(chatObj.messages) : [];
        const last = msgs[msgs.length-1]?.text?.substring(0,40) || 'No messages';
        list += `• \`${uid.substring(0,12)}\` - ${last}...\n`;
      });
      list += "\nUse `/reply USER_ID message` to reply";

      await sendTelegramReply(message.chat.id, list);
      return res.status(200).send("ok");
    }

    // Handle /help
    if (text === "/help") {
      await sendTelegramReply(message.chat.id,
        `🆘 *Help - Google Marketing Bot*\n\n` +
        `*How it works:*\n` +
        `1. When a visitor sends message on website, you get notification here\n` +
        `2. Reply using: /reply USER_ID your message\n` +
        `3. Your reply appears instantly on website chat\n\n` +
        `*Commands:*\n` +
        `/reply USER_ID message - Reply to user\n` +
        `/users - List recent chat users\n` +
        `/start - Welcome message\n` +
        `/help - This help\n\n` +
        `*Example:*\n` +
        `/reply guest_123456 Hello! Thanks for contacting us. How can I help you today?`
      );
      return res.status(200).send("ok");
    }

    // Unknown command - show help
    await sendTelegramReply(message.chat.id, "Unknown command. Use /help for available commands or /reply USER_ID message to reply to a client.");
    return res.status(200).send("ok");

  } catch (error) {
    console.error("Webhook error:", error);
    return res.status(200).send("error but ok");
  }
});

async function sendTelegramReply(chatId, text) {
  try {
    const token = functions.config().telegram?.token || process.env.TELEGRAM_BOT_TOKEN;
    if (!token) return;
    await axios.post(`https://api.telegram.org/bot${token}/sendMessage`, {
      chat_id: chatId,
      text: text,
      parse_mode: "Markdown"
    });
  } catch (e) {
    console.error("Failed to send Telegram reply:", e.message);
  }
}

// 3. HTTP function to send notification to all users (called from admin)
exports.sendNotificationToAll = functions.https.onCall(async (data, context) => {
  // Check if admin
  if (!context.auth || context.auth.token.email !== "afshakil279@gmail.com") {
    throw new functions.https.HttpsError("permission-denied", "Only admin can send notifications");
  }

  const { title, message, type } = data;
  if (!title || !message) {
    throw new functions.https.HttpsError("invalid-argument", "Title and message required");
  }

  // Save to Firestore - client listeners will pick it up
  await admin.firestore().collection('notifications').add({
    title,
    message,
    type: type || 'offer',
    createdAt: admin.firestore.FieldValue.serverTimestamp(),
    createdBy: context.auth.token.email
  });

  // Also send to Telegram as broadcast confirmation
  try {
    const token = functions.config().telegram?.token || process.env.TELEGRAM_BOT_TOKEN;
    const chatId = functions.config().telegram?.chat_id || process.env.TELEGRAM_CHAT_ID;
    if (token && chatId) {
      await axios.post(`https://api.telegram.org/bot${token}/sendMessage`, {
        chat_id: chatId,
        text: `📢 *Notification Sent to All Users*\n\n*Title:* ${title}\n*Message:* ${message}\n*Type:* ${type}`,
        parse_mode: "Markdown"
      });
    }
  } catch (e) {}

  return { success: true };
});
