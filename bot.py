#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ========== ПРИНУДИТЕЛЬНЫЙ ВЫВОД В НАЧАЛЕ ==========
import sys
print("🔥🔥🔥 ФАЙЛ bot.py НАЧАЛ ВЫПОЛНЕНИЕ 🔥🔥🔥", file=sys.stderr)
print("🔥🔥🔥 ТЕКУЩАЯ ДИРЕКТОРИЯ:", __file__, file=sys.stderr)
sys.stderr.flush()
# ==================================================

import logging
import os
import asyncio
import traceback
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ПРИНУДИТЕЛЬНЫЙ ВЫВОД ==========
print("📢 ИМПОРТЫ ЗАВЕРШЕНЫ", file=sys.stderr)
sys.stderr.flush()
# =========================================

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN", "8238978593:AAG-rgNUQXF8_MAkLjBgeON2FGUfHhm7YO0")
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

print(f"📢 ТОКЕН ПОЛУЧЕН: {TOKEN[:10]}...", file=sys.stderr)
print(f"📢 PORT: {PORT}", file=sys.stderr)
print(f"📢 RENDER_URL: {RENDER_EXTERNAL_URL}", file=sys.stderr)
sys.stderr.flush()

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

print("📢 ЛОГИРОВАНИЕ НАСТРОЕНО", file=sys.stderr)
sys.stderr.flush()

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
application = None
bot_initialized = False

print("📢 ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ СОЗДАНЫ", file=sys.stderr)
sys.stderr.flush()

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
    
    print("🔥🔥🔥 ВХОД В ФУНКЦИЮ init_bot 🔥🔥🔥", file=sys.stderr)
    sys.stderr.flush()
    
    try:
        logger.info("=" * 60)
        logger.info("🚀 НАЧАЛО ИНИЦИАЛИЗАЦИИ БОТА")
        logger.info("=" * 60)
        
        logger.info(f"📌 Токен: {TOKEN[:10]}...{TOKEN[-5:]}")
        logger.info(f"📌 Render URL: {RENDER_EXTERNAL_URL}")
        
        # Проверяем, что токен не пустой
        if not TOKEN or TOKEN == "8238978593:AAG-rgNUQXF8_MAkLjBgeON2FGUfHhm7YO0":
            logger.error("❌ Токен не задан или используется токен по умолчанию!")
            raise ValueError("TOKEN не задан в переменных окружения")
        
        # Создаём приложение
        logger.info("1️⃣ Создание Application...")
        application = Application.builder().token(TOKEN).build()
        logger.info("   ✅ Application создан")
        
        # Добавляем обработчики
        logger.info("2️⃣ Добавление обработчиков...")
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file))
        logger.info("   ✅ Обработчики добавлены")
        
        # Создаём event loop
        logger.info("3️⃣ Настройка event loop...")
        try:
            loop = asyncio.get_event_loop()
            logger.info("   ✅ Получен существующий event loop")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logger.info("   ✅ Создан новый event loop")
        
        # Инициализируем
        logger.info("4️⃣ Инициализация Application...")
        loop.run_until_complete(application.initialize())
        logger.info("   ✅ Application инициализирован")
        
        logger.info("5️⃣ Запуск Application...")
        loop.run_until_complete(application.start())
        logger.info("   ✅ Application запущен")
        
        # Проверяем соединение с Telegram
        logger.info("6️⃣ Проверка соединения с Telegram...")
        bot_info = loop.run_until_complete(application.bot.get_me())
        logger.info(f"   ✅ Бот @{bot_info.username} успешно подключился")
        
        # Устанавливаем веб-хук
        logger.info("7️⃣ Настройка веб-хука...")
        if RENDER_EXTERNAL_URL:
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            logger.info(f"   🔧 URL веб-хука: {webhook_url}")
            
            # Удаляем старый
            logger.info("   🗑️ Удаление старого веб-хука...")
            loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
            logger.info("   ✅ Старый веб-хук удалён")
            
            # Устанавливаем новый
            logger.info("   🔧 Установка нового веб-хука...")
            result = loop.run_until_complete(application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES
            ))
            
            if result:
                logger.info(f"   ✅ Веб-хук установлен: {webhook_url}")
                
                # Проверяем
                webhook_info = loop.run_until_complete(application.bot.get_webhook_info())
                logger.info(f"   📊 Информация о веб-хуке: {webhook_info.url}")
            else:
                logger.error("   ❌ Не удалось установить веб-хук")
                raise Exception("Не удалось установить веб-хук")
        else:
            logger.warning("⚠️ RENDER_EXTERNAL_URL не задан, веб-хук не установлен")
            raise Exception("RENDER_EXTERNAL_URL не задан")
        
        bot_initialized = True
        logger.info("=" * 60)
        logger.info("✅ БОТ УСПЕШНО ИНИЦИАЛИЗИРОВАН И ГОТОВ К РАБОТЕ!")
        logger.info("=" * 60)
        
        print("🔥🔥🔥 ФУНКЦИЯ init_bot УСПЕШНО ЗАВЕРШЕНА 🔥🔥🔥", file=sys.stderr)
        sys.stderr.flush()
        return True
        
    except Exception as e:
        logger.error("=" * 60)
        logger.error(f"❌ ОШИБКА ИНИЦИАЛИЗАЦИИ: {e}")
        logger.error("=" * 60)
        logger.error("📋 ПОЛНЫЙ TRACEBACK:")
        logger.error(traceback.format_exc())
        logger.error("=" * 60)
        
        print(f"🔥🔥🔥 ОШИБКА В init_bot: {e} 🔥🔥🔥", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.stderr.flush()
        
        # ВАЖНО: Поднимаем исключение, чтобы остановить запуск
        raise e

# ========== FLASK ==========
app = Flask(__name__)

print("📢 FLASK ПРИЛОЖЕНИЕ СОЗДАНО", file=sys.stderr)
sys.stderr.flush()

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    global application
    
    try:
        # Проверяем инициализацию
        if not bot_initialized or application is None:
            logger.error("❌ Бот не инициализирован в момент запроса")
            return jsonify({
                "error": "Bot not initialized", 
                "bot_ready": bot_initialized,
                "message": "Проверьте логи инициализации"
            }), 500
        
        # Получаем обновление
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        logger.info(f"📩 Получено обновление: {update_data.get('update_id')}")
        
        # Создаём объект Update
        update = Update.de_json(update_data, application.bot)
        
        # Получаем event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Обрабатываем
        loop.run_until_complete(application.process_update(update))
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Проверка здоровья"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_initialized,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/debug')
def debug():
    """Отладочная информация"""
    bot_info = None
    if application and application.bot:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            bot_info = loop.run_until_complete(application.bot.get_me()).username
        except:
            bot_info = "Ошибка получения информации"
    
    return jsonify({
        "bot_initialized": bot_initialized,
        "webhook_url": f"{RENDER_EXTERNAL_URL}/webhook" if RENDER_EXTERNAL_URL else None,
        "bot_username": bot_info,
        "python_version": sys.version,
        "token_first_chars": TOKEN[:10] if TOKEN else None,
        "render_url": RENDER_EXTERNAL_URL
    })

@app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_initialized else "❌ Бот не инициализирован"
    color = "green" if bot_initialized else "red"
    return f"""
    <html>
        <head>
            <title>Print Bot</title>
            <style>
                body {{ font-family: Arial; text-align: center; margin-top: 50px; }}
                .status {{ padding: 20px; margin: 20px; }}
                .green {{ color: green; }}
                .red {{ color: red; }}
            </style>
        </head>
        <body>
            <h1>🤖 Print Bot</h1>
            <div class="status">
                <h2 class="{color}">{status}</h2>
            </div>
            <p>Используйте Telegram для заказов.</p>
            <p>
                <a href="/health">Проверка здоровья</a> |
                <a href="/debug">Отладка</a>
            </p>
        </body>
    </html>
    """

# ========== ЗАПУСК ==========
print("🔥🔥🔥 ДОХОДИМ ДО if __name__ == '__main__' 🔥🔥🔥", file=sys.stderr)
sys.stderr.flush()

if __name__ == "__main__":
    print("🔥🔥🔥 ВХОД В if __name__ == '__main__' 🔥🔥🔥", file=sys.stderr)
    print("="*60, file=sys.stderr)
    print("🚀 ЗАПУСК БОТА", file=sys.stderr)
    print("="*60, file=sys.stderr)
    sys.stderr.flush()
    
    try:
        # Инициализируем бота
        print("🔥🔥🔥 ВЫЗЫВАЕМ init_bot() 🔥🔥🔥", file=sys.stderr)
        sys.stderr.flush()
        
        init_bot()
        
        print("🔥🔥🔥 init_bot() ВЫПОЛНЕНА УСПЕШНО 🔥🔥🔥", file=sys.stderr)
        print(f"🌐 ЗАПУСК FLASK НА ПОРТУ {PORT}", file=sys.stderr)
        sys.stderr.flush()
        
        app.run(host='0.0.0.0', port=PORT, debug=False)
        
    except Exception as e:
        print("="*60, file=sys.stderr)
        print(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {e}", file=sys.stderr)
        print("="*60, file=sys.stderr)
        print("📋 ПОЛНЫЙ TRACEBACK:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("="*60, file=sys.stderr)
        sys.stderr.flush()
        sys.exit(1)
