import logging
import os
import sys
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update
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
bot_app = None
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
    """Инициализирует бота"""
    global bot_app, loop
    
    try:
        logger.info("🚀 Инициализация бота...")
        
        # Создаём новый event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Создаём приложение
        bot_app = Application.builder().token(TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        
        # Инициализируем
        loop.run_until_complete(bot_app.initialize())
        loop.run_until_complete(bot_app.start())
        
        logger.info("✅ Бот инициализирован")
        
        # Устанавливаем веб-хук
        if RENDER_EXTERNAL_URL:
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            logger.info(f"🔧 Установка веб-хука на {webhook_url}")
            
            # Удаляем старый
            loop.run_until_complete(bot_app.bot.delete_webhook(drop_pending_updates=True))
            
            # Устанавливаем новый
            result = loop.run_until_complete(bot_app.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES
            ))
            
            if result:
                logger.info(f"✅ Веб-хук установлен: {webhook_url}")
            else:
                logger.error("❌ Не удалось установить веб-хук")
        
        logger.info("✅ Бот готов к работе!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        logger.error(traceback.format_exc())
        return False

# ========== FLASK ==========
flask_app = Flask(__name__)

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    global bot_app, loop
    
    try:
        # Проверяем, инициализирован ли бот
        if bot_app is None:
            logger.error("❌ Бот не инициализирован")
            return jsonify({"error": "Bot not initialized"}), 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update
        update = Update.de_json(update_data, bot_app.bot)
        
        # Запускаем обработку в event loop
        if loop and bot_app:
            asyncio.run_coroutine_threadsafe(
                bot_app.process_update(update),
                loop
            )
            return "OK", 200
        else:
            logger.error("❌ Event loop не доступен")
            return jsonify({"error": "Event loop not available"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@flask_app.route('/health')
def health():
    """Проверка здоровья для Render"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_app is not None,
        "timestamp": datetime.now().isoformat()
    })

@flask_app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_app else "⏳ Бот инициализируется..."
    return f"""
    <html>
        <head>
            <title>Print Bot</title>
            <style>
                body {{ font-family: Arial; text-align: center; margin-top: 50px; }}
                .status {{ padding: 20px; margin: 20px; }}
                .ok {{ color: green; }}
            </style>
        </head>
        <body>
            <h1>🤖 Print Bot</h1>
            <div class="status">
                <h2 class="ok">{status}</h2>
            </div>
            <p>Используйте Telegram для заказов.</p>
            <p>
                <a href="/health">Проверка здоровья</a>
            </p>
        </body>
    </html>
    """

@flask_app.route('/debug')
def debug():
    """Отладочная информация"""
    return jsonify({
        "bot_initialized": bot_app is not None,
        "loop_exists": loop is not None,
        "render_url": RENDER_EXTERNAL_URL,
        "port": PORT
    })

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Инициализируем бота
    if init_bot():
        # Запускаем Flask
        logger.info(f"🌐 Запуск Flask на порту {PORT}")
        flask_app.run(host='0.0.0.0', port=PORT, debug=False)
    else:
        logger.error("❌ Не удалось инициализировать бота")
        sys.exit(1)
