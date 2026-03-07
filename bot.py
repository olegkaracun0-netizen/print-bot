#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати - РАБОЧАЯ ВЕРСИЯ
"""

import logging
import os
import sys
import asyncio
import tempfile
import json
import re
import shutil
import traceback
from datetime import datetime
from flask import Flask, request, jsonify

# ИМПОРТЫ ИЗ TELEGRAM.BOT
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler,
    CallbackQueryHandler
)

import PyPDF2
from docx import Document

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
ORDERS_FOLDER = "заказы"
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб, СДЭК, Яндекс Доставка"

# Создаем папку для заказов
os.makedirs(ORDERS_FOLDER, exist_ok=True)

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЯ ==========
(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
) = range(5)

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
user_sessions = {}
media_groups = {}
bot_app = None
loop = None

# ========== ЦЕНЫ ==========
PHOTO_PRICES = {
    "small": {(1, 9): 35, (10, 50): 28, (51, 100): 23, (101, float("inf")): 18},
    "medium": {(1, 9): 65, (10, 50): 55, (51, 100): 45, (101, float("inf")): 35},
    "large": {(1, 4): 200, (5, 20): 170, (21, 50): 150, (51, float("inf")): 120},
}

DOC_PRICES = {
    "bw": {(1, 20): 25, (21, 100): 18, (101, 300): 14, (301, float("inf")): 10},
    "color": {(1, 20): 50, (21, 100): 35, (101, 300): 25, (301, float("inf")): 20},
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def calculate_price(price_dict, quantity):
    for (min_q, max_q), price in price_dict.items():
        if min_q <= quantity <= max_q:
            return price * quantity
    return 0

def estimate_delivery_time(total_pages):
    if total_pages <= 50:
        return "1 день"
    elif total_pages <= 200:
        return "2 дня"
    else:
        return "3 дня"

def extract_number_from_text(text):
    numbers = re.findall(r'\d+', text)
    return int(numbers[0]) if numbers else None

async def count_pages_in_file(file_path, file_name):
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                return len(pdf.pages)
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            return max(1, len(doc.paragraphs) // 30)
        return 1
    except Exception as e:
        logger.error(f"Ошибка подсчета страниц: {e}")
        return 1

async def download_file(file, file_name, user_id):
    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file_name)
        file_obj = await file.get_file()
        await file_obj.download_to_drive(file_path)
        return file_path, temp_dir
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None, None

def save_order_to_folder(user_id, username, order_data, files_info):
    try:
        clean_name = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_folder = os.path.join(ORDERS_FOLDER, f"{clean_name}_{timestamp}")
        os.makedirs(order_folder, exist_ok=True)
        
        saved_files = []
        for i, f in enumerate(files_info, 1):
            new_path = os.path.join(order_folder, f"{i}_{f['name']}")
            shutil.copy2(f['path'], new_path)
            saved_files.append(new_path)
        
        info_file = os.path.join(order_folder, "информация_о_заказе.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"ЗАКАЗ ОТ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")
            f.write(f"Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"ID: {user_id}\n\n")
            
            if order_data['type'] == 'photo':
                format_names = {"small": "Малый", "medium": "Средний", "large": "Большой"}
                f.write(f"Тип: Фото\n")
                f.write(f"Формат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "Черно-белая", "color": "Цветная"}
                f.write(f"Тип: Документы\n")
                f.write(f"Печать: {color_names[order_data['color']]}\n")
            
            f.write(f"Количество копий: {order_data['quantity']}\n")
            f.write(f"Всего страниц: {order_data['total_pages']}\n")
            f.write(f"Сумма: {order_data['total']} руб.\n")
            f.write(f"Срок: {order_data['delivery']}\n\n")
            
            f.write("ФАЙЛЫ:\n")
            for i, file_info in enumerate(files_info, 1):
                icon = "📸" if file_info['type'] == 'photo' else "📄"
                f.write(f"{icon} {i}. {file_info['name']} - {file_info['pages']} стр.\n")
        
        return True, order_folder
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return False, None

# ========== ОБРАБОТЧИКИ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"✅ /start от {user.id}")
    
    if user.id in user_sessions:
        if "temp_dirs" in user_sessions[user.id]:
            for d in user_sessions[user.id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user.id]
    
    welcome = (
        f"👋 Привет, {user.first_name}!\n\n"
        "📸🖨️ Я помогу распечатать фото и документы.\n\n"
        "📎 Отправляй файлы (JPG, PNG, PDF, DOC, DOCX)\n"
        "📦 Можно отправлять несколько файлов за раз\n"
        f"📞 Контакт: {CONTACT_PHONE}\n"
        f"🚚 Доставка: {DELIVERY_OPTIONS}"
    )
    
    await update.message.reply_text(welcome)
    return WAITING_FOR_FILE

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "files": [],
            "temp_dirs": [],
            "total_pages": 0,
            "user_info": {
                "user_id": user_id,
                "username": update.effective_user.username or update.effective_user.first_name,
                "first_name": update.effective_user.first_name
            }
        }
    
    file = None
    file_name = None
    file_type = None
    
    if update.message.document:
        file = update.message.document
        file_name = file.file_name
        ext = file_name.lower().split('.')[-1]
        if ext in ['jpg', 'jpeg', 'png']:
            file_type = "photo"
        elif ext in ['pdf', 'doc', 'docx']:
            file_type = "doc"
        else:
            await update.message.reply_text("❌ Неподдерживаемый формат")
            return WAITING_FOR_FILE
    elif update.message.photo:
        file = update.message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE
    
    file_path, temp_dir = await download_file(file, file_name, user_id)
    if not file_path:
        await update.message.reply_text("❌ Ошибка загрузки")
        return WAITING_FOR_FILE
    
    pages = await count_pages_in_file(file_path, file_name)
    
    user_sessions[user_id]["files"].append({
        "path": file_path,
        "name": file_name,
        "type": file_type,
        "pages": pages
    })
    user_sessions[user_id]["temp_dirs"].append(temp_dir)
    user_sessions[user_id]["total_pages"] += pages
    
    files_count = len(user_sessions[user_id]["files"])
    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')
    
    text = f"✅ Файл добавлен!\n\n📊 Статистика:\n"
    if photo_count > 0:
        text += f"📸 Фото: {photo_count}\n"
    if doc_count > 0:
        text += f"📄 Документы: {doc_count}\n"
    text += f"📄 Всего страниц: {user_sessions[user_id]['total_pages']}\n\n"
    
    if doc_count > 0:
        text += "Выберите тип печати:"
        keyboard = [
            [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
            [InlineKeyboardButton("➕ Добавить ещё", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
    else:
        text += "Выберите формат печати:"
        keyboard = [
            [InlineKeyboardButton("🖼 Малый (A6)", callback_data="photo_small")],
            [InlineKeyboardButton("🖼 Средний", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼 Большой (A4)", callback_data="photo_large")],
            [InlineKeyboardButton("➕ Добавить ещё", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WAITING_FOR_FILE

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data}")
    
    if data == "add_more":
        await query.message.edit_text("📤 Отправьте следующие файлы")
        return WAITING_FOR_FILE
    
    if data == "cancel":
        if user_id in user_sessions:
            for d in user_sessions[user_id].get("temp_dirs", []):
                shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]
        await query.message.edit_text("❌ Заказ отменён")
        return WAITING_FOR_FILE
    
    if data == "new_order":
        if user_id in user_sessions:
            for d in user_sessions[user_id].get("temp_dirs", []):
                shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]
        await query.message.edit_text("🔄 Новый заказ. Отправьте файлы.")
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        await query.message.edit_text(
            "🔢 Сколько копий?",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = data.split("_")[1]
        await query.message.edit_text(
            "🔢 Сколько копий?",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("qty_"):
        quantity = int(data.split("_")[1])
        session = user_sessions[user_id]
        session["quantity"] = quantity
        
        files = session["files"]
        file_type = session["type"]
        
        total = 0
        total_pages = 0
        details = "📊 **ДЕТАЛЬНЫЙ РАСЧЁТ:**\n\n"
        
        for i, f in enumerate(files, 1):
            if file_type == "photo":
                file_total = calculate_price(PHOTO_PRICES[session["format"]], quantity)
                total += file_total
                total_pages += f['pages'] * quantity
                details += f"📸 Файл {i}: {f['pages']} стр. × {quantity} = {file_total} руб.\n"
            else:
                file_pages = f['pages'] * quantity
                file_total = calculate_price(DOC_PRICES[session["color"]], file_pages)
                total += file_total
                total_pages += file_pages
                details += f"📄 Файл {i}: {f['pages']} стр. × {quantity} = {file_pages} стр., {file_total} руб.\n"
        
        session["total"] = total
        session["total_pages"] = total_pages
        session["delivery"] = estimate_delivery_time(total_pages)
        
        text = f"{details}\n📋 **ИТОГО:**\n"
        text += f"📦 Файлов: {len(files)}\n"
        text += f"📊 Страниц: {total_pages}\n"
        text += f"💰 Сумма: {total} руб.\n"
        text += f"⏳ Срок: {session['delivery']}\n\n"
        text += "Подтверждаете?"
        
        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="confirm"),
             InlineKeyboardButton("❌ Нет", callback_data="cancel")]
        ]
        
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return CONFIRMING_ORDER
    
    if data == "confirm":
        session = user_sessions.get(user_id)
        if not session:
            return WAITING_FOR_FILE
        
        success, folder = save_order_to_folder(
            user_id,
            session['user_info']['username'],
            session,
            session['files']
        )
        
        text = "✅ **ЗАКАЗ ОФОРМЛЕН!**\n\n" if success else "❌ Ошибка сохранения\n\n"
        text += f"📞 {CONTACT_PHONE}\n🚚 {DELIVERY_OPTIONS}"
        
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 Новый заказ", callback_data="new_order")]]
        await query.message.delete()
        await context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

def get_quantity_keyboard():
    keyboard = [
        [InlineKeyboardButton("1", callback_data="qty_1"), InlineKeyboardButton("2", callback_data="qty_2"),
         InlineKeyboardButton("3", callback_data="qty_3"), InlineKeyboardButton("4", callback_data="qty_4"),
         InlineKeyboardButton("5", callback_data="qty_5")],
        [InlineKeyboardButton("10", callback_data="qty_10"), InlineKeyboardButton("20", callback_data="qty_20"),
         InlineKeyboardButton("30", callback_data="qty_30"), InlineKeyboardButton("50", callback_data="qty_50"),
         InlineKeyboardButton("100", callback_data="qty_100")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    quantity = extract_number_from_text(update.message.text)
    
    if not quantity or quantity < 1 or quantity > 1000:
        await update.message.reply_text(
            "Введите число от 1 до 1000:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    # Имитируем нажатие кнопки
    query = type('Query', (), {
        'data': f'qty_{quantity}',
        'from_user': update.effective_user,
        'message': update.message,
        'answer': lambda: None
    })
    return await button_handler(update, context)

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

# Создаем приложение
bot_app = Application.builder().token(TOKEN).build()

# Добавляем обработчики
conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file),
        CommandHandler("start", start),
    ],
    states={
        WAITING_FOR_FILE: [
            MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file),
            CallbackQueryHandler(button_handler),
        ],
        SELECTING_PHOTO_FORMAT: [CallbackQueryHandler(button_handler)],
        SELECTING_DOC_TYPE: [CallbackQueryHandler(button_handler)],
        ENTERING_QUANTITY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quantity_input),
            CallbackQueryHandler(button_handler),
        ],
        CONFIRMING_ORDER: [CallbackQueryHandler(button_handler)],
    },
    fallbacks=[CommandHandler("start", start)],
)

bot_app.add_handler(conv_handler)

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
            logger.info(f"📩 Обновление: {update_data.get('update_id')}")
            update = Update.de_json(update_data, bot_app.bot)
            asyncio.run_coroutine_threadsafe(bot_app.process_update(update), loop)
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot_ready": True})

@app.route('/')
def home():
    return "✅ Бот работает! Используйте Telegram."

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=PORT)
