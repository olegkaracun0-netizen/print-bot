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
    print("❌ ОШИБКА: TOKEN не задан в переменных окружения!")
    sys.exit(1)

ADMIN_CHAT_ID = 483613049
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("❌ ОШИБКА: RENDER_EXTERNAL_URL не задан!")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб | СДЭК | Яндекс Доставка"

# ========== ПУТЬ К ПАПКЕ ЗАКАЗОВ ==========
ORDERS_FOLDER = "заказы"
ORDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ORDERS_FOLDER)

try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"📁 Папка заказов: {ORDERS_PATH}")
except Exception as e:
    print(f"❌ Ошибка создания папки: {e}")
    sys.exit(1)

# ========== ФАЙЛ ДЛЯ ХРАНЕНИЯ ИСТОРИИ ==========
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
                        text=f"📢 **Статус вашего заказа изменен**\n\n🆔 Заказ: `{order_id}`\n📌 Новый статус: {get_status_display(new_status)}",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
            
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

# ========== ФУНКЦИЯ ДЛЯ ФОРМАТИРОВАНИЯ РАЗМЕРА ФАЙЛА ==========
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

# ========== ПРЕМИУМ ДИЗАЙН С РОВНЫМИ РАМКАМИ ==========
# Все рамки имеют ширину 34 символа

def create_header(title, emoji):
    """Создает красивый заголовок с рамкой (ровно 34 символа)"""
    # Вычисляем сколько пробелов нужно добавить по бокам
    title_len = len(title) + 4  # +4 для emoji и пробелов
    side_spaces = (32 - title_len) // 2
    left_spaces = " " * side_spaces
    right_spaces = " " * (32 - title_len - side_spaces)
    
    return f"""
╔══════════════════════════════════╗
║{left_spaces}{emoji}  {title.upper()}  {emoji}{right_spaces}║
╚══════════════════════════════════╝"""

def create_divider():
    """Создает разделитель (ровно 34 символа)"""
    return "▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰"

def create_point(text):
    """Создает пункт списка"""
    return f"● {text}"

def create_price(amount):
    """Форматирует цену"""
    return f"💰 **{amount} руб.**"

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
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
            else:
                logger.error(f"❌ Файл не найден: {f['path']}")
        
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
        
        admin_message = f"🆕 НОВЫЙ ЗАКАЗ!\n\n"
        admin_message += f"👤 Клиент: {order_data['user_info']['first_name']} (@{order_data['user_info']['username']})\n"
        admin_message += f"🆔 ID: {order_data['user_info']['user_id']}\n\n"
        
        if order_data['type'] == 'photo':
            format_names = {"small": "Малый (A6)", "medium": "Средний", "large": "Большой (A4)"}
            admin_message += f"📸 Тип: Фото\n"
            admin_message += f"📸 Формат: {format_names[order_data['format']]}\n"
        else:
            color_names = {"bw": "Черно-белая", "color": "Цветная"}
            admin_message += f"📄 Тип: Документы\n"
            admin_message += f"📄 Печать: {color_names[order_data['color']]}\n"
        
        admin_message += f"📦 Копий: {order_data['quantity']}\n"
        admin_message += f"📦 Файлов: {len(order_data['files'])}\n"
        
        if photo_files:
            admin_message += f"📸 Фото: {len(photo_files)} файлов, {total_photos} фото\n"
        if doc_files:
            admin_message += f"📄 Документы: {len(doc_files)} файлов, {total_pages} страниц\n"
        
        admin_message += f"💰 Сумма: {order_data['total']} руб.\n"
        admin_message += f"⏳ Срок: {order_data['delivery']}\n\n"
        admin_message += f"🔗 Ссылка на заказ:\n{order_url}"
        
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message
            )
            logger.info(f"✅ Уведомление отправлено админу {ADMIN_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

# ========== ФУНКЦИИ TELEGRAM ==========

def start(update, context):
    user = update.effective_user
    user_id = user.id
    logger.info(f"✅ /start от {user_id}")
    
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    welcome = f"""
{create_header('ДОБРО ПОЖАЛОВАТЬ', '🤖')}

✨ **Привет, {user.first_name}!** ✨

{create_divider()}

⚡️ **PRINT BOT PREMIUM** ⚡️

{create_divider()}

📌 **МОИ ВОЗМОЖНОСТИ:**

{create_point('📸 Фото (JPG, PNG) - 3 формата')}
{create_point('📄 Документы (PDF, DOC, DOCX)')}
{create_point('📦 Пакетная загрузка до 10 файлов')}
{create_point('💰 Мгновенный расчет стоимости')}
{create_point('📊 Отслеживание статуса заказа')}

{create_divider()}

📞 **Контакты:** `{CONTACT_PHONE}`
🚚 **Доставка:** {DELIVERY_OPTIONS}

{create_divider()}

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
{create_header('ОШИБКА', '❌')}

✨ Неподдерживаемый формат файла

{create_divider()}

📌 **Допустимые форматы:**

{create_point('📸 JPG, PNG')}
{create_point('📄 PDF, DOC, DOCX')}

{create_divider()}

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
        message.reply_text("❌ Не удалось загрузить файл")
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
{create_header('ФАЙЛ ДОБАВЛЕН', '✅')}

📄 **Файл:** `{file_name[:40]}{'...' if len(file_name) > 40 else ''}`
📦 **Тип:** {'📸 Фото' if file_type == 'photo' else '📄 Документ'}
🔢 **Количество:** `{items}` {unit}

{create_divider()}

📊 **ТЕКУЩАЯ СТАТИСТИКА:**
"""
    
    if photo_count > 0:
        text += f"\n{create_point(f'📸 Фото: {photo_count} файлов')}"
    if doc_count > 0:
        text += f"\n{create_point(f'📄 Документы: {doc_count} файлов')}"
    if total_photos > 0:
        text += f"\n{create_point(f'📸 Всего фото: {total_photos}')}"
    if total_pages > 0:
        text += f"\n{create_point(f'📄 Всего страниц: {total_pages}')}"
    text += f"\n{create_point(f'📦 Всего файлов: {files_count}')}"
    
    text += f"\n\n{create_divider()}\n"
    
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
                text="❌ Не удалось загрузить файлы"
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        text = f"""
{create_header(f'ЗАГРУЖЕНО {files_count} ФАЙЛОВ', '✅')}

{create_divider()}

📊 **СТАТИСТИКА:**"""
        
        if photo_count > 0:
            text += f"\n{create_point(f'📸 Фото: {photo_count}')}"
        if doc_count > 0:
            text += f"\n{create_point(f'📄 Документы: {doc_count}')}"
        if total_photos > 0:
            text += f"\n{create_point(f'📸 Всего фото: {total_photos}')}"
        if total_pages > 0:
            text += f"\n{create_point(f'📄 Всего страниц: {total_pages}')}"
        
        text += f"\n\n{create_divider()}\n"
        
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
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text(
            f"""
{create_header('ДОБАВЬТЕ ФАЙЛЫ', '📤')}

✨ Отправьте следующие файлы для печати

{create_divider()}

📌 JPG, PNG, PDF, DOC, DOCX

{create_divider()}
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
{create_header('НОВЫЙ ЗАКАЗ', '🔄')}

✨ Отправьте файлы для печати

{create_divider()}

📌 JPG, PNG, PDF, DOC, DOCX

{create_divider()}
""",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        format_type = data.split("_")[1]
        format_names = {
            "small": "МАЛЫЙ (A6/10x15)",
            "medium": "СРЕДНИЙ (13x18/15x21)",
            "large": "БОЛЬШОЙ (A4/21x30)"
        }
        format_prices = {
            "small": "1-9: 35₽ | 10-50: 28₽ | 51-100: 23₽ | 101+: 18₽",
            "medium": "1-9: 65₽ | 10-50: 55₽ | 51-100: 45₽ | 101+: 35₽",
            "large": "1-4: 200₽ | 5-20: 170₽ | 21-50: 150₽ | 51+: 120₽"
        }
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = format_type
        
        text = f"""
{create_header(format_names[format_type], '🖼️')}

💰 **Цены:**
`{format_prices[format_type]}`

{create_divider()}

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
{create_header(color_names[doc_type], '📄')}

💰 **Цены:**
`{color_prices[doc_type]}`

📊 **В файлах:**
{create_point(f'📸 Фото: {total_photos}')}
{create_point(f'📄 Страниц: {total_pages}')}
{create_point(f'📦 Всего: {total_items} ед.')}

{create_divider()}

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
        details = f"{create_header('ДЕТАЛЬНЫЙ РАСЧЕТ', '💰')}\n\n"
        
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
                details += f"📸 **Файл {i}:**\n   {create_point(f'{f["items"]} фото × {quantity} коп. = {f["items"] * quantity} фото → {file_total} ₽')}\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += f"📄 **Файл {i}:**\n   {create_point(f'{f["items"]} стр. × {quantity} коп. = {file_items} стр. → {file_total} ₽')}\n\n"
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = f"""
{details}
{create_header('ПРОВЕРЬТЕ ЗАКАЗ', '📋')}

📦 **Всего файлов:** {len(files)}
"""
        if total_photos_result > 0:
            text += f"\n📸 **Фото к печати:** {total_photos_result}"
        if total_pages_result > 0:
            text += f"\n📄 **Страниц к печати:** {total_pages_result}"
        
        text += f"""
{create_divider()}

💰 **ИТОГОВАЯ СУММА:** {create_price(total)}
⏳ **Срок выполнения:** {session['delivery']}

{create_divider()}

❓ **Всё верно?**
"""
        
        keyboard = [
            [InlineKeyboardButton("✅ ДА, ПОДТВЕРДИТЬ", callback_data="confirm")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНИТЬ", callback_data="cancel")]
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
{create_header('ЗАКАЗ ОФОРМЛЕН', '🎉')}

✨ **Спасибо, {session['user_info']['first_name']}!** ✨

{create_divider()}

🆔 **Номер заказа:** `{order_id}`
👤 **Заказчик:** {session['user_info']['first_name']}
📦 **Файлов:** {len(session['files'])}

{create_divider()}
"""
            
            if total_photos > 0:
                client_message += f"\n📸 **Фото в оригинале:** {total_photos}"
                client_message += f"\n📸 **Фото к печати:** {total_photos * session['quantity']}"
            if total_pages > 0:
                client_message += f"\n📄 **Страниц в оригинале:** {total_pages}"
                client_message += f"\n📄 **Страниц к печати:** {total_pages * session['quantity']}"
            
            client_message += f"""
{create_divider()}

💰 **Сумма к оплате:** {create_price(session['total'])}
⏳ **Срок выполнения:** {session['delivery']}

{create_divider()}

📞 **Контактный телефон:** `{CONTACT_PHONE}`
🚚 **Доставка:** {DELIVERY_OPTIONS}

{create_divider()}

📌 **Статус:** {get_status_display('new')}
Вы будете получать уведомления при изменении статуса.

{create_header('СПАСИБО', '✨')}
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
                text="❌ Ошибка при сохранении заказа"
            )
        
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=f"""
{create_header('ЕЩЕ ЗАКАЗ?', '🤔')}

✨ Нажмите кнопку ниже чтобы начать заново! ✨

{create_divider()}
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
        logger.info(f"✅ Сессия пользователя {user_id} очищена")
    
    keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = f"""
{create_header('ЗАКАЗ ОТМЕНЕН', '❌')}

✨ Все загруженные файлы удалены ✨

{create_divider()}

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
{create_header('ОШИБКА', '❌')}

✨ Введите число от **1** до **1000**

Или выберите из кнопок ниже:

{create_divider()}
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

# ========== ВЕБ-ИНТЕРФЕЙС ==========
@app.route('/')
def home():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    
    history = load_orders_history()
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos', 0) for order in history)
    total_pages = sum(order.get('total_pages', 0) for order in history)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Print Bot</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; background: #f0f0f0; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .card {{ background: white; border-radius: 10px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; }}
            .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin: 20px 0; }}
            .stat {{ background: #4CAF50; color: white; padding: 20px; border-radius: 10px; text-align: center; }}
            .stat-value {{ font-size: 2em; font-weight: bold; }}
            .btn {{ background: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>Print Bot</h1>
                <div class="stats">
                    <div class="stat"><div class="stat-value">{orders_count}</div>заказов</div>
                    <div class="stat"><div class="stat-value">{total_revenue} ₽</div>выручка</div>
                    <div class="stat"><div class="stat-value">{total_photos}</div>фото</div>
                    <div class="stat"><div class="stat-value">{total_pages}</div>страниц</div>
                </div>
                <a href="/orders/" class="btn">Все заказы</a>
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
                    'status': status,
                    'total': total,
                })
    
    html = "<h1>Все заказы</h1>"
    for order in orders:
        html += f"<div>{order['id']} - {order['total']}₽</div>"
    
    return html

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
        return f"Webhook set to {webhook_url}", 200
    except Exception as e:
        return f"Error: {e}", 500

def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}")
    
    try:
        if update and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Произошла ошибка. Пожалуйста, попробуйте еще раз или начните заново с /start",
                parse_mode="Markdown"
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
        logger.info(f"✅ Webhook установлен: {webhook_url}")
        
        bot_info = bot.get_me()
        logger.info(f"✅ Бот запущен: @{bot_info.username}")
        logger.info(f"✅ Папка заказов: {ORDERS_PATH}")
        
        app.run(host="0.0.0.0", port=PORT)
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    if not os.path.exists(ORDERS_PATH):
        os.makedirs(ORDERS_PATH, exist_ok=True)
        logger.info(f"📁 Создана папка заказов: {ORDERS_PATH}")
    
    print("🚀 Запуск Print Bot...")
    print(f"📁 Папка заказов: {ORDERS_PATH}")
    print(f"🌍 Render URL: {RENDER_URL}")
    print(f"🔗 Webhook URL: {RENDER_URL}/webhook")
    print(f"❤️ Health check: {RENDER_URL}/health")
    
    run_bot()
