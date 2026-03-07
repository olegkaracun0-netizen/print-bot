import logging
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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
application = None
bot_initialized = False

# ========== ОБРАБОТЧИКИ ==========
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

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик файлов"""
    user = update.effective_user
    logger.info(f"📎 Получен файл от {user.id}")
    
    if update.message.document:
        file_name = update.message.document.file_name
        await update.message.reply_text(f"✅ Получен документ: {file_name}")
    elif update.message.photo:
        await update.message.reply_text(f"✅ Получено фото")
    else:
        await update.message.reply_text(f"✅ Файл получен")

# ========== ИНИЦИАЛИЗАЦИЯ ==========
def init_bot():
    """Инициализирует бота"""
    global application, bot_initialized
    
    try:
        logger.info("🚀 Инициализация бота...")
        
        # Создаём приложение
        application = Application.builder().token(TOKEN).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
        
        # Инициализируем приложение
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.initialize())
        
        logger.info("✅ Бот инициализирован")
        
        # Устанавливаем веб-хук
        if RENDER_EXTERNAL_URL:
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            logger.info(f"🔧 Установка веб-хука на {webhook_url}")
            
            # Удаляем старый веб-хук
            loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
            
            # Устанавливаем новый
            loop.run_until_complete(application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES
            ))
            
            # Проверяем
            webhook_info = loop.run_until_complete(application.bot.get_webhook_info())
            logger.info(f"✅ Веб-хук установлен: {webhook_info.url}")
        
        bot_initialized = True
        logger.info("✅ Бот готов к работе!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        import traceback
        traceback.print_exc()
        bot_initialized = False
        return False

# ========== FLASK ==========
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    global application
    
    try:
        if not bot_initialized or application is None:
            logger.error("❌ Бот не инициализирован")
            return jsonify({"error": "Bot not initialized"}), 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update
        update = Update.de_json(update_data, application.bot)
        
        # Запускаем обработку в event loop
        import asyncio
        
        # Получаем или создаём event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Запускаем обработку
        loop.run_until_complete(application.process_update(update))
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Проверка здоровья"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_initialized,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_initialized else "⏳ Бот инициализируется..."
    return f"""
    <html>
        <head>
            <title>Print Bot</title>
            <style>
                body {{ font-family: Arial; text-align: center; margin-top: 50px; }}
                .status {{ padding: 20px; margin: 20px; }}
                .ok {{ color: green; }}
                .wait {{ color: orange; }}
            </style>
        </head>
        <body>
            <h1>🤖 Print Bot</h1>
            <div class="status">
                <h2 class="{ 'ok' if bot_initialized else 'wait' }">{status}</h2>
            </div>
            <p>Используйте Telegram для заказов.</p>
            <p>
                <a href="/health">Проверка здоровья</a> |
                <a href="/debug">Отладка</a>
            </p>
        </body>
    </html>
    """

@app.route('/debug')
def debug():
    """Отладка"""
    bot_info = None
    if application and application.bot:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_info = loop.run_until_complete(application.bot.get_me()).username
        except:
            bot_info = "Ошибка получения информации"
    
    return jsonify({
        "bot_initialized": bot_initialized,
        "webhook_url": f"{RENDER_EXTERNAL_URL}/webhook" if RENDER_EXTERNAL_URL else None,
        "bot_username": bot_info,
        "python_version": sys.version
    })

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Инициализируем бота
    if init_bot():
        logger.info(f"🌐 Запуск Flask на порту {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False)
    else:
        logger.error("❌ Не удалось инициализировать бота")
        # Всё равно запускаем Flask для отладки
        logger.info(f"🌐 Запуск Flask на порту {PORT} (режим отладки)")
        app.run(host='0.0.0.0', port=PORT, debug=False)
