import logging
import os
import sys
import json
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "8238978593:AAG-rgNUQXF8_MAkLjBgeON2FGUfHhm7YO0")
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
bot_application = None
loop = None

# ========== КОМАНДА START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"✅ Получена команда /start от {user.id}")
    
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для печати фото и документов. 📸🖨️\n\n"
        "✨ **Что я умею:**\n"
        "• Принимать файлы (JPG, PNG, PDF, DOC, DOCX)\n"
        "• Считать количество листов\n"
        "• Рассчитывать стоимость\n\n"
        "Просто отправь мне файл для печати!"
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
def init_bot():
    """Инициализирует бота (без запуска polling!)"""
    global bot_application, loop
    
    logger.info("🚀 Инициализация бота...")
    
    # Создаём новый event loop для этого потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создаём приложение
    bot_application = Application.builder().token(TOKEN).build()
    bot_application.add_handler(CommandHandler("start", start))
    
    # Инициализируем (но НЕ ЗАПУСКАЕМ polling!)
    loop.run_until_complete(bot_application.initialize())
    loop.run_until_complete(bot_application.start())
    
    # Устанавливаем веб-хук
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        logger.info(f"🔧 Установка веб-хука на {webhook_url}")
        
        # Удаляем старый
        loop.run_until_complete(bot_application.bot.delete_webhook(drop_pending_updates=True))
        
        # Устанавливаем новый
        loop.run_until_complete(bot_application.bot.set_webhook(
            url=webhook_url,
            allowed_updates=Update.ALL_TYPES
        ))
        
        webhook_info = loop.run_until_complete(bot_application.bot.get_webhook_info())
        logger.info(f"✅ Веб-хук установлен: {webhook_info.url}")
    
    logger.info("✅ Бот готов к работе (режим веб-хука)!")
    return True

# ========== FLASK ==========
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    global bot_application, loop
    
    try:
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update
        update = Update.de_json(update_data, bot_application.bot)
        
        # ВАЖНО: Запускаем обработку в существующем event loop
        if loop and bot_application:
            # Создаём задачу в event loop'е бота
            asyncio.run_coroutine_threadsafe(
                bot_application.process_update(update),
                loop
            )
            return "OK", 200
        else:
            logger.error("❌ Бот не инициализирован")
            return jsonify({"error": "Bot not initialized"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.route('/health')
def health():
    """Проверка здоровья для Render"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_application is not None,
        "timestamp": datetime.now().isoformat()
    })

@flask_app.route('/')
def home():
    """Главная страница"""
    return "✅ Бот работает! Используйте Telegram для заказов."

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Инициализируем бота перед запуском Flask
    init_bot()
    
    # Запускаем Flask (в том же потоке)
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    flask_app.run(host='0.0.0.0', port=PORT)











