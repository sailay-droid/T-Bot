import os
import telebot
from flask import Flask, request

# === Render ရဲ့ Environment Variable ကနေ Token ကို ယူမယ် (အောက်မှာ ရှင်းပြမယ်) ===
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set!")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# === Flask Web Server ရဲ့ Health Check Route (Render အတွက် လိုအပ်) ===
@app.route('/')
@app.route('/health')
def health_check():
    return "Bot is running!", 200

# === Webhook Route (Telegram က မက်ဆေ့ချ်တွေ ဒီကိုပဲ ပို့မယ်) ===
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    else:
        return 'Unsupported content type', 400

# === Bot ရဲ့ Command Handler တွေ (အရင်က ရေးထားတဲ့အတိုင်း) ===
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "မင်္ဂလာပါ! ငါအခု Cloud ပေါ်မှာ ၂၄ နာရီ အလုပ်လုပ်နေပါပြီ။")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"မင်းပြောတာက: {message.text}")

# === Webhook ကို သတ်မှတ်တဲ့အပိုင်း (Render ရဲ့ URL ကို သုံးမယ်) ===
# Render က Service ကို အလိုအလျောက် Public URL ပေးတယ်
# ဒီ URL ကို အောက်က နေရာမှာ သုံးမယ်
WEBHOOK_URL = os.environ.get('RENDER_EXTERNAL_URL', '') + '/webhook'

if __name__ == '__main__':
    # Webhook ကို စနစ်ထည့်မယ်
    if WEBHOOK_URL and WEBHOOK_URL.startswith('http'):
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        print(f"Webhook set to: {WEBHOOK_URL}")
    else:
        print("Warning: RENDER_EXTERNAL_URL not set, using polling mode (not recommended for Render).")
        # Render မှာ Polling သုံးရင် Conflict ဖြစ်နိုင်လို့ Webhook က အကောင်းဆုံးပါ[reference:9]
    
    # Flask Server ကို စတင်မယ် (Render ရဲ့ PORT ကို သုံးမယ်)[reference:10]
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)