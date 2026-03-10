#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
✨ ПРОСТАЯ И КРАСИВАЯ ВЕРСИЯ ✨
"""

import os
import sys
import logging
import tempfile
import json
import re
import shutil
import traceback
import zipfile
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template_string, abort

# Используем синхронную версию python-telegram-bot
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, Filters

import PyPDF2
from docx import Document

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: TOKEN не задан в переменных окружения!")
    sys.exit(1)

# ID администратора для уведомлений
ADMIN_CHAT_ID = 483613049

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("❌ ОШИБКА: RENDER_EXTERNAL_URL не задан!")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб, СДЭК, Яндекс Доставка"

# ========== ПУТЬ К ПАПКЕ ЗАКАЗОВ ==========
ORDERS_FOLDER = "заказы"
ORDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ORDERS_FOLDER)

try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"📁 Папка заказов: {ORDERS_PATH}")
except Exception as e:
    print(f"❌ Ошибка создания папки: {e}")
    sys.exit(1)

# ========== ФАЙЛ ДЛЯ ХРАНЕНИЯ ИСТОРИИ ЗАКАЗОВ ==========
ORDERS_DB_FILE = os.path.join(ORDERS_PATH, "orders_history.json")

# ========== СТАТУСЫ ЗАКАЗОВ ==========
ORDER_STATUSES = {
    "new": "🆕 Новый",
    "processing": "🔄 В обработке",
    "printing": "🖨️ В печати",
    "ready": "✅ Готов",
    "shipped": "📦 Отправлен",
    "delivered": "🏁 Доставлен",
    "cancelled": "❌ Отменен"
}

def get_status_display(status):
    return ORDER_STATUSES.get(status, status)

# Загружаем историю заказов
def load_orders_history():
    try:
        if os.path.exists(ORDERS_DB_FILE):
            with open(ORDERS_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"Ошибка загрузки истории: {e}")
        return []

def save_order_to_history(order_data):
    try:
        history = load_orders_history()
        history.append(order_data)
        with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {e}")
        return False

def update_order_status(order_id, new_status):
    try:
        history = load_orders_history()
        updated = False
        user_id = None
        
        for order in history:
            if order.get('order_id') == order_id:
                order['status'] = new_status
                user_id = order.get('user_id')
                updated = True
                break
        
        if updated:
            with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
            order_folder = os.path.join(ORDERS_PATH, order_id)
            info_file = os.path.join(order_folder, "информация_о_заказе.txt")
            if os.path.exists(info_file):
                with open(info_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                content = re.sub(r'Статус:.*\n', f'Статус: {get_status_display(new_status)}\n', content)
                
                with open(info_file, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            if user_id and bot:
                try:
                    bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"📢 *Статус вашего заказа изменен*\n\n"
                            f"🆔 Заказ: `{order_id}`\n"
                            f"📌 Новый статус: {get_status_display(new_status)}\n\n"
                            f"❤️ Спасибо за заказ!"
                        ),
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
            
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

def format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

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
group_timers = {}
updater = None
dispatcher = None
bot = None

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

def calculate_price(price_dict, quantity):
    for (min_q, max_q), price in price_dict.items():
        if min_q <= quantity <= max_q:
            return price * quantity
    return 0

def estimate_delivery_time(total_items):
    if total_items <= 50:
        return "1 день ⚡"
    elif total_items <= 200:
        return "2 дня ⏰"
    else:
        return "3 дня 📅"

def extract_number_from_text(text):
    numbers = re.findall(r'\d+', text)
    return int(numbers[0]) if numbers else None

def count_items_in_file(file_path, file_name):
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                page_count = len(pdf.pages)
                return page_count, "страниц", "документ"
                
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            paragraphs = len(doc.paragraphs)
            estimated_pages = max(1, paragraphs // 35)
            tables_count = len(doc.tables)
            if tables_count > 0:
                estimated_pages += tables_count // 2
            return estimated_pages, "страниц", "документ"
            
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
            return 1, "фото", "фото"
            
        return 1, "единиц", "неизвестно"
    except Exception as e:
        logger.error(f"Ошибка подсчета: {e}")
        return 1, "единиц", "неизвестно"

def download_file(file_obj, file_name):
    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file_name)
        
        if hasattr(file_obj, 'get_file'):
            file = file_obj.get_file()
            file.download(custom_path=file_path)
        elif hasattr(file_obj, 'download'):
            file_obj.download(custom_path=file_path)
        else:
            with open(file_path, 'wb') as f:
                file_content = file_obj.download_as_bytearray()
                f.write(file_content)
        
        return file_path, temp_dir
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None, None

def save_order_to_folder(user_id, username, order_data, files_info):
    try:
        clean_name = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_id = f"{clean_name}_{timestamp}"
        order_folder = os.path.join(ORDERS_PATH, order_id)
        os.makedirs(order_folder, exist_ok=True)
        
        for i, f in enumerate(files_info, 1):
            if os.path.exists(f['path']):
                safe_name = re.sub(r'[<>:"/\\|?*]', '', f['name'])
                new_path = os.path.join(order_folder, f"{i}_{safe_name}")
                shutil.copy2(f['path'], new_path)
        
        photo_files = [ff for ff in files_info if ff['type'] == 'photo']
        doc_files = [ff for ff in files_info if ff['type'] == 'doc']
        
        total_photos = sum(ff['items'] for ff in photo_files)
        total_pages = sum(ff['items'] for ff in doc_files)
        
        info_file = os.path.join(order_folder, "информация_о_заказе.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"ЗАКАЗ ОТ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")
            f.write(f"Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"ID: {user_id}\n")
            f.write(f"Телефон: {CONTACT_PHONE}\n")
            f.write(f"Статус: {get_status_display('new')}\n\n")
            
            if order_data['type'] == 'photo':
                format_names = {"small": "Малый (A6/10x15)", "medium": "Средний (13x18/15x21)", "large": "Большой (A4/21x30)"}
                f.write(f"Тип: Фото\n")
                f.write(f"Формат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "Черно-белая", "color": "Цветная"}
                f.write(f"Тип: Документы\n")
                f.write(f"Печать: {color_names[order_data['color']]}\n")
            
            f.write(f"Количество копий: {order_data['quantity']}\n\n")
            
            if photo_files:
                f.write(f"ФОТО:\n")
                f.write(f"  • Количество фото: {len(photo_files)}\n")
                f.write(f"  • Всего фото в оригинале: {total_photos}\n")
                f.write(f"  • Всего фото к печати: {total_photos * order_data['quantity']}\n\n")
            
            if doc_files:
                f.write(f"ДОКУМЕНТЫ:\n")
                f.write(f"  • Количество документов: {len(doc_files)}\n")
                f.write(f"  • Всего страниц в оригинале: {total_pages}\n")
                f.write(f"  • Всего страниц к печати: {total_pages * order_data['quantity']}\n\n")
            
            f.write(f"ИТОГО К ОПЛАТЕ: {order_data['total']} руб.\n")
            f.write(f"Срок выполнения: {order_data['delivery']}\n\n")
            
            f.write("ФАЙЛЫ:\n")
            for i, file_info in enumerate(files_info, 1):
                icon = "📸" if file_info['type'] == 'photo' else "📄"
                type_text = "фото" if file_info['type'] == 'photo' else "документ"
                unit_text = "фото" if file_info['type'] == 'photo' else "страниц"
                f.write(f"{icon} {i}. {file_info['name']}\n")
                f.write(f"   • Тип: {type_text}\n")
                f.write(f"   • Количество: {file_info['items']} {unit_text}\n")
            
            f.write(f"\nВсего файлов: {len(files_info)}")
        
        history_entry = {
            "order_id": order_id,
            "folder": order_folder,
            "user_id": user_id,
            "username": username,
            "user_name": order_data['user_info']['first_name'],
            "date": datetime.now().isoformat(),
            "type": order_data['type'],
            "quantity": order_data['quantity'],
            "total_photos": total_photos,
            "total_pages": total_pages,
            "total_price": order_data['total'],
            "delivery": order_data['delivery'],
            "status": "new"
        }
        save_order_to_history(history_entry)
        
        return True, order_id, order_folder
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        logger.error(traceback.format_exc())
        return False, None, None

def send_admin_notification(order_data, order_id, order_folder):
    try:
        order_url = f"{RENDER_URL}/orders/{order_id}/"
        
        photo_files = [f for f in order_data['files'] if f['type'] == 'photo']
        doc_files = [f for f in order_data['files'] if f['type'] == 'doc']
        
        total_photos = sum(f['items'] for f in photo_files)
        total_pages = sum(f['items'] for f in doc_files)
        
        admin_message = (
            f"🎉 *НОВЫЙ ЗАКАЗ!*\n\n"
            f"👤 Клиент: {order_data['user_info']['first_name']}\n"
            f"🆔 Username: @{order_data['user_info']['username']}\n"
            f"📱 ID: `{order_data['user_info']['user_id']}`\n\n"
        )
        
        if order_data['type'] == 'photo':
            format_names = {"small": "Малый (A6)", "medium": "Средний", "large": "Большой (A4)"}
            admin_message += (
                f"📸 Тип: Фотопечать\n"
                f"📏 Формат: {format_names[order_data['format']]}\n"
            )
        else:
            color_names = {"bw": "⚫ Черно-белая", "color": "🎨 Цветная"}
            admin_message += (
                f"📄 Тип: Документы\n"
                f"🎨 Печать: {color_names[order_data['color']]}\n"
            )
        
        admin_message += (
            f"📦 Копий: {order_data['quantity']}\n"
            f"📎 Файлов: {len(order_data['files'])}\n\n"
        )
        
        if photo_files:
            admin_message += f"📸 Фото: {len(photo_files)} файлов, {total_photos} фото\n"
        if doc_files:
            admin_message += f"📄 Документы: {len(doc_files)} файлов, {total_pages} страниц\n"
        
        admin_message += (
            f"\n💰 Сумма: {order_data['total']} руб.\n"
            f"⏰ Срок: {order_data['delivery']}\n\n"
            f"🔗 Ссылка: {order_url}"
        )
        
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message,
                parse_mode="Markdown"
            )
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

# ========== ФОРМАТИРОВАНИЕ СООБЩЕНИЙ ==========
def format_welcome_message(user_first_name):
    return (
        f"👋 *Добро пожаловать, {user_first_name}!*\n\n"
        f"📸🖨️ Я помогу распечатать фото и документы\n\n"
        f"📎 *Как это работает:*\n"
        f"1️⃣ Отправляй файлы (можно несколько)\n"
        f"2️⃣ Выбери параметры печати\n"
        f"3️⃣ Получи расчёт стоимости\n"
        f"4️⃣ Подтверди заказ\n\n"
        f"📞 Контакт: {CONTACT_PHONE}\n"
        f"🚚 Доставка: {DELIVERY_OPTIONS}"
    )

def format_file_added_message(stats):
    photo_count = stats.get('photo_count', 0)
    doc_count = stats.get('doc_count', 0)
    total_photos = stats.get('total_photos', 0)
    total_pages = stats.get('total_pages', 0)
    files_count = stats.get('files_count', 0)
    
    text = f"✅ *Файл добавлен!*\n\n"
    text += f"📊 *Статистика:*\n"
    
    if photo_count > 0:
        text += f"📸 Фото: {photo_count} файлов\n"
    if doc_count > 0:
        text += f"📄 Документы: {doc_count} файлов\n"
    text += f"📦 Всего файлов: {files_count}\n"
    
    if total_photos > 0:
        text += f"\n📸 Фото в оригинале: {total_photos}\n"
    if total_pages > 0:
        text += f"📄 Страниц: {total_pages}\n"
    text += "\n"
    
    return text

def format_photo_format_choice():
    return (
        f"📸 *Выберите формат печати*\n\n"
        f"🖼 *Малый* – A6 / 10x15 см\n"
        f"🖼 *Средний* – 13x18 / 15x21 см\n"
        f"🖼 *Большой* – A4 / 21x30 см\n\n"
        f"💰 Цены зависят от количества"
    )

def format_doc_type_choice():
    return (
        f"📄 *Выберите тип печати*\n\n"
        f"⚫ *Черно-белая* – для текстов\n"
        f"🎨 *Цветная* – для графики\n\n"
        f"💰 Цены:\n"
        f"⚫ от 10 руб./стр.\n"
        f"🎨 от 20 руб./стр."
    )

def format_quantity_choice(total_photos, total_pages, total_items):
    text = f"🔢 *Выберите количество копий*\n\n"
    text += f"📊 *В ваших файлах:*\n"
    
    if total_photos > 0:
        text += f"📸 Фото: {total_photos}\n"
    if total_pages > 0:
        text += f"📄 Страниц: {total_pages}\n"
    text += f"📦 Всего единиц: {total_items}\n\n"
    text += f"👉 *Введите число* от 1 до 1000\n"
    text += f"или выберите из кнопок:"
    
    return text

def format_order_summary(session, details):
    total_photos_result = session.get('total_photos', 0)
    total_pages_result = session.get('total_pages', 0)
    total = session.get('total', 0)
    delivery = session.get('delivery', '')
    files = session.get('files', [])
    
    text = f"{details}\n"
    text += f"📦 *Итог:*\n"
    text += f"📎 Всего файлов: {len(files)}\n"
    
    if total_photos_result > 0:
        text += f"📸 Фото к печати: {total_photos_result}\n"
    if total_pages_result > 0:
        text += f"📄 Страниц к печати: {total_pages_result}\n"
    text += f"\n"
    text += f"💰 *ИТОГО:* {total} руб.\n"
    text += f"⏰ Срок: {delivery}\n\n"
    text += f"❓ *Всё верно?*"
    
    return text

def format_order_confirmation(order_id, session):
    total_photos = session.get('total_photos', 0)
    total_pages = session.get('total_pages', 0)
    total = session.get('total', 0)
    delivery = session.get('delivery', '')
    quantity = session.get('quantity', 1)
    
    photo_files = [f for f in session['files'] if f['type'] == 'photo']
    doc_files = [f for f in session['files'] if f['type'] == 'doc']
    original_photos = sum(f['items'] for f in photo_files)
    original_pages = sum(f['items'] for f in doc_files)
    
    text = (
        f"✅ *ЗАКАЗ ОФОРМЛЕН!*\n\n"
        f"🆔 Номер: `{order_id}`\n"
        f"👤 Клиент: {session['user_info']['first_name']}\n"
    )
    
    if original_photos > 0:
        text += f"\n📸 Фото в оригинале: {original_photos}\n"
        text += f"📸 Фото к печати: {original_photos * quantity}\n"
    if original_pages > 0:
        text += f"\n📄 Страниц в оригинале: {original_pages}\n"
        text += f"📄 Страниц к печати: {original_pages * quantity}\n"
    text += f"\n"
    text += f"💰 Сумма: {total} руб.\n"
    text += f"⏰ Срок: {delivery}\n\n"
    text += f"📞 Контакт: {CONTACT_PHONE}\n"
    text += f"🚚 Доставка: {DELIVERY_OPTIONS}\n\n"
    text += f"📌 Статус: {get_status_display('new')}\n\n"
    text += f"❤️ *Спасибо за заказ!*"
    
    return text

# ========== ОБРАБОТЧИКИ КОМАНД ==========
def start(update, context):
    user = update.effective_user
    user_id = user.id
    logger.info(f"✅ /start от {user_id}")
    
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    welcome = format_welcome_message(user.first_name)
    
    update.message.reply_text(
        welcome,
        parse_mode="Markdown"
    )
    return WAITING_FOR_FILE

def handle_file(update, context):
    user_id = update.effective_user.id
    message = update.message
    
    if message.media_group_id:
        return handle_media_group(update, context)
    
    return process_single_file(update, context)

def handle_media_group(update, context):
    user_id = update.effective_user.id
    message = update.message
    media_group_id = message.media_group_id
    
    if user_id not in media_groups:
        media_groups[user_id] = {}
    
    if media_group_id not in media_groups[user_id]:
        media_groups[user_id][media_group_id] = []
    
    media_groups[user_id][media_group_id].append(message)
    
    timer_key = f"{user_id}_{media_group_id}"
    if timer_key in group_timers:
        group_timers[timer_key].cancel()
    
    timer = threading.Timer(2.0, process_media_group, args=[user_id, media_group_id, context])
    timer.daemon = True
    timer.start()
    group_timers[timer_key] = timer
    
    return WAITING_FOR_FILE

def process_media_group(user_id, media_group_id, context):
    try:
        if user_id not in media_groups or media_group_id not in media_groups[user_id]:
            return
        
        messages = media_groups[user_id].pop(media_group_id)
        if not messages:
            return
        
        timer_key = f"{user_id}_{media_group_id}"
        if timer_key in group_timers:
            del group_timers[timer_key]
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "files": [],
                "temp_dirs": [],
                "total_photos": 0,
                "total_pages": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": messages[0].from_user.username or messages[0].from_user.first_name,
                    "first_name": messages[0].from_user.first_name,
                    "last_name": messages[0].from_user.last_name or ""
                }
            }
        else:
            if "total_photos" not in user_sessions[user_id]:
                user_sessions[user_id]["total_photos"] = 0
            if "total_pages" not in user_sessions[user_id]:
                user_sessions[user_id]["total_pages"] = 0
        
        doc_count = 0
        photo_count = 0
        
        for message in messages:
            file_obj = None
            file_name = None
            file_type = None
            
            if message.document:
                file_obj = message.document
                file_name = file_obj.file_name
                ext = file_name.lower().split('.')[-1]
                if ext in ['jpg', 'jpeg', 'png']:
                    file_type = "photo"
                    photo_count += 1
                elif ext in ['pdf', 'doc', 'docx']:
                    file_type = "doc"
                    doc_count += 1
                else:
                    continue
            elif message.photo:
                file_obj = message.photo[-1]
                file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                file_type = "photo"
                photo_count += 1
            else:
                continue
            
            file_path, temp_dir = download_file(file_obj, file_name)
            if not file_path:
                continue
            
            items, unit, type_name = count_items_in_file(file_path, file_name)
            
            file_info = {
                "path": file_path,
                "name": file_name,
                "type": file_type,
                "items": items,
                "unit": unit,
                "type_name": type_name
            }
            user_sessions[user_id]["files"].append(file_info)
            user_sessions[user_id]["temp_dirs"].append(temp_dir)
            
            if file_type == 'photo':
                user_sessions[user_id]["total_photos"] += items
            else:
                user_sessions[user_id]["total_pages"] += items
        
        if not user_sessions[user_id]["files"]:
            context.bot.send_message(
                chat_id=user_id,
                text="❌ *Ошибка загрузки файлов*",
                parse_mode="Markdown"
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        stats = {
            'photo_count': photo_count,
            'doc_count': doc_count,
            'files_count': files_count,
            'total_photos': total_photos,
            'total_pages': total_pages
        }
        
        text = format_file_added_message(stats)
        
        if doc_count > 0:
            text += format_doc_type_choice()
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("📎 Добавить ещё", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
        else:
            text += format_photo_format_choice()
            keyboard = [
                [InlineKeyboardButton("🖼 Малый", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой", callback_data="photo_large")],
                [InlineKeyboardButton("📎 Добавить ещё", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
            ]
        
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Ошибка: {e}")

def process_single_file(update, context):
    user_id = update.effective_user.id
    message = update.message
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "files": [],
            "temp_dirs": [],
            "total_photos": 0,
            "total_pages": 0,
            "user_info": {
                "user_id": user_id,
                "username": update.effective_user.username or update.effective_user.first_name,
                "first_name": update.effective_user.first_name,
                "last_name": update.effective_user.last_name or ""
            }
        }
    else:
        if "total_photos" not in user_sessions[user_id]:
            user_sessions[user_id]["total_photos"] = 0
        if "total_pages" not in user_sessions[user_id]:
            user_sessions[user_id]["total_pages"] = 0
    
    file_obj = None
    file_name = None
    file_type = None
    
    if message.document:
        file_obj = message.document
        file_name = file_obj.file_name
        ext = file_name.lower().split('.')[-1]
        if ext in ['jpg', 'jpeg', 'png']:
            file_type = "photo"
        elif ext in ['pdf', 'doc', 'docx']:
            file_type = "doc"
        else:
            message.reply_text(
                "❌ *Неподдерживаемый формат*\n\nОтправьте: JPG, PNG, PDF, DOC, DOCX",
                parse_mode="Markdown"
            )
            return WAITING_FOR_FILE
    elif message.photo:
        file_obj = message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE
    
    file_path, temp_dir = download_file(file_obj, file_name)
    if not file_path:
        message.reply_text(
            "❌ *Ошибка загрузки*",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    items, unit, type_name = count_items_in_file(file_path, file_name)
    
    file_info = {
        "path": file_path,
        "name": file_name,
        "type": file_type,
        "items": items,
        "unit": unit,
        "type_name": type_name
    }
    user_sessions[user_id]["files"].append(file_info)
    user_sessions[user_id]["temp_dirs"].append(temp_dir)
    
    if file_type == 'photo':
        user_sessions[user_id]["total_photos"] += items
    else:
        user_sessions[user_id]["total_pages"] += items
    
    files_count = len(user_sessions[user_id]["files"])
    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')
    total_photos = user_sessions[user_id]["total_photos"]
    total_pages = user_sessions[user_id]["total_pages"]
    
    stats = {
        'photo_count': photo_count,
        'doc_count': doc_count,
        'files_count': files_count,
        'total_photos': total_photos,
        'total_pages': total_pages
    }
    
    text = format_file_added_message(stats)
    
    if doc_count > 0:
        text += format_doc_type_choice()
        keyboard = [
            [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
            [InlineKeyboardButton("📎 Добавить ещё", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
    else:
        text += format_photo_format_choice()
        keyboard = [
            [InlineKeyboardButton("🖼 Малый", callback_data="photo_small")],
            [InlineKeyboardButton("🖼 Средний", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼 Большой", callback_data="photo_large")],
            [InlineKeyboardButton("📎 Добавить ещё", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
        ]
    
    message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_FOR_FILE

def cancel_order(user_id, query=None, context=None):
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except:
                    pass
        del user_sessions[user_id]
        logger.info(f"✅ Сессия пользователя {user_id} очищена")
    
    keyboard = [[InlineKeyboardButton("🔄 Новый заказ", callback_data="new_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "❌ *Заказ отменён*\n\nВсе файлы удалены.\n\n🔄 Хотите оформить новый заказ?"
    
    if query:
        try:
            query.edit_message_text(
                text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        except:
            if context:
                context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
    elif context:
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    
    return WAITING_FOR_FILE

def button_handler(update, context):
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text(
            "📤 *Отправьте следующие файлы*",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data == "new_order":
        if user_id in user_sessions:
            if "temp_dirs" in user_sessions[user_id]:
                for d in user_sessions[user_id]["temp_dirs"]:
                    try:
                        shutil.rmtree(d, ignore_errors=True)
                    except:
                        pass
            del user_sessions[user_id]
        
        query.edit_message_text(
            "🔄 *Новый заказ*\n\nОтправьте файлы для печати:",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        total_items = total_photos + total_pages
        
        text = format_quantity_choice(total_photos, total_pages, total_items)
        
        query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = data.split("_")[1]
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        total_items = total_photos + total_pages
        
        text = format_quantity_choice(total_photos, total_pages, total_items)
        
        query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("qty_"):
        quantity = int(data.split("_")[1])
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)
        
        session["quantity"] = quantity
        
        files = session["files"]
        file_type = session["type"]
        
        total = 0
        total_photos_result = 0
        total_pages_result = 0
        details = "📊 *Детальный расчёт:*\n\n"
        
        for i, f in enumerate(files, 1):
            if f['type'] == 'photo':
                total_photos_result += f['items'] * quantity
            else:
                total_pages_result += f['items'] * quantity
        
        for i, f in enumerate(files, 1):
            if file_type == "photo":
                price_dict = PHOTO_PRICES[session["format"]]
                file_total = calculate_price(price_dict, quantity)
                total += file_total
                details += (
                    f"📸 *Файл {i}:*\n"
                    f"📄 {f['name'][:30]}...\n"
                    f"📸 {f['items']} фото × {quantity} = {f['items'] * quantity} фото\n"
                    f"💰 {file_total // quantity} руб./копия\n"
                    f"💰 Итого: {file_total} руб.\n\n"
                )
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += (
                    f"📄 *Файл {i}:*\n"
                    f"📄 {f['name'][:30]}...\n"
                    f"📄 {f['items']} стр. × {quantity} = {file_items} стр.\n"
                    f"💰 {file_total // file_items} руб./стр.\n"
                    f"💰 Итого: {file_total} руб.\n\n"
                )
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = format_order_summary(session, details)
        
        keyboard = [
            [InlineKeyboardButton("✅ Да", callback_data="confirm"),
             InlineKeyboardButton("❌ Нет", callback_data="cancel")]
        ]
        
        try:
            query.message.delete()
        except:
            pass
        
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CONFIRMING_ORDER
    
    if data == "confirm":
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)
        
        success, order_id, folder = save_order_to_folder(
            user_id,
            session['user_info']['username'],
            session,
            session['files']
        )
        
        if success:
            send_admin_notification(session, order_id, folder)
            
            client_message = format_order_confirmation(order_id, session)
            
            context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode="Markdown"
            )
            
            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            if photo_files:
                try:
                    media_group = []
                    for i, photo_file in enumerate(photo_files[:5]):
                        with open(photo_file['path'], 'rb') as photo:
                            if i == 0:
                                caption = f"📸 Загруженные фото ({len(photo_files)} шт.)"
                                media_group.append(InputMediaPhoto(photo.read(), caption=caption))
                            else:
                                media_group.append(InputMediaPhoto(photo.read()))
                    
                    if media_group:
                        context.bot.send_media_group(
                            chat_id=user_id,
                            media=media_group
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки предпросмотра: {e}")
            
        else:
            context.bot.send_message(
                chat_id=user_id,
                text="❌ *Ошибка при сохранении заказа*",
                parse_mode="Markdown"
            )
        
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 Новый заказ", callback_data="new_order")]]
        try:
            query.message.delete()
        except:
            pass
        
        context.bot.send_message(
            chat_id=user_id,
            text="🔄 Хотите оформить ещё один заказ?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

def get_quantity_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("1", callback_data="qty_1"), 
            InlineKeyboardButton("2", callback_data="qty_2"),
            InlineKeyboardButton("3", callback_data="qty_3"), 
            InlineKeyboardButton("4", callback_data="qty_4"),
            InlineKeyboardButton("5", callback_data="qty_5")
        ],
        [
            InlineKeyboardButton("10", callback_data="qty_10"), 
            InlineKeyboardButton("20", callback_data="qty_20"),
            InlineKeyboardButton("30", callback_data="qty_30"), 
            InlineKeyboardButton("50", callback_data="qty_50"),
            InlineKeyboardButton("100", callback_data="qty_100")
        ],
        [
            InlineKeyboardButton("200", callback_data="qty_200"), 
            InlineKeyboardButton("300", callback_data="qty_300"),
            InlineKeyboardButton("400", callback_data="qty_400"), 
            InlineKeyboardButton("500", callback_data="qty_500")
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def handle_quantity_input(update, context):
    user_id = update.effective_user.id
    message = update.message
    text = message.text.strip()
    
    if user_id not in user_sessions:
        message.reply_text(
            "❌ *Ошибка*\n\nНачните заново с /start",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if user_sessions[user_id].get("type") not in ["photo", "doc"]:
        message.reply_text(
            "⚠️ *Сначала выберите тип печати*",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    numbers = re.findall(r'\d+', text)
    
    if not numbers:
        message.reply_text(
            "⚠️ *Введите число*\n\nНапример: 1, 5, 10, 100",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    quantity = int(numbers[0])
    
    if quantity < 1:
        message.reply_text(
            "⚠️ *Число должно быть больше 0*",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if quantity > 1000:
        message.reply_text(
            "⚠️ *Максимум 1000*\n\nВведите число от 1 до 1000",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    # Отправляем подтверждение
    message.reply_text(
        f"✅ *Принято!* Количество: {quantity}",
        parse_mode="Markdown"
    )
    
    # Создаем callback query
    class FakeQuery:
        def __init__(self, user_id, data):
            self.data = data
            self.from_user = type('User', (), {'id': user_id})()
            self.message = type('Msg', (), {'delete': lambda: None})()
        def answer(self):
            pass
    
    fake_query = FakeQuery(user_id, f'qty_{quantity}')
    
    return button_handler(update, context)

# ========== ВЕБ-ИНТЕРФЕЙС ==========
app = Flask(__name__)

@app.route('/orders/')
def list_orders():
    try:
        orders = []
        if os.path.exists(ORDERS_PATH):
            for item in sorted(os.listdir(ORDERS_PATH), reverse=True):
                item_path = os.path.join(ORDERS_PATH, item)
                if os.path.isdir(item_path) and item != "orders_history.json":
                    info_file = os.path.join(item_path, "информация_о_заказе.txt")
                    info_text = ""
                    if os.path.exists(info_file):
                        with open(info_file, 'r', encoding='utf-8') as f:
                            info_text = f.read()
                    
                    status = "new"
                    history = load_orders_history()
                    for h in history:
                        if h.get('order_id') == item:
                            status = h.get('status', 'new')
                            break
                    
                    files = []
                    photos = []
                    total_size = 0
                    file_count = 0
                    
                    for f in os.listdir(item_path):
                        if f != "информация_о_заказе.txt":
                            file_path = os.path.join(item_path, f)
                            file_size = os.path.getsize(file_path)
                            total_size += file_size
                            file_count += 1
                            is_photo = f.lower().endswith(('.jpg', '.jpeg', '.png'))
                            
                            file_info = {
                                'name': f,
                                'size': file_size,
                                'size_formatted': format_file_size(file_size),
                                'url': f'/orders/{item}/{f}',
                                'is_photo': is_photo
                            }
                            files.append(file_info)
                            
                            if is_photo:
                                photos.append(file_info)
                    
                    created = datetime.fromtimestamp(os.path.getctime(item_path))
                    
                    orders.append({
                        'id': item,
                        'name': item,
                        'info': info_text,
                        'files': files,
                        'photos': photos[:5],
                        'file_count': file_count,
                        'total_size': format_file_size(total_size),
                        'created': created.strftime('%d.%m.%Y %H:%M'),
                        'status': get_status_display(status)
                    })
        
        orders.sort(key=lambda x: x['created'], reverse=True)
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Заказы - Print Bot</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; margin: 0; }
                .container { max-width: 1400px; margin: 0 auto; }
                .header { background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 30px; margin-bottom: 30px; color: white; }
                .nav-links { display: flex; gap: 15px; margin-bottom: 30px; }
                .nav-btn { background: rgba(255,255,255,0.15); color: white; text-decoration: none; padding: 10px 20px; border-radius: 10px; }
                .orders-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 25px; }
                .order-card { background: white; border-radius: 20px; overflow: hidden; }
                .order-header { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; }
                .order-content { padding: 20px; }
                .status-btn { padding: 5px 10px; border: none; border-radius: 5px; cursor: pointer; margin: 2px; }
                .status-btn.new { background: #e3f2fd; }
                .status-btn.processing { background: #fff3e0; }
                .status-btn.printing { background: #e8f5e8; }
                .status-btn.ready { background: #e8e8f5; }
                .status-btn.shipped { background: #f3e5f5; }
                .status-btn.delivered { background: #e8f0fe; }
                .status-btn.cancelled { background: #ffebee; }
                .photo-gallery { display: flex; gap: 10px; overflow-x: auto; padding: 10px 0; }
                .photo-preview { width: 80px; height: 80px; object-fit: cover; border-radius: 8px; cursor: pointer; }
                .action-btn { display: inline-block; padding: 10px 20px; background: #28a745; color: white; text-decoration: none; border-radius: 10px; margin-top: 15px; }
            </style>
            <script>
                function updateStatus(orderId, status) {
                    fetch(`/orders/${orderId}/status`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({status: status})
                    }).then(r => r.json()).then(d => { if(d.success) location.reload(); else alert('Ошибка'); });
                }
            </script>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📦 Заказы на печать</h1>
                    <p>Всего заказов: {{ orders|length }}</p>
                </div>
                <div class="nav-links">
                    <a href="/" class="nav-btn">🏠 Главная</a>
                    <a href="/stats" class="nav-btn">📊 Статистика</a>
                </div>
                <div class="orders-grid">
                    {% for order in orders %}
                    <div class="order-card">
                        <div class="order-header">
                            <h2>{{ order.id }}</h2>
                            <div>{{ order.status }}</div>
                        </div>
                        <div class="order-content">
                            <div class="status-buttons">
                                <button class="status-btn new" onclick="updateStatus('{{ order.id }}','new')">🆕</button>
                                <button class="status-btn processing" onclick="updateStatus('{{ order.id }}','processing')">🔄</button>
                                <button class="status-btn printing" onclick="updateStatus('{{ order.id }}','printing')">🖨️</button>
                                <button class="status-btn ready" onclick="updateStatus('{{ order.id }}','ready')">✅</button>
                                <button class="status-btn shipped" onclick="updateStatus('{{ order.id }}','shipped')">📦</button>
                                <button class="status-btn delivered" onclick="updateStatus('{{ order.id }}','delivered')">🏁</button>
                                <button class="status-btn cancelled" onclick="updateStatus('{{ order.id }}','cancelled')">❌</button>
                            </div>
                            {% if order.photos %}
                            <div class="photo-gallery">
                                {% for photo in order.photos %}
                                <img src="{{ photo.url }}" class="photo-preview" onclick="window.open('{{ photo.url }}')">
                                {% endfor %}
                            </div>
                            {% endif %}
                            <a href="/orders/{{ order.id }}/" class="action-btn">👁️ Подробнее</a>
                            <a href="/orders/{{ order.id }}/download" class="action-btn">⬇️ Скачать</a>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(html, orders=orders)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_id>/')
def view_order(order_id):
    try:
        order_path = os.path.join(ORDERS_PATH, order_id)
        if not os.path.exists(order_path) or not os.path.isdir(order_path):
            abort(404)
        
        info_file = os.path.join(order_path, "информация_о_заказе.txt")
        info_text = ""
        if os.path.exists(info_file):
            with open(info_file, 'r', encoding='utf-8') as f:
                info_text = f.read()
        
        status = "new"
        history = load_orders_history()
        for h in history:
            if h.get('order_id') == order_id:
                status = h.get('status', 'new')
                break
        
        files = []
        photos = []
        total_size = 0
        
        for f in sorted(os.listdir(order_path)):
            if f != "информация_о_заказе.txt":
                file_path = os.path.join(order_path, f)
                file_size = os.path.getsize(file_path)
                total_size += file_size
                is_photo = f.lower().endswith(('.jpg', '.jpeg', '.png'))
                
                file_info = {
                    'name': f,
                    'size': file_size,
                    'size_formatted': format_file_size(file_size),
                    'url': f'/orders/{order_id}/{f}',
                    'is_photo': is_photo
                }
                files.append(file_info)
                if is_photo:
                    photos.append(file_info)
        
        created = datetime.fromtimestamp(os.path.getctime(order_path))
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Заказ {order_id}</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; margin: 0; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .header {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 20px; padding: 30px; color: white; margin-bottom: 30px; }}
                .nav-links {{ display: flex; gap: 15px; margin-bottom: 30px; }}
                .nav-btn {{ background: rgba(255,255,255,0.15); color: white; text-decoration: none; padding: 10px 20px; border-radius: 10px; }}
                .content {{ background: white; border-radius: 20px; padding: 30px; }}
                .status-btn {{ padding: 10px 15px; border: none; border-radius: 8px; cursor: pointer; margin: 2px; }}
                .status-btn.new {{ background: #e3f2fd; }}
                .status-btn.processing {{ background: #fff3e0; }}
                .status-btn.printing {{ background: #e8f5e8; }}
                .status-btn.ready {{ background: #e8e8f5; }}
                .status-btn.shipped {{ background: #f3e5f5; }}
                .status-btn.delivered {{ background: #e8f0fe; }}
                .status-btn.cancelled {{ background: #ffebee; }}
                .photo-gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(150px,1fr)); gap: 15px; margin: 20px 0; }}
                .photo-item {{ background: #f8f9fa; border-radius: 10px; padding: 10px; text-align: center; }}
                .photo-img {{ max-width: 100%; max-height: 150px; border-radius: 8px; cursor: pointer; }}
                .files-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px,1fr)); gap: 15px; margin: 20px 0; }}
                .file-card {{ background: #f8f9fa; border-radius: 10px; padding: 15px; text-align: center; text-decoration: none; color: #333; display: block; }}
                .download-all {{ display: inline-block; background: #28a745; color: white; text-decoration: none; padding: 15px 30px; border-radius: 10px; margin-top: 20px; }}
            </style>
            <script>
                function updateStatus(status) {{
                    fetch('/orders/{order_id}/status', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{status: status}})
                    }}).then(r=>r.json()).then(d=>{{ if(d.success) location.reload(); else alert('Ошибка'); }});
                }}
            </script>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>📁 Заказ: {order_id}</h1>
                    <p>Создан: {created.strftime('%d.%m.%Y %H:%M')}</p>
                </div>
                <div class="nav-links">
                    <a href="/orders/" class="nav-btn">← К списку</a>
                    <a href="/" class="nav-btn">🏠 Главная</a>
                </div>
                <div class="content">
                    <div>
                        <h3>📌 Статус: {get_status_display(status)}</h3>
                        <div>
                            <button class="status-btn new" onclick="updateStatus('new')">🆕</button>
                            <button class="status-btn processing" onclick="updateStatus('processing')">🔄</button>
                            <button class="status-btn printing" onclick="updateStatus('printing')">🖨️</button>
                            <button class="status-btn ready" onclick="updateStatus('ready')">✅</button>
                            <button class="status-btn shipped" onclick="updateStatus('shipped')">📦</button>
                            <button class="status-btn delivered" onclick="updateStatus('delivered')">🏁</button>
                            <button class="status-btn cancelled" onclick="updateStatus('cancelled')">❌</button>
                        </div>
                    </div>
                    
                    <div>
                        <h3>📋 Информация</h3>
                        <pre>{info_text}</pre>
                    </div>
                    
                    <div style="text-align: center;">
                        <a href="/orders/{order_id}/download" class="download-all">⬇️ Скачать все</a>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_id>/status', methods=['POST'])
def update_order_status_route(order_id):
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Нет данных"}), 400
        new_status = data.get('status')
        if not new_status:
            return jsonify({"success": False, "error": "Не указан статус"}), 400
        success = update_order_status(order_id, new_status)
        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/orders/<path:order_id>/download')
def download_all_files(order_id):
    try:
        order_path = os.path.join(ORDERS_PATH, order_id)
        if not os.path.exists(order_path):
            return "Заказ не найден", 404
        temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        with zipfile.ZipFile(temp_zip.name, 'w') as zipf:
            for root, dirs, files in os.walk(order_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, order_path)
                    zipf.write(file_path, arcname)
        return send_file(temp_zip.name, as_attachment=True, download_name=f"{order_id}.zip")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_id>/<filename>')
def download_order_file(order_id, filename):
    try:
        order_path = os.path.join(ORDERS_PATH, order_id)
        return send_from_directory(order_path, filename, as_attachment=True)
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {e}", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    global dispatcher
    try:
        if dispatcher is None:
            return jsonify({"error": "Dispatcher not initialized"}), 500
        update_data = request.get_json()
        if update_data:
            logger.info(f"📩 Обновление: {update_data.get('update_id')}")
            update = telegram.Update.de_json(update_data, bot)
            dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({"status": "ok", "bot_ready": dispatcher is not None})

@app.route('/stats')
def stats():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    return jsonify({"status": "ok", "orders_count": orders_count, "active_sessions": len(user_sessions)})

@app.route('/')
def home():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>Print Bot</title>
    <style>
        body {{ font-family: Arial, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; margin: 0; }}
        .container {{ max-width: 800px; width: 100%; }}
        .hero {{ background: rgba(255,255,255,0.1); backdrop-filter: blur(10px); border-radius: 30px; padding: 40px; color: white; text-align: center; }}
        h1 {{ font-size: 3em; margin-bottom: 20px; }}
        .stats {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 20px; margin: 30px 0; }}
        .stat-card {{ background: rgba(255,255,255,0.15); border-radius: 15px; padding: 20px; }}
        .nav-links {{ display: flex; gap: 15px; justify-content: center; margin-top: 30px; }}
        .nav-btn {{ background: white; color: #667eea; text-decoration: none; padding: 15px 30px; border-radius: 10px; font-weight: bold; }}
        .info {{ margin-top: 30px; padding: 20px; background: rgba(0,0,0,0.2); border-radius: 10px; }}
    </style>
    </head>
    <body>
        <div class="container">
            <div class="hero">
                <h1>🤖 Print Bot</h1>
                <p>Сервис для печати фото и документов через Telegram</p>
                <div class="stats">
                    <div class="stat-card"><div class="stat-value">{orders_count}</div><div>заказов</div></div>
                    <div class="stat-card"><div class="stat-value">24/7</div><div>работа</div></div>
                    <div class="stat-card"><div class="stat-value">1-3</div><div>дня</div></div>
                </div>
                <div class="nav-links">
                    <a href="/orders/" class="nav-btn">📦 Заказы</a>
                    <a href="/stats" class="nav-btn">📊 Статистика</a>
                </div>
                <div class="info">
                    <p>📞 Контакт: {CONTACT_PHONE}</p>
                    <p>🚚 Доставка: {DELIVERY_OPTIONS}</p>
                    <p>⏰ Время: {current_time}</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📁 Папка для заказов: {ORDERS_PATH}")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")

bot = telegram.Bot(token=TOKEN)
updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Добавляем обработчик ошибок
def error_handler(update, context):
    logger.error(f"Ошибка: {context.error}")
    try:
        if update and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ *Произошла ошибка*\n\nПожалуйста, начните заново с /start",
                parse_mode="Markdown"
            )
    except:
        pass

dispatcher.add_error_handler(error_handler)

conv_handler = ConversationHandler(
    entry_points=[
        MessageHandler(Filters.document | Filters.photo, handle_file),
        CommandHandler("start", start),
    ],
    states={
        WAITING_FOR_FILE: [
            MessageHandler(Filters.document | Filters.photo, handle_file),
            CallbackQueryHandler(button_handler),
        ],
        SELECTING_PHOTO_FORMAT: [
            CallbackQueryHandler(button_handler, pattern="^photo_.*"),
            CallbackQueryHandler(button_handler, pattern="^cancel$"),
        ],
        SELECTING_DOC_TYPE: [
            CallbackQueryHandler(button_handler, pattern="^doc_.*"),
            CallbackQueryHandler(button_handler, pattern="^cancel$"),
        ],
        ENTERING_QUANTITY: [
            MessageHandler(Filters.text & ~Filters.command, handle_quantity_input),
            CallbackQueryHandler(button_handler, pattern="^qty_.*"),
            CallbackQueryHandler(button_handler, pattern="^cancel$"),
        ],
        CONFIRMING_ORDER: [
            CallbackQueryHandler(button_handler, pattern="^(confirm|cancel|new_order)$"),
        ],
    },
    fallbacks=[CommandHandler("start", start)],
    allow_reentry=True,
)

dispatcher.add_handler(conv_handler)

# Устанавливаем вебхук
webhook_url = f"{RENDER_URL}/webhook"
try:
    updater.bot.set_webhook(url=webhook_url)
    logger.info(f"✅ Веб-хук установлен: {webhook_url}")
except Exception as e:
    logger.error(f"❌ Ошибка установки веб-хука: {e}")

print(f"✅ Веб-хук: {webhook_url}")
print("✅ БОТ ГОТОВ К РАБОТЕ!")
print("=" * 60)

if __name__ == "__main__":
    print("🌐 Запуск Flask сервера...")
    app.run(host='0.0.0.0', port=PORT)
