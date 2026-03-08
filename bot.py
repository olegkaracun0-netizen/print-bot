#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
ПРЕМИУМ ДИЗАЙН
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
from datetime import datetime
from flask import Flask, request, send_file, render_template_string, abort, jsonify

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, Filters

import PyPDF2
from docx import Document

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("ОШИБКА: TOKEN не задан")
    sys.exit(1)

ADMIN_CHAT_ID = 483613049
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("ОШИБКА: RENDER_EXTERNAL_URL не задан")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб | СДЭК | Яндекс Доставка"

# ========== ПУТЬ К ПАПКЕ ЗАКАЗОВ ==========
ORDERS_FOLDER = "заказы"
ORDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ORDERS_FOLDER)

try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"Папка заказов: {ORDERS_PATH}")
except Exception as e:
    print(f"Ошибка создания папки: {e}")
    sys.exit(1)

# ========== ФАЙЛ ДЛЯ ХРАНЕНИЯ ИСТОРИИ ==========
ORDERS_DB_FILE = os.path.join(ORDERS_PATH, "orders_history.json")

# ========== СТАТУСЫ ЗАКАЗОВ ==========
ORDER_STATUSES = {
    "new": "Новый",
    "processing": "В обработке",
    "printing": "В печати",
    "ready": "Готов",
    "shipped": "Отправлен",
    "delivered": "Доставлен",
    "cancelled": "Отменен"
}

def get_status_display(status):
    status_icons = {
        "new": "🆕 Новый",
        "processing": "🔄 В обработке",
        "printing": "🖨️ В печати",
        "ready": "✅ Готов",
        "shipped": "📦 Отправлен",
        "delivered": "🏁 Доставлен",
        "cancelled": "❌ Отменен"
    }
    return status_icons.get(status, status)

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С ИСТОРИЕЙ ==========
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
                        text=f"Статус вашего заказа изменен\n\nЗаказ: {order_id}\nНовый статус: {get_status_display(new_status)}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
            
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
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
app = Flask(__name__)

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

# ========== ПРЕМИУМ ДИЗАЙН ДЛЯ TELEGRAM ==========

def header(text, emoji="✨"):
    return f"""
╔══════════════════════════════════╗
║     {emoji}  {text.upper()}  {emoji}     ║
╚══════════════════════════════════╝"""

def section(text, emoji="📌"):
    return f"\n{emoji}  **{text}**\n" + "▔" * 25

def point(text, emoji="●"):
    return f"{emoji}  {text}"

def price_tag(amount):
    return f"💎  **{amount} ₽**  💎"

def status_badge(status):
    badges = {
        "new": "🆕 **НОВЫЙ**",
        "processing": "🔄 **В ОБРАБОТКЕ**",
        "printing": "🖨️ **ПЕЧАТАЕТСЯ**",
        "ready": "✅ **ГОТОВ**",
        "shipped": "📦 **ОТПРАВЛЕН**",
        "delivered": "🏁 **ДОСТАВЛЕН**",
        "cancelled": "❌ **ОТМЕНЕН**"
    }
    return badges.get(status, status)

def divider():
    return "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"

# ========== ОСНОВНЫЕ ФУНКЦИИ ==========
def calculate_price(price_dict, quantity):
    for (min_q, max_q), price in price_dict.items():
        if min_q <= quantity <= max_q:
            return price * quantity
    return 0

def estimate_delivery_time(total_items):
    if total_items <= 50:
        return "1 день"
    elif total_items <= 200:
        return "2 дня"
    else:
        return "3 дня"

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
            return estimated_pages, "страниц", "документ"
            
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
            return 1, "фото", "фото"
            
        return 1, "ед", "неизвестно"
    except Exception as e:
        logger.error(f"Ошибка подсчета: {e}")
        return 1, "ед", "неизвестно"

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
        logger.error(f"Ошибка скачивания: {e}")
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
            else:
                logger.error(f"Файл не найден: {f['path']}")
        
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
        logger.error(f"Ошибка сохранения: {e}")
        logger.error(traceback.format_exc())
        return False, None, None

def send_admin_notification(order_data, order_id, order_folder):
    try:
        order_url = f"{RENDER_URL}/orders/{order_id}/"
        
        photo_files = [f for f in order_data['files'] if f['type'] == 'photo']
        doc_files = [f for f in order_data['files'] if f['type'] == 'doc']
        
        total_photos = sum(f['items'] for f in photo_files)
        total_pages = sum(f['items'] for f in doc_files)
        
        admin_message = f"""
╔══════════════════════════════════╗
║        НОВЫЙ ЗАКАЗ              ║
╚══════════════════════════════════╝

👤 **Клиент:** {order_data['user_info']['first_name']} (@{order_data['user_info']['username']})
🆔 **ID:** {order_data['user_info']['user_id']}

▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
📦 **Параметры:**
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔"""
        
        if order_data['type'] == 'photo':
            format_names = {"small": "Малый", "medium": "Средний", "large": "Большой"}
            admin_message += f"\n📸 Тип: Фото ({format_names[order_data['format']]})"
        else:
            color_names = {"bw": "Ч/Б", "color": "Цветная"}
            admin_message += f"\n📄 Тип: Документы ({color_names[order_data['color']]})"
        
        admin_message += f"""
📦 Копий: {order_data['quantity']}
📁 Файлов: {len(order_data['files'])}"""
        
        if photo_files:
            admin_message += f"\n📸 Фото: {len(photo_files)} файлов, {total_photos} шт."
        if doc_files:
            admin_message += f"\n📄 Документы: {len(doc_files)} файлов, {total_pages} стр."
        
        admin_message += f"""

💰 **Сумма:** {order_data['total']} ₽
⏱️ **Срок:** {order_data['delivery']}

{divider()}
🔗 {order_url}"""
        
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message,
                parse_mode="Markdown"
            )
            logger.info(f"Уведомление отправлено админу {ADMIN_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления админу: {e}")

# ========== ФУНКЦИИ TELEGRAM ==========

def start(update, context):
    user = update.effective_user
    user_id = user.id
    logger.info(f"/start от {user_id}")
    
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    welcome = f"""
╔══════════════════════════════════╗
║     ✨  ДОБРО ПОЖАЛОВАТЬ  ✨     ║
╚══════════════════════════════════╝

✨ **Привет, {user.first_name}!** ✨

{divider()}

📌 **МОИ ВОЗМОЖНОСТИ:**

📸 **Фото** (JPG, PNG)
   ● 3 формата на выбор
   ● Гибкая система скидок

📄 **Документы** (PDF, DOC, DOCX)
   ● Ч/б и цветная печать
   ● Автоподсчет страниц

📦 **Пакетная загрузка**
   ● До 10 файлов за раз
   ● Мгновенный расчет

{divider()}

📞 **Контакты:** {CONTACT_PHONE}
🚚 **Доставка:** {DELIVERY_OPTIONS}

{divider()}

⬇️ **Отправьте файлы для печати** ⬇️
"""
    
    update.message.reply_text(welcome, parse_mode="Markdown")
    return WAITING_FOR_FILE

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
            error_msg = f"""
╔══════════════════════════════════╗
║        ❌  ОШИБКА ФОРМАТА       ║
╚══════════════════════════════════╝

✨ Неподдерживаемый формат файла

{divider()}
📌 **ДОПУСТИМЫЕ ФОРМАТЫ:**
{divider()}

📸 **Фото:** JPG, PNG
📄 **Документы:** PDF, DOC, DOCX

{divider()}

Попробуйте еще раз 🔄
"""
            message.reply_text(error_msg, parse_mode="Markdown")
            return WAITING_FOR_FILE
    elif message.photo:
        file_obj = message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE
    
    file_path, temp_dir = download_file(file_obj, file_name)
    if not file_path:
        message.reply_text("Не удалось загрузить файл")
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
    
    text = f"""
╔══════════════════════════════════╗
║     ✅  ФАЙЛ УСПЕШНО ДОБАВЛЕН   ║
╚══════════════════════════════════╝

📄 **Файл:** {file_name[:40]}{'...' if len(file_name) > 40 else ''}
📦 **Тип:** {'📸 Фото' if file_type == 'photo' else '📄 Документ'}
🔢 **Количество:** {items} {unit}

{divider()}
📊 **ТЕКУЩАЯ СТАТИСТИКА**
{divider()}
"""
    
    if photo_count > 0:
        text += f"\n📸 Фото: {photo_count} файлов"
    if doc_count > 0:
        text += f"\n📄 Документы: {doc_count} файлов"
    if total_photos > 0:
        text += f"\n📸 Всего фото: {total_photos}"
    if total_pages > 0:
        text += f"\n📄 Всего страниц: {total_pages}"
    text += f"\n📦 Всего файлов: {files_count}"
    
    text += f"\n\n{divider()}\n"
    
    if doc_count > 0:
        text += "\n📋 **Выберите тип печати:**"
        keyboard = [
            [InlineKeyboardButton("⚫ ЧЕРНО-БЕЛАЯ", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 ЦВЕТНАЯ", callback_data="doc_color")],
            [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
        ]
    else:
        text += "\n🖼️ **Выберите формат печати:**"
        keyboard = [
            [InlineKeyboardButton("🖼 МАЛЫЙ (A6)", callback_data="photo_small")],
            [InlineKeyboardButton("🖼 СРЕДНИЙ (13x18)", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼 БОЛЬШОЙ (A4)", callback_data="photo_large")],
            [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
        ]
    
    message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return WAITING_FOR_FILE

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
                text="Не удалось загрузить файлы"
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        text = f"""
╔══════════════════════════════════╗
║   ✅  ЗАГРУЖЕНО {files_count} ФАЙЛОВ   ║
╚══════════════════════════════════╝

{divider()}
📊 **СТАТИСТИКА**
{divider()}"""
        
        if photo_count > 0:
            text += f"\n📸 Фото: {photo_count}"
        if doc_count > 0:
            text += f"\n📄 Документы: {doc_count}"
        if total_photos > 0:
            text += f"\n📸 Всего фото: {total_photos}"
        if total_pages > 0:
            text += f"\n📄 Всего страниц: {total_pages}"
        
        text += f"\n\n{divider()}\n"
        
        if doc_count > 0:
            text += "\n📋 **Выберите тип печати:**"
            keyboard = [
                [InlineKeyboardButton("⚫ ЧЕРНО-БЕЛАЯ", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 ЦВЕТНАЯ", callback_data="doc_color")],
                [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
                [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
            ]
        else:
            text += "\n🖼️ **Выберите формат печати:**"
            keyboard = [
                [InlineKeyboardButton("🖼 МАЛЫЙ (A6)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 СРЕДНИЙ (13x18)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 БОЛЬШОЙ (A4)", callback_data="photo_large")],
                [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
                [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
            ]
        
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке группы файлов: {e}")
        logger.error(traceback.format_exc())

def button_handler(update, context):
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"Callback: {data} от {user_id}")
    
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text(
            f"""
╔══════════════════════════════════╗
║     📤  ДОБАВЬТЕ ЕЩЕ ФАЙЛЫ      ║
╚══════════════════════════════════╝

✨ Отправьте следующие файлы для печати

{divider()}
📌 JPG, PNG, PDF, DOC, DOCX
{divider()}
""",
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
            f"""
╔══════════════════════════════════╗
║       🔄  НОВЫЙ ЗАКАЗ            ║
╚══════════════════════════════════╝

✨ Отправьте файлы для печати

{divider()}
📌 JPG, PNG, PDF, DOC, DOCX
{divider()}
""",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        format_type = data.split("_")[1]
        format_names = {"small": "МАЛЫЙ (A6/10x15)", "medium": "СРЕДНИЙ (13x18/15x21)", "large": "БОЛЬШОЙ (A4/21x30)"}
        format_prices = {
            "small": "1-9: 35₽ | 10-50: 28₽ | 51-100: 23₽ | 101+: 18₽",
            "medium": "1-9: 65₽ | 10-50: 55₽ | 51-100: 45₽ | 101+: 35₽",
            "large": "1-4: 200₽ | 5-20: 170₽ | 21-50: 150₽ | 51+: 120₽"
        }
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = format_type
        
        text = f"""
╔══════════════════════════════════╗
║     🖼️  {format_names[format_type]}  🖼️     ║
╚══════════════════════════════════╝

💰 **Цены:**
{format_prices[format_type]}

{divider()}

🔢 **Сколько копий напечатать?**

Введите число или выберите из вариантов ниже:
"""
        
        query.edit_message_text(
            text,
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
        )
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        doc_type = data.split("_")[1]
        color_names = {"bw": "ЧЕРНО-БЕЛАЯ", "color": "ЦВЕТНАЯ"}
        color_prices = {
            "bw": "1-20: 25₽ | 21-100: 18₽ | 101-300: 14₽ | 301+: 10₽",
            "color": "1-20: 50₽ | 21-100: 35₽ | 101-300: 25₽ | 301+: 20₽"
        }
        
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = doc_type
        
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        total_items = total_photos + total_pages
        
        text = f"""
╔══════════════════════════════════╗
║     📄  {color_names[doc_type]}  📄      ║
╚══════════════════════════════════╝

💰 **Цены:**
{color_prices[doc_type]}

{divider()}

📊 **В ФАЙЛАХ:**
● 📸 Фото: {total_photos}
● 📄 Страниц: {total_pages}
● 📦 Всего: {total_items} ед.

{divider()}

🔢 **Сколько копий напечатать?**

Введите число или выберите из вариантов ниже:
"""
        
        query.edit_message_text(
            text,
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
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
        details = f"╔══════════════════════════════════╗\n║     💰  ДЕТАЛЬНЫЙ РАСЧЕТ      ║\n╚══════════════════════════════════╝\n\n"
        
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
                details += f"📸 **Файл {i}:**\n   ● {f['items']} фото × {quantity} коп.\n   ● = {f['items'] * quantity} фото → {file_total} ₽\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += f"📄 **Файл {i}:**\n   ● {f['items']} стр. × {quantity} коп.\n   ● = {file_items} стр. → {file_total} ₽\n\n"
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = f"""
{details}
╔══════════════════════════════════╗
║     📋  ПРОВЕРЬТЕ ЗАКАЗ         ║
╚══════════════════════════════════╝

📦 **Всего файлов:** {len(files)}
"""
        if total_photos_result > 0:
            text += f"\n📸 **Фото к печати:** {total_photos_result}"
        if total_pages_result > 0:
            text += f"\n📄 **Страниц к печати:** {total_pages_result}"
        
        text += f"""

💰 **ИТОГОВАЯ СУММА:** {total} ₽
⏳ **Срок выполнения:** {session['delivery']}

{divider()}

❓ **Всё верно?**
"""
        
        keyboard = [
            [InlineKeyboardButton("✅ ДА, ПОДТВЕРДИТЬ", callback_data="confirm"),
             InlineKeyboardButton("❌ НЕТ, ОТМЕНИТЬ", callback_data="cancel")]
        ]
        
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
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
            
            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            doc_files = [f for f in session['files'] if f['type'] == 'doc']
            
            total_photos = sum(f['items'] for f in photo_files)
            total_pages = sum(f['items'] for f in doc_files)
            
            client_message = f"""
╔══════════════════════════════════╗
║     🎉  ЗАКАЗ УСПЕШНО ОФОРМЛЕН! ║
╚══════════════════════════════════╝

✨ **Спасибо, {session['user_info']['first_name']}!** ✨

{divider()}

🆔 **Номер заказа:** {order_id}
👤 **Заказчик:** {session['user_info']['first_name']}
📦 **Файлов:** {len(session['files'])}
"""
            
            if total_photos > 0:
                client_message += f"\n📸 **Фото в оригинале:** {total_photos}"
                client_message += f"\n📸 **Фото к печати:** {total_photos * session['quantity']}"
            if total_pages > 0:
                client_message += f"\n📄 **Страниц в оригинале:** {total_pages}"
                client_message += f"\n📄 **Страниц к печати:** {total_pages * session['quantity']}"
            
            client_message += f"""

💰 **Сумма к оплате:** {session['total']} ₽
⏳ **Срок выполнения:** {session['delivery']}

{divider()}

📞 **Контактный телефон:** {CONTACT_PHONE}
🚚 **Доставка:** {DELIVERY_OPTIONS}

{divider()}

📌 **Статус:** {status_badge('new')}
Вы будете получать уведомления при изменении статуса.

╔══════════════════════════════════╗
║        ✨  СПАСИБО!  ✨          ║
╚══════════════════════════════════╝
"""
            
            context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode="Markdown"
            )
            
            if photo_files:
                try:
                    media_group = []
                    for i, photo_file in enumerate(photo_files[:5]):
                        with open(photo_file['path'], 'rb') as photo:
                            if i == 0:
                                media_group.append(InputMediaPhoto(
                                    photo.read(),
                                    caption=f"📸 Загруженные фото ({len(photo_files)} шт.)"
                                ))
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
                text="Ошибка при сохранении заказа"
            )
        
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=f"""
╔══════════════════════════════════╗
║     🤔  ХОТИТЕ ЕЩЕ ЗАКАЗ?       ║
╚══════════════════════════════════╝

✨ Нажмите кнопку ниже чтобы начать заново! ✨

{divider()}
""",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
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
        logger.info(f"Сессия пользователя {user_id} очищена")
    
    keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = f"""
╔══════════════════════════════════╗
║        ❌  ЗАКАЗ ОТМЕНЕН         ║
╚══════════════════════════════════╝

✨ Все загруженные файлы удалены ✨

{divider()}

🤔 **Хотите оформить новый заказ?**

Нажмите кнопку ниже чтобы начать заново 🔄
"""
    
    if query:
        try:
            query.edit_message_text(message, reply_markup=reply_markup, parse_mode="Markdown")
        except:
            if context:
                context.bot.send_message(
                    chat_id=user_id,
                    text=message,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
    elif context:
        context.bot.send_message(
            chat_id=user_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
    return WAITING_FOR_FILE

def handle_file(update, context):
    user_id = update.effective_user.id
    message = update.message
    
    if message.media_group_id:
        return handle_media_group(update, context)
    
    return process_single_file(update, context)

def get_quantity_keyboard():
    keyboard = [
        [InlineKeyboardButton("1️⃣", callback_data="qty_1"), 
         InlineKeyboardButton("2️⃣", callback_data="qty_2"),
         InlineKeyboardButton("3️⃣", callback_data="qty_3"), 
         InlineKeyboardButton("4️⃣", callback_data="qty_4"),
         InlineKeyboardButton("5️⃣", callback_data="qty_5")],
        [InlineKeyboardButton("🔟", callback_data="qty_10"), 
         InlineKeyboardButton("2️⃣0️⃣", callback_data="qty_20"),
         InlineKeyboardButton("3️⃣0️⃣", callback_data="qty_30"), 
         InlineKeyboardButton("5️⃣0️⃣", callback_data="qty_50"),
         InlineKeyboardButton("1️⃣0️⃣0️⃣", callback_data="qty_100")],
        [InlineKeyboardButton("2️⃣0️⃣0️⃣", callback_data="qty_200"), 
         InlineKeyboardButton("3️⃣0️⃣0️⃣", callback_data="qty_300"),
         InlineKeyboardButton("4️⃣0️⃣0️⃣", callback_data="qty_400"), 
         InlineKeyboardButton("5️⃣0️⃣0️⃣", callback_data="qty_500")],
        [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def handle_quantity_input(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    quantity = extract_number_from_text(text)
    
    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            f"""
╔══════════════════════════════════╗
║         ❌  ОШИБКА                ║
╚══════════════════════════════════╝

✨ Введите число от **1** до **1000**

Или выберите из кнопок ниже:

{divider()}
""",
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
        )
        return ENTERING_QUANTITY
    
    context.user_data['temp_quantity'] = quantity
    query = type('Query', (), {
        'data': f'qty_{quantity}',
        'from_user': update.effective_user,
        'message': update.message,
        'answer': lambda: None
    })
    return button_handler(update, context)

# ========== ВЕБ-ИНТЕРФЕЙС ==========

PREMIUM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap');
    
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }
    
    body {
        font-family: 'Poppins', sans-serif;
        background: linear-gradient(-45deg, #ee7752, #e73c7e, #23a6d5, #23d5ab);
        background-size: 400% 400%;
        animation: gradientBG 15s ease infinite;
        min-height: 100vh;
        padding: 20px;
    }
    
    @keyframes gradientBG {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    
    .container {
        max-width: 1400px;
        margin: 0 auto;
    }
    
    .premium-card {
        background: rgba(255, 255, 255, 0.1);
        backdrop-filter: blur(20px);
        border-radius: 50px;
        padding: 40px;
        margin-bottom: 30px;
        border: 1px solid rgba(255, 255, 255, 0.3);
        box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.5);
        transform: perspective(1000px) rotateX(1deg);
        transition: all 0.5s;
        animation: float 6s ease-in-out infinite;
    }
    
    .premium-card:hover {
        transform: perspective(1000px) rotateX(2deg) translateY(-10px);
        box-shadow: 0 40px 80px -20px rgba(0, 0, 0, 0.7);
    }
    
    @keyframes float {
        0%, 100% { transform: perspective(1000px) rotateX(1deg) translateY(0); }
        50% { transform: perspective(1000px) rotateX(1.5deg) translateY(-10px); }
    }
    
    .neon-text {
        font-size: 3.5em;
        font-weight: 800;
        color: #fff;
        text-shadow: 0 0 10px #fff, 0 0 20px #fff, 0 0 40px #fff, 0 0 80px #0ff;
        animation: neonPulse 2s ease-in-out infinite;
    }
    
    @keyframes neonPulse {
        0%, 100% { text-shadow: 0 0 10px #fff, 0 0 20px #fff, 0 0 40px #fff, 0 0 80px #0ff; }
        50% { text-shadow: 0 0 20px #fff, 0 0 40px #fff, 0 0 80px #fff, 0 0 160px #f0f; }
    }
    
    .glow-btn {
        background: linear-gradient(45deg, #ff6b6b, #feca57, #48dbfb, #1dd1a1);
        background-size: 300% 300%;
        color: white;
        border: none;
        padding: 15px 30px;
        border-radius: 50px;
        font-weight: 600;
        font-size: 1.1em;
        cursor: pointer;
        transition: all 0.3s;
        box-shadow: 0 0 20px rgba(255, 255, 255, 0.5);
        animation: gradientShift 3s ease infinite;
        text-decoration: none;
        display: inline-block;
    }
    
    .glow-btn:hover {
        transform: scale(1.1) rotate(2deg);
        box-shadow: 0 0 40px rgba(255, 255, 255, 0.8);
    }
    
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    
    .order-card {
        background: white;
        border-radius: 40px;
        overflow: hidden;
        margin-bottom: 30px;
        box-shadow: 0 30px 60px -20px rgba(0, 0, 0, 0.4);
        transform: perspective(1000px) rotateX(0deg);
        transition: all 0.5s;
        position: relative;
    }
    
    .order-card:hover {
        transform: perspective(1000px) rotateX(2deg) translateY(-15px) scale(1.02);
        box-shadow: 0 50px 100px -30px rgba(0, 0, 0, 0.6);
    }
    
    .order-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 30px;
    }
    
    .status-badge {
        display: inline-block;
        padding: 8px 20px;
        border-radius: 50px;
        font-weight: 600;
        font-size: 0.9em;
        text-transform: uppercase;
        letter-spacing: 1px;
        color: white;
    }
    
    .status-new { background: #4CAF50; box-shadow: 0 0 20px #4CAF50; }
    .status-processing { background: #FF9800; box-shadow: 0 0 20px #FF9800; }
    .status-printing { background: #2196F3; box-shadow: 0 0 20px #2196F3; }
    .status-ready { background: #9C27B0; box-shadow: 0 0 20px #9C27B0; }
    .status-shipped { background: #3F51B5; box-shadow: 0 0 20px #3F51B5; }
    .status-delivered { background: #009688; box-shadow: 0 0 20px #009688; }
    .status-cancelled { background: #f44336; box-shadow: 0 0 20px #f44336; }
    
    .photo-gallery {
        display: flex;
        gap: 20px;
        overflow-x: auto;
        padding: 20px 0;
    }
    
    .photo-preview {
        width: 150px;
        height: 150px;
        object-fit: cover;
        border-radius: 30px;
        cursor: pointer;
        transition: all 0.5s;
        border: 4px solid white;
        box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.4);
    }
    
    .photo-preview:hover {
        transform: perspective(500px) rotateY(10deg) scale(1.1);
        box-shadow: 0 30px 60px -10px rgba(0, 0, 0, 0.6);
    }
    
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 30px;
        margin-bottom: 40px;
    }
    
    .stat-card {
        background: rgba(255, 255, 255, 0.15);
        backdrop-filter: blur(10px);
        border-radius: 40px;
        padding: 30px;
        text-align: center;
        color: white;
        border: 1px solid rgba(255, 255, 255, 0.3);
        transition: all 0.5s;
    }
    
    .stat-card:hover {
        transform: translateY(-15px) scale(1.05);
        box-shadow: 0 40px 80px -20px rgba(0, 0, 0, 0.5);
    }
    
    .stat-icon {
        font-size: 4em;
        margin-bottom: 20px;
        animation: bounce 2s ease-in-out infinite;
    }
    
    @keyframes bounce {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-15px); }
    }
    
    .stat-value {
        font-size: 3em;
        font-weight: 800;
        margin-bottom: 10px;
        text-shadow: 0 0 20px rgba(255,255,255,0.5);
    }
    
    .stat-label {
        font-size: 1.1em;
        opacity: 0.9;
        text-transform: uppercase;
        letter-spacing: 2px;
    }
    
    .empty-state {
        text-align: center;
        padding: 100px;
        background: rgba(255, 255, 255, 0.1);
        backdrop-filter: blur(20px);
        border-radius: 60px;
        color: white;
        border: 1px solid rgba(255, 255, 255, 0.3);
    }
    
    .empty-icon {
        font-size: 8em;
        margin-bottom: 30px;
        animation: float 3s ease-in-out infinite;
    }
    
    @media (max-width: 768px) {
        .premium-card { padding: 25px; }
        .neon-text { font-size: 2.5em; }
        .stats-grid { grid-template-columns: 1fr; }
        .photo-preview { width: 100px; height: 100px; }
    }
</style>
"""

@app.route('/')
def home():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    
    history = load_orders_history()
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos', 0) for order in history)
    total_pages = sum(order.get('total_pages', 0) for order in history)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card" style="text-align: center;">
                <h1 class="neon-text">PRINT BOT</h1>
                <p style="color: white; font-size: 1.5em; margin-top: 20px;">Система печати</p>
                <div style="margin-top: 30px;">
                    <a href="/orders/" class="glow-btn">📦 ПРОСМОТР ЗАКАЗОВ</a>
                    <a href="/stats/" class="glow-btn" style="margin-left: 20px;">📊 СТАТИСТИКА</a>
                </div>
            </div>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon">📦</div>
                    <div class="stat-value">{orders_count}</div>
                    <div class="stat-label">Активных заказов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">💰</div>
                    <div class="stat-value">{total_revenue} ₽</div>
                    <div class="stat-label">Выручка</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📸</div>
                    <div class="stat-value">{total_photos}</div>
                    <div class="stat-label">Напечатано фото</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📄</div>
                    <div class="stat-value">{total_pages}</div>
                    <div class="stat-label">Напечатано страниц</div>
                </div>
            </div>
            
            <div class="premium-card">
                <h2 style="color: white; font-size: 2.5em; margin-bottom: 30px;">🔥 ПОСЛЕДНИЕ ЗАКАЗЫ</h2>
                <div style="display: grid; gap: 20px;">
    """
    
    for order in sorted(history, key=lambda x: x.get('date', ''), reverse=True)[:5]:
        status_class = f"status-{order.get('status', 'new')}"
        status_text = get_status_display(order.get('status', 'new'))
        
        html += f"""
                    <div class="order-card" style="background: rgba(255,255,255,0.95); padding: 25px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <h3>🆔 {order.get('order_id', 'N/A')[:10]}...</h3>
                                <p style="color: #666;">👤 {order.get('user_name', 'Неизвестно')}</p>
                            </div>
                            <span class="status-badge {status_class}">{status_text}</span>
                        </div>
                        <div style="margin-top: 15px;">
                            <span>📸 {order.get('total_photos', 0)} фото</span> | 
                            <span>📄 {order.get('total_pages', 0)} стр.</span> | 
                            <span>💰 {order.get('total_price', 0)} ₽</span>
                        </div>
                        <a href="/orders/{order.get('order_id')}/" style="display: inline-block; margin-top: 15px; color: #667eea;">Подробнее →</a>
                    </div>
        """
    
    html += """
                </div>
            </div>
            
            <div class="premium-card" style="text-align: center;">
                <h2 style="color: white; font-size: 2em; margin-bottom: 20px;">🤖 О БОТЕ</h2>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 30px; color: white;">
                    <div><div style="font-size: 3em;">📸</div><h3>Фото</h3><p>JPG, PNG<br>3 формата</p></div>
                    <div><div style="font-size: 3em;">📄</div><h3>Документы</h3><p>PDF, DOC, DOCX<br>Ч/б и цветная</p></div>
                    <div><div style="font-size: 3em;">🚚</div><h3>Доставка</h3><p>Самовывоз, СДЭК, Яндекс</p></div>
                </div>
                <div style="margin-top: 40px;">
                    <p style="color: white; font-size: 1.3em;">📞 {CONTACT_PHONE}</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/')
def list_orders():
    orders = []
    if os.path.exists(ORDERS_PATH):
        for item in os.listdir(ORDERS_PATH):
            order_path = os.path.join(ORDERS_PATH, item)
            if os.path.isdir(order_path):
                info_file = os.path.join(order_path, "информация_о_заказе.txt")
                status = "new"
                total = 0
                if os.path.exists(info_file):
                    with open(info_file, 'r', encoding='utf-8') as f:
                        content = f.read()
                        status_match = re.search(r'Статус: (.*?)(?:\n|$)', content)
                        if status_match:
                            status_text = status_match.group(1)
                            for key, value in ORDER_STATUSES.items():
                                if value == status_text:
                                    status = key
                                    break
                        total_match = re.search(r'ИТОГО К ОПЛАТЕ: (\d+)', content)
                        if total_match:
                            total = int(total_match.group(1))
                
                orders.append({
                    'id': item,
                    'path': order_path,
                    'status': status,
                    'total': total,
                })
    
    orders.sort(key=lambda x: x['id'], reverse=True)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📦 Все заказы | Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text" style="font-size: 2.5em;">📦 ВСЕ ЗАКАЗЫ</h1>
                    <a href="/" class="glow-btn">🏠 НА ГЛАВНУЮ</a>
                </div>
                
                <div style="display: grid; gap: 30px;">
    """
    
    for order in orders:
        status_class = f"status-{order['status']}"
        status_text = get_status_display(order['status'])
        
        html += f"""
                    <div class="order-card">
                        <div class="order-header">
                            <div style="display: flex; justify-content: space-between; align-items: center;">
                                <h2 style="color: white;">🆔 {order['id'][:15]}...</h2>
                                <span class="status-badge {status_class}">{status_text}</span>
                            </div>
                        </div>
                        <div style="padding: 30px;">
                            <div style="margin-bottom: 20px;">
                                <span>💰 Сумма: <strong>{order['total']} ₽</strong></span>
                            </div>
                            <a href="/orders/{order['id']}/" class="glow-btn">ПОДРОБНЕЕ →</a>
                        </div>
                    </div>
        """
    
    html += """
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/')
def view_order(order_id):
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    info_file = os.path.join(order_path, "информация_о_заказе.txt")
    info_content = ""
    if os.path.exists(info_file):
        with open(info_file, 'r', encoding='utf-8') as f:
            info_content = f.read()
    
    files = []
    for f in os.listdir(order_path):
        file_path = os.path.join(order_path, f)
        if os.path.isfile(file_path) and not f.startswith('информация'):
            ext = f.lower().split('.')[-1] if '.' in f else ''
            files.append({
                'name': f,
                'path': file_path,
                'size_str': format_file_size(os.path.getsize(file_path)),
                'ext': ext
            })
    
    status = "new"
    status_match = re.search(r'Статус: (.*?)(?:\n|$)', info_content)
    if status_match:
        status_text = status_match.group(1)
        for key, value in ORDER_STATUSES.items():
            if value == status_text:
                status = key
                break
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Заказ | Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text" style="font-size: 2em;">🆔 ЗАКАЗ {order_id[:15]}...</h1>
                    <div>
                        <a href="/orders/" class="glow-btn" style="padding: 10px 20px;">📋 НАЗАД</a>
                        <a href="/" class="glow-btn" style="padding: 10px 20px;">🏠 ГЛАВНАЯ</a>
                    </div>
                </div>
                
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 40px; padding: 30px; margin-bottom: 30px;">
                    <h2 style="color: white; margin-bottom: 20px;">Текущий статус:</h2>
                    <div style="display: flex; gap: 10px; flex-wrap: wrap;">
    """
    
    for status_key, status_value in ORDER_STATUSES.items():
        if status_key == status:
            html += f"""<span class="glow-btn" style="cursor: default;">{status_value}</span>"""
        else:
            html += f"""<a href="/orders/{order_id}/status/{status_key}/" class="status-badge status-{status_key}" style="text-decoration: none;">{status_value}</a>"""
    
    html += f"""
                    </div>
                </div>
                
                <div class="order-card" style="padding: 30px;">
                    <h2>📊 Детали заказа</h2>
                    <pre style="white-space: pre-wrap; font-family: inherit;">{info_content}</pre>
                </div>
                
                <div class="order-card" style="padding: 30px; margin-top: 20px;">
                    <h2>📁 Файлы ({len(files)})</h2>
                    <div style="margin-top: 20px;">
    """
    
    for file in files:
        icon = "📸" if file['ext'] in ['jpg', 'jpeg', 'png'] else "📄"
        html += f"""
                        <div style="padding: 10px; border-bottom: 1px solid #eee; display: flex; justify-content: space-between;">
                            <span>{icon} {file['name']} ({file['size_str']})</span>
                            <a href="/orders/{order_id}/file/{file['name']}" class="glow-btn" style="padding: 5px 15px;" download>📥</a>
                        </div>
        """
    
    html += f"""
                    </div>
                </div>
                
                <div style="display: flex; gap: 20px; margin-top: 30px;">
                    <a href="/orders/{order_id}/download/" class="glow-btn" style="flex: 1; text-align: center;">📦 СКАЧАТЬ ВСЕ</a>
                    <a href="/orders/{order_id}/delete/" class="glow-btn" style="flex: 1; text-align: center; background: linear-gradient(45deg, #f44336, #ff1744);"
                       onclick="return confirm('Удалить заказ?');">🗑️ УДАЛИТЬ</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/status/<new_status>/')
def change_status(order_id, new_status):
    if new_status not in ORDER_STATUSES:
        abort(404)
    
    if update_order_status(order_id, new_status):
        return f"""
        <html>
        <head>
            <meta http-equiv="refresh" content="2;url=/orders/{order_id}/">
            {PREMIUM_CSS}
        </head>
        <body>
            <div class="container" style="display: flex; justify-content: center; align-items: center; min-height: 100vh;">
                <div class="premium-card" style="text-align: center;">
                    <div class="stat-icon">✅</div>
                    <h1 class="neon-text">СТАТУС ИЗМЕНЕН</h1>
                    <p style="color: white;">Новый статус: {get_status_display(new_status)}</p>
                </div>
            </div>
        </body>
        </html>
        """
    else:
        abort(500)

@app.route('/orders/<order_id>/file/<filename>')
def download_order_file(order_id, filename):
    order_path = os.path.join(ORDERS_PATH, order_id)
    file_path = os.path.join(order_path, filename)
    
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        abort(404)
    
    return send_file(file_path, as_attachment=True, download_name=filename)

@app.route('/orders/<order_id>/download/')
def download_all_files(order_id):
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    with zipfile.ZipFile(temp_zip.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(order_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, order_path)
                zipf.write(file_path, arcname)
    
    return send_file(
        temp_zip.name,
        as_attachment=True,
        download_name=f"заказ_{order_id}.zip",
        mimetype='application/zip'
    )

@app.route('/orders/<order_id>/delete/')
def delete_order(order_id):
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    try:
        shutil.rmtree(order_path)
        
        history = load_orders_history()
        history = [order for order in history if order.get('order_id') != order_id]
        with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        return f"""
        <html>
        <head>
            <meta http-equiv="refresh" content="2;url=/orders/">
            {PREMIUM_CSS}
        </head>
        <body>
            <div class="container" style="display: flex; justify-content: center; align-items: center; min-height: 100vh;">
                <div class="premium-card" style="text-align: center;">
                    <div class="stat-icon">🗑️</div>
                    <h1 class="neon-text">ЗАКАЗ УДАЛЕН</h1>
                </div>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"Ошибка удаления заказа: {e}")
        abort(500)

@app.route('/stats/')
def stats():
    history = load_orders_history()
    
    total_orders = len(history)
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos', 0) for order in history)
    total_pages = sum(order.get('total_pages', 0) for order in history)
    
    status_stats = {}
    for status in ORDER_STATUSES.keys():
        status_stats[status] = sum(1 for order in history if order.get('status') == status)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Статистика | Print Bot</title>
        {PREMIUM_CSS}
    </head>
    <body>
        <div class="container">
            <div class="premium-card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px;">
                    <h1 class="neon-text">📊 СТАТИСТИКА</h1>
                    <a href="/" class="glow-btn">🏠 ГЛАВНАЯ</a>
                </div>
                
                <div class="stats-grid">
                    <div class="stat-card">
                        <div class="stat-icon">📦</div>
                        <div class="stat-value">{total_orders}</div>
                        <div class="stat-label">Всего заказов</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">💰</div>
                        <div class="stat-value">{total_revenue} ₽</div>
                        <div class="stat-label">Выручка</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">📸</div>
                        <div class="stat-value">{total_photos}</div>
                        <div class="stat-label">Фото</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-icon">📄</div>
                        <div class="stat-value">{total_pages}</div>
                        <div class="stat-label">Страниц</div>
                    </div>
                </div>
                
                <div class="order-card" style="padding: 30px; margin-top: 30px;">
                    <h2>📌 Статусы заказов</h2>
                    <div style="display: flex; gap: 20px; flex-wrap: wrap; margin-top: 20px;">
    """
    
    for status, count in status_stats.items():
        status_class = f"status-{status}"
        status_text = ORDER_STATUSES.get(status, status)
        html += f"""
                        <div style="text-align: center;">
                            <span class="status-badge {status_class}">{status_text}</span>
                            <div style="font-size: 2em; margin-top: 5px;">{count}</div>
                        </div>
        """
    
    html += """
                    </div>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

# ========== HEALTH CHECK ДЛЯ RENDER ==========
@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/healthz')
def healthz():
    return "OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/webhook', methods=['POST'])
def webhook():
    if not bot:
        return "Bot not initialized", 500
    
    try:
        update = telegram.Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "Error", 500

@app.route('/set_webhook')
def set_webhook():
    if not bot:
        return "Bot not initialized", 500
    
    try:
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook manually set to {webhook_url}")
        return f"Webhook set to {webhook_url}", 200
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")
        return f"Error: {e}", 500

def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Произошла ошибка. Пожалуйста, попробуйте еще раз или начните заново с /start"
            )
    except:
        pass

def run_bot():
    global updater, dispatcher, bot
    
    try:
        bot = telegram.Bot(token=TOKEN)
        
        updater = Updater(token=TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        dispatcher.add_error_handler(error_handler)
        dispatcher.add_handler(CommandHandler("start", start))
        
        file_handler = MessageHandler(Filters.document | Filters.photo, handle_file)
        dispatcher.add_handler(file_handler)
        
        text_handler = MessageHandler(Filters.text & ~Filters.command, handle_quantity_input)
        dispatcher.add_handler(text_handler)
        
        dispatcher.add_handler(CallbackQueryHandler(button_handler))
        
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook установлен: {webhook_url}")
        
        bot_info = bot.get_me()
        logger.info(f"Бот запущен: @{bot_info.username}")
        logger.info(f"Папка заказов: {ORDERS_PATH}")
        logger.info(f"Health check доступен по адресу: {RENDER_URL}/health")
        
        app.run(host="0.0.0.0", port=PORT)
        
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    if not os.path.exists(ORDERS_PATH):
        os.makedirs(ORDERS_PATH, exist_ok=True)
        logger.info(f"Создана папка заказов: {ORDERS_PATH}")
    
    print("Запуск Print Bot...")
    print(f"Папка заказов: {ORDERS_PATH}")
    print(f"Render URL: {RENDER_URL}")
    print(f"Webhook URL: {RENDER_URL}/webhook")
    print(f"Health check: {RENDER_URL}/health")
    print(f"Запуск на порту: {PORT}")
    
    run_bot()
