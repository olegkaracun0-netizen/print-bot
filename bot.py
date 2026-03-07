#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import asyncio
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: TOKEN не задан!")
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
    """Команда /start с кнопкой"""
    user = update.effective_user
    logger.info(f"✅ /start от {user.id}")
    
    # Создаем клавиатуру с одной кнопкой
    keyboard = [[InlineKeyboardButton("🔘 Нажми меня!", callback_data="test_button")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! Нажми кнопку:", 
        reply_markup=reply_markup
    )
    logger.info(f"✅ Сообщение с кнопкой отправлено {user.id}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()  # Обязательно!
    
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Нажата кнопка: {data} от {user_id}")
    
    if data == "test_button":
        await query.edit_message_text("✅ Кнопка работает! Отправь /start еще раз.")
        logger.info(f"✅ Ответ на кнопку отправлен {user_id}")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ТЕСТОВЫЙ ЗАПУСК С КНОПКОЙ")
print("=" * 60)

# Создаем event loop
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Создаем приложение
bot_app = Application.builder().token(TOKEN).build()
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(button_handler))

# Инициализация
loop.run_until_complete(bot_app.initialize())
loop.run_until_complete(bot_app.start())

# Веб-хук
webhook_url = f"{RENDER_URL}/webhook"
loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
loop.run_until_complete(bot_app.bot.set_webhook(webhook_url))

print(f"✅ Веб-хук: {webhook_url}")
print("✅ БОТ ГОТОВ!")
print("=" * 60)

# ========== FLASK ==========
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    if not bot_app:
        return jsonify({"error": "Bot not initialized"}), 500
    
    try:
        update_data = request.get_json()
        if update_data:
            logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
            update = Update.de_json(update_data, bot_app.bot)
            asyncio.run_coroutine_threadsafe(
                bot_app.process_update(update), 
                loop
            )
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot_ready": True})

@app.route('/')
def home():
    return "✅ Тестовый бот с кнопками работает!"

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT)
