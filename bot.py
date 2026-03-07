#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
Ссылки на заказы только для админа
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
from flask import Flask, request, jsonify, send_file, send_from_directory

# Используем синхронную версию python-telegram-bot
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, Filters

import PyPDF2
from docx import Document

# ========== НАСТРОЙКИ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: TOKEN не задан в переменных окружения!")
    sys.exit(1)

# ID администратора для уведомлений (ваш Telegram ID)
ADMIN_CHAT_ID = 483613049  # Ваш ID из логов

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

# Создаем папку для заказов
try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"📁 Папка заказов: {ORDERS_PATH}")
except Exception as e:
    print(f"❌ Ошибка создания папки: {e}")
    sys.exit(1)

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

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def calculate_price(price_dict, quantity):
    """Рассчитывает стоимость по количеству"""
    for (min_q, max_q), price in price_dict.items():
        if min_q <= quantity <= max_q:
            return price * quantity
    return 0

def estimate_delivery_time(total_items):
    """Расчет срока доставки"""
    if total_items <= 50:
        return "1 день"
    elif total_items <= 200:
        return "2 дня"
    else:
        return "3 дня"

def extract_number_from_text(text):
    """Извлекает число из текста"""
    numbers = re.findall(r'\d+', text)
    return int(numbers[0]) if numbers else None

def count_items_in_file(file_path, file_name):
    """Подсчет количества единиц в файле (для фото = 1, для документов = страницы)"""
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                page_count = len(pdf.pages)
                logger.info(f"📄 PDF: {file_name} - {page_count} стр.")
                return page_count, "страниц"
                
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            paragraphs = len(doc.paragraphs)
            estimated_pages = max(1, paragraphs // 35)
            
            tables_count = len(doc.tables)
            if tables_count > 0:
                estimated_pages += tables_count // 2
            
            logger.info(f"📄 Word: {file_name} - {estimated_pages} стр.")
            return estimated_pages, "страниц"
            
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png')):
            # Для фото всегда 1 фото
            logger.info(f"📸 Фото: {file_name} - 1 фото")
            return 1, "фото"
            
        return 1, "единиц"
    except Exception as e:
        logger.error(f"Ошибка подсчета: {e}")
        return 1, "единиц"

def download_file(file_obj, file_name):
    """Скачивает файл во временную папку"""
    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file_name)
        
        # Универсальный метод скачивания
        if hasattr(file_obj, 'get_file'):  # Для PhotoSize
            file = file_obj.get_file()
            file.download(custom_path=file_path)
        elif hasattr(file_obj, 'download'):  # Для Document
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
    """Сохраняет заказ в папку на сервере"""
    try:
        # Создаем уникальную папку для заказа
        clean_name = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_folder = os.path.join(ORDERS_PATH, f"{clean_name}_{timestamp}")
        os.makedirs(order_folder, exist_ok=True)
        logger.info(f"📁 Создана папка заказа: {order_folder}")
        
        saved_files = []
        
        for i, f in enumerate(files_info, 1):
            if os.path.exists(f['path']):
                # Очищаем имя файла от недопустимых символов
                safe_name = re.sub(r'[<>:"/\\|?*]', '', f['name'])
                new_path = os.path.join(order_folder, f"{i}_{safe_name}")
                shutil.copy2(f['path'], new_path)
                saved_files.append(new_path)
                logger.info(f"📄 Файл {i} скопирован: {new_path}")
            else:
                logger.error(f"❌ Файл не найден: {f['path']}")
        
        # Сохраняем информацию о заказе
        info_file = os.path.join(order_folder, "информация_о_заказе.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write(f"ЗАКАЗ ОТ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")
            f.write(f"Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"ID: {user_id}\n")
            f.write(f"Телефон: {CONTACT_PHONE}\n\n")
            
            if order_data['type'] == 'photo':
                format_names = {"small": "Малый (A6/10x15)", "medium": "Средний (13x18/15x21)", "large": "Большой (A4/21x30)"}
                f.write(f"Тип: Фото\n")
                f.write(f"Формат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "Черно-белая", "color": "Цветная"}
                f.write(f"Тип: Документы\n")
                f.write(f"Печать: {color_names[order_data['color']]}\n")
            
            f.write(f"Количество копий: {order_data['quantity']}\n")
            f.write(f"Всего единиц в оригинале: {order_data['total_items']}\n")
            f.write(f"Всего единиц к печати: {order_data['total_items'] * order_data['quantity']}\n")
            f.write(f"Сумма к оплате: {order_data['total']} руб.\n")
            f.write(f"Срок выполнения: {order_data['delivery']}\n\n")
            
            f.write("ФАЙЛЫ:\n")
            for i, file_info in enumerate(files_info, 1):
                icon = "📸" if file_info['type'] == 'photo' else "📄"
                f.write(f"{icon} {i}. {file_info['name']}\n")
                f.write(f"   • Тип: {'Фото' if file_info['type'] == 'photo' else 'Документ'}\n")
                f.write(f"   • Количество: {file_info['items']} {file_info['unit']}\n")
            
            f.write(f"\nВсего файлов: {len(files_info)}")
        
        logger.info(f"📝 Информация о заказе сохранена в {info_file}")
        return True, order_folder
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        logger.error(traceback.format_exc())
        return False, None

def send_admin_notification(order_data, order_folder):
    """Отправляет уведомление админу о новом заказе"""
    try:
        order_name = os.path.basename(order_folder)
        # ВАЖНО: добавляем слеш в конце URL
        order_url = f"{RENDER_URL}/orders/{order_name}/"
        
        # Формируем сообщение для админа
        admin_message = (
            f"🆕 НОВЫЙ ЗАКАЗ!\n\n"
            f"👤 Клиент: {order_data['user_info']['first_name']} (@{order_data['user_info']['username']})\n"
            f"🆔 ID: {order_data['user_info']['user_id']}\n\n"
            f"📦 Детали заказа:\n"
            f"• Тип: {'Фото' if order_data['type'] == 'photo' else 'Документы'}\n"
        )
        
        if order_data['type'] == 'photo':
            format_names = {"small": "Малый (A6)", "medium": "Средний", "large": "Большой (A4)"}
            admin_message += f"• Формат: {format_names[order_data['format']]}\n"
        else:
            color_names = {"bw": "Черно-белая", "color": "Цветная"}
            admin_message += f"• Печать: {color_names[order_data['color']]}\n"
        
        admin_message += (
            f"• Копий: {order_data['quantity']}\n"
            f"• Файлов: {len(order_data['files'])}\n"
            f"• Всего единиц в оригинале: {order_data['total_items']}\n"
            f"💰 Сумма: {order_data['total']} руб.\n"
            f"⏳ Срок: {order_data['delivery']}\n\n"
            f"🔗 Ссылка для скачивания:\n{order_url}\n\n"
            f"📁 Папка на сервере:\n{order_folder}"
        )
        
        # Отправляем админу
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message
            )
            logger.info(f"✅ Уведомление отправлено админу {ADMIN_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

# ========== ОБРАБОТЧИКИ КОМАНД ==========
def start(update, context):
    """Команда /start"""
    user = update.effective_user
    user_id = user.id
    logger.info(f"✅ /start от {user_id}")
    
    # Очищаем старую сессию
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    welcome = (
        f"👋 Привет, {user.first_name}!\n\n"
        "📸🖨️ Я помогу распечатать фото и документы.\n\n"
        "📎 Отправляй файлы (JPG, PNG, PDF, DOC, DOCX)\n"
        "📦 Можно отправлять несколько файлов за раз\n"
        "📊 Я посчитаю количество и рассчитаю стоимость\n\n"
        f"📞 Контакт: {CONTACT_PHONE}\n"
        f"🚚 Доставка: {DELIVERY_OPTIONS}"
    )
    
    update.message.reply_text(welcome)
    return WAITING_FOR_FILE

def handle_file(update, context):
    """Обработка входящих файлов"""
    user_id = update.effective_user.id
    message = update.message
    
    # Проверяем, является ли это медиа-группой (несколько файлов)
    if message.media_group_id:
        return handle_media_group(update, context)
    
    # Обработка одиночного файла
    return process_single_file(update, context)

def handle_media_group(update, context):
    """Обработка группы файлов (несколько в одном сообщении)"""
    user_id = update.effective_user.id
    message = update.message
    media_group_id = message.media_group_id
    
    # Сохраняем сообщение в группу
    if user_id not in media_groups:
        media_groups[user_id] = {}
    
    if media_group_id not in media_groups[user_id]:
        media_groups[user_id][media_group_id] = []
    
    media_groups[user_id][media_group_id].append(message)
    
    # Отменяем предыдущий таймер если есть
    timer_key = f"{user_id}_{media_group_id}"
    if timer_key in group_timers:
        group_timers[timer_key].cancel()
    
    # Создаем новый таймер на 2 секунды
    timer = threading.Timer(2.0, process_media_group, args=[user_id, media_group_id, context])
    timer.daemon = True
    timer.start()
    group_timers[timer_key] = timer
    
    return WAITING_FOR_FILE

def process_media_group(user_id, media_group_id, context):
    """Обрабатывает группу файлов после сбора всех сообщений"""
    try:
        if user_id not in media_groups or media_group_id not in media_groups[user_id]:
            return
        
        messages = media_groups[user_id].pop(media_group_id)
        if not messages:
            return
        
        # Очищаем таймер
        timer_key = f"{user_id}_{media_group_id}"
        if timer_key in group_timers:
            del group_timers[timer_key]
        
        # Создаем сессию если нужно
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "files": [],
                "temp_dirs": [],
                "total_items": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": messages[0].from_user.username or messages[0].from_user.first_name,
                    "first_name": messages[0].from_user.first_name,
                    "last_name": messages[0].from_user.last_name or ""
                }
            }
        
        # Обрабатываем все файлы из группы
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
            
            # Скачиваем файл
            file_path, temp_dir = download_file(file_obj, file_name)
            if not file_path:
                continue
            
            # Считаем количество (для фото = 1, для документов = страницы)
            items, unit = count_items_in_file(file_path, file_name)
            
            # Сохраняем в сессию
            user_sessions[user_id]["files"].append({
                "path": file_path,
                "name": file_name,
                "type": file_type,
                "items": items,
                "unit": unit
            })
            user_sessions[user_id]["temp_dirs"].append(temp_dir)
            user_sessions[user_id]["total_items"] += items
        
        if not user_sessions[user_id]["files"]:
            context.bot.send_message(
                chat_id=user_id,
                text="❌ Не удалось загрузить файлы"
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_items = user_sessions[user_id]["total_items"]
        
        text = f"✅ Загружено {files_count} файлов!\n\n📊 Статистика:\n"
        if photo_count > 0:
            text += f"📸 Фото: {photo_count}\n"
        if doc_count > 0:
            text += f"📄 Документы: {doc_count}\n"
        text += f"📊 Всего единиц: {total_items}\n\n"
        
        # Предлагаем выбор (если есть документы, приоритет у них)
        if doc_count > 0:
            text += "Выберите тип печати для документов:"
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
            ]
        else:
            text += "Выберите формат печати для фото:"
            keyboard = [
                [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
                [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
            ]
        
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке группы файлов: {e}")
        logger.error(traceback.format_exc())

def process_single_file(update, context):
    """Обработка одиночного файла"""
    user_id = update.effective_user.id
    message = update.message
    
    # Создаем сессию если нужно
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "files": [],
            "temp_dirs": [],
            "total_items": 0,
            "user_info": {
                "user_id": user_id,
                "username": update.effective_user.username or update.effective_user.first_name,
                "first_name": update.effective_user.first_name,
                "last_name": update.effective_user.last_name or ""
            }
        }
    
    # Определяем тип файла
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
            message.reply_text("❌ Неподдерживаемый формат")
            return WAITING_FOR_FILE
    elif message.photo:
        file_obj = message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE
    
    # Скачиваем файл
    file_path, temp_dir = download_file(file_obj, file_name)
    if not file_path:
        message.reply_text("❌ Ошибка загрузки")
        return WAITING_FOR_FILE
    
    # Считаем количество (для фото = 1, для документов = страницы)
    items, unit = count_items_in_file(file_path, file_name)
    
    # Сохраняем в сессию
    user_sessions[user_id]["files"].append({
        "path": file_path,
        "name": file_name,
        "type": file_type,
        "items": items,
        "unit": unit
    })
    user_sessions[user_id]["temp_dirs"].append(temp_dir)
    user_sessions[user_id]["total_items"] += items
    
    # Статистика
    files_count = len(user_sessions[user_id]["files"])
    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')
    total_items = user_sessions[user_id]["total_items"]
    
    text = f"✅ Файл добавлен!\n\n📊 Статистика:\n"
    if photo_count > 0:
        text += f"📸 Фото: {photo_count}\n"
    if doc_count > 0:
        text += f"📄 Документы: {doc_count}\n"
    text += f"📊 Всего единиц: {total_items}\n\n"
    
    # Предлагаем выбор
    if doc_count > 0:
        text += "Выберите тип печати для документов:"
        keyboard = [
            [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
            [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
        ]
    else:
        text += "Выберите формат печати для фото:"
        keyboard = [
            [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
            [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
            [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
        ]
    
    message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WAITING_FOR_FILE

def button_handler(update, context):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    if data == "add_more":
        query.edit_message_text("📤 Отправьте следующие файлы")
        return WAITING_FOR_FILE
    
    if data == "cancel":
        if user_id in user_sessions:
            for d in user_sessions[user_id].get("temp_dirs", []):
                shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]
        query.edit_message_text("❌ Заказ отменён")
        return WAITING_FOR_FILE
    
    if data == "new_order":
        if user_id in user_sessions:
            for d in user_sessions[user_id].get("temp_dirs", []):
                shutil.rmtree(d, ignore_errors=True)
            del user_sessions[user_id]
        query.edit_message_text("🔄 Новый заказ. Отправьте файлы.")
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        query.edit_message_text(
            "🔢 Сколько копий каждого фото напечатать?\n"
            "Введите число или выберите из вариантов:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = data.split("_")[1]
        total_items = user_sessions[user_id]["total_items"]
        query.edit_message_text(
            f"🔢 В файлах всего {total_items} единиц.\n"
            f"Сколько копий каждого документа напечатать?\n"
            f"(Каждая копия = {total_items} единиц)\n"
            f"Введите число или выберите из вариантов:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if data.startswith("qty_"):
        quantity = int(data.split("_")[1])
        session = user_sessions.get(user_id)
        if not session:
            query.edit_message_text("❌ Ошибка: сессия не найдена")
            return WAITING_FOR_FILE
        
        session["quantity"] = quantity
        
        files = session["files"]
        file_type = session["type"]
        
        total = 0
        total_items = 0
        details = "📊 ДЕТАЛЬНЫЙ РАСЧЁТ:\n\n"
        
        for i, f in enumerate(files, 1):
            if file_type == "photo":
                price_dict = PHOTO_PRICES[session["format"]]
                file_total = calculate_price(price_dict, quantity)
                total += file_total
                total_items += f['items'] * quantity
                details += f"📸 Файл {i}: {f['name'][:30]}...\n"
                details += f"   • {f['items']} {f['unit']} × {quantity} копий = {f['items'] * quantity} {f['unit']}\n"
                details += f"   • {file_total // quantity} руб./копия\n"
                details += f"   • Итого: {file_total} руб.\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                total_items += file_items
                details += f"📄 Файл {i}: {f['name'][:30]}...\n"
                details += f"   • {f['items']} {f['unit']} × {quantity} копий = {file_items} {f['unit']}\n"
                details += f"   • {file_total // file_items} руб./{f['unit'][:-1] if f['unit'] == 'страниц' else f['unit'][:-1]}\n"
                details += f"   • Итого: {file_total} руб.\n\n"
        
        session["total"] = total
        session["total_items"] = total_items
        session["delivery"] = estimate_delivery_time(total_items)
        
        text = f"{details}\n"
        text += "📋 ПРОВЕРЬТЕ ЗАКАЗ:\n\n"
        text += f"📦 Всего файлов: {len(files)}\n"
        text += f"📊 Всего единиц к печати: {total_items}\n"
        text += f"💰 ИТОГОВАЯ СУММА: {total} руб.\n"
        text += f"⏳ Срок выполнения: {session['delivery']}\n\n"
        text += "Всё верно?"
        
        keyboard = [
            [InlineKeyboardButton("✅ Да, подтвердить заказ", callback_data="confirm"),
             InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel")]
        ]
        
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return CONFIRMING_ORDER
    
    if data == "confirm":
        session = user_sessions.get(user_id)
        if not session:
            query.edit_message_text("❌ Ошибка: сессия не найдена")
            return WAITING_FOR_FILE
        
        success, folder = save_order_to_folder(
            user_id,
            session['user_info']['username'],
            session,
            session['files']
        )
        
        if success:
            # Отправляем уведомление админу
            send_admin_notification(session, folder)
            
            text = (
                "✅ ЗАКАЗ УСПЕШНО ОФОРМЛЕН!\n\n"
                f"👤 Заказчик: {session['user_info']['first_name']}\n"
                f"📦 Файлов: {len(session['files'])}\n"
                f"📊 Всего единиц в оригинале: {session['total_items']}\n"
                f"📊 Всего единиц к печати: {session['total_items'] * session['quantity']}\n"
                f"💰 Сумма к оплате: {session['total']} руб.\n"
                f"⏳ Срок выполнения: {session['delivery']}\n\n"
                f"📞 Контактный телефон: {CONTACT_PHONE}\n"
                f"🚚 Способы получения: {DELIVERY_OPTIONS}\n\n"
                "Спасибо за заказ! 😊"
            )
            
        else:
            text = "❌ Ошибка при сохранении заказа"
        
        # Очищаем временные файлы
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        keyboard = [[InlineKeyboardButton("🔄 Сделать новый заказ", callback_data="new_order")]]
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
        [InlineKeyboardButton("200", callback_data="qty_200"), 
         InlineKeyboardButton("300", callback_data="qty_300"),
         InlineKeyboardButton("400", callback_data="qty_400"), 
         InlineKeyboardButton("500", callback_data="qty_500")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def handle_quantity_input(update, context):
    """Ручной ввод количества"""
    user_id = update.effective_user.id
    text = update.message.text
    quantity = extract_number_from_text(text)
    
    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            "Пожалуйста, введите число от 1 до 1000\n"
            "Или выберите из кнопок:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    # Создаем callback как при нажатии кнопки
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
    """Список всех заказов"""
    try:
        orders = []
        if os.path.exists(ORDERS_PATH):
            for item in os.listdir(ORDERS_PATH):
                item_path = os.path.join(ORDERS_PATH, item)
                if os.path.isdir(item_path):
                    # Получаем информацию о заказе
                    info_file = os.path.join(item_path, "информация_о_заказе.txt")
                    info_text = ""
                    if os.path.exists(info_file):
                        with open(info_file, 'r', encoding='utf-8') as f:
                            info_text = f.read()
                    
                    # Список файлов
                    files = []
                    for f in os.listdir(item_path):
                        if f != "информация_о_заказе.txt":
                            file_path = os.path.join(item_path, f)
                            file_size = os.path.getsize(file_path) // 1024
                            files.append({
                                'name': f,
                                'size': file_size,
                                'url': f'/orders/{item}/{f}'
                            })
                    
                    orders.append({
                        'name': item,
                        'path': item_path,
                        'info': info_text.replace('\n', '<br>'),
                        'files': files,
                        'created': datetime.fromtimestamp(os.path.getctime(item_path)).strftime('%d.%m.%Y %H:%M:%S')
                    })
        
        # Сортируем по дате (новые сверху)
        orders.sort(key=lambda x: x['created'], reverse=True)
        
        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Заказы - Print Bot</title>
            <meta charset="utf-8">
            <style>
                body { font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }
                h1 { color: #333; }
                .order { background: white; margin: 20px 0; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
                .order-header { background: #667eea; color: white; margin: -20px -20px 20px -20px; padding: 15px 20px; border-radius: 10px 10px 0 0; }
                .order-header h2 { margin: 0; font-size: 1.2em; }
                .order-date { font-size: 0.9em; opacity: 0.9; margin-top: 5px; }
                .info { background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 10px 0; white-space: pre-wrap; font-family: monospace; }
                .files { margin: 10px 0; }
                .file { display: inline-block; background: #e9ecef; padding: 8px 15px; margin: 5px; border-radius: 20px; text-decoration: none; color: #333; }
                .file:hover { background: #dee2e6; }
                .download-all { display: inline-block; background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 10px 0; }
                .download-all:hover { background: #218838; }
                .stats { background: white; padding: 20px; border-radius: 10px; margin-bottom: 20px; }
                .back { display: inline-block; margin-bottom: 20px; color: #667eea; text-decoration: none; }
            </style>
        </head>
        <body>
            <a href="/" class="back">← На главную</a>
            <h1>📦 Заказы</h1>
            
            <div class="stats">
                <strong>Всего заказов:</strong> """ + str(len(orders)) + """
            </div>
            
            {% for order in orders %}
            <div class="order">
                <div class="order-header">
                    <h2>{{ order.name }}</h2>
                    <div class="order-date">Создан: {{ order.created }}</div>
                </div>
                
                <div class="info">{{ order.info|safe }}</div>
                
                <div class="files">
                    <h3>Файлы:</h3>
                    {% for file in order.files %}
                    <a href="{{ file.url }}" class="file" download>{{ file.name }} ({{ file.size }} KB)</a>
                    {% endfor %}
                </div>
                
                <a href="/orders/{{ order.name }}/download" class="download-all">⬇️ Скачать все файлы (ZIP)</a>
            </div>
            {% endfor %}
        </body>
        </html>
        """
        
        from jinja2 import Template
        template = Template(html)
        return template.render(orders=orders)
    except Exception as e:
        return f"Ошибка: {e}"

@app.route('/orders/<path:order_name>/')
def view_order(order_name):
    """Просмотр конкретного заказа"""
    order_path = os.path.join(ORDERS_PATH, order_name)
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        return "Заказ не найден", 404
    
    files = []
    for f in os.listdir(order_path):
        file_path = os.path.join(order_path, f)
        if os.path.isfile(file_path) and f != "информация_о_заказе.txt":
            file_size = os.path.getsize(file_path) // 1024
            files.append({
                'name': f,
                'size': file_size,
                'url': f'/orders/{order_name}/{f}'
            })
    
    # Читаем информацию о заказе
    info = ""
    info_file = os.path.join(order_path, "информация_о_заказе.txt")
    if os.path.exists(info_file):
        with open(info_file, 'r', encoding='utf-8') as f:
            info = f.read().replace('\n', '<br>')
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Заказ {order_name}</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; font-size: 1.5em; }}
            .info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; white-space: pre-wrap; font-family: monospace; }}
            .file {{ display: block; background: #e9ecef; padding: 10px; margin: 5px 0; border-radius: 5px; text-decoration: none; color: #333; }}
            .file:hover {{ background: #dee2e6; }}
            .back {{ display: inline-block; margin-bottom: 20px; color: #667eea; text-decoration: none; }}
            .download-all {{ display: inline-block; background: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 10px 0; }}
            .download-all:hover {{ background: #218838; }}
        </style>
    </head>
    <body>
        <a href="/orders/" class="back">← К списку заказов</a>
        <div class="container">
            <h1>📁 Заказ: {order_name}</h1>
            <div class="info">{info}</div>
            <h3>Файлы:</h3>
            {''.join([f'<a href="{f["url"]}" class="file" download>📄 {f["name"]} ({f["size"]} KB)</a>' for f in files])}
            <a href="/orders/{order_name}/download" class="download-all">⬇️ Скачать все файлы (ZIP)</a>
        </div>
    </body>
    </html>
    """
    return html

@app.route('/orders/<path:order_name>/<filename>')
def download_order_file(order_name, filename):
    """Скачивание отдельного файла"""
    order_path = os.path.join(ORDERS_PATH, order_name)
    return send_from_directory(order_path, filename, as_attachment=True)

@app.route('/orders/<path:order_name>/download')
def download_all_files(order_name):
    """Скачивание всех файлов заказа в ZIP-архиве"""
    order_path = os.path.join(ORDERS_PATH, order_name)
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        return "Заказ не найден", 404
    
    # Создаем временный ZIP-файл
    temp_zip = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    with zipfile.ZipFile(temp_zip.name, 'w') as zipf:
        for root, dirs, files in os.walk(order_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, order_path)
                zipf.write(file_path, arcname)
    
    return send_file(temp_zip.name, as_attachment=True, download_name=f"{order_name}.zip", mimetype='application/zip')

@app.route('/webhook', methods=['POST'])
def webhook():
    """Прием обновлений от Telegram"""
    global dispatcher
    
    try:
        if dispatcher is None:
            logger.error("❌ dispatcher is None")
            return jsonify({"error": "Dispatcher not initialized"}), 500
            
        update_data = request.get_json()
        if update_data:
            logger.info(f"📩 Обновление: {update_data.get('update_id')}")
            update = telegram.Update.de_json(update_data, bot)
            
            # Передаем обновление в диспетчер
            dispatcher.process_update(update)
            
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
        "bot_ready": dispatcher is not None,
        "orders_count": len(os.listdir(ORDERS_PATH)) if os.path.exists(ORDERS_PATH) else 0,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/stats')
def stats():
    """Статистика заказов"""
    try:
        orders_count = 0
        if os.path.exists(ORDERS_PATH):
            orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))])
        
        return jsonify({
            "status": "ok",
            "orders_count": orders_count,
            "active_sessions": len(user_sessions),
            "bot_ready": dispatcher is not None,
            "orders_folder": ORDERS_PATH,
            "orders_url": f"{RENDER_URL}/orders/"
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route('/')
def home():
    """Главная страница"""
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    orders_count = len(os.listdir(ORDERS_PATH)) if os.path.exists(ORDERS_PATH) else 0
    
    return f"""
    <html>
        <head>
            <title>Print Bot</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
                .container {{ max-width: 800px; margin: 0 auto; background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; backdrop-filter: blur(10px); }}
                h1 {{ text-align: center; }}
                .status {{ background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px; margin: 20px 0; }}
                .info {{ margin: 10px 0; }}
                .btn {{ display: inline-block; background: white; color: #667eea; padding: 10px 20px; text-decoration: none; border-radius: 5px; margin: 5px; }}
                .btn:hover {{ background: #f0f0f0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 Print Bot</h1>
                <div class="status">
                    <h2>✅ Бот работает 24/7!</h2>
                    <p class="info">📁 Всего заказов: <strong>{orders_count}</strong></p>
                    <p class="info">📞 Контакт: <strong>{CONTACT_PHONE}</strong></p>
                    <p class="info">🚚 Доставка: <strong>{DELIVERY_OPTIONS}</strong></p>
                    <p class="info">⏰ Время сервера: <strong>{current_time}</strong></p>
                </div>
                <p>
                    <a href="/orders/" class="btn">📦 Просмотр заказов</a>
                    <a href="/stats" class="btn">📊 Статистика</a>
                    <a href="/health" class="btn">❤️ Проверка здоровья</a>
                </p>
                <p>Бот активен и принимает заказы в Telegram!</p>
            </div>
        </body>
    </html>
    """

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📁 Папка для заказов: {ORDERS_PATH}")
print(f"📁 URL для просмотра заказов: {RENDER_URL}/orders/")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")

# Создаем бота
bot = telegram.Bot(token=TOKEN)

# Создаем updater и dispatcher
updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

# Создаем ConversationHandler
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
        ],
        SELECTING_DOC_TYPE: [
            CallbackQueryHandler(button_handler, pattern="^doc_.*"),
        ],
        ENTERING_QUANTITY: [
            MessageHandler(Filters.text & ~Filters.command, handle_quantity_input),
            CallbackQueryHandler(button_handler, pattern="^qty_.*"),
        ],
        CONFIRMING_ORDER: [
            CallbackQueryHandler(button_handler, pattern="^(confirm|cancel|new_order)$"),
        ],
    },
    fallbacks=[CommandHandler("start", start)],
    name="print_bot_conversation",
    persistent=False,
)

dispatcher.add_handler(conv_handler)

# Устанавливаем веб-хук
webhook_url = f"{RENDER_URL}/webhook"
updater.bot.set_webhook(url=webhook_url)

print(f"✅ Веб-хук: {webhook_url}")
print("✅ БОТ ГОТОВ К РАБОТЕ!")
print("=" * 60)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    print("🌐 Запуск Flask сервера...")
    app.run(host='0.0.0.0', port=PORT)
