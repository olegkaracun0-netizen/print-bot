import logging
import os
import sys
import json
import asyncio
import threading
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
# Используем threading.local для изоляции между воркерами
thread_local = threading.local()
bot_app = None
bot_initialized = False
init_lock = threading.Lock()

# ========== КОМАНДА START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    logger.info(f"✅ Получена команда /start от {user.id} (@{user.username})")
    
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

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
def init_bot_sync():
    """Синхронная инициализация бота для Flask"""
    global bot_app, bot_initialized
    
    with init_lock:
        if bot_initialized:
            return True
        
        try:
            logger.info("🚀 Инициализация бота...")
            
            # Создаём новый event loop для этого потока
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Создаём приложение
            bot_app = Application.builder().token(TOKEN).build()
            bot_app.add_handler(CommandHandler("start", start))
            
            # Инициализируем
            loop.run_until_complete(bot_app.initialize())
            loop.run_until_complete(bot_app.start())
            
            # Устанавливаем веб-хук
            if RENDER_EXTERNAL_URL:
                webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
                logger.info(f"🔧 Установка веб-хука на {webhook_url}")
                
                # Удаляем старый
                loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
                
                # Устанавливаем новый
                loop.run_until_complete(bot_app.bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=Update.ALL_TYPES
                ))
                
                webhook_info = loop.run_until_complete(bot_app.bot.get_webhook_info())
                logger.info(f"✅ Веб-хук установлен: {webhook_info.url}")
            
            bot_initialized = True
            logger.info("✅ Бот готов к работе!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации: {e}")
            logger.error(traceback.format_exc())
            bot_initialized = False
            return False

# ========== FLASK ==========
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    try:
        # Инициализируем бота при первом запросе
        if not bot_initialized:
            if not init_bot_sync():
                return jsonify({"error": "Bot not initialized"}), 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update
        update = Update.de_json(update_data, bot_app.bot)
        
        # Запускаем обработку в event loop'е бота
        future = asyncio.run_coroutine_threadsafe(
            bot_app.process_update(update),
            bot_app.loop
        )
        
        # Ждём результат с таймаутом
        try:
            future.result(timeout=5)
        except Exception as e:
            logger.error(f"❌ Ошибка обработки обновления: {e}")
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в webhook: {e}")
        return jsonify({"error": str(e)}), 500

@flask_app.route('/health')
def health():
    """Проверка здоровья для Render"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_initialized,
        "timestamp": datetime.now().isoformat()
    })

@flask_app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_initialized else "⏳ Бот инициализируется..."
    return f"""
    <html>
        <head><title>Print Bot</title></head>
        <body style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h1>🤖 Print Bot</h1>
            <p>{status}</p>
            <p>Используйте Telegram для заказов.</p>
            <p><a href="/health">Проверка здоровья</a></p>
        </body>
    </html>
    """

@flask_app.route('/init', methods=['POST'])
def force_init():
    """Принудительная инициализация (для тестов)"""
    if init_bot_sync():
        return jsonify({"status": "ok", "message": "Bot initialized"})
    else:
        return jsonify({"status": "error", "message": "Init failed"}), 500

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    # Инициализируем бота при старте
    init_bot_sync()
    # Запускаем Flask
    flask_app.run(host='0.0.0.0', port=PORT)










