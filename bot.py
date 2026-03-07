#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
Современный дизайн
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
    """Возвращает отображение статуса"""
    return ORDER_STATUSES.get(status, status)

# Загружаем историю заказов
def load_orders_history():
    """Загружает историю заказов из JSON файла"""
    try:
        if os.path.exists(ORDERS_DB_FILE):
            with open(ORDERS_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"Ошибка загрузки истории: {e}")
        return []

def save_order_to_history(order_data):
    """Сохраняет заказ в историю"""
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
    """Обновляет статус заказа"""
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
            
            # Обновляем файл информации в папке заказа
            order_folder = os.path.join(ORDERS_PATH, order_id)
            info_file = os.path.join(order_folder, "информация_о_заказе.txt")
            if os.path.exists(info_file):
                with open(info_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Заменяем статус в файле
                import re
                content = re.sub(r'Статус:.*\n', f'Статус: {get_status_display(new_status)}\n', content)
                
                with open(info_file, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Отправляем уведомление клиенту
            if user_id and bot:
                try:
                    bot.send_message(
                        chat_id=user_id,
                        text=f"📢 **Статус вашего заказа изменен**\n\n"
                             f"🆔 Заказ: {order_id}\n"
                             f"📌 Новый статус: {get_status_display(new_status)}",
                        parse_mode="Markdown"
                    )
                    logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления: {e}")
            
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return False

# ========== ФУНКЦИЯ ДЛЯ ФОРМАТИРОВАНИЯ РАЗМЕРА ФАЙЛА ==========
def format_file_size(size_bytes):
    """Форматирует размер файла в человеко-читаемый вид"""
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
    """Подсчет количества в файле (фото = 1, документы = страницы)"""
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
            # Для фото всегда 1 фото
            logger.info(f"📸 Фото: {file_name} - 1 фото")
            return 1, "фото", "фото"
            
        return 1, "единиц", "неизвестно"
    except Exception as e:
        logger.error(f"Ошибка подсчета: {e}")
        return 1, "единиц", "неизвестно"

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
        order_id = f"{clean_name}_{timestamp}"
        order_folder = os.path.join(ORDERS_PATH, order_id)
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
        
        # Подсчитываем отдельно фото и документы
        photo_files = [ff for ff in files_info if ff['type'] == 'photo']
        doc_files = [ff for ff in files_info if ff['type'] == 'doc']
        
        total_photos = sum(ff['items'] for ff in photo_files)
        total_pages = sum(ff['items'] for ff in doc_files)
        
        # Сохраняем информацию о заказе
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
        
        logger.info(f"📝 Информация о заказе сохранена в {info_file}")
        
        # Сохраняем в историю
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
    """Отправляет уведомление админу о новом заказе"""
    try:
        order_url = f"{RENDER_URL}/orders/{order_id}/"
        
        # Подсчитываем отдельно фото и документы
        photo_files = [f for f in order_data['files'] if f['type'] == 'photo']
        doc_files = [f for f in order_data['files'] if f['type'] == 'doc']
        
        total_photos = sum(f['items'] for f in photo_files)
        total_pages = sum(f['items'] for f in doc_files)
        
        # Формируем сообщение для админа
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
            # Обновляем счетчики, если сессия уже есть
            if "total_photos" not in user_sessions[user_id]:
                user_sessions[user_id]["total_photos"] = 0
            if "total_pages" not in user_sessions[user_id]:
                user_sessions[user_id]["total_pages"] = 0
        
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
            
            # Считаем количество
            items, unit, type_name = count_items_in_file(file_path, file_name)
            
            # Сохраняем в сессию
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
        
        text = f"✅ Загружено {files_count} файлов!\n\n📊 Статистика:\n"
        if photo_count > 0:
            text += f"📸 Фото: {photo_count}\n"
        if doc_count > 0:
            text += f"📄 Документы: {doc_count}\n"
        
        if total_photos > 0:
            text += f"📸 Всего фото: {total_photos}\n"
        if total_pages > 0:
            text += f"📄 Всего страниц: {total_pages}\n"
        text += "\n"
        
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
        # Обновляем счетчики, если сессия уже есть
        if "total_photos" not in user_sessions[user_id]:
            user_sessions[user_id]["total_photos"] = 0
        if "total_pages" not in user_sessions[user_id]:
            user_sessions[user_id]["total_pages"] = 0
    
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
    
    # Считаем количество
    items, unit, type_name = count_items_in_file(file_path, file_name)
    
    # Сохраняем в сессию
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
    
    # Статистика
    files_count = len(user_sessions[user_id]["files"])
    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')
    total_photos = user_sessions[user_id]["total_photos"]
    total_pages = user_sessions[user_id]["total_pages"]
    
    text = f"✅ Файл добавлен!\n\n📊 Статистика:\n"
    if photo_count > 0:
        text += f"📸 Фото: {photo_count}\n"
    if doc_count > 0:
        text += f"📄 Документы: {doc_count}\n"
    
    if total_photos > 0:
        text += f"📸 Всего фото: {total_photos}\n"
    if total_pages > 0:
        text += f"📄 Всего страниц: {total_pages}\n"
    text += "\n"
    
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

def cancel_order(user_id, query=None, context=None):
    """Общая функция для отмены заказа"""
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except:
                    pass
        del user_sessions[user_id]
        logger.info(f"✅ Сессия пользователя {user_id} очищена")
    
    # Создаем клавиатуру для нового заказа
    keyboard = [[InlineKeyboardButton("🔄 Сделать новый заказ", callback_data="new_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем или редактируем сообщение
    if query:
        try:
            query.edit_message_text(
                "❌ Заказ отменён. Все загруженные файлы удалены.\n\nХотите оформить новый заказ?",
                reply_markup=reply_markup
            )
        except:
            if context:
                context.bot.send_message(
                    chat_id=user_id,
                    text="❌ Заказ отменён. Все загруженные файлы удалены.\n\nХотите оформить новый заказ?",
                    reply_markup=reply_markup
                )
    elif context:
        context.bot.send_message(
            chat_id=user_id,
            text="❌ Заказ отменён. Все загруженные файлы удалены.\n\nХотите оформить новый заказ?",
            reply_markup=reply_markup
        )
    
    return WAITING_FOR_FILE

def button_handler(update, context):
    """Обработка нажатий кнопок"""
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    # Отмена заказа - обрабатываем ВСЕГДА в первую очередь
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text("📤 Отправьте следующие файлы")
        return WAITING_FOR_FILE
    
    if data == "new_order":
        # Очищаем сессию и начинаем новый заказ
        if user_id in user_sessions:
            if "temp_dirs" in user_sessions[user_id]:
                for d in user_sessions[user_id]["temp_dirs"]:
                    try:
                        shutil.rmtree(d, ignore_errors=True)
                    except:
                        pass
            del user_sessions[user_id]
        
        query.edit_message_text(
            "🔄 **НОВЫЙ ЗАКАЗ**\n\n"
            "Отправьте файлы для печати (JPG, PNG, PDF, DOC, DOCX):",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = data.split("_")[1]
        query.edit_message_text(
            "🔢 Сколько копий каждого фото напечатать?\n"
            "Введите число или выберите из вариантов:",
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
        query.edit_message_text(
            f"🔢 В файлах всего {total_items} единиц.\n"
            f"📸 Фото: {total_photos}\n"
            f"📄 Страниц: {total_pages}\n\n"
            f"Сколько копий каждого документа напечатать?\n"
            f"Введите число или выберите из вариантов:",
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
        details = "📊 ДЕТАЛЬНЫЙ РАСЧЁТ:\n\n"
        
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
                details += f"📸 Файл {i}: {f['name'][:30]}...\n"
                details += f"   • {f['items']} фото × {quantity} копий = {f['items'] * quantity} фото\n"
                details += f"   • {file_total // quantity} руб./копия\n"
                details += f"   • Итого: {file_total} руб.\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += f"📄 Файл {i}: {f['name'][:30]}...\n"
                details += f"   • {f['items']} стр. × {quantity} копий = {file_items} стр.\n"
                details += f"   • {file_total // file_items} руб./стр.\n"
                details += f"   • Итого: {file_total} руб.\n\n"
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = f"{details}\n"
        text += "📋 ПРОВЕРЬТЕ ЗАКАЗ:\n\n"
        text += f"📦 Всего файлов: {len(files)}\n"
        if total_photos_result > 0:
            text += f"📸 Всего фото к печати: {total_photos_result}\n"
        if total_pages_result > 0:
            text += f"📄 Всего страниц к печати: {total_pages_result}\n"
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
            return cancel_order(user_id, query, context)
        
        success, order_id, folder = save_order_to_folder(
            user_id,
            session['user_info']['username'],
            session,
            session['files']
        )
        
        if success:
            # Отправляем уведомление админу
            send_admin_notification(session, order_id, folder)
            
            # Подсчитываем для сообщения пользователю
            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            doc_files = [f for f in session['files'] if f['type'] == 'doc']
            
            total_photos = sum(f['items'] for f in photo_files)
            total_pages = sum(f['items'] for f in doc_files)
            
            # Уведомление клиенту
            client_message = (
                "✅ **ЗАКАЗ УСПЕШНО ОФОРМЛЕН!**\n\n"
                f"🆔 Номер заказа: `{order_id}`\n"
                f"👤 Заказчик: {session['user_info']['first_name']}\n"
                f"📦 Файлов: {len(session['files'])}\n"
            )
            
            if total_photos > 0:
                client_message += f"📸 Фото в оригинале: {total_photos}\n"
                client_message += f"📸 Фото к печати: {total_photos * session['quantity']}\n"
            if total_pages > 0:
                client_message += f"📄 Страниц в оригинале: {total_pages}\n"
                client_message += f"📄 Страниц к печати: {total_pages * session['quantity']}\n"
            
            client_message += (
                f"💰 Сумма к оплате: {session['total']} руб.\n"
                f"⏳ Срок выполнения: {session['delivery']}\n\n"
                f"📞 Контактный телефон: {CONTACT_PHONE}\n"
                f"🚚 Способы получения: {DELIVERY_OPTIONS}\n\n"
                f"📌 **Статус вашего заказа:** {get_status_display('new')}\n"
                "Вы будете получать уведомления при изменении статуса.\n\n"
                "Спасибо за заказ! 😊"
            )
            
            context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode="Markdown"
            )
            
            # Если есть фото, отправляем предпросмотр
            if photo_files:
                try:
                    # Отправляем первые 5 фото
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
        
        # Очищаем временные файлы
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        # Кнопка для нового заказа
        keyboard = [[InlineKeyboardButton("🔄 Сделать новый заказ", callback_data="new_order")]]
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text="Хотите оформить ещё один заказ?",
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

# ========== ВЕБ-ИНТЕРФЕЙС С НОВЫМ ДИЗАЙНОМ ==========
app = Flask(__name__)

# Главная страница с новым дизайном
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Print Bot - Сервис печати</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .glass-card {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            padding: 40px;
            width: 100%;
            max-width: 1000px;
            box-shadow: 0 25px 50px rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .header {
            text-align: center;
            margin-bottom: 40px;
        }
        
        .header h1 {
            font-size: 3.5em;
            color: white;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
        }
        
        .header p {
            color: rgba(255, 255, 255, 0.9);
            font-size: 1.2em;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .stat-card {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 20px;
            padding: 25px;
            text-align: center;
            transition: transform 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .stat-card:hover {
            transform: translateY(-5px);
            background: rgba(255, 255, 255, 0.2);
        }
        
        .stat-icon {
            font-size: 2.5em;
            margin-bottom: 15px;
        }
        
        .stat-value {
            font-size: 2.5em;
            font-weight: bold;
            color: white;
            margin-bottom: 5px;
        }
        
        .stat-label {
            color: rgba(255, 255, 255, 0.8);
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        
        .actions-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }
        
        .action-card {
            background: white;
            border-radius: 20px;
            padding: 30px;
            text-align: center;
            text-decoration: none;
            color: #333;
            transition: all 0.3s ease;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1);
        }
        
        .action-card:hover {
            transform: translateY(-10px);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
        }
        
        .action-icon {
            font-size: 3em;
            margin-bottom: 15px;
        }
        
        .action-title {
            font-size: 1.3em;
            font-weight: bold;
            margin-bottom: 10px;
            color: #667eea;
        }
        
        .action-desc {
            font-size: 0.9em;
            color: #666;
        }
        
        .info-section {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 20px;
            padding: 30px;
            color: white;
        }
        
        .info-title {
            font-size: 1.3em;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .contacts {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            justify-content: center;
        }
        
        .contact-item {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px 25px;
            border-radius: 50px;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 1.1em;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        @media (max-width: 768px) {
            .header h1 {
                font-size: 2.5em;
            }
            
            .glass-card {
                padding: 20px;
            }
        }
    </style>
</head>
<body>
    <div class="glass-card">
        <div class="header">
            <h1>
                <span>🤖</span> Print Bot
            </h1>
            <p>Сервис для печати фото и документов через Telegram</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-icon">📦</div>
                <div class="stat-value">{orders_count}</div>
                <div class="stat-label">активных заказов</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">⏰</div>
                <div class="stat-value">24/7</div>
                <div class="stat-label">работа</div>
            </div>
            <div class="stat-card">
                <div class="stat-icon">⚡</div>
                <div class="stat-value">1-3</div>
                <div class="stat-label">дня</div>
            </div>
        </div>
        
        <div class="actions-grid">
            <a href="/orders/" class="action-card">
                <div class="action-icon">📋</div>
                <div class="action-title">Заказы</div>
                <div class="action-desc">Просмотр и управление заказами</div>
            </a>
            <a href="/stats" class="action-card">
                <div class="action-icon">📊</div>
                <div class="action-title">Статистика</div>
                <div class="action-desc">Детальная информация</div>
            </a>
            <a href="/health" class="action-card">
                <div class="action-icon">❤️</div>
                <div class="action-title">Здоровье</div>
                <div class="action-desc">Проверка состояния бота</div>
            </a>
        </div>
        
        <div class="info-section">
            <div class="info-title">
                <span>📞</span> Контактная информация
            </div>
            <div class="contacts">
                <div class="contact-item">
                    <span>📞</span> {phone}
                </div>
                <div class="contact-item">
                    <span>🚚</span> {delivery}
                </div>
                <div class="contact-item">
                    <span>⏰</span> {time}
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# Страница списка заказов с новым дизайном
ORDERS_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Print Bot - Заказы</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        /* Хедер */
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            padding: 30px;
            margin-bottom: 30px;
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .header p {
            color: rgba(255, 255, 255, 0.9);
        }
        
        /* Навигация */
        .nav-links {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .nav-btn {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            color: white;
            text-decoration: none;
            padding: 12px 25px;
            border-radius: 50px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .nav-btn:hover {
            background: rgba(255, 255, 255, 0.25);
            transform: translateY(-2px);
        }
        
        /* Статистика */
        .stats-row {
            display: flex;
            gap: 20px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .stat-pill {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            padding: 15px 30px;
            border-radius: 50px;
            color: white;
            display: flex;
            align-items: center;
            gap: 10px;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .stat-pill strong {
            font-size: 1.2em;
            margin-right: 5px;
        }
        
        /* Сетка заказов */
        .orders-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 25px;
        }
        
        .order-card {
            background: white;
            border-radius: 30px;
            overflow: hidden;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.2);
            transition: all 0.3s ease;
        }
        
        .order-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 25px 45px rgba(0, 0, 0, 0.3);
        }
        
        .order-header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            position: relative;
        }
        
        .order-header h2 {
            font-size: 1.1em;
            margin-bottom: 10px;
            word-break: break-all;
            opacity: 0.9;
            padding-right: 120px;
        }
        
        .order-date {
            font-size: 0.9em;
            opacity: 0.8;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        
        .order-status-badge {
            position: absolute;
            top: 15px;
            right: 15px;
            background: rgba(255, 255, 255, 0.2);
            padding: 8px 15px;
            border-radius: 50px;
            font-size: 0.9em;
            backdrop-filter: blur(5px);
            border: 1px solid rgba(255, 255, 255, 0.3);
        }
        
        .order-content {
            padding: 20px;
        }
        
        /* Статистика заказа */
        .order-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 20px;
            border-bottom: 1px solid #eee;
        }
        
        .stat-item {
            text-align: center;
        }
        
        .stat-value {
            font-size: 1.2em;
            font-weight: bold;
            color: #333;
        }
        
        .stat-label {
            font-size: 0.8em;
            color: #666;
            margin-top: 5px;
        }
        
        /* Управление статусами */
        .status-section {
            background: #f8f9fa;
            border-radius: 20px;
            padding: 15px;
            margin-bottom: 20px;
        }
        
        .status-section h4 {
            color: #333;
            margin-bottom: 10px;
            font-size: 0.95em;
        }
        
        .status-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .status-btn {
            padding: 8px 12px;
            border: none;
            border-radius: 50px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s ease;
            background: white;
            border: 1px solid #dee2e6;
        }
        
        .status-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }
        
        .status-btn.new { background: #e3f2fd; }
        .status-btn.processing { background: #fff3e0; }
        .status-btn.printing { background: #e8f5e8; }
        .status-btn.ready { background: #e8e8f5; }
        .status-btn.shipped { background: #f3e5f5; }
        .status-btn.delivered { background: #e8f0fe; }
        .status-btn.cancelled { background: #ffebee; }
        
        /* Галерея фото */
        .photo-gallery {
            display: flex;
            gap: 10px;
            overflow-x: auto;
            padding: 10px 0;
            margin-bottom: 15px;
        }
        
        .photo-preview {
            width: 80px;
            height: 80px;
            object-fit: cover;
            border-radius: 15px;
            cursor: pointer;
            transition: all 0.2s ease;
            border: 3px solid white;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .photo-preview:hover {
            transform: scale(1.1);
            box-shadow: 0 10px 25px rgba(0,0,0,0.2);
        }
        
        /* Список файлов */
        .files-list {
            margin: 15px 0;
            max-height: 200px;
            overflow-y: auto;
        }
        
        .file-item {
            display: flex;
            align-items: center;
            padding: 10px;
            background: #f8f9fa;
            border-radius: 15px;
            margin-bottom: 8px;
            transition: all 0.2s ease;
        }
        
        .file-item:hover {
            background: #e9ecef;
        }
        
        .file-icon {
            font-size: 1.5em;
            margin-right: 15px;
        }
        
        .file-info {
            flex: 1;
        }
        
        .file-name {
            font-size: 0.9em;
            color: #333;
            word-break: break-all;
            margin-bottom: 3px;
        }
        
        .file-size {
            font-size: 0.8em;
            color: #666;
        }
        
        .file-download {
            color: #667eea;
            text-decoration: none;
            padding: 8px 15px;
            border-radius: 50px;
            transition: all 0.2s ease;
            background: white;
        }
        
        .file-download:hover {
            background: #667eea;
            color: white;
        }
        
        /* Кнопки действий */
        .order-actions {
            display: flex;
            gap: 10px;
            margin-top: 20px;
        }
        
        .action-btn {
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 50px;
            font-size: 0.95em;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            text-decoration: none;
            transition: all 0.3s ease;
            font-weight: 500;
        }
        
        .btn-download-all {
            background: #28a745;
            color: white;
        }
        
        .btn-download-all:hover {
            background: #218838;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(40, 167, 69, 0.3);
        }
        
        .btn-view {
            background: #667eea;
            color: white;
        }
        
        .btn-view:hover {
            background: #5a67d8;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.3);
        }
        
        /* Пустое состояние */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .empty-icon {
            font-size: 5em;
            margin-bottom: 20px;
            opacity: 0.5;
        }
        
        .empty-text {
            font-size: 1.2em;
            opacity: 0.8;
        }
        
        /* Адаптивность */
        @media (max-width: 768px) {
            .orders-grid {
                grid-template-columns: 1fr;
            }
            
            .header h1 {
                font-size: 2em;
            }
            
            .stats-row {
                flex-direction: column;
            }
            
            .stat-pill {
                width: 100%;
            }
        }
    </style>
    <script>
        function updateStatus(orderId, status) {
            fetch(`/orders/${orderId}/status`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status: status})
            })
            .then(r => r.json())
            .then(d => { 
                if(d.success) {
                    showNotification('Статус обновлен!', 'success');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('Ошибка: ' + (d.error || 'неизвестная ошибка'), 'error');
                }
            })
            .catch(error => {
                showNotification('Ошибка при обновлении статуса', 'error');
            });
        }
        
        function showNotification(message, type) {
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 15px 25px;
                border-radius: 50px;
                background: ${type === 'success' ? '#28a745' : '#dc3545'};
                color: white;
                font-weight: 500;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                z-index: 9999;
                animation: slideIn 0.3s ease;
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            
            setTimeout(() => {
                notification.style.animation = 'slideOut 0.3s ease';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
    </script>
</head>
<body>
    <div class="container">
        <!-- Хедер -->
        <div class="header">
            <h1>
                <span>📦</span> Заказы на печать
            </h1>
            <p>Управление заказами и файлами</p>
        </div>
        
        <!-- Навигация -->
        <div class="nav-links">
            <a href="/" class="nav-btn">
                <span>🏠</span> Главная
            </a>
            <a href="/stats" class="nav-btn">
                <span>📊</span> Статистика
            </a>
            <a href="/health" class="nav-btn">
                <span>❤️</span> Здоровье
            </a>
        </div>
        
        <!-- Статистика -->
        <div class="stats-row">
            <div class="stat-pill">
                <span>📊</span> Всего заказов: <strong>{{ orders|length }}</strong>
            </div>
            <div class="stat-pill">
                <span>📁</span> Всего файлов: <strong>{{ total_files }}</strong>
            </div>
        </div>
        
        {% if orders %}
        <!-- Сетка заказов -->
        <div class="orders-grid">
            {% for order in orders %}
            <div class="order-card" id="order-{{ order.id }}">
                <div class="order-header">
                    <h2>{{ order.id }}</h2>
                    <div class="order-date">
                        <span>📅</span> {{ order.created }}
                    </div>
                    <div class="order-status-badge">
                        {{ order.status }}
                    </div>
                </div>
                
                <div class="order-content">
                    <!-- Статистика заказа -->
                    <div class="order-stats">
                        <div class="stat-item">
                            <div class="stat-value">{{ order.file_count }}</div>
                            <div class="stat-label">файлов</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value">{{ order.total_size }}</div>
                            <div class="stat-label">объем</div>
                        </div>
                        <div class="stat-item">
                            <div class="stat-value">{{ order.age_days }}д</div>
                            <div class="stat-label">возраст</div>
                        </div>
                    </div>
                    
                    <!-- Управление статусами -->
                    <div class="status-section">
                        <h4>📌 Изменить статус:</h4>
                        <div class="status-buttons">
                            <button class="status-btn new" onclick="updateStatus('{{ order.id }}', 'new')">🆕 Новый</button>
                            <button class="status-btn processing" onclick="updateStatus('{{ order.id }}', 'processing')">🔄 В обработке</button>
                            <button class="status-btn printing" onclick="updateStatus('{{ order.id }}', 'printing')">🖨️ В печати</button>
                            <button class="status-btn ready" onclick="updateStatus('{{ order.id }}', 'ready')">✅ Готов</button>
                            <button class="status-btn shipped" onclick="updateStatus('{{ order.id }}', 'shipped')">📦 Отправлен</button>
                            <button class="status-btn delivered" onclick="updateStatus('{{ order.id }}', 'delivered')">🏁 Доставлен</button>
                            <button class="status-btn cancelled" onclick="updateStatus('{{ order.id }}', 'cancelled')">❌ Отменен</button>
                        </div>
                    </div>
                    
                    <!-- Галерея фото -->
                    {% if order.photos %}
                    <div class="photo-gallery">
                        {% for photo in order.photos %}
                        <img src="{{ photo.url }}" class="photo-preview" onclick="window.open('{{ photo.url }}', '_blank')">
                        {% endfor %}
                    </div>
                    {% endif %}
                    
                    <!-- Список файлов -->
                    <div class="files-list">
                        {% for file in order.files %}
                        <div class="file-item">
                            <span class="file-icon">{{ '📸' if file.is_photo else '📄' }}</span>
                            <div class="file-info">
                                <div class="file-name">{{ file.name }}</div>
                                <div class="file-size">{{ file.size_formatted }}</div>
                            </div>
                            <a href="{{ file.url }}" class="file-download" download>⬇️</a>
                        </div>
                        {% endfor %}
                    </div>
                    
                    <!-- Кнопки действий -->
                    <div class="order-actions">
                        <a href="/orders/{{ order.id }}/" class="action-btn btn-view">
                            <span>👁️</span> Подробнее
                        </a>
                        <a href="/orders/{{ order.id }}/download" class="action-btn btn-download-all">
                            <span>⬇️</span> Все файлы
                        </a>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
        {% else %}
        <!-- Пустое состояние -->
        <div class="empty-state">
            <div class="empty-icon">📭</div>
            <div class="empty-text">Заказов пока нет</div>
            <p style="margin-top: 20px; opacity: 0.7;">Отправьте файлы в Telegram бот для создания заказа</p>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

# Страница детального просмотра заказа
ORDER_DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Print Bot - Заказ {{ order_id }}</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        
        /* Хедер */
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            padding: 30px;
            margin-bottom: 30px;
            color: white;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .header h1 {
            font-size: 2em;
            margin-bottom: 10px;
            word-break: break-all;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .header-meta {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            margin-top: 15px;
        }
        
        .meta-item {
            background: rgba(255, 255, 255, 0.15);
            padding: 10px 20px;
            border-radius: 50px;
            font-size: 0.95em;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        /* Навигация */
        .nav-links {
            display: flex;
            gap: 15px;
            margin-bottom: 30px;
            flex-wrap: wrap;
        }
        
        .nav-btn {
            background: rgba(255, 255, 255, 0.15);
            backdrop-filter: blur(10px);
            color: white;
            text-decoration: none;
            padding: 12px 25px;
            border-radius: 50px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .nav-btn:hover {
            background: rgba(255, 255, 255, 0.25);
            transform: translateY(-2px);
        }
        
        /* Основной контент */
        .content {
            background: white;
            border-radius: 30px;
            padding: 30px;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.2);
        }
        
        /* Управление статусами */
        .status-section {
            background: #f8f9fa;
            border-radius: 20px;
            padding: 20px;
            margin-bottom: 30px;
        }
        
        .status-section h3 {
            color: #333;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }
        
        .status-btn {
            padding: 10px 20px;
            border: none;
            border-radius: 50px;
            cursor: pointer;
            font-size: 0.95em;
            transition: all 0.2s ease;
            background: white;
            border: 1px solid #dee2e6;
        }
        
        .status-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .status-btn.new { background: #e3f2fd; }
        .status-btn.processing { background: #fff3e0; }
        .status-btn.printing { background: #e8f5e8; }
        .status-btn.ready { background: #e8e8f5; }
        .status-btn.shipped { background: #f3e5f5; }
        .status-btn.delivered { background: #e8f0fe; }
        .status-btn.cancelled { background: #ffebee; }
        
        /* Информация о заказе */
        .info-section {
            background: #f8f9fa;
            border-radius: 20px;
            padding: 20px;
            margin-bottom: 30px;
        }
        
        .info-section h3 {
            color: #333;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .info-content {
            white-space: pre-wrap;
            font-family: 'Consolas', monospace;
            background: white;
            padding: 20px;
            border-radius: 15px;
            border: 1px solid #eee;
            max-height: 400px;
            overflow-y: auto;
            font-size: 0.95em;
            line-height: 1.5;
        }
        
        /* Галерея фото */
        .photo-gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 20px;
            margin: 20px 0;
        }
        
        .photo-item {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 15px;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .photo-item:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.1);
        }
        
        .photo-img {
            max-width: 100%;
            max-height: 150px;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .photo-img:hover {
            transform: scale(1.05);
        }
        
        .photo-name {
            margin-top: 10px;
            font-size: 0.85em;
            color: #666;
            word-break: break-all;
        }
        
        /* Сетка файлов */
        .files-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }
        
        .file-card {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 20px;
            text-align: center;
            text-decoration: none;
            color: #333;
            transition: all 0.3s ease;
            display: block;
            border: 1px solid #eee;
        }
        
        .file-card:hover {
            background: #e9ecef;
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.1);
        }
        
        .file-icon {
            font-size: 2.5em;
            margin-bottom: 10px;
        }
        
        .file-name {
            word-break: break-all;
            font-size: 0.95em;
            margin-bottom: 5px;
        }
        
        .file-size {
            color: #666;
            font-size: 0.85em;
        }
        
        /* Кнопка скачивания */
        .download-all {
            display: inline-block;
            background: linear-gradient(135deg, #28a745, #20c997);
            color: white;
            text-decoration: none;
            padding: 15px 40px;
            border-radius: 50px;
            font-size: 1.1em;
            margin-top: 30px;
            transition: all 0.3s ease;
            border: none;
            cursor: pointer;
            box-shadow: 0 5px 15px rgba(40, 167, 69, 0.3);
        }
        
        .download-all:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(40, 167, 69, 0.4);
        }
        
        /* Статистика */
        .stats-row {
            display: flex;
            gap: 20px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        
        .stat-item {
            background: white;
            padding: 15px 25px;
            border-radius: 50px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.05);
            border: 1px solid #eee;
        }
    </style>
    <script>
        function updateStatus(status) {
            fetch('/orders/{{ order_id }}/status', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({status: status})
            })
            .then(r => r.json())
            .then(d => {
                if(d.success) {
                    showNotification('Статус обновлен!', 'success');
                    setTimeout(() => location.reload(), 1000);
                } else {
                    showNotification('Ошибка: ' + (d.error || 'неизвестная ошибка'), 'error');
                }
            })
            .catch(error => {
                showNotification('Ошибка при обновлении статуса', 'error');
            });
        }
        
        function showNotification(message, type) {
            const notification = document.createElement('div');
            notification.style.cssText = `
                position: fixed;
                top: 20px;
                right: 20px;
                padding: 15px 25px;
                border-radius: 50px;
                background: ${type === 'success' ? '#28a745' : '#dc3545'};
                color: white;
                font-weight: 500;
                box-shadow: 0 5px 15px rgba(0,0,0,0.2);
                z-index: 9999;
                animation: slideIn 0.3s ease;
            `;
            notification.textContent = message;
            document.body.appendChild(notification);
            
            setTimeout(() => {
                notification.style.animation = 'slideOut 0.3s ease';
                setTimeout(() => notification.remove(), 300);
            }, 3000);
        }
    </script>
</head>
<body>
    <div class="container">
        <!-- Хедер -->
        <div class="header">
            <h1>
                <span>📁</span> {{ order_id }}
            </h1>
            <p>Информация о заказе и файлы</p>
            <div class="header-meta">
                <span class="meta-item">📅 {{ created }}</span>
                <span class="meta-item">📦 {{ file_count }} файлов</span>
                <span class="meta-item">💾 {{ total_size }}</span>
                <span class="meta-item">📌 {{ status }}</span>
            </div>
        </div>
        
        <!-- Навигация -->
        <div class="nav-links">
            <a href="/orders/" class="nav-btn">
                <span>←</span> К списку заказов
            </a>
            <a href="/" class="nav-btn">
                <span>🏠</span> Главная
            </a>
        </div>
        
        <!-- Основной контент -->
        <div class="content">
            <!-- Управление статусами -->
            <div class="status-section">
                <h3>
                    <span>📌</span> Управление статусом заказа
                </h3>
                <div class="status-buttons">
                    <button class="status-btn new" onclick="updateStatus('new')">🆕 Новый</button>
                    <button class="status-btn processing" onclick="updateStatus('processing')">🔄 В обработке</button>
                    <button class="status-btn printing" onclick="updateStatus('printing')">🖨️ В печати</button>
                    <button class="status-btn ready" onclick="updateStatus('ready')">✅ Готов</button>
                    <button class="status-btn shipped" onclick="updateStatus('shipped')">📦 Отправлен</button>
                    <button class="status-btn delivered" onclick="updateStatus('delivered')">🏁 Доставлен</button>
                    <button class="status-btn cancelled" onclick="updateStatus('cancelled')">❌ Отменен</button>
                </div>
            </div>
            
            <!-- Информация о заказе -->
            <div class="info-section">
                <h3>
                    <span>📋</span> Информация о заказе
                </h3>
                <div class="info-content">{{ info_text }}</div>
            </div>
            
            <!-- Фото -->
            {% if photos %}
            <h3 style="margin: 20px 0;">📸 Фото ({{ photos|length }})</h3>
            <div class="photo-gallery">
                {% for photo in photos %}
                <div class="photo-item">
                    <img src="{{ photo.url }}" class="photo-img" onclick="window.open('{{ photo.url }}', '_blank')">
                    <div class="photo-name">{{ photo.name }}</div>
                </div>
                {% endfor %}
            </div>
            {% endif %}
            
            <!-- Файлы -->
            <h3 style="margin: 20px 0;">📄 Файлы ({{ files|length }})</h3>
            <div class="files-grid">
                {% for file in files %}
                <a href="{{ file.url }}" class="file-card" download>
                    <div class="file-icon">{{ '📸' if file.is_photo else '📄' }}</div>
                    <div class="file-name">{{ file.name }}</div>
                    <div class="file-size">{{ file.size_formatted }}</div>
                </a>
                {% endfor %}
            </div>
            
            <!-- Кнопка скачивания -->
            <div style="text-align: center;">
                <a href="/orders/{{ order_id }}/download" class="download-all">
                    ⬇️ Скачать все файлы (ZIP)
                </a>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    """Главная страница с новым дизайном"""
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    return render_template_string(
        HOME_TEMPLATE,
        orders_count=orders_count,
        phone=CONTACT_PHONE,
        delivery=DELIVERY_OPTIONS,
        time=current_time
    )

@app.route('/orders/')
def list_orders():
    """Список заказов с новым дизайном"""
    try:
        orders = []
        total_files = 0
        
        if os.path.exists(ORDERS_PATH):
            for item in sorted(os.listdir(ORDERS_PATH), reverse=True):
                item_path = os.path.join(ORDERS_PATH, item)
                if os.path.isdir(item_path) and item != "orders_history.json":
                    # Получаем информацию о заказе
                    info_file = os.path.join(item_path, "информация_о_заказе.txt")
                    info_text = ""
                    if os.path.exists(info_file):
                        with open(info_file, 'r', encoding='utf-8') as f:
                            info_text = f.read()
                    
                    # Ищем статус в истории
                    status = "new"
                    history = load_orders_history()
                    for h in history:
                        if h.get('order_id') == item:
                            status = h.get('status', 'new')
                            break
                    
                    # Список файлов
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
                            total_files += 1
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
                    
                    # Получаем время создания
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
        
        return render_template_string(
            ORDERS_TEMPLATE,
            orders=orders,
            total_files=total_files
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_id>/')
def view_order(order_id):
    """Просмотр конкретного заказа с новым дизайном"""
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
        file_count = 0
        
        for f in sorted(os.listdir(order_path)):
            if f != "информация_о_заказе.txt":
                file_path = os.path.join(order_path, f)
                file_size = os.path.getsize(file_path)
                total_size += file_size
                file_count += 1
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
        
        created = datetime.fromtimestamp(os.path.getctime(order_path)).strftime('%d.%m.%Y %H:%M')
        
        return render_template_string(
            ORDER_DETAIL_TEMPLATE,
            order_id=order_id,
            created=created,
            file_count=file_count,
            total_size=format_file_size(total_size),
            status=get_status_display(status),
            info_text=info_text,
            photos=photos,
            files=files
        )
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
    return jsonify({
        "status": "ok", 
        "bot_ready": dispatcher is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/stats')
def stats():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    active_sessions = len(user_sessions)
    
    return jsonify({
        "status": "ok",
        "orders_count": orders_count,
        "active_sessions": active_sessions,
        "bot_ready": dispatcher is not None,
        "timestamp": datetime.now().isoformat()
    })

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📁 Папка для заказов: {ORDERS_PATH}")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")

bot = telegram.Bot(token=TOKEN)
updater = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

# ConversationHandler
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
