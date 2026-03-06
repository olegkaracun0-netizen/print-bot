import logging
import os
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== FLASK ==========
flask_app = Flask(__name__)
bot_app = None
init_lock = asyncio.Lock()

# ========== КОМАНДА START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"✅ Получена команда /start от {update.effective_user.id}")
    await update.message.reply_text(
        "✅ Бот работает! Отправьте файл для печати.\n\n"
        "Поддерживаемые форматы: JPG, PNG, PDF, DOC, DOCX"
    )

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
async def ensure_bot_initialized():
    """Гарантирует, что бот инициализирован"""
    global bot_app
    
    if bot_app is not None:
        return True
    
    async with init_lock:
        if bot_app is not None:
            return True
        
        try:
            logger.info("🚀 Инициализация бота...")
            
            bot_app = Application.builder().token(TOKEN).build()
            bot_app.add_handler(CommandHandler("start", start))
            
            await bot_app.initialize()
            await bot_app.start()
            
            if RENDER_EXTERNAL_URL:
                webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
                logger.info(f"🔧 Установка веб-хука на {webhook_url}")
                
                # Удаляем старый веб-хук
                await bot_app.bot.delete_webhook(drop_pending_updates=True)
                
                # Устанавливаем новый
                await bot_app.bot.set_webhook(
                    url=webhook_url,
                    allowed_updates=Update.ALL_TYPES
                )
                
                webhook_info = await bot_app.bot.get_webhook_info()
                logger.info(f"✅ Веб-хук установлен: {webhook_info.url}")
            
            logger.info("✅ Бот готов к работе!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации бота: {e}")
            bot_app = None
            return False

# ========== WEBHOOK ==========
@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    try:
        # Проверяем/инициализируем бота синхронно
        if not asyncio.run(ensure_bot_initialized()):
            logger.error("❌ Бот не инициализирован")
            return "Bot not initialized", 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return "No data", 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update и обрабатываем
        update = Update.de_json(update_data, bot_app.bot)
        
        # Запускаем обработку в фоне
        asyncio.run_coroutine_threadsafe(
            bot_app.process_update(update),
            bot_app.loop
        )
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return "Error", 500

# ========== HEALTH CHECK ==========
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
    return "✅ Бот работает! Используйте Telegram для заказов."

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Создаём event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Запускаем Flask (он будет обрабатывать запросы)
    # Бот инициализируется при первом запросе на /webhook
    logger.info(f"🌐 Запуск Flask на порту {PORT}")
    flask_app.run(host='0.0.0.0', port=PORT)









