#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати - МИНИМАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ
"""

import logging
import os
import sys
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: TOKEN не задан в переменных окружения!")
    sys.exit(1)

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("❌ ОШИБКА: RENDER_EXTERNAL_URL не задан!")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
bot_app = None
loop = None

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    user = update.effective_user
    logger.info(f"✅ Получена команда /start от {user.id}")
    await update.message.reply_text(f"Привет, {user.first_name}! Бот работает! 🎉")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📌 Токен: {TOKEN[:10]}...")
print(f"📌 Render URL: {RENDER_URL}")
print(f"📌 Порт: {PORT}")

# Создаем event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Создаем и инициализируем приложение
print("🔄 Создание Application...")
bot_app = Application.builder().token(TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))

print("🔄 Инициализация...")
loop.run_until_complete(bot_app.initialize())
loop.run_until_complete(bot_app.start())

# Устанавливаем веб-хук
webhook_url = f"{RENDER_URL}/webhook"
print(f"🔄 Установка веб-хука: {webhook_url}")
loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
loop.run_until_complete(bot_app.bot.set_webhook(webhook_url))

print("✅ БОТ ГОТОВ К РАБОТЕ!")
print("=" * 60)

# ========== FLASK ==========
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Прием обновлений от Telegram"""
    global bot_app, loop
    
    if not bot_app:
        logger.error("❌ Бот не инициализирован")
        return jsonify({"error": "Bot not initialized"}), 500
    
    try:
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        update = Update.de_json(update_data, bot_app.bot)
        
        # Отправляем в event loop бота
        asyncio.run_coroutine_threadsafe(
            bot_app.process_update(update),
            loop
        )
        
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "bot_ready": bot_app is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    return "✅ Бот работает! Используйте Telegram для заказов."

@app.route('/debug')
def debug():
    return jsonify({
        "bot_initialized": bot_app is not None,
        "webhook_url": f"{RENDER_URL}/webhook",
        "token_exists": bool(TOKEN)
    })

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print(f"🌐 Запуск Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT)
