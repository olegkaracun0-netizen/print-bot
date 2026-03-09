#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
🎨 АНИМИРОВАННЫЙ ДИЗАЙН С ЦЕНТРИРОВАНИЕМ 🎨
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
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template_string, abort

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

ORDERS_DB_FILE = os.path.join(ORDERS_PATH, "orders_history.json")

ORDER_STATUSES = {
    "new": "🆕 Новый заказ",
    "processing": "🔄 В обработке",
    "printing": "🖨️ В печати",
    "ready": "✅ Готов к выдаче",
    "shipped": "📦 Отправлен",
    "delivered": "🏁 Доставлен",
    "cancelled": "❌ Отменен"
}

# ========== АНИМАЦИИ ==========
ANIMATIONS = {
    "loading": ["⏳", "⌛️"],
    "success": ["✅", "✨", "🌟"],
    "processing": ["🔄", "⏳", "⌛️"],
    "photo": ["📸", "📷", "🎞️"],
    "doc": ["📄", "📑", "📃"],
    "delivery": ["🚚", "📦", "✈️"],
    "money": ["💰", "💵", "💶"]
}

def get_animation(anim_type, index):
    animations = ANIMATIONS.get(anim_type, ["•"])
    return animations[index % len(animations)]

class AnimationManager:
    def __init__(self):
        self.counters = {}
    
    def get_next(self, user_id, anim_type):
        key = f"{user_id}_{anim_type}"
        if key not in self.counters:
            self.counters[key] = 0
        self.counters[key] += 1
        return get_animation(anim_type, self.counters[key])

anim_manager = AnimationManager()

# ========== ФУНКЦИИ ==========
def get_status_display(status):
    return ORDER_STATUSES.get(status, status)

def format_order_link(order_id):
    """Форматирует ссылку на заказ для Telegram"""
    url = f"{RENDER_URL}/orders/{order_id}/"
    return f"[🔗 Перейти к заказу]({url})"

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
                    status_emojis = {
                        "new": "🆕",
                        "processing": "🔄",
                        "printing": "🖨️",
                        "ready": "✅",
                        "shipped": "📦",
                        "delivered": "🏁",
                        "cancelled": "❌"
                    }
                    emoji = status_emojis.get(new_status, "📢")
                    
                    message = (
                        f"╔════════════════════════╗\n"
                        f"║  {emoji} **СТАТУС ОБНОВЛЕН** {emoji}  ║\n"
                        f"╚════════════════════════╝\n\n"
                        f"🆔 **Заказ:** `{order_id}`\n"
                        f"📌 **Новый статус:** {get_status_display(new_status)}\n\n"
                        f"{format_order_link(order_id)}\n\n"
                        f"Спасибо, что пользуетесь нашим сервисом! 🌟"
                    )
                    
                    bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode="Markdown",
                        disable_web_page_preview=False
                    )
                    logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
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

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
) = range(5)

user_sessions = {}
media_groups = {}
group_timers = {}
updater = None
dispatcher = None
bot = None

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
        return "⚡ 1 день (экспресс)"
    elif total_items <= 200:
        return "📅 2 дня (стандарт)"
    else:
        return "📆 3 дня (обычный)"

def extract_number_from_text(text):
    numbers = re.findall(r'\d+', text)
    return int(numbers[0]) if numbers else None

def count_items_in_file(file_path, file_name):
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                page_count = len(pdf.pages)
                logger.info(f"📄 PDF: {file_name} - {page_count} страниц")
                return page_count, "страниц", "документ"
                
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            paragraphs = len(doc.paragraphs)
            estimated_pages = max(1, paragraphs // 35)
            
            tables_count = len(doc.tables)
            if tables_count > 0:
                estimated_pages += tables_count // 2
            
            logger.info(f"📄 Word: {file_name} - {estimated_pages} страниц")
            return estimated_pages, "страниц", "документ"
            
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
            logger.info(f"📸 Фото: {file_name} - 1 фото")
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
        
        logger.info(f"✅ Файл скачан: {file_path}")
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
        logger.info(f"📁 Создана папка заказа: {order_folder}")
        
        saved_files = []
        
        for i, f in enumerate(files_info, 1):
            if os.path.exists(f['path']):
                safe_name = re.sub(r'[<>:"/\\|?*]', '', f['name'])
                new_path = os.path.join(order_folder, f"{i}_{safe_name}")
                shutil.copy2(f['path'], new_path)
                saved_files.append(new_path)
                logger.info(f"📄 Файл {i} скопирован: {new_path}")
            else:
                logger.error(f"❌ Файл не найден: {f['path']}")
        
        photo_files = [ff for ff in files_info if ff['type'] == 'photo']
        doc_files = [ff for ff in files_info if ff['type'] == 'doc']
        
        total_photos = sum(ff['items'] for ff in photo_files)
        total_pages = sum(ff['items'] for ff in doc_files)
        
        info_file = os.path.join(order_folder, "информация_о_заказе.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"╔════════════════════════════╗\n")
            f.write(f"║     ЗАКАЗ ОТ {datetime.now().strftime('%d.%m.%Y')}     ║\n")
            f.write(f"╚════════════════════════════╝\n\n")
            f.write(f"👤 Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"🆔 ID: {user_id}\n")
            f.write(f"📞 Телефон: {CONTACT_PHONE}\n")
            f.write(f"📌 Статус: {get_status_display('new')}\n\n")
            
            if order_data['type'] == 'photo':
                format_names = {"small": "🖼️ Малый (A6/10x15)", "medium": "🖼️ Средний (13x18/15x21)", "large": "🖼️ Большой (A4/21x30)"}
                f.write(f"📸 Тип: Фотопечать\n")
                f.write(f"📸 Формат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "⚫ Черно-белая", "color": "🎨 Цветная"}
                f.write(f"📄 Тип: Печать документов\n")
                f.write(f"📄 Цветность: {color_names[order_data['color']]}\n")
            
            f.write(f"📦 Количество копий: {order_data['quantity']}\n\n")
            
            if photo_files:
                f.write(f"📸 ФОТОГРАФИИ:\n")
                f.write(f"   • Количество файлов: {len(photo_files)}\n")
                f.write(f"   • Всего фото: {total_photos}\n")
                f.write(f"   • К печати: {total_photos * order_data['quantity']}\n\n")
            
            if doc_files:
                f.write(f"📄 ДОКУМЕНТЫ:\n")
                f.write(f"   • Количество файлов: {len(doc_files)}\n")
                f.write(f"   • Всего страниц: {total_pages}\n")
                f.write(f"   • К печати: {total_pages * order_data['quantity']}\n\n")
            
            f.write(f"💰 ИТОГО К ОПЛАТЕ: {order_data['total']} руб.\n")
            f.write(f"⏳ Срок выполнения: {order_data['delivery']}\n\n")
            
            f.write(f"📁 ЗАГРУЖЕННЫЕ ФАЙЛЫ:\n")
            for i, file_info in enumerate(files_info, 1):
                icon = "📸" if file_info['type'] == 'photo' else "📄"
                f.write(f"{icon} {i}. {file_info['name']}\n")
                f.write(f"   • Тип: {file_info['type_name']}\n")
                f.write(f"   • Количество: {file_info['items']} {file_info['unit']}\n")
            
            f.write(f"\n📊 Всего файлов: {len(files_info)}")
        
        logger.info(f"📝 Информация о заказе сохранена в {info_file}")
        
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
            f"╔════════════════════════╗\n"
            f"║    🆕 НОВЫЙ ЗАКАЗ 🆕    ║\n"
            f"╚════════════════════════╝\n\n"
            f"👤 **Клиент:** {order_data['user_info']['first_name']}\n"
            f"🆔 **Username:** @{order_data['user_info']['username']}\n"
            f"📱 **ID:** `{order_data['user_info']['user_id']}`\n\n"
        )
        
        if order_data['type'] == 'photo':
            format_names = {"small": "🖼️ Малый (A6)", "medium": "🖼️ Средний", "large": "🖼️ Большой (A4)"}
            admin_message += f"📸 **Тип:** Фотопечать\n"
            admin_message += f"📸 **Формат:** {format_names[order_data['format']]}\n"
        else:
            color_names = {"bw": "⚫ Черно-белая", "color": "🎨 Цветная"}
            admin_message += f"📄 **Тип:** Печать документов\n"
            admin_message += f"📄 **Цветность:** {color_names[order_data['color']]}\n"
        
        admin_message += f"📦 **Копий:** {order_data['quantity']}\n"
        admin_message += f"📦 **Файлов:** {len(order_data['files'])}\n\n"
        
        if photo_files:
            admin_message += f"📸 **Фото:** {len(photo_files)} файлов, {total_photos} фото\n"
        if doc_files:
            admin_message += f"📄 **Документы:** {len(doc_files)} файлов, {total_pages} страниц\n"
        
        admin_message += f"\n💰 **Сумма:** {order_data['total']} руб.\n"
        admin_message += f"⏳ **Срок:** {order_data['delivery']}\n\n"
        admin_message += f"🔗 **Ссылка:**\n`{order_url}`"
        
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message,
                parse_mode="Markdown"
            )
            logger.info(f"✅ Уведомление отправлено админу {ADMIN_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

def start(update, context):
    user = update.effective_user
    user_id = user.id
    logger.info(f"✅ /start от {user_id}")
    
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    anim = anim_manager.get_next(user_id, "success")
    
    welcome = (
        f"╔════════════════════════╗\n"
        f"║  {anim} **ДОБРО ПОЖАЛОВАТЬ** {anim}  ║\n"
        f"╚════════════════════════╝\n\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃  📸🖨️ **Фотостудия**    ┃\n"
        f"┃     **в телефоне**      ┃\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"**{user.first_name}**, рады видеть тебя! ✨\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📎 **Что я умею:**\n"
        f"   {anim_manager.get_next(user_id, 'photo')} Печать фото (любые форматы)\n"
        f"   {anim_manager.get_next(user_id, 'doc')} Печать документов (PDF, Word)\n"
        f"   {anim_manager.get_next(user_id, 'money')} Авто-расчет стоимости\n"
        f"   {anim_manager.get_next(user_id, 'delivery')} Отслеживание статуса\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📤 **Как сделать заказ:**\n"
        f"   **1.** Отправь файлы\n"
        f"   **2.** Выбери формат\n"
        f"   **3.** Укажи количество\n"
        f"   **4.** Подтверди заказ\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 **Контакты:** `{CONTACT_PHONE}`\n"
        f"🚚 **Доставка:** {DELIVERY_OPTIONS}\n\n"
        f"🌟 **Отправляй файлы и начинаем!** 🌟"
    )
    
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
                text="❌ **Ошибка загрузки**\n\nПопробуйте еще раз!",
                parse_mode="Markdown"
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        anim = anim_manager.get_next(user_id, "success")
        
        text = (
            f"╔════════════════════════╗\n"
            f"║   {anim} **ФАЙЛЫ ЗАГРУЖЕНЫ** {anim}   ║\n"
            f"╚════════════════════════╝\n\n"
            f"✅ **Загружено:** {files_count} файлов\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 **Статистика:**\n"
        )
        
        if photo_count > 0:
            text += f"   {anim_manager.get_next(user_id, 'photo')} Фото: {photo_count}\n"
        if doc_count > 0:
            text += f"   {anim_manager.get_next(user_id, 'doc')} Документы: {doc_count}\n"
        
        if total_photos > 0:
            text += f"   📸 Всего фото: {total_photos}\n"
        if total_pages > 0:
            text += f"   📄 Всего страниц: {total_pages}\n"
        
        text += f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if doc_count > 0:
            text += "🔽 **Выберите тип печати:**"
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая печать", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная печать", callback_data="doc_color")],
                [InlineKeyboardButton("📤 Добавить еще файлы", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
            ]
        else:
            text += "🔽 **Выберите формат:**"
            keyboard = [
                [InlineKeyboardButton("🖼️ Малый (A6) • 18-35₽", callback_data="photo_small")],
                [InlineKeyboardButton("🖼️ Средний (13x18) • 35-65₽", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼️ Большой (A4) • 120-200₽", callback_data="photo_large")],
                [InlineKeyboardButton("📤 Добавить еще файлы", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
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
                "❌ **Неподдерживаемый формат**\n\n"
                "Отправьте файлы в форматах:\n"
                "   📸 JPG, PNG\n"
                "   📄 PDF, DOC, DOCX",
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
        message.reply_text("❌ **Ошибка загрузки**\n\nПопробуйте еще раз!")
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
    
    anim = anim_manager.get_next(user_id, "success")
    
    text = (
        f"╔════════════════════════╗\n"
        f"║    {anim} **ФАЙЛ ДОБАВЛЕН** {anim}    ║\n"
        f"╚════════════════════════╝\n\n"
        f"📄 **{file_name[:30]}**\n"
        f"📊 **Размер:** {format_file_size(file_obj.file_size if hasattr(file_obj, 'file_size') else 0)}\n"
        f"📦 **Количество:** {items} {unit}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **Всего загружено:**\n"
        f"   📁 Файлов: {files_count}\n"
    )
    
    if photo_count > 0:
        text += f"   {anim_manager.get_next(user_id, 'photo')} Фото: {photo_count}\n"
    if doc_count > 0:
        text += f"   {anim_manager.get_next(user_id, 'doc')} Документы: {doc_count}\n"
    
    if total_photos > 0:
        text += f"   📸 Всего фото: {total_photos}\n"
    if total_pages > 0:
        text += f"   📄 Всего страниц: {total_pages}\n"
    
    text += f"\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if doc_count > 0:
        text += "🔽 **Выберите тип печати:**"
        keyboard = [
            [InlineKeyboardButton("⚫ Черно-белая печать", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 Цветная печать", callback_data="doc_color")],
            [InlineKeyboardButton("📤 Добавить еще файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
        ]
    else:
        text += "🔽 **Выберите формат:**"
        keyboard = [
            [InlineKeyboardButton("🖼️ Малый (A6) • 18-35₽", callback_data="photo_small")],
            [InlineKeyboardButton("🖼️ Средний (13x18) • 35-65₽", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼️ Большой (A4) • 120-200₽", callback_data="photo_large")],
            [InlineKeyboardButton("📤 Добавить еще файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отменить", callback_data="cancel")]
        ]
    
    message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
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
    
    message_text = (
        f"╔════════════════════════╗\n"
        f"║     ❌ **ОТМЕНА** ❌     ║\n"
        f"╚════════════════════════╝\n\n"
        f"Заказ отменен\n"
        f"Файлы удалены\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Хотите оформить новый заказ?"
    )
    
    if query:
        try:
            query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        except:
            if context:
                context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
    elif context:
        context.bot.send_message(
            chat_id=user_id,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
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
        anim = anim_manager.get_next(user_id, "processing")
        query.edit_message_text(
            f"╔════════════════════════╗\n"
            f"║  {anim} **ДОБАВЬТЕ ФАЙЛЫ** {anim}  ║\n"
            f"╚════════════════════════╝\n\n"
            f"Отправляйте файлы по одному или группой.",
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
        
        anim = anim_manager.get_next(user_id, "success")
        query.edit_message_text(
            f"╔════════════════════════╗\n"
            f"║  {anim} **НОВЫЙ ЗАКАЗ** {anim}  ║\n"
            f"╚════════════════════════╝\n\n"
            f"Отправьте файлы для печати:\n"
            f"   📸 JPG, PNG\n"
            f"   📄 PDF, DOC, DOCX",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        anim = anim_manager.get_next(user_id, "photo")
        query.edit_message_text(
            f"╔════════════════════════╗\n"
            f"║  {anim} **ФОТОПЕЧАТЬ** {anim}  ║\n"
            f"╚════════════════════════╝\n\n"
            f"🔢 **Сколько копий напечатать?**\n\n"
            f"Выберите количество:",
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
        )
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = data.split("_")[1]
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        anim = anim_manager.get_next(user_id, "doc")
        query.edit_message_text(
            f"╔════════════════════════╗\n"
            f"║  {anim} **ПЕЧАТЬ ДОКУМЕНТОВ** {anim}  ║\n"
            f"╚════════════════════════╝\n\n"
            f"📊 **В файлах:**\n"
            f"   📸 Фото: {total_photos}\n"
            f"   📄 Страниц: {total_pages}\n\n"
            f"🔢 **Сколько копий напечатать?**\n\n"
            f"Выберите количество:",
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
        details = f"╔════════════════════════╗\n║   💰 **РАСЧЕТ** 💰   ║\n╚════════════════════════╝\n\n"
        
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
                details += f"📸 **Файл {i}:**\n"
                details += f"   📄 {f['name'][:30]}...\n"
                details += f"   🔢 {f['items']} фото × {quantity} = {f['items'] * quantity} фото\n"
                details += f"   💰 {file_total // quantity}₽/шт • **{file_total}₽**\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += f"📄 **Файл {i}:**\n"
                details += f"   📄 {f['name'][:30]}...\n"
                details += f"   🔢 {f['items']} стр. × {quantity} = {file_items} стр.\n"
                details += f"   💰 {file_total // file_items}₽/стр • **{file_total}₽**\n\n"
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        anim_money = anim_manager.get_next(user_id, "money")
        
        text = f"{details}\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += f"📋 **Проверьте заказ:**\n\n"
        text += f"   📦 Файлов: {len(files)}\n"
        if total_photos_result > 0:
            text += f"   📸 Фото к печати: {total_photos_result}\n"
        if total_pages_result > 0:
            text += f"   📄 Страниц к печати: {total_pages_result}\n"
        text += f"   {anim_money} **ИТОГО: {total}₽**\n"
        text += f"   ⏳ Срок: {session['delivery']}\n\n"
        text += f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "**Все верно?**"
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, подтвердить", callback_data="confirm"),
             InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel")]
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
            
            anim_success = anim_manager.get_next(user_id, "success")
            
            client_message = (
                f"╔════════════════════════╗\n"
                f"║  {anim_success} **ЗАКАЗ ПРИНЯТ** {anim_success}  ║\n"
                f"╚════════════════════════╝\n\n"
                f"🆔 **Номер заказа:** `{order_id}`\n"
                f"👤 **Заказчик:** {session['user_info']['first_name']}\n"
                f"📦 **Файлов:** {len(session['files'])}\n\n"
                f"{format_order_link(order_id)}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            )
            
            if total_photos > 0:
                client_message += f"   📸 Фото в оригинале: {total_photos}\n"
                client_message += f"   📸 Фото к печати: {total_photos * session['quantity']}\n"
            if total_pages > 0:
                client_message += f"   📄 Страниц в оригинале: {total_pages}\n"
                client_message += f"   📄 Страниц к печати: {total_pages * session['quantity']}\n"
            
            client_message += (
                f"\n   💰 **Сумма к оплате:** {session['total']}₽\n"
                f"   ⏳ **Срок:** {session['delivery']}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 **Контакт:** {CONTACT_PHONE}\n"
                f"🚚 **Доставка:** {DELIVERY_OPTIONS}\n\n"
                f"📌 **Статус:** {get_status_display('new')}\n"
                f"📢 Вы получите уведомление!\n\n"
                f"🌟 **Спасибо за заказ!** 🌟"
            )
            
            context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode="Markdown",
                disable_web_page_preview=False
            )
            
            if photo_files:
                try:
                    media_group = []
                    for i, photo_file in enumerate(photo_files[:5]):
                        with open(photo_file['path'], 'rb') as photo:
                            if i == 0:
                                media_group.append(InputMediaPhoto(
                                    photo.read(),
                                    caption=f"📸 Загружено фото: {len(photo_files)} шт."
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
                text="❌ **Ошибка при сохранении заказа**\n\nПопробуйте еще раз!",
                parse_mode="Markdown"
            )
        
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 Новый заказ", callback_data="new_order")]]
        query.message.delete()
        anim = anim_manager.get_next(user_id, "success")
        context.bot.send_message(
            chat_id=user_id,
            text=f"{anim} **Хотите оформить еще один заказ?** {anim}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

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
         InlineKeyboardButton("💯", callback_data="qty_100")],
        [InlineKeyboardButton("2️⃣0️⃣0️⃣", callback_data="qty_200"), 
         InlineKeyboardButton("3️⃣0️⃣0️⃣", callback_data="qty_300"),
         InlineKeyboardButton("4️⃣0️⃣0️⃣", callback_data="qty_400"), 
         InlineKeyboardButton("5️⃣0️⃣0️⃣", callback_data="qty_500")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def handle_quantity_input(update, context):
    user_id = update.effective_user.id
    text = update.message.text
    quantity = extract_number_from_text(text)
    
    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            "❌ **Ошибка ввода**\n\n"
            "Введите число от 1 до 1000",
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
                    age = datetime.now() - created
                    
                    orders.append({
                        'id': item,
                        'name': item,
                        'info': info_text,
                        'files': files,
                        'photos': photos[:5],
                        'file_count': file_count,
                        'total_size': format_file_size(total_size),
                        'created': created.strftime('%d.%m.%Y %H:%M'),
                        'age_days': age.days,
                        'status': get_status_display(status)
                    })
        
        orders.sort(key=lambda x: x['created'], reverse=True)
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>📦 Заказы - Print Bot</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @keyframes float {
                    0% { transform: translateY(0px); }
                    50% { transform: translateY(-5px); }
                    100% { transform: translateY(0px); }
                }
                
                @keyframes pulse {
                    0% { opacity: 1; }
                    50% { opacity: 0.7; }
                    100% { opacity: 1; }
                }
                
                @keyframes gradient {
                    0% { background-position: 0% 50%; }
                    50% { background-position: 100% 50%; }
                    100% { background-position: 0% 50%; }
                }
                
                * { margin: 0; padding: 0; box-sizing: border-box; }
                
                body { 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                    background: linear-gradient(-45deg, #667eea, #764ba2, #6b8cff, #a855f7);
                    background-size: 400% 400%;
                    animation: gradient 15s ease infinite;
                    min-height: 100vh;
                    padding: 20px;
                }
                
                .container { 
                    max-width: 1400px; 
                    margin: 0 auto; 
                }
                
                .header { 
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 30px;
                    margin-bottom: 30px;
                    color: white;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.2);
                    text-align: center;
                    animation: float 3s ease-in-out infinite;
                }
                
                .nav-links { 
                    display: flex;
                    gap: 15px;
                    justify-content: center;
                    margin-bottom: 30px;
                    flex-wrap: wrap;
                }
                
                .nav-btn { 
                    background: rgba(255,255,255,0.15);
                    color: white;
                    text-decoration: none;
                    padding: 12px 25px;
                    border-radius: 12px;
                    font-weight: 500;
                    transition: all 0.3s;
                    backdrop-filter: blur(5px);
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                }
                
                .nav-btn:hover { 
                    background: rgba(255,255,255,0.25);
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                }
                
                .orders-grid { 
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
                    gap: 25px;
                }
                
                .order-card { 
                    background: white;
                    border-radius: 20px;
                    overflow: hidden;
                    box-shadow: 0 15px 35px rgba(0,0,0,0.2);
                    transition: all 0.3s;
                    text-align: center;
                }
                
                .order-card:hover { 
                    transform: translateY(-5px) scale(1.02);
                    box-shadow: 0 20px 40px rgba(0,0,0,0.3);
                }
                
                .order-header { 
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    padding: 20px;
                    text-align: center;
                }
                
                .order-content { 
                    padding: 20px;
                }
                
                .status-badge {
                    display: inline-block;
                    padding: 5px 15px;
                    border-radius: 20px;
                    font-size: 14px;
                    font-weight: bold;
                    margin: 10px 0;
                    animation: pulse 2s ease-in-out infinite;
                }
                
                .status-badge.new { background: #e3f2fd; color: #1976d2; }
                .status-badge.processing { background: #fff3e0; color: #f57c00; }
                .status-badge.printing { background: #e8f5e8; color: #388e3c; }
                .status-badge.ready { background: #e8e8f5; color: #5c6bc0; }
                .status-badge.shipped { background: #f3e5f5; color: #8e24aa; }
                .status-badge.delivered { background: #e8f0fe; color: #1e88e5; }
                .status-badge.cancelled { background: #ffebee; color: #d32f2f; }
                
                .status-buttons { 
                    display: flex;
                    gap: 5px;
                    flex-wrap: wrap;
                    justify-content: center;
                    margin: 15px 0;
                }
                
                .status-btn { 
                    padding: 8px 12px;
                    border: none;
                    border-radius: 8px;
                    cursor: pointer;
                    font-size: 14px;
                    transition: all 0.2s;
                }
                
                .status-btn:hover { 
                    transform: scale(1.1);
                    box-shadow: 0 3px 10px rgba(0,0,0,0.2);
                }
                
                .status-btn.new { background: #e3f2fd; color: #1976d2; }
                .status-btn.processing { background: #fff3e0; color: #f57c00; }
                .status-btn.printing { background: #e8f5e8; color: #388e3c; }
                .status-btn.ready { background: #e8e8f5; color: #5c6bc0; }
                .status-btn.shipped { background: #f3e5f5; color: #8e24aa; }
                .status-btn.delivered { background: #e8f0fe; color: #1e88e5; }
                .status-btn.cancelled { background: #ffebee; color: #d32f2f; }
                
                .photo-gallery { 
                    display: flex;
                    gap: 10px;
                    overflow-x: auto;
                    padding: 10px 0;
                    justify-content: center;
                }
                
                .photo-preview { 
                    width: 60px;
                    height: 60px;
                    object-fit: cover;
                    border-radius: 10px;
                    cursor: pointer;
                    transition: all 0.3s;
                }
                
                .photo-preview:hover { 
                    transform: scale(1.2) rotate(5deg);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.3);
                }
                
                .action-btn { 
                    display: inline-block;
                    padding: 10px 20px;
                    background: #28a745;
                    color: white;
                    text-decoration: none;
                    border-radius: 10px;
                    margin: 5px;
                    transition: all 0.3s;
                }
                
                .action-btn:hover { 
                    background: #218838;
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                }
                
                .stats { 
                    display: flex;
                    gap: 10px;
                    justify-content: center;
                    margin-top: 10px;
                    flex-wrap: wrap;
                }
                
                .stat { 
                    background: #f8f9fa;
                    padding: 5px 10px;
                    border-radius: 20px;
                    font-size: 12px;
                    display: flex;
                    align-items: center;
                    gap: 5px;
                }
                
                .order-link {
                    display: inline-block;
                    background: #f0f0f0;
                    padding: 8px 15px;
                    border-radius: 20px;
                    color: #667eea;
                    text-decoration: none;
                    font-weight: bold;
                    margin: 10px 0;
                    transition: all 0.3s;
                }
                
                .order-link:hover {
                    background: #667eea;
                    color: white;
                    transform: scale(1.05);
                }
                
                .text-center { text-align: center; }
                
                .floating-emoji {
                    display: inline-block;
                    animation: float 2s ease-in-out infinite;
                }
            </style>
            <script>
                function updateStatus(orderId, status) {
                    fetch(`/orders/${orderId}/status`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({status: status})
                    }).then(r => r.json()).then(d => { 
                        if(d.success) {
                            location.reload();
                        } else {
                            alert('❌ Ошибка обновления статуса');
                        }
                    });
                }
            </script>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1><span class="floating-emoji">📦</span> Заказы на печать <span class="floating-emoji">🖨️</span></h1>
                    <p>Всего заказов: {{ orders|length }}</p>
                </div>
                
                <div class="nav-links">
                    <a href="/" class="nav-btn"><span class="floating-emoji">🏠</span> Главная</a>
                    <a href="/stats" class="nav-btn"><span class="floating-emoji">📊</span> Статистика</a>
                </div>
                
                <div class="orders-grid">
                    {% for order in orders %}
                    <div class="order-card">
                        <div class="order-header">
                            <h3>{{ order.id[:20] }}...</h3>
                            <div class="status-badge {{ order.status.split()[0].lower() }}">
                                {{ order.status }}
                            </div>
                        </div>
                        
                        <div class="order-content">
                            <div class="stats">
                                <span class="stat"><span class="floating-emoji">📁</span> {{ order.file_count }}</span>
                                <span class="stat"><span class="floating-emoji">📦</span> {{ order.total_size }}</span>
                                <span class="stat"><span class="floating-emoji">📅</span> {{ order.created }}</span>
                            </div>
                            
                            <div class="status-buttons">
                                <button class="status-btn new" onclick="updateStatus('{{ order.id }}','new')" title="Новый">🆕</button>
                                <button class="status-btn processing" onclick="updateStatus('{{ order.id }}','processing')" title="В обработке">🔄</button>
                                <button class="status-btn printing" onclick="updateStatus('{{ order.id }}','printing')" title="В печати">🖨️</button>
                                <button class="status-btn ready" onclick="updateStatus('{{ order.id }}','ready')" title="Готов">✅</button>
                                <button class="status-btn shipped" onclick="updateStatus('{{ order.id }}','shipped')" title="Отправлен">📦</button>
                                <button class="status-btn delivered" onclick="updateStatus('{{ order.id }}','delivered')" title="Доставлен">🏁</button>
                                <button class="status-btn cancelled" onclick="updateStatus('{{ order.id }}','cancelled')" title="Отменен">❌</button>
                            </div>
                            
                            <a href="/orders/{{ order.id }}/" class="order-link">
                                <span class="floating-emoji">🔗</span> Перейти к заказу
                            </a>
                            
                            {% if order.photos %}
                            <div class="photo-gallery">
                                {% for photo in order.photos %}
                                <img src="{{ photo.url }}" class="photo-preview" onclick="window.open('{{ photo.url }}')" title="{{ photo.name }}">
                                {% endfor %}
                            </div>
                            {% endif %}
                            
                            <div>
                                <a href="/orders/{{ order.id }}/" class="action-btn"><span class="floating-emoji">👁️</span> Подробнее</a>
                                <a href="/orders/{{ order.id }}/download" class="action-btn"><span class="floating-emoji">⬇️</span> Скачать</a>
                            </div>
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
            <title>Заказ {order_id[:30]} - Print Bot</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                @keyframes float {{
                    0% {{ transform: translateY(0px); }}
                    50% {{ transform: translateY(-5px); }}
                    100% {{ transform: translateY(0px); }}
                }}
                
                @keyframes gradient {{
                    0% {{ background-position: 0% 50%; }}
                    50% {{ background-position: 100% 50%; }}
                    100% {{ background-position: 0% 50%; }}
                }}
                
                * {{ margin: 0; padding: 0; box-sizing: border-box; }}
                
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                    background: linear-gradient(-45deg, #667eea, #764ba2, #6b8cff, #a855f7);
                    background-size: 400% 400%;
                    animation: gradient 15s ease infinite;
                    padding: 20px;
                }}
                
                .container {{ 
                    max-width: 1200px; 
                    margin: 0 auto; 
                }}
                
                .header {{ 
                    background: rgba(255,255,255,0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 30px;
                    color: white;
                    margin-bottom: 30px;
                    text-align: center;
                }}
                
                .nav-links {{ 
                    display: flex;
                    gap: 15px;
                    justify-content: center;
                    margin-bottom: 30px;
                    flex-wrap: wrap;
                }}
                
                .nav-btn {{ 
                    background: rgba(255,255,255,0.15);
                    color: white;
                    text-decoration: none;
                    padding: 10px 20px;
                    border-radius: 10px;
                    transition: all 0.3s;
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                }}
                
                .nav-btn:hover {{ 
                    background: rgba(255,255,255,0.25);
                    transform: translateY(-2px);
                }}
                
                .content {{ 
                    background: white;
                    border-radius: 20px;
                    padding: 30px;
                    box-shadow: 0 15px 35px rgba(0,0,0,0.2);
                    text-align: center;
                }}
                
                .status-badge {{
                    display: inline-block;
                    padding: 10px 20px;
                    border-radius: 30px;
                    font-size: 18px;
                    font-weight: bold;
                    margin: 20px 0;
                    animation: float 3s ease-in-out infinite;
                }}
                
                .status-badge.new {{ background: #e3f2fd; color: #1976d2; }}
                .status-badge.processing {{ background: #fff3e0; color: #f57c00; }}
                .status-badge.printing {{ background: #e8f5e8; color: #388e3c; }}
                .status-badge.ready {{ background: #e8e8f5; color: #5c6bc0; }}
                .status-badge.shipped {{ background: #f3e5f5; color: #8e24aa; }}
                .status-badge.delivered {{ background: #e8f0fe; color: #1e88e5; }}
                .status-badge.cancelled {{ background: #ffebee; color: #d32f2f; }}
                
                .status-buttons {{ 
                    display: flex;
                    gap: 5px;
                    flex-wrap: wrap;
                    justify-content: center;
                    margin: 20px 0;
                }}
                
                .status-btn {{ 
                    padding: 10px 15px;
                    border: none;
                    border-radius: 8px;
                    cursor: pointer;
                    transition: all 0.2s;
                    font-size: 14px;
                }}
                
                .status-btn:hover {{ 
                    transform: scale(1.1);
                    box-shadow: 0 3px 10px rgba(0,0,0,0.2);
                }}
                
                .status-btn.new {{ background: #e3f2fd; color: #1976d2; }}
                .status-btn.processing {{ background: #fff3e0; color: #f57c00; }}
                .status-btn.printing {{ background: #e8f5e8; color: #388e3c; }}
                .status-btn.ready {{ background: #e8e8f5; color: #5c6bc0; }}
                .status-btn.shipped {{ background: #f3e5f5; color: #8e24aa; }}
                .status-btn.delivered {{ background: #e8f0fe; color: #1e88e5; }}
                .status-btn.cancelled {{ background: #ffebee; color: #d32f2f; }}
                
                .photo-gallery {{ 
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(150px,1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                
                .photo-item {{
                    background: #f8f9fa;
                    border-radius: 10px;
                    padding: 10px;
                    text-align: center;
                }}
                
                .photo-img {{ 
                    max-width: 100%;
                    max-height: 150px;
                    border-radius: 10px;
                    cursor: pointer;
                    transition: all 0.3s;
                }}
                
                .photo-img:hover {{ 
                    transform: scale(1.05) rotate(2deg);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                }}
                
                .files-grid {{ 
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(200px,1fr));
                    gap: 15px;
                    margin: 20px 0;
                }}
                
                .file-card {{ 
                    background: #f8f9fa;
                    border-radius: 10px;
                    padding: 15px;
                    text-align: center;
                    text-decoration: none;
                    color: #333;
                    display: block;
                    transition: all 0.3s;
                }}
                
                .file-card:hover {{ 
                    background: #e9ecef;
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.1);
                }}
                
                .download-all {{ 
                    display: inline-block;
                    background: #28a745;
                    color: white;
                    text-decoration: none;
                    padding: 15px 30px;
                    border-radius: 10px;
                    margin-top: 20px;
                    transition: all 0.3s;
                }}
                
                .download-all:hover {{ 
                    background: #218838;
                    transform: translateY(-2px);
                    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                }}
                
                pre {{ 
                    background: #f8f9fa;
                    padding: 15px;
                    border-radius: 10px;
                    overflow-x: auto;
                    margin: 20px 0;
                    text-align: left;
                }}
                
                .order-link {{
                    display: inline-block;
                    background: #f0f0f0;
                    padding: 10px 20px;
                    border-radius: 30px;
                    color: #667eea;
                    text-decoration: none;
                    font-weight: bold;
                    margin: 10px 0;
                    transition: all 0.3s;
                }}
                
                .order-link:hover {{
                    background: #667eea;
                    color: white;
                    transform: scale(1.05);
                }}
                
                .floating-emoji {{
                    display: inline-block;
                    animation: float 2s ease-in-out infinite;
                }}
                
                h2, h3 {{ margin: 20px 0 10px; }}
            </style>
            <script>
                function updateStatus(status) {{
                    fetch('/orders/{order_id}/status', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{status: status}})
                    }}).then(r=>r.json()).then(d=>{{ 
                        if(d.success) {{
                            location.reload();
                        }} else {{
                            alert('❌ Ошибка обновления статуса');
                        }}
                    }});
                }}
            </script>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1><span class="floating-emoji">📁</span> Заказ: {order_id[:30]} <span class="floating-emoji">📁</span></h1>
                    <p>Создан: {created.strftime('%d.%m.%Y %H:%M')}</p>
                </div>
                
                <div class="nav-links">
                    <a href="/orders/" class="nav-btn"><span class="floating-emoji">←</span> К списку</a>
                    <a href="/" class="nav-btn"><span class="floating-emoji">🏠</span> Главная</a>
                </div>
                
                <div class="content">
                    <div class="status-badge {status}">
                        {get_status_display(status)}
                    </div>
                    
                    <div class="status-buttons">
                        <button class="status-btn new" onclick="updateStatus('new')" title="Новый">🆕</button>
                        <button class="status-btn processing" onclick="updateStatus('processing')" title="В обработке">🔄</button>
                        <button class="status-btn printing" onclick="updateStatus('printing')" title="В печати">🖨️</button>
                        <button class="status-btn ready" onclick="updateStatus('ready')" title="Готов">✅</button>
                        <button class="status-btn shipped" onclick="updateStatus('shipped')" title="Отправлен">📦</button>
                        <button class="status-btn delivered" onclick="updateStatus('delivered')" title="Доставлен">🏁</button>
                        <button class="status-btn cancelled" onclick="updateStatus('cancelled')" title="Отменен">❌</button>
                    </div>
                    
                    <a href="{RENDER_URL}/orders/{order_id}/" class="order-link" target="_blank">
                        <span class="floating-emoji">🔗</span> Постоянная ссылка на заказ
                    </a>
                    
                    <h3><span class="floating-emoji">📋</span> Информация</h3>
                    <pre>{info_text}</pre>
                    
                    <h3><span class="floating-emoji">📸</span> Фото ({len(photos)})</h3>
                    <div class="photo-gallery">
        """
        
        for p in photos:
            html += f'<div class="photo-item"><img src="{p["url"]}" class="photo-img" onclick="window.open(\'{p["url"]}\')"><br><small>{p["name"][:30]}</small></div>'
        
        html += f"""
                    </div>
                    
                    <h3><span class="floating-emoji">📄</span> Файлы ({len(files)})</h3>
                    <div class="files-grid">
        """
        
        for f in files:
            icon = "📸" if f["is_photo"] else "📄"
            html += f'<a href="{f["url"]}" class="file-card" download><div style="font-size:2em;margin-bottom:10px">{icon}</div><div>{f["name"][:30]}</div><div style="font-size:12px;color:#666">{f["size_formatted"]}</div></a>'
        
        html += f"""
                    </div>
                    
                    <a href="/orders/{order_id}/download" class="download-all">
                        <span class="floating-emoji">⬇️</span> Скачать все (ZIP)
                    </a>
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
    return jsonify({
        "status": "ok", 
        "orders_count": orders_count, 
        "active_sessions": len(user_sessions)
    })

@app.route('/')
def home():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Print Bot</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            @keyframes float {{
                0% {{ transform: translateY(0px); }}
                50% {{ transform: translateY(-10px); }}
                100% {{ transform: translateY(0px); }}
            }}
            
            @keyframes pulse {{
                0% {{ transform: scale(1); }}
                50% {{ transform: scale(1.05); }}
                100% {{ transform: scale(1); }}
            }}
            
            @keyframes gradient {{
                0% {{ background-position: 0% 50%; }}
                50% {{ background-position: 100% 50%; }}
                100% {{ background-position: 0% 50%; }}
            }}
            
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            
            body {{ 
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(-45deg, #667eea, #764ba2, #6b8cff, #a855f7);
                background-size: 400% 400%;
                animation: gradient 15s ease infinite;
                min-height: 100vh;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
            }}
            
            .container {{ max-width: 800px; width: 100%; }}
            
            .hero {{ 
                background: rgba(255,255,255,0.1);
                backdrop-filter: blur(10px);
                border-radius: 30px;
                padding: 40px;
                color: white;
                text-align: center;
                box-shadow: 0 20px 40px rgba(0,0,0,0.3);
            }}
            
            h1 {{ 
                font-size: 3.5em; 
                margin-bottom: 20px;
                animation: float 3s ease-in-out infinite;
            }}
            
            .stats {{ 
                display: grid;
                grid-template-columns: repeat(3,1fr);
                gap: 20px;
                margin: 40px 0;
            }}
            
            .stat-card {{ 
                background: rgba(255,255,255,0.15);
                border-radius: 20px;
                padding: 25px;
                transition: all 0.3s;
                animation: float 3s ease-in-out infinite;
                animation-delay: calc(var(--i) * 0.2s);
            }}
            
            .stat-card:hover {{ 
                transform: translateY(-10px) scale(1.05);
                background: rgba(255,255,255,0.25);
            }}
            
            .stat-value {{ 
                font-size: 2.5em; 
                font-weight: bold; 
                margin-bottom: 5px;
                animation: pulse 2s ease-in-out infinite;
            }}
            
            .nav-links {{ 
                display: flex;
                gap: 15px;
                justify-content: center;
                margin-top: 30px;
                flex-wrap: wrap;
            }}
            
            .nav-btn {{ 
                background: white;
                color: #667eea;
                text-decoration: none;
                padding: 15px 30px;
                border-radius: 15px;
                font-weight: bold;
                transition: all 0.3s;
                display: inline-flex;
                align-items: center;
                gap: 10px;
            }}
            
            .nav-btn:hover {{ 
                transform: translateY(-5px);
                box-shadow: 0 15px 30px rgba(0,0,0,0.3);
                background: #f0f0f0;
            }}
            
            .info {{ 
                margin-top: 30px;
                padding: 20px;
                background: rgba(0,0,0,0.2);
                border-radius: 15px;
            }}
            
            .info p {{ margin: 10px 0; }}
            
            .floating-emoji {{
                display: inline-block;
                animation: float 2s ease-in-out infinite;
            }}
            
            .pulse-emoji {{
                display: inline-block;
                animation: pulse 2s ease-in-out infinite;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="hero">
                <h1>
                    <span class="floating-emoji">🤖</span> 
                    Print Bot 
                    <span class="floating-emoji">🖨️</span>
                </h1>
                <p style="font-size:1.2em">Сервис для печати фото и документов через Telegram</p>
                
                <div class="stats">
                    <div class="stat-card" style="--i:0">
                        <div class="stat-value"><span class="pulse-emoji">📦</span> {orders_count}</div>
                        <div>заказов</div>
                    </div>
                    <div class="stat-card" style="--i:1">
                        <div class="stat-value"><span class="pulse-emoji">⏰</span> 24/7</div>
                        <div>работа</div>
                    </div>
                    <div class="stat-card" style="--i:2">
                        <div class="stat-value"><span class="pulse-emoji">⚡</span> 1-3</div>
                        <div>дня</div>
                    </div>
                </div>
                
                <div class="nav-links">
                    <a href="/orders/" class="nav-btn">
                        <span class="floating-emoji">📦</span> Заказы
                    </a>
                    <a href="/stats" class="nav-btn">
                        <span class="floating-emoji">📊</span> Статистика
                    </a>
                </div>
                
                <div class="info">
                    <p><span class="floating-emoji">📞</span> Контакт: {CONTACT_PHONE}</p>
                    <p><span class="floating-emoji">🚚</span> Доставка: {DELIVERY_OPTIONS}</p>
                    <p><span class="floating-emoji">⏰</span> Время: {current_time}</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """

print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📁 Папка для заказов: {ORDERS_PATH}")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")

bot = telegram.Bot(token=TOKEN)
updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

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
)

dispatcher.add_handler(conv_handler)

webhook_url = f"{RENDER_URL}/webhook"
updater.bot.set_webhook(url=webhook_url)

print(f"✅ Веб-хук: {webhook_url}")
print("✅ БОТ ГОТОВ К РАБОТЕ!")
print("=" * 60)

if __name__ == "__main__":
    print("🌐 Запуск Flask сервера...")
    app.run(host='0.0.0.0', port=PORT)
