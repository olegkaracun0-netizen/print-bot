import logging
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify
import telegram
from telegram.ext import Dispatcher, CommandHandler, MessageHandler, Filters

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

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
try:
    # Создаём бота
    bot = telegram.Bot(token=TOKEN)
    logger.info(f"✅ Бот создан: {bot.get_me().username}")
    
    # Создаём диспетчер для обработки команд
    dispatcher = Dispatcher(bot, None, workers=0)
    
    # ========== ОБРАБОТЧИКИ ==========
    def start(update, context):
        user = update.effective_user
        logger.info(f"✅ Получена команда /start от {user.id}")
        update.message.reply_text(
            f"Привет, {user.first_name}! 👋\n\n"
            "Я бот для печати фото и документов. 📸🖨️\n\n"
            "Просто отправь мне файл для печати!"
        )
    
    def handle_file(update, context):
        user = update.effective_user
        logger.info(f"📎 Получен файл от {user.id}")
        update.message.reply_text("✅ Файл получен! Функция печати в разработке.")
    
    # Регистрируем обработчики
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.document | Filters.photo, handle_file))
    
    logger.info("✅ Обработчики зарегистрированы")
    
    # Устанавливаем веб-хук
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"✅ Веб-хук установлен: {webhook_url}")
    
    bot_ready = True
    logger.info("✅ Бот готов к работе!")
    
except Exception as e:
    logger.error(f"❌ Ошибка при инициализации: {e}")
    bot = None
    dispatcher = None
    bot_ready = False

# ========== FLASK ==========
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    try:
        if not bot_ready:
            return jsonify({"error": "Bot not ready"}), 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Преобразуем в объект Update
        update = telegram.Update.de_json(update_data, bot)
        
        # Обрабатываем
        dispatcher.process_update(update)
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Проверка здоровья"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_ready,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_ready else "❌ Бот не работает"
    return f"""
    <html>
        <head><title>Print Bot</title></head>
        <body style="font-family: Arial; text-align: center; margin-top: 50px;">
            <h1>🤖 Print Bot</h1>
            <h2>{status}</h2>
            <p>Используйте Telegram для заказов.</p>
            <p><a href="/health">Проверка здоровья</a></p>
        </body>
    </html>
    """

@app.route('/debug')
def debug():
    """Отладка"""
    return jsonify({
        "bot_ready": bot_ready,
        "webhook_url": f"{RENDER_EXTERNAL_URL}/webhook" if RENDER_EXTERNAL_URL else None,
        "bot_username": bot.get_me().username if bot else None
    })

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
