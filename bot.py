import logging
import os
import asyncio
from datetime import datetime
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "8238978593:AAG-rgNUQXF8_MAkLjBgeON2FGUfHhm7YO0")
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== FLASK ==========
flask_app = Flask(__name__)
bot_app = None

# ========== КОМАНДА START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает! Отправьте файл для печати.")

# ========== WEBHOOK ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    global bot_app
    if bot_app is None:
        return "Bot not initialized", 500
    
    update = Update.de_json(request.get_json(), bot_app.bot)
    asyncio.run_coroutine_threadsafe(bot_app.process_update(update), bot_app.loop)
    return "OK", 200

@flask_app.route('/')
def home():
    return "✅ Бот работает!"

@flask_app.route('/health')
def health():
    return {"status": "ok", "bot_ready": bot_app is not None}, 200

# ========== ИНИЦИАЛИЗАЦИЯ ==========
async def init_bot():
    global bot_app
    logger.info("🚀 Инициализация бота...")
    
    bot_app = Application.builder().token(TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    
    await bot_app.initialize()
    await bot_app.start()
    
    webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
    await bot_app.bot.set_webhook(url=webhook_url)
    logger.info(f"✅ Веб-хук установлен: {webhook_url}")
    
    return bot_app

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_bot())
    flask_app.run(host='0.0.0.0', port=PORT)








