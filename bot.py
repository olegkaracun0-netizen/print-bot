#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
ИСПРАВЛЕНО: ручной ввод количества, анимированные эмодзи в UI
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
                            "🔔 *Статус вашего заказа изменён!*\n"
                            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
                            f"🆔 Заказ: `{order_id}`\n"
                            f"📌 Новый статус: *{get_status_display(new_status)}*"
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
    "small":  {(1, 9): 35,  (10, 50): 28,  (51, 100): 23,  (101, float("inf")): 18},
    "medium": {(1, 9): 65,  (10, 50): 55,  (51, 100): 45,  (101, float("inf")): 35},
    "large":  {(1, 4): 200, (5, 20): 170,  (21, 50): 150,  (51, float("inf")): 120},
}

DOC_PRICES = {
    "bw":    {(1, 20): 25, (21, 100): 18, (101, 300): 14, (301, float("inf")): 10},
    "color": {(1, 20): 50, (21, 100): 35, (101, 300): 25, (301, float("inf")): 20},
}

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
                return len(pdf.pages), "страниц", "документ"
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
                f.write(file_obj.download_as_bytearray())
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
                shutil.copy2(f['path'], os.path.join(order_folder, f"{i}_{safe_name}"))

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
                f.write(f"Тип: Фото\nФормат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "Черно-белая", "color": "Цветная"}
                f.write(f"Тип: Документы\nПечать: {color_names[order_data['color']]}\n")

            f.write(f"Количество копий: {order_data['quantity']}\n\n")

            if photo_files:
                f.write(f"ФОТО:\n  • Файлов: {len(photo_files)}\n  • В оригинале: {total_photos}\n  • К печати: {total_photos * order_data['quantity']}\n\n")
            if doc_files:
                f.write(f"ДОКУМЕНТЫ:\n  • Файлов: {len(doc_files)}\n  • Страниц в оригинале: {total_pages}\n  • Страниц к печати: {total_pages * order_data['quantity']}\n\n")

            f.write(f"ИТОГО К ОПЛАТЕ: {order_data['total']} руб.\n")
            f.write(f"Срок выполнения: {order_data['delivery']}\n\nФАЙЛЫ:\n")
            for i, fi in enumerate(files_info, 1):
                icon = "📸" if fi['type'] == 'photo' else "📄"
                unit = "фото" if fi['type'] == 'photo' else "страниц"
                f.write(f"{icon} {i}. {fi['name']} — {fi['items']} {unit}\n")
            f.write(f"\nВсего файлов: {len(files_info)}")

        save_order_to_history({
            "order_id": order_id, "folder": order_folder,
            "user_id": user_id, "username": username,
            "user_name": order_data['user_info']['first_name'],
            "date": datetime.now().isoformat(),
            "type": order_data['type'], "quantity": order_data['quantity'],
            "total_photos": total_photos, "total_pages": total_pages,
            "total_price": order_data['total'], "delivery": order_data['delivery'],
            "status": "new"
        })

        return True, order_id, order_folder
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}\n{traceback.format_exc()}")
        return False, None, None

def send_admin_notification(order_data, order_id, order_folder):
    try:
        order_url = f"{RENDER_URL}/orders/{order_id}/"
        photo_files = [f for f in order_data['files'] if f['type'] == 'photo']
        doc_files = [f for f in order_data['files'] if f['type'] == 'doc']
        total_photos = sum(f['items'] for f in photo_files)
        total_pages = sum(f['items'] for f in doc_files)

        msg = (
            "🔔 *НОВЫЙ ЗАКАЗ!*\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 *{order_data['user_info']['first_name']}* (@{order_data['user_info']['username']})\n"
            f"🆔 ID: `{order_data['user_info']['user_id']}`\n\n"
        )
        if order_data['type'] == 'photo':
            fn = {"small": "10×15 / A6", "medium": "13×18 / 15×21", "large": "A4 / 21×30"}
            msg += f"🖼 Тип: *Фото* | Формат: *{fn[order_data['format']]}*\n"
        else:
            cn = {"bw": "⚫️ Чёрно-белая", "color": "🌈 Цветная"}
            msg += f"📄 Тип: *Документы* | Печать: *{cn[order_data['color']]}*\n"

        msg += f"📦 Копий: *{order_data['quantity']}* | Файлов: *{len(order_data['files'])}*\n"
        if photo_files:
            msg += f"📸 Фото: {len(photo_files)} файлов → *{total_photos} шт.*\n"
        if doc_files:
            msg += f"📄 Документы: {len(doc_files)} файлов → *{total_pages} стр.*\n"
        msg += (
            f"\n━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Сумма: {order_data['total']} руб.*\n"
            f"⏳ Срок: *{order_data['delivery']}*\n\n"
            f"🔗 [Открыть заказ]({order_url})"
        )

        if bot:
            bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"❌ Ошибка уведомления админу: {e}")

# ========== HANDLERS ==========

def start(update, context):
    user = update.effective_user
    user_id = user.id
    if user_id in user_sessions:
        for d in user_sessions[user_id].get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]

    update.message.reply_text(
        f"👋 *Привет, {user.first_name}!*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        "🖨️ Я твой личный помощник по печати!\n"
        "Фото, документы — всё напечатаю быстро и качественно ✨\n\n"
        "╔═══════════════════╗\n"
        "║  📎 КАК РАБОТАЕТ  ║\n"
        "╚═══════════════════╝\n\n"
        "1️⃣ Отправь файлы _(JPG, PNG, PDF, DOC, DOCX)_\n"
        "2️⃣ Выбери формат и количество копий\n"
        "3️⃣ Подтверди заказ — готово! 🎉\n\n"
        "📦 Можно кидать сразу несколько файлов!\n"
        "🧮 Я сам всё посчитаю и назову цену\n\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
        f"📞 *Телефон:* {CONTACT_PHONE}\n"
        f"🚚 *Доставка:* {DELIVERY_OPTIONS}\n\n"
        "⬇️ _Жду твои файлы!_",
        parse_mode="Markdown"
    )
    return WAITING_FOR_FILE

def _init_session(user_id, user):
    user_sessions[user_id] = {
        "files": [], "temp_dirs": [],
        "total_photos": 0, "total_pages": 0,
        "user_info": {
            "user_id": user_id,
            "username": user.username or user.first_name,
            "first_name": user.first_name,
            "last_name": user.last_name or ""
        }
    }

def handle_file(update, context):
    if update.message.media_group_id:
        return handle_media_group(update, context)
    return process_single_file(update, context)

def handle_media_group(update, context):
    user_id = update.effective_user.id
    message = update.message
    media_group_id = message.media_group_id

    media_groups.setdefault(user_id, {}).setdefault(media_group_id, []).append(message)

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
        group_timers.pop(timer_key, None)

        if user_id not in user_sessions:
            _init_session(user_id, messages[0].from_user)
        else:
            user_sessions[user_id].setdefault("total_photos", 0)
            user_sessions[user_id].setdefault("total_pages", 0)

        doc_count = photo_count = 0

        for message in messages:
            file_obj = file_name = file_type = None
            if message.document:
                file_obj = message.document
                file_name = file_obj.file_name
                ext = file_name.lower().split('.')[-1]
                if ext in ['jpg', 'jpeg', 'png']:
                    file_type = "photo"; photo_count += 1
                elif ext in ['pdf', 'doc', 'docx']:
                    file_type = "doc"; doc_count += 1
                else:
                    continue
            elif message.photo:
                file_obj = message.photo[-1]
                file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
                file_type = "photo"; photo_count += 1
            else:
                continue

            file_path, temp_dir = download_file(file_obj, file_name)
            if not file_path:
                continue

            items, unit, type_name = count_items_in_file(file_path, file_name)
            user_sessions[user_id]["files"].append({"path": file_path, "name": file_name, "type": file_type, "items": items, "unit": unit, "type_name": type_name})
            user_sessions[user_id]["temp_dirs"].append(temp_dir)

            if file_type == 'photo':
                user_sessions[user_id]["total_photos"] += items
            else:
                user_sessions[user_id]["total_pages"] += items

        if not user_sessions[user_id]["files"]:
            context.bot.send_message(chat_id=user_id, text="❌ Не удалось загрузить файлы")
            return

        _send_format_choice(user_id, context, photo_count, doc_count,
                            user_sessions[user_id]["total_photos"],
                            user_sessions[user_id]["total_pages"])
    except Exception as e:
        logger.error(f"Ошибка группы файлов: {e}\n{traceback.format_exc()}")

def process_single_file(update, context):
    user_id = update.effective_user.id
    message = update.message

    if user_id not in user_sessions:
        _init_session(user_id, update.effective_user)
    else:
        user_sessions[user_id].setdefault("total_photos", 0)
        user_sessions[user_id].setdefault("total_pages", 0)

    file_obj = file_name = file_type = None
    if message.document:
        file_obj = message.document
        file_name = file_obj.file_name
        ext = file_name.lower().split('.')[-1]
        if ext in ['jpg', 'jpeg', 'png']:
            file_type = "photo"
        elif ext in ['pdf', 'doc', 'docx']:
            file_type = "doc"
        else:
            message.reply_text("❌ Неподдерживаемый формат. Поддерживаются: JPG, PNG, PDF, DOC, DOCX")
            return WAITING_FOR_FILE
    elif message.photo:
        file_obj = message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE

    file_path, temp_dir = download_file(file_obj, file_name)
    if not file_path:
        message.reply_text("❌ Ошибка загрузки файла")
        return WAITING_FOR_FILE

    items, unit, type_name = count_items_in_file(file_path, file_name)
    user_sessions[user_id]["files"].append({"path": file_path, "name": file_name, "type": file_type, "items": items, "unit": unit, "type_name": type_name})
    user_sessions[user_id]["temp_dirs"].append(temp_dir)

    if file_type == 'photo':
        user_sessions[user_id]["total_photos"] += items
    else:
        user_sessions[user_id]["total_pages"] += items

    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')

    text = (
        "✅ *Файл принят!*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        "📂 *В очереди на печать:*\n"
    )
    if photo_count > 0:
        text += f"  📸 Фото-файлов: *{photo_count}*\n"
    if doc_count > 0:
        text += f"  📄 Документов: *{doc_count}*\n"
    if user_sessions[user_id]["total_photos"] > 0:
        text += f"  🖼 Всего фото: *{user_sessions[user_id]['total_photos']} шт.*\n"
    if user_sessions[user_id]["total_pages"] > 0:
        text += f"  📃 Всего страниц: *{user_sessions[user_id]['total_pages']} шт.*\n"

    text += "\n"
    keyboard = _build_format_keyboard(doc_count)
    if doc_count > 0:
        text += "🖨️ *Выберите тип печати:*"
    else:
        text += "🖼 *Выберите формат фото:*"
    message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return WAITING_FOR_FILE

def _send_format_choice(user_id, context, photo_count, doc_count, total_photos, total_pages):
    total_files = photo_count + doc_count
    text = (
        f"🎉 *Отлично! Загружено {total_files} {'файл' if total_files == 1 else 'файлов'}*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        "📂 *Состав заказа:*\n"
    )
    if photo_count > 0:
        text += f"  📸 Фото-файлов: *{photo_count}*\n"
    if doc_count > 0:
        text += f"  📄 Документов: *{doc_count}*\n"
    if total_photos > 0:
        text += f"  🖼 Всего фото: *{total_photos} шт.*\n"
    if total_pages > 0:
        text += f"  📃 Всего страниц: *{total_pages} шт.*\n"
    text += "\n"
    if doc_count > 0:
        text += "🖨️ *Выберите тип печати:*"
    else:
        text += "🖼 *Выберите формат фото:*"

    context.bot.send_message(
        chat_id=user_id,
        text=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(_build_format_keyboard(doc_count))
    )

def _build_format_keyboard(doc_count):
    if doc_count > 0:
        return [
            [InlineKeyboardButton("⚫️ Чёрно-белая печать", callback_data="doc_bw")],
            [InlineKeyboardButton("🌈 Цветная печать", callback_data="doc_color")],
            [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
            [InlineKeyboardButton("🗑 Отменить заказ", callback_data="cancel")]
        ]
    return [
        [InlineKeyboardButton("🔹 Малый — 10×15 / A6", callback_data="photo_small")],
        [InlineKeyboardButton("🔷 Средний — 13×18 / 15×21", callback_data="photo_medium")],
        [InlineKeyboardButton("🟦 Большой — A4 / 21×30", callback_data="photo_large")],
        [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
        [InlineKeyboardButton("🗑 Отменить заказ", callback_data="cancel")]
    ]

def cancel_order(user_id, query=None, context=None):
    if user_id in user_sessions:
        for d in user_sessions[user_id].get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]

    keyboard = [[InlineKeyboardButton("🔄 Начать новый заказ", callback_data="new_order")]]
    text = (
        "🗑 *Заказ отменён*\n"
        "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
        "Все загруженные файлы удалены 🧹\n\n"
        "Хотите оформить новый заказ? 👇"
    )

    if query:
        try:
            query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            if context:
                context.bot.send_message(chat_id=user_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif context:
        context.bot.send_message(chat_id=user_id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))

    return WAITING_FOR_FILE

def get_quantity_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣",  callback_data="qty_1"),
         InlineKeyboardButton("2️⃣",  callback_data="qty_2"),
         InlineKeyboardButton("3️⃣",  callback_data="qty_3"),
         InlineKeyboardButton("4️⃣",  callback_data="qty_4"),
         InlineKeyboardButton("5️⃣",  callback_data="qty_5")],
        [InlineKeyboardButton("🔟",  callback_data="qty_10"),
         InlineKeyboardButton("20",  callback_data="qty_20"),
         InlineKeyboardButton("30",  callback_data="qty_30"),
         InlineKeyboardButton("50",  callback_data="qty_50"),
         InlineKeyboardButton("💯",  callback_data="qty_100")],
        [InlineKeyboardButton("200", callback_data="qty_200"),
         InlineKeyboardButton("300", callback_data="qty_300"),
         InlineKeyboardButton("400", callback_data="qty_400"),
         InlineKeyboardButton("500", callback_data="qty_500")],
        [InlineKeyboardButton("✏️ Ввести своё число", callback_data="qty_custom_hint")],
        [InlineKeyboardButton("🗑 Отменить заказ",    callback_data="cancel")]
    ])

def _do_calculate(quantity, user_id):
    """
    Расчёт стоимости и формирование итогового текста.
    Возвращает (text, total_photos, total_pages, delivery) или None если нет сессии.
    """
    session = user_sessions.get(user_id)
    if not session:
        return None

    session["quantity"] = quantity
    files = session["files"]
    file_type = session["type"]

    total = 0
    total_photos_result = 0
    total_pages_result = 0

    text = f"🧾 *ДЕТАЛИ ЗАКАЗА:*\n┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"

    for i, f in enumerate(files, 1):
        if file_type == "photo":
            price_dict = PHOTO_PRICES[session["format"]]
            file_total = calculate_price(price_dict, quantity)
            total += file_total
            total_photos_result += f['items'] * quantity
            text += f"📸 *Файл {i}:* `{f['name'][:35]}`\n"
            text += f"   └ {f['items']} фото × {quantity} копий = *{f['items'] * quantity} шт.*\n"
            if quantity > 0:
                text += f"   └ Цена: {file_total // quantity} руб./копия → *{file_total} руб.*\n\n"
        else:
            price_dict = DOC_PRICES[session["color"]]
            file_items = f['items'] * quantity
            file_total = calculate_price(price_dict, file_items)
            total += file_total
            total_pages_result += file_items
            text += f"📄 *Файл {i}:* `{f['name'][:35]}`\n"
            text += f"   └ {f['items']} стр. × {quantity} копий = *{file_items} стр.*\n"
            if file_items > 0:
                text += f"   └ Цена: {file_total // file_items} руб./стр. → *{file_total} руб.*\n\n"

    delivery = estimate_delivery_time(total_photos_result + total_pages_result)
    session["total"] = total
    session["total_photos"] = total_photos_result
    session["total_pages"] = total_pages_result
    session["delivery"] = delivery

    text += "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    text += f"📦 Файлов: *{len(files)}*\n"
    if total_photos_result > 0:
        text += f"📸 Фото к печати: *{total_photos_result} шт.*\n"
    if total_pages_result > 0:
        text += f"📃 Страниц к печати: *{total_pages_result} шт.*\n"
    text += f"⏳ Срок: *{delivery}*\n"
    text += f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
    text += f"💰 *ИТОГО: {total} руб.*\n\n"
    text += "☝️ _Всё верно? Подтверждаем?_"

    return text

# ========== ИСПРАВЛЕНА: ручной ввод количества ==========
def handle_quantity_input(update, context):
    """
    ИСПРАВЛЕНО: убран фиктивный query-объект.
    Теперь напрямую вызывает _do_calculate и отправляет результат.
    """
    user_id = update.effective_user.id
    quantity = extract_number_from_text(update.message.text)

    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            "⚠️ *Упс!* Введите число от *1 до 1000*\n\n"
            "Или просто нажмите кнопку ниже 👇",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY

    session = user_sessions.get(user_id)
    if not session:
        return cancel_order(user_id, context=context)

    text = _do_calculate(quantity, user_id)
    if text is None:
        return cancel_order(user_id, context=context)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, оформить заказ!", callback_data="confirm")],
        [InlineKeyboardButton("🗑 Нет, отменить",       callback_data="cancel")]
    ])
    context.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard, parse_mode="Markdown")
    return CONFIRMING_ORDER

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
            "📎 *Отправьте следующие файлы*\n\n"
            "_JPG, PNG, PDF, DOC, DOCX — всё принимается!_",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE

    if data == "qty_custom_hint":
        query.answer("Просто напишите число в чат! Например: 7", show_alert=True)
        return ENTERING_QUANTITY

    if data == "new_order":
        if user_id in user_sessions:
            for d in user_sessions[user_id].get("temp_dirs", []):
                shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]
        query.edit_message_text(
            "🔄 *НОВЫЙ ЗАКАЗ*\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
            "📎 Отправьте файлы для печати\n"
            "_JPG, PNG, PDF, DOC, DOCX_",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE

    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        fmt_names = {"small": "10×15 / A6", "medium": "13×18 / 15×21", "large": "A4 / 21×30"}
        fmt = user_sessions[user_id]["format"]
        query.edit_message_text(
            f"🖼 *Формат: {fmt_names.get(fmt, fmt)}* — отличный выбор!\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
            "🔢 *Сколько копий напечатать?*\n\n"
            "👆 Нажми кнопку или *напиши число* прямо в чат:",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY

    if data.startswith("doc_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = data.split("_")[1]
        total_photos = user_sessions[user_id].get("total_photos", 0)
        total_pages = user_sessions[user_id].get("total_pages", 0)
        color_name = "⚫️ Чёрно-белая" if data.split("_")[1] == "bw" else "🌈 Цветная"
        query.edit_message_text(
            f"🖨️ *Печать: {color_name}* — принято!\n"
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
            f"📊 В заказе: 📸 {total_photos} фото + 📄 {total_pages} стр. = *{total_photos + total_pages} ед.*\n\n"
            "🔢 *Сколько копий напечатать?*\n\n"
            "👆 Нажми кнопку или *напиши число* прямо в чат:",
            parse_mode="Markdown",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY

    if data.startswith("qty_"):
        quantity = int(data.split("_")[1])
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)

        text = _do_calculate(quantity, user_id)
        if text is None:
            return cancel_order(user_id, query, context)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, оформить заказ!", callback_data="confirm")],
            [InlineKeyboardButton("🗑 Нет, отменить",       callback_data="cancel")]
        ])

        try:
            query.message.delete()
        except Exception:
            pass
        context.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard, parse_mode="Markdown")
        return CONFIRMING_ORDER

    if data == "confirm":
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)

        success, order_id, folder = save_order_to_folder(
            user_id, session['user_info']['username'], session, session['files']
        )

        if success:
            send_admin_notification(session, order_id, folder)

            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            doc_files = [f for f in session['files'] if f['type'] == 'doc']
            total_photos = sum(f['items'] for f in photo_files)
            total_pages = sum(f['items'] for f in doc_files)

            client_msg = (
                "🎉 *ЗАКАЗ ОФОРМЛЕН!*\n"
                "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
                f"🆔 Номер: `{order_id}`\n"
                f"👤 Заказчик: *{session['user_info']['first_name']}*\n"
                f"📦 Файлов в заказе: *{len(session['files'])}*\n"
            )
            if total_photos > 0:
                client_msg += f"📸 Фото к печати: *{total_photos * session['quantity']} шт.*\n"
            if total_pages > 0:
                client_msg += f"📃 Страниц к печати: *{total_pages * session['quantity']} шт.*\n"
            client_msg += (
                f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n"
                f"💰 *К оплате: {session['total']} руб.*\n"
                f"⏳ Срок выполнения: *{session['delivery']}*\n"
                f"┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄\n\n"
                f"📞 *Телефон:* {CONTACT_PHONE}\n"
                f"🚚 *Доставка:* {DELIVERY_OPTIONS}\n\n"
                f"📌 *Статус:* {get_status_display('new')}\n"
                "🔔 Пришлю уведомление при каждом изменении статуса\n\n"
                "Спасибо за заказ! 🙏✨"
            )
            context.bot.send_message(chat_id=user_id, text=client_msg, parse_mode="Markdown")

            if photo_files:
                try:
                    media_group = []
                    for i, pf in enumerate(photo_files[:5]):
                        with open(pf['path'], 'rb') as ph:
                            data_bytes = ph.read()
                        if i == 0:
                            media_group.append(InputMediaPhoto(data_bytes, caption=f"📸 Ваши фото ({len(photo_files)} шт.) — всё получили!"))
                        else:
                            media_group.append(InputMediaPhoto(data_bytes))
                    if media_group:
                        context.bot.send_media_group(chat_id=user_id, media=media_group)
                except Exception as e:
                    logger.error(f"Ошибка предпросмотра: {e}")
        else:
            context.bot.send_message(
                chat_id=user_id,
                text="😔 *Что-то пошло не так при сохранении заказа*\n\nПожалуйста, попробуйте ещё раз или свяжитесь с нами: " + CONTACT_PHONE,
                parse_mode="Markdown"
            )

        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]

        keyboard = [[InlineKeyboardButton("🔄 Оформить ещё один заказ", callback_data="new_order")]]
        try:
            query.message.delete()
        except Exception:
            pass
        context.bot.send_message(
            chat_id=user_id,
            text="Хотите напечатать что-то ещё? 👇",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_FOR_FILE

    return WAITING_FOR_FILE

# ========== ВЕБ-ИНТЕРФЕЙС ==========
app = Flask(__name__)

ANIMATED_STYLE = """
<style>
  @keyframes float  {0%,100%{transform:translateY(0)}  50%{transform:translateY(-8px)}}
  @keyframes pulse  {0%,100%{transform:scale(1)}       50%{transform:scale(1.15)}}
  @keyframes spin   {from{transform:rotate(0deg)}      to{transform:rotate(360deg)}}
  @keyframes bounce {0%,100%{transform:translateY(0)}  30%{transform:translateY(-12px)} 60%{transform:translateY(-5px)}}
  @keyframes glow   {0%,100%{text-shadow:0 0 5px rgba(255,255,255,.3)} 50%{text-shadow:0 0 20px rgba(255,255,255,.9),0 0 40px rgba(255,200,100,.6)}}

  .emoji-float  {display:inline-block;animation:float  2.5s ease-in-out infinite}
  .emoji-pulse  {display:inline-block;animation:pulse  1.8s ease-in-out infinite}
  .emoji-spin   {display:inline-block;animation:spin     3s linear      infinite}
  .emoji-bounce {display:inline-block;animation:bounce 1.5s ease        infinite}
  .emoji-glow   {display:inline-block;animation:glow     2s ease-in-out infinite}

  *{box-sizing:border-box}
  body{font-family:'Segoe UI',Arial,sans-serif;
       background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
       min-height:100vh;padding:20px;margin:0}
  .container{max-width:1400px;margin:0 auto}
  .header{background:rgba(255,255,255,.12);backdrop-filter:blur(14px);
          border-radius:24px;padding:30px;margin-bottom:30px;color:#fff;
          border:1px solid rgba(255,255,255,.2)}
  .header h1{margin:0 0 8px;font-size:2.2em}
  .nav-links{display:flex;gap:12px;margin-bottom:28px;flex-wrap:wrap}
  .nav-btn{background:rgba(255,255,255,.18);color:#fff;text-decoration:none;
           padding:10px 22px;border-radius:12px;font-weight:600;transition:background .2s}
  .nav-btn:hover{background:rgba(255,255,255,.32)}
  .orders-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:24px}
  .order-card{background:#fff;border-radius:20px;overflow:hidden;
              box-shadow:0 8px 32px rgba(0,0,0,.12);transition:transform .2s,box-shadow .2s}
  .order-card:hover{transform:translateY(-4px);box-shadow:0 16px 48px rgba(0,0,0,.2)}
  .order-header{background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;padding:18px 22px}
  .order-header h2{margin:0 0 4px;font-size:1.05em;word-break:break-all}
  .order-content{padding:18px}
  .status-buttons{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px}
  .status-btn{padding:5px 10px;border:none;border-radius:8px;cursor:pointer;
              font-size:.8em;font-weight:600;transition:filter .15s}
  .status-btn:hover{filter:brightness(1.1)}
  .status-btn.new       {background:#dbeafe;color:#1d4ed8}
  .status-btn.processing{background:#fef3c7;color:#92400e}
  .status-btn.printing  {background:#dcfce7;color:#166534}
  .status-btn.ready     {background:#ede9fe;color:#5b21b6}
  .status-btn.shipped   {background:#fce7f3;color:#9d174d}
  .status-btn.delivered {background:#d1fae5;color:#065f46}
  .status-btn.cancelled {background:#fee2e2;color:#991b1b}
  .photo-gallery{display:flex;gap:8px;overflow-x:auto;padding:6px 0}
  .photo-preview{width:76px;height:76px;object-fit:cover;border-radius:10px;
                 cursor:pointer;transition:transform .2s;flex-shrink:0}
  .photo-preview:hover{transform:scale(1.08)}
  .action-btn{display:inline-block;padding:8px 16px;color:#fff;text-decoration:none;
              border-radius:10px;margin-top:10px;margin-right:6px;font-weight:600;font-size:.88em}
  .btn-view{background:linear-gradient(135deg,#667eea,#764ba2)}
  .btn-dl{background:linear-gradient(135deg,#11998e,#38ef7d)}
  .stats-bar{display:flex;gap:10px;margin-top:10px;flex-wrap:wrap}
  .stat{background:#f3f4f6;border-radius:8px;padding:5px 12px;font-size:.8em;color:#374151}
</style>
"""

@app.route('/orders/')
def list_orders():
    try:
        orders = []
        if os.path.exists(ORDERS_PATH):
            history_map = {h.get('order_id'): h.get('status', 'new') for h in load_orders_history()}
            for item in sorted(os.listdir(ORDERS_PATH), reverse=True):
                item_path = os.path.join(ORDERS_PATH, item)
                if not os.path.isdir(item_path):
                    continue

                files = []
                photos = []
                total_size = 0
                for fname in os.listdir(item_path):
                    if fname == "информация_о_заказе.txt":
                        continue
                    fp = os.path.join(item_path, fname)
                    fsize = os.path.getsize(fp)
                    total_size += fsize
                    is_photo = fname.lower().endswith(('.jpg', '.jpeg', '.png'))
                    fi = {'name': fname, 'size_formatted': format_file_size(fsize),
                          'url': f'/orders/{item}/{fname}', 'is_photo': is_photo}
                    files.append(fi)
                    if is_photo:
                        photos.append(fi)

                created = datetime.fromtimestamp(os.path.getctime(item_path))
                status = history_map.get(item, 'new')
                orders.append({
                    'id': item, 'photos': photos[:5], 'file_count': len(files),
                    'total_size': format_file_size(total_size),
                    'created': created.strftime('%d.%m.%Y %H:%M'),
                    'age_days': (datetime.now() - created).days,
                    'status': get_status_display(status)
                })

        orders.sort(key=lambda x: x['created'], reverse=True)

        html = """<!DOCTYPE html><html><head>
        <title>Заказы — Print Bot</title><meta charset="utf-8">
        """ + ANIMATED_STYLE + """
        <script>
        function updateStatus(id,s){
            fetch('/orders/'+id+'/status',{method:'POST',
                headers:{'Content-Type':'application/json'},
                body:JSON.stringify({status:s})
            }).then(r=>r.json()).then(d=>{if(d.success)location.reload();else alert('Ошибка');});
        }
        </script></head><body>
        <div class="container">
          <div class="header">
            <h1><span class="emoji-bounce">📦</span> Заказы на печать</h1>
            <p>Всего: <b>{{ orders|length }}</b></p>
          </div>
          <div class="nav-links">
            <a href="/" class="nav-btn"><span class="emoji-float">🏠</span> Главная</a>
            <a href="/stats" class="nav-btn"><span class="emoji-pulse">📊</span> Статистика</a>
          </div>
          <div class="orders-grid">
          {% for o in orders %}
          <div class="order-card">
            <div class="order-header">
              <h2>{{ o.id }}</h2>
              <div style="font-size:.85em;opacity:.85;">{{ o.status }} &bull; {{ o.created }}</div>
            </div>
            <div class="order-content">
              <div class="status-buttons">
                <button class="status-btn new"        onclick="updateStatus('{{ o.id }}','new')">🆕 Новый</button>
                <button class="status-btn processing" onclick="updateStatus('{{ o.id }}','processing')">🔄</button>
                <button class="status-btn printing"   onclick="updateStatus('{{ o.id }}','printing')">🖨️</button>
                <button class="status-btn ready"      onclick="updateStatus('{{ o.id }}','ready')">✅ Готов</button>
                <button class="status-btn shipped"    onclick="updateStatus('{{ o.id }}','shipped')">📦</button>
                <button class="status-btn delivered"  onclick="updateStatus('{{ o.id }}','delivered')">🏁</button>
                <button class="status-btn cancelled"  onclick="updateStatus('{{ o.id }}','cancelled')">❌</button>
              </div>
              {% if o.photos %}
              <div class="photo-gallery">
                {% for p in o.photos %}
                <img src="{{ p.url }}" class="photo-preview" onclick="window.open('{{ p.url }}')">
                {% endfor %}
              </div>
              {% endif %}
              <div class="stats-bar">
                <span class="stat">📁 {{ o.file_count }} файлов</span>
                <span class="stat">💾 {{ o.total_size }}</span>
                <span class="stat">📅 {{ o.age_days }} дн.</span>
              </div>
              <a href="/orders/{{ o.id }}/" class="action-btn btn-view">👁️ Подробнее</a>
              <a href="/orders/{{ o.id }}/download" class="action-btn btn-dl">⬇️ ZIP</a>
            </div>
          </div>
          {% endfor %}
          </div>
        </div></body></html>"""
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
        for h in load_orders_history():
            if h.get('order_id') == order_id:
                status = h.get('status', 'new')
                break

        files = []
        photos = []
        total_size = 0
        for fname in sorted(os.listdir(order_path)):
            if fname == "информация_о_заказе.txt":
                continue
            fp = os.path.join(order_path, fname)
            fsize = os.path.getsize(fp)
            total_size += fsize
            is_photo = fname.lower().endswith(('.jpg', '.jpeg', '.png'))
            fi = {'name': fname, 'size_formatted': format_file_size(fsize),
                  'url': f'/orders/{order_id}/{fname}', 'is_photo': is_photo}
            files.append(fi)
            if is_photo:
                photos.append(fi)

        created = datetime.fromtimestamp(os.path.getctime(order_path))

        photo_html = "".join(
            f'<div class="ph-item"><img src="{p["url"]}" class="ph-img" onclick="window.open(\'{p["url"]}\')">'
            f'<div class="ph-lbl">{p["name"]}</div></div>'
            for p in photos
        )
        file_html = "".join(
            f'<a href="{f["url"]}" class="file-card" download>'
            f'<div class="file-icon">{"📸" if f["is_photo"] else "📄"}</div>'
            f'<div class="file-name">{f["name"]}</div>'
            f'<div class="file-sz">{f["size_formatted"]}</div></a>'
            for f in files
        )

        return f"""<!DOCTYPE html><html><head>
        <title>Заказ {order_id}</title><meta charset="utf-8">
        {ANIMATED_STYLE}
        <style>
          .content{{background:#fff;border-radius:20px;padding:30px}}
          .sec{{margin-bottom:28px}}
          .sec h3{{color:#374151;margin-bottom:12px}}
          pre{{background:#f9fafb;border-radius:12px;padding:16px;font-size:.88em;
               overflow-x:auto;white-space:pre-wrap;line-height:1.6}}
          .ph-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px}}
          .ph-item{{background:#f3f4f6;border-radius:12px;padding:10px;text-align:center}}
          .ph-img{{max-width:100%;max-height:120px;border-radius:8px;cursor:pointer;transition:transform .2s}}
          .ph-img:hover{{transform:scale(1.06)}}
          .ph-lbl{{font-size:.72em;color:#6b7280;margin-top:5px;word-break:break-all}}
          .files-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}}
          .file-card{{background:#f3f4f6;border-radius:12px;padding:14px;text-align:center;
                     text-decoration:none;color:#374151;display:block;transition:background .15s}}
          .file-card:hover{{background:#e5e7eb}}
          .file-icon{{font-size:2em;margin-bottom:6px}}
          .file-name{{font-size:.82em;word-break:break-all;margin-bottom:3px}}
          .file-sz{{font-size:.72em;color:#9ca3af}}
          .dl-all{{display:inline-block;background:linear-gradient(135deg,#11998e,#38ef7d);
                  color:#fff;text-decoration:none;padding:14px 30px;
                  border-radius:12px;font-weight:700;font-size:1em}}
        </style>
        <script>
        function updateStatus(s){{
            fetch('/orders/{order_id}/status',{{method:'POST',
                headers:{{'Content-Type':'application/json'}},
                body:JSON.stringify({{status:s}})
            }}).then(r=>r.json()).then(d=>{{if(d.success)location.reload();else alert('Ошибка');}});
        }}
        </script></head><body>
        <div class="container">
          <div class="header">
            <h1><span class="emoji-float">📁</span> Заказ: {order_id}</h1>
            <p>Создан: {created.strftime('%d.%m.%Y %H:%M')} &bull; {len(files)} файлов &bull; {format_file_size(total_size)}</p>
          </div>
          <div class="nav-links">
            <a href="/orders/" class="nav-btn">← К списку</a>
            <a href="/" class="nav-btn"><span class="emoji-float">🏠</span> Главная</a>
          </div>
          <div class="content">
            <div class="sec">
              <h3><span class="emoji-pulse">📌</span> Статус: {get_status_display(status)}</h3>
              <div style="display:flex;flex-wrap:wrap;gap:7px;">
                <button class="status-btn new"        onclick="updateStatus('new')">🆕 Новый</button>
                <button class="status-btn processing" onclick="updateStatus('processing')">🔄 В обработке</button>
                <button class="status-btn printing"   onclick="updateStatus('printing')">🖨️ В печати</button>
                <button class="status-btn ready"      onclick="updateStatus('ready')">✅ Готов</button>
                <button class="status-btn shipped"    onclick="updateStatus('shipped')">📦 Отправлен</button>
                <button class="status-btn delivered"  onclick="updateStatus('delivered')">🏁 Доставлен</button>
                <button class="status-btn cancelled"  onclick="updateStatus('cancelled')">❌ Отменён</button>
              </div>
            </div>
            <div class="sec">
              <h3>📋 Информация о заказе</h3>
              <pre>{info_text}</pre>
            </div>
            <div class="sec">
              <h3><span class="emoji-bounce">📸</span> Фото ({len(photos)})</h3>
              <div class="ph-grid">{photo_html}</div>
            </div>
            <div class="sec">
              <h3>📄 Все файлы ({len(files)})</h3>
              <div class="files-grid">{file_html}</div>
            </div>
            <div style="text-align:center;margin-top:20px;">
              <a href="/orders/{order_id}/download" class="dl-all">
                <span class="emoji-bounce">⬇️</span> Скачать всё (ZIP)
              </a>
            </div>
          </div>
        </div></body></html>"""
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
        return jsonify({"success": update_order_status(order_id, new_status)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/orders/<path:order_id>/download')
def download_all_files(order_id):
    try:
        order_path = os.path.join(ORDERS_PATH, order_id)
        if not os.path.exists(order_path):
            return "Заказ не найден", 404
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        with zipfile.ZipFile(tmp.name, 'w') as zipf:
            for root, dirs, files in os.walk(order_path):
                for file in files:
                    fp = os.path.join(root, file)
                    zipf.write(fp, os.path.relpath(fp, order_path))
        return send_file(tmp.name, as_attachment=True, download_name=f"{order_id}.zip")
    except Exception as e:
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_id>/<filename>')
def download_order_file(order_id, filename):
    try:
        return send_from_directory(os.path.join(ORDERS_PATH, order_id), filename, as_attachment=True)
    except Exception as e:
        return f"Ошибка: {e}", 500

@app.route('/webhook', methods=['POST'])
def webhook():
    global dispatcher
    try:
        if dispatcher is None:
            return jsonify({"error": "Dispatcher not initialized"}), 500
        update_data = request.get_json()
        if update_data:
            update = telegram.Update.de_json(update_data, bot)
            dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка вебхука: {e}")
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
    return f"""<!DOCTYPE html><html><head>
    <title>Print Bot</title><meta charset="utf-8">
    {ANIMATED_STYLE}
    <style>
      body{{display:flex;align-items:center;justify-content:center}}
      .hero{{background:rgba(255,255,255,.12);backdrop-filter:blur(14px);border-radius:30px;
             padding:50px 40px;color:#fff;text-align:center;
             border:1px solid rgba(255,255,255,.2);max-width:700px;width:100%}}
      h1{{font-size:3.2em;margin-bottom:16px}}
      .stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:30px 0}}
      .stat-card{{background:rgba(255,255,255,.15);border-radius:16px;padding:22px}}
      .stat-value{{font-size:2.4em;font-weight:700;margin-bottom:4px}}
      .nav-links2{{display:flex;gap:16px;justify-content:center;margin-top:32px;flex-wrap:wrap}}
      .nav-btn2{{background:#fff;color:#667eea;text-decoration:none;
                padding:14px 28px;border-radius:12px;font-weight:700;
                font-size:1.02em;transition:transform .2s,box-shadow .2s}}
      .nav-btn2:hover{{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.2)}}
      .info{{margin-top:28px;padding:18px;background:rgba(0,0,0,.2);border-radius:14px;
             font-size:.95em;line-height:2}}
    </style></head><body>
    <div class="hero">
      <h1><span class="emoji-glow">🤖</span> Print Bot</h1>
      <p style="font-size:1.1em;opacity:.9;">Сервис печати фото и документов через Telegram</p>
      <div class="stats">
        <div class="stat-card">
          <div class="stat-value"><span class="emoji-bounce">📦</span></div>
          <div style="font-size:1.8em;font-weight:700">{orders_count}</div>
          <div>заказов</div>
        </div>
        <div class="stat-card">
          <div class="stat-value"><span class="emoji-spin">⚙️</span></div>
          <div style="font-size:1.8em;font-weight:700">24/7</div>
          <div>работа</div>
        </div>
        <div class="stat-card">
          <div class="stat-value"><span class="emoji-float">🚀</span></div>
          <div style="font-size:1.8em;font-weight:700">1–3</div>
          <div>дня</div>
        </div>
      </div>
      <div class="nav-links2">
        <a href="/orders/" class="nav-btn2">📦 Все заказы</a>
        <a href="/stats" class="nav-btn2">📊 Статистика</a>
      </div>
      <div class="info">
        <div><span class="emoji-pulse">📞</span> {CONTACT_PHONE}</div>
        <div><span class="emoji-float">🚚</span> {DELIVERY_OPTIONS}</div>
        <div><span class="emoji-spin">⏰</span> {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</div>
      </div>
    </div></body></html>"""

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print(f"📁 Папка заказов: {ORDERS_PATH}")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")
print("=" * 60)

bot = telegram.Bot(token=TOKEN)
updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

conv_handler = ConversationHandler(
    entry_points=[
        CommandHandler("start", start),
        MessageHandler(Filters.document | Filters.photo, handle_file),
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
            # ИСПРАВЛЕНО: текстовый ввод обрабатывается ПЕРВЫМ
            MessageHandler(Filters.text & ~Filters.command, handle_quantity_input),
            CallbackQueryHandler(button_handler, pattern=r"^qty_\d+$"),
            CallbackQueryHandler(button_handler, pattern="^qty_custom_hint$"),
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

webhook_url = f"{RENDER_URL}/webhook"
updater.bot.set_webhook(url=webhook_url)

print(f"✅ Веб-хук: {webhook_url}")
print("✅ БОТ ГОТОВ К РАБОТЕ!")
print("=" * 60)

if __name__ == "__main__":
    print("🌐 Запуск Flask сервера...")
    app.run(host='0.0.0.0', port=PORT)
