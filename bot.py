#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Полноценный бот для печати фото и документов
Работает на Render через веб-хуки
"""

import logging
import os
import sys
import asyncio
import traceback
import tempfile
import json
import re
import shutil
from datetime import datetime
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import PyPDF2
from docx import Document

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: TOKEN не задан в переменных окружения!")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
ORDERS_FOLDER = "заказы"
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб, СДЭК, Яндекс Доставка"

# Создаём папку для заказов
if not os.path.exists(ORDERS_FOLDER):
    os.makedirs(ORDERS_FOLDER)
    print(f"📁 Создана папка: {ORDERS_FOLDER}")

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ========== СОСТОЯНИЯ ДЛЯ РАЗГОВОРА ==========
(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
) = range(5)

# ========== ХРАНИЛИЩЕ ДАННЫХ ==========
media_groups = {}
user_sessions = {}
application = None
bot_initialized = False

# ========== ЦЕНЫ ==========
PHOTO_PRICES = {
    "small": {
        "name": "Малый (A6/10x15)",
        "ranges": [(1, 9, 35), (10, 50, 28), (51, 100, 23), (101, float("inf"), 18)]
    },
    "medium": {
        "name": "Средний (13x18/15x21)",
        "ranges": [(1, 9, 65), (10, 50, 55), (51, 100, 45), (101, float("inf"), 35)]
    },
    "large": {
        "name": "Большой (A4/21x30)",
        "ranges": [(1, 4, 200), (5, 20, 170), (21, 50, 150), (51, float("inf"), 120)]
    },
}

DOC_PRICES = {
    "bw": {
        "name": "Черно-белая",
        "ranges": [(1, 20, 25), (21, 100, 18), (101, 300, 14), (301, float("inf"), 10)]
    },
    "color": {
        "name": "Цветная",
        "ranges": [(1, 20, 50), (21, 100, 35), (101, 300, 25), (301, float("inf"), 20)]
    },
}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def calculate_price(price_dict, total_pages):
    """Рассчитывает стоимость на основе количества страниц"""
    for (min_q, max_q, price_per_page) in price_dict["ranges"]:
        if min_q <= total_pages <= max_q:
            return price_per_page * total_pages
    return 0

def estimate_delivery_time(total_items):
    """Расчёт срока выполнения"""
    if total_items <= 50:
        return "1 день"
    elif total_items <= 200:
        return "2 дня"
    else:
        return "3 дня"

def extract_number_from_text(text):
    """Извлекает число из текста"""
    text = text.lower().strip()
    numbers = re.findall(r'\d+', text)
    if numbers:
        return int(numbers[0])
    return None

async def count_pages_in_file(file_path, file_name):
    """Подсчёт страниц в файле"""
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as pdf_file:
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                return len(pdf_reader.pages)
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            # Приблизительный подсчёт страниц в Word
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + " "
            chars = len(text)
            estimated_pages = max(1, chars // 2000)
            
            # Учитываем таблицы
            tables_count = len(doc.tables)
            if tables_count > 0:
                estimated_pages += tables_count // 2
            return estimated_pages
        else:
            return 1
    except Exception as e:
        logger.error(f"Ошибка подсчета страниц: {e}")
        return 1

async def download_file(file, file_name, user_id):
    """Скачивает файл и сохраняет во временную папку"""
    try:
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, file_name)
        
        file_obj = await file.get_file()
        await file_obj.download_to_drive(temp_file_path)
        
        return temp_file_path, temp_dir
    except Exception as e:
        logger.error(f"Ошибка скачивания файла: {e}")
        return None, None

def save_order_to_files(user_id, username, order_data, files_info):
    """Сохраняет заказ в папку"""
    try:
        # Создаём уникальную папку для заказа
        clean_username = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_folder = os.path.join(ORDERS_FOLDER, f"{clean_username}_{timestamp}")
        os.makedirs(order_folder, exist_ok=True)
        
        # Копируем все файлы
        saved_files = []
        for i, file_info in enumerate(files_info, 1):
            old_path = file_info['path']
            file_name = file_info['name']
            new_path = os.path.join(order_folder, f"{i}_{file_name}")
            shutil.copy2(old_path, new_path)
            saved_files.append({
                'name': file_name,
                'path': new_path,
                'pages': file_info['pages'],
                'type': file_info['type']
            })
        
        # Сохраняем информацию о заказе
        total_pages = sum(f['pages'] for f in files_info) * order_data['quantity']
        
        info_file = os.path.join(order_folder, "информация_о_заказе.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"ЗАКАЗ НА ПЕЧАТЬ\n")
            f.write(f"{'='*50}\n")
            f.write(f"Дата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write(f"Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"ID: {user_id}\n\n")
            
            if order_data['file_type'] == 'photo':
                f.write(f"Тип: Фото\n")
                f.write(f"Формат: {PHOTO_PRICES[order_data['format']]['name']}\n")
            else:
                f.write(f"Тип: Документы\n")
                f.write(f"Печать: {DOC_PRICES[order_data['color']]['name']}\n")
            
            f.write(f"Количество копий: {order_data['quantity']}\n")
            f.write(f"Всего страниц к печати: {total_pages}\n")
            f.write(f"Сумма: {order_data['total']} руб.\n")
            f.write(f"Срок: {order_data['delivery']}\n\n")
            
            f.write("ФАЙЛЫ:\n")
            for i, f_info in enumerate(files_info, 1):
                file_icon = "📸" if f_info['type'] == 'photo' else "📄"
                f.write(f"{file_icon} {i}. {f_info['name']} - {f_info['pages']} стр.\n")
        
        return True, order_folder
    except Exception as e:
        logger.error(f"Ошибка сохранения заказа: {e}")
        return False, None

def calculate_detailed_prices(session):
    """Детальный расчёт стоимости для всех файлов"""
    files = session.get("temp_files", [])
    quantity = session.get("quantity", 1)
    file_type = session.get("file_type")
    
    detailed = []
    total_sum = 0
    total_pages_all = 0
    
    for i, file in enumerate(files, 1):
        if file_type == "photo":
            format_key = session.get("format", "small")
            price_info = PHOTO_PRICES[format_key]
            price_per_copy = next((p for mn, mx, p in price_info["ranges"] if mn <= quantity <= mx), 0)
            file_total = price_per_copy * quantity
            total_sum += file_total
            total_pages_all += file['pages'] * quantity
            
            detailed.append({
                "num": i,
                "name": file['name'],
                "pages": file['pages'],
                "copies": quantity,
                "price_per": price_per_copy,
                "total": file_total,
                "type": "photo"
            })
        else:
            color_key = session.get("color", "bw")
            price_info = DOC_PRICES[color_key]
            file_pages = file['pages'] * quantity
            price_per_page = next((p for mn, mx, p in price_info["ranges"] if mn <= file_pages <= mx), 0)
            file_total = price_per_page * file_pages
            total_sum += file_total
            total_pages_all += file_pages
            
            detailed.append({
                "num": i,
                "name": file['name'],
                "pages": file['pages'],
                "copies": quantity,
                "total_pages": file_pages,
                "price_per": price_per_page,
                "total": file_total,
                "type": "doc"
            })
    
    return {
        "detailed": detailed,
        "total_sum": total_sum,
        "total_pages_all": total_pages_all,
        "files_count": len(files)
    }

# ========== ОБРАБОТЧИКИ TELEGRAM ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    user_id = user.id
    
    # Очищаем предыдущую сессию
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for temp_dir in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(temp_dir, ignore_errors=True)
        del user_sessions[user_id]
    
    if user_id in media_groups:
        del media_groups[user_id]
    
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для печати фото и документов. 📸🖨️\n\n"
        "✨ **Что я умею:**\n"
        "• Считать страницы в PDF и Word\n"
        "• Принимать несколько файлов сразу\n"
        "• Рассчитывать стоимость\n\n"
        "📎 Поддерживаемые форматы: JPG, PNG, PDF, DOC, DOCX\n\n"
        f"📞 **Контакт:** {CONTACT_PHONE}\n"
        f"🚚 **Доставка:** {DELIVERY_OPTIONS}\n\n"
        "Просто отправь мне файлы для заказа!"
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    return WAITING_FOR_FILE

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик файлов"""
    user_id = update.effective_user.id
    
    if update.message.media_group_id:
        return await handle_media_group(update, context)
    else:
        return await handle_single_file(update, context)

async def handle_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает группу файлов"""
    user_id = update.effective_user.id
    media_group_id = update.message.media_group_id
    
    if user_id not in media_groups:
        media_groups[user_id] = {}
    
    if media_group_id not in media_groups[user_id]:
        media_groups[user_id][media_group_id] = []
    
    media_groups[user_id][media_group_id].append(update.message)
    
    async def process_group():
        await asyncio.sleep(2)
        if user_id in media_groups and media_group_id in media_groups[user_id]:
            messages = media_groups[user_id].pop(media_group_id)
            if messages:
                await process_multiple_files(user_id, messages, context)
    
    asyncio.create_task(process_group())
    return WAITING_FOR_FILE

async def process_multiple_files(user_id, messages, context):
    """Обрабатывает несколько файлов"""
    try:
        username = messages[0].from_user.username or messages[0].from_user.first_name
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "temp_files": [],
                "temp_dirs": [],
                "total_pages": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": messages[0].from_user.first_name
                }
            }
        
        doc_count = 0
        photo_count = 0
        
        for message in messages:
            if message.document:
                file = message.document
                file_name = file.file_name
                if file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                    file_type = "photo"
                    photo_count += 1
                elif file_name.lower().endswith(('.pdf', '.docx', '.doc')):
                    file_type = "doc"
                    doc_count += 1
                else:
                    continue
            elif message.photo:
                file = message.photo[-1]
                file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                file_type = "photo"
                photo_count += 1
            else:
                continue
            
            file_path, temp_dir = await download_file(file, file_name, user_id)
            if not file_path:
                continue
            
            page_count = await count_pages_in_file(file_path, file_name)
            
            user_sessions[user_id]["temp_files"].append({
                "path": file_path,
                "name": file_name,
                "type": file_type,
                "pages": page_count
            })
            user_sessions[user_id]["temp_dirs"].append(temp_dir)
            user_sessions[user_id]["total_pages"] += page_count
        
        main_type = "doc" if doc_count > 0 else "photo"
        user_sessions[user_id]["file_type"] = main_type
        
        files_count = len(user_sessions[user_id]["temp_files"])
        total_pages = user_sessions[user_id]["total_pages"]
        
        if main_type == "doc":
            text = (f"📄 Загружено **{files_count}** документов!\n"
                   f"📊 Всего страниц: **{total_pages}**\n\n"
                   f"Выберите тип печати:")
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        else:
            text = (f"📸 Загружено **{files_count}** фото!\n\n"
                   f"Выберите формат печати:")
            keyboard = [
                [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        
        await messages[0].reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        return SELECTING_DOC_TYPE if main_type == "doc" else SELECTING_PHOTO_FORMAT
        
    except Exception as e:
        logger.error(f"Ошибка обработки файлов: {e}")
        return WAITING_FOR_FILE

async def handle_single_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает один файл"""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    try:
        if update.message.document:
            file = update.message.document
            file_name = file.file_name
            if file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                file_type = "photo"
            elif file_name.lower().endswith(('.pdf', '.docx', '.doc')):
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
        
        page_count = await count_pages_in_file(file_path, file_name)
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "temp_files": [],
                "temp_dirs": [],
                "total_pages": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": update.effective_user.first_name
                }
            }
        
        user_sessions[user_id]["temp_files"] = [{
            "path": file_path,
            "name": file_name,
            "type": file_type,
            "pages": page_count
        }]
        user_sessions[user_id]["temp_dirs"] = [temp_dir]
        user_sessions[user_id]["total_pages"] = page_count
        user_sessions[user_id]["file_type"] = file_type
        
        if file_type == "photo":
            text = f"📸 Загружено фото. Выберите формат:"
            keyboard = [
                [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        else:
            text = f"📄 Загружен документ ({page_count} стр.). Выберите тип печати:"
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        return SELECTING_DOC_TYPE if file_type == "doc" else SELECTING_PHOTO_FORMAT
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("Произошла ошибка")
        return WAITING_FOR_FILE

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок"""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data}")
    
    if data == "cancel_order":
        if user_id in user_sessions:
            if "temp_dirs" in user_sessions[user_id]:
                for temp_dir in user_sessions[user_id]["temp_dirs"]:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            del user_sessions[user_id]
        await query.message.edit_text("❌ Заказ отменен")
        return WAITING_FOR_FILE
    
    if data == "new_order":
        if user_id in user_sessions:
            if "temp_dirs" in user_sessions[user_id]:
                for temp_dir in user_sessions[user_id]["temp_dirs"]:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            del user_sessions[user_id]
        await query.message.edit_text("🔄 Отправьте файлы для нового заказа")
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        format_type = data.split("_")[1]
        user_sessions[user_id]["format"] = format_type
        text = "Сколько копий напечатать?"
        await query.message.edit_text(text, reply_markup=get_quantity_keyboard())
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        color_type = data.split("_")[1]
        user_sessions[user_id]["color"] = color_type
        text = "Сколько копий напечатать?"
        await query.message.edit_text(text, reply_markup=get_quantity_keyboard())
        return ENTERING_QUANTITY
    
    if data.startswith("qty_"):
        try:
            quantity = int(data.split("_")[1])
            user_sessions[user_id]["quantity"] = quantity
            session = user_sessions[user_id]
            
            # Детальный расчёт
            price_details = calculate_detailed_prices(session)
            session["total"] = price_details["total_sum"]
            session["total_pages_all"] = price_details["total_pages_all"]
            session["delivery"] = estimate_delivery_time(price_details["total_pages_all"])
            
            # Формируем детальный отчёт
            detailed = "📊 **ДЕТАЛЬНЫЙ РАСЧЁТ:**\n\n"
            for item in price_details["detailed"]:
                if item["type"] == "photo":
                    detailed += f"📸 Файл {item['num']}: {item['name'][:30]}...\n"
                    detailed += f"   • {item['pages']} стр. × {item['copies']} копий\n"
                    detailed += f"   • {item['price_per']} руб./копия\n"
                    detailed += f"   • **{item['total']} руб.**\n\n"
                else:
                    detailed += f"📄 Файл {item['num']}: {item['name'][:30]}...\n"
                    detailed += f"   • {item['pages']} стр. × {item['copies']} копий = {item['total_pages']} стр.\n"
                    detailed += f"   • {item['price_per']} руб./стр.\n"
                    detailed += f"   • **{item['total']} руб.**\n\n"
            
            text = (f"{detailed}\n"
                   f"📋 **ИТОГО:**\n"
                   f"📦 Файлов: {price_details['files_count']}\n"
                   f"📊 Страниц: {price_details['total_pages_all']}\n"
                   f"💰 **Сумма: {price_details['total_sum']} руб.**\n"
                   f"⏳ Срок: {session['delivery']}\n\n"
                   "Подтверждаете заказ?")
            
            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_order"),
                 InlineKeyboardButton("❌ Отменить", callback_data="cancel_order")]
            ]
            
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return CONFIRMING_ORDER
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return ENTERING_QUANTITY
    
    if data == "confirm_order":
        try:
            session = user_sessions.get(user_id, {})
            files = session.get("temp_files", [])
            
            if not session or not files:
                await query.edit_message_text("Ошибка: данные не найдены")
                return WAITING_FOR_FILE
            
            # Сохраняем заказ
            success, order_folder = save_order_to_files(
                user_id,
                session['user_info']['username'],
                session,
                files
            )
            
            if success:
                text = (
                    "✅ **ЗАКАЗ ОФОРМЛЕН!**\n\n"
                    f"👤 Клиент: {session['user_info']['first_name']}\n"
                    f"📦 Файлов: {len(files)}\n"
                    f"📊 Страниц: {session['total_pages_all']}\n"
                    f"💰 Сумма: {session['total']} руб.\n"
                    f"⏳ Срок: {session['delivery']}\n\n"
                    f"📞 Контакт: {CONTACT_PHONE}\n"
                    f"🚚 Доставка: {DELIVERY_OPTIONS}\n\n"
                    "Спасибо за заказ!"
                )
            else:
                text = "❌ Ошибка при сохранении заказа"
            
            # Очищаем временные файлы
            if "temp_dirs" in session:
                for temp_dir in session["temp_dirs"]:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            
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
            
        except Exception as e:
            logger.error(f"Ошибка подтверждения: {e}")
            return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

def get_quantity_keyboard():
    """Клавиатура выбора количества"""
    keyboard = [
        [InlineKeyboardButton("1", callback_data="qty_1"),
         InlineKeyboardButton("2", callback_data="qty_2"),
         InlineKeyboardButton("3", callback_data="qty_3"),
         InlineKeyboardButton("4", callback_data="qty_4"),
         InlineKeyboardButton("5", callback_data="qty_5")],
        [InlineKeyboardButton("10", callback_data="qty_10"),
         InlineKeyboardButton("20", callback_data="qty_20"),
         InlineKeyboardButton("30", callback_data="qty_30"),
         InlineKeyboardButton("50", callback_data="qty_50"),
         InlineKeyboardButton("100", callback_data="qty_100")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ручной ввод количества"""
    user_id = update.effective_user.id
    text = update.message.text
    
    quantity = extract_number_from_text(text)
    if quantity is None or quantity < 1 or quantity > 1000:
        await update.message.reply_text(
            "Введите число от 1 до 1000:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    # Создаём callback как при нажатии кнопки
    query = type('Query', (), {
        'data': f'qty_{quantity}',
        'from_user': update.effective_user,
        'message': update.message,
        'answer': lambda: None
    })
    
    return await button_handler(update, context)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}")

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========

def init_bot():
    """Инициализирует бота и все обработчики"""
    global application, bot_initialized
    
    try:
        logger.info("=" * 60)
        logger.info("🚀 ИНИЦИАЛИЗАЦИЯ БОТА")
        logger.info("=" * 60)
        
        # Создаём приложение
        application = Application.builder().token(TOKEN).build()
        
        # Регистрируем обработчики
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
                SELECTING_PHOTO_FORMAT: [
                    CallbackQueryHandler(button_handler),
                ],
                SELECTING_DOC_TYPE: [
                    CallbackQueryHandler(button_handler),
                ],
                ENTERING_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quantity_input),
                    CallbackQueryHandler(button_handler),
                ],
                CONFIRMING_ORDER: [
                    CallbackQueryHandler(button_handler),
                ],
            },
            fallbacks=[CommandHandler("start", start)],
        )
        
        application.add_handler(conv_handler)
        application.add_error_handler(error_handler)
        
        # Создаём event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Инициализируем
        loop.run_until_complete(application.initialize())
        loop.run_until_complete(application.start())
        
        # Устанавливаем веб-хук
        if RENDER_EXTERNAL_URL:
            webhook_url = f"{RENDER_EXTERNAL_URL}/webhook"
            logger.info(f"🔧 Установка веб-хука: {webhook_url}")
            
            loop.run_until_complete(application.bot.delete_webhook(drop_pending_updates=True))
            loop.run_until_complete(application.bot.set_webhook(
                url=webhook_url,
                allowed_updates=Update.ALL_TYPES
            ))
            
            logger.info("✅ Веб-хук установлен")
        
        bot_initialized = True
        logger.info("✅ БОТ ГОТОВ К РАБОТЕ!")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        logger.error(traceback.format_exc())
        return False

# ========== FLASK ПРИЛОЖЕНИЕ ==========

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка обновлений от Telegram"""
    if not bot_initialized or application is None:
        return jsonify({"error": "Bot not initialized"}), 500
    
    try:
        update_data = request.get_json()
        if not update_data:
            return jsonify({"error": "No data"}), 400
        
        update = Update.de_json(update_data, application.bot)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(application.process_update(update))
        
        return "OK", 200
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    """Проверка здоровья"""
    return jsonify({
        "status": "ok",
        "bot_ready": bot_initialized,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/stats')
def stats():
    """Статистика"""
    orders_count = len(os.listdir(ORDERS_FOLDER)) if os.path.exists(ORDERS_FOLDER) else 0
    return jsonify({
        "orders": orders_count,
        "active_sessions": len(user_sessions),
        "bot_ready": bot_initialized
    })

@app.route('/debug')
def debug():
    """Отладка"""
    return jsonify({
        "bot_initialized": bot_initialized,
        "webhook": f"{RENDER_EXTERNAL_URL}/webhook" if RENDER_EXTERNAL_URL else None,
        "token_set": bool(TOKEN),
        "orders_folder": ORDERS_FOLDER
    })

@app.route('/')
def home():
    """Главная страница"""
    status = "✅ Бот работает!" if bot_initialized else "❌ Бот не инициализирован"
    return f"""
    <html>
        <head><title>Print Bot</title>
        <style>
            body {{ font-family: Arial; text-align: center; margin-top: 50px; }}
            .status {{ padding: 20px; }}
        </style>
        </head>
        <body>
            <h1>🤖 Print Bot</h1>
            <h2 class="status">{status}</h2>
            <p>Используйте Telegram для заказов.</p>
            <p><a href="/stats">Статистика</a> | <a href="/debug">Отладка</a></p>
        </body>
    </html>
    """

# ========== ЗАПУСК ==========

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 ЗАПУСК БОТА")
    print("=" * 60)
    
    if init_bot():
        print(f"🌐 Запуск Flask на порту {PORT}")
        app.run(host='0.0.0.0', port=PORT, debug=False)
    else:
        print("❌ Не удалось инициализировать бота")
        sys.exit(1)
