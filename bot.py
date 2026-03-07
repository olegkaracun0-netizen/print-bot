#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════════════╗
║                                                                                  ║
║   ░█▀▀░█▀█░█▀▄░█▀▀░█░█░░░█▀▀░█▀█░█▄█░█▀█░█░░░█▀▀░▀█▀░█▀▀░█░█░█▀▄░█▀▀░█▀▄      ║
║   ░█░░░█░█░█░█░█▀▀░▄▀▄░░░█░░░█░█░█░█░█▀▀░█░░░█▀▀░░█░░█▀▀░█▄█░█▀▄░█▀▀░█▀▄      ║
║   ░▀▀▀░▀▀▀░▀▀░░▀▀▀░▀░▀░░░▀▀▀░▀▀▀░▀░▀░▀░░░▀▀▀░▀▀▀░░▀░░▀▀▀░▀░▀░▀░▀░▀▀▀░▀░▀      ║
║                                                                                  ║
║                     ⚡ СУПЕР-ПРЕМИУМ БОТ ДЛЯ ПЕЧАТИ v4.20 ⚡                      ║
║                                                                                  ║
║                      ✦ 3D-АНИМАЦИИ ✦ НЕОНОВЫЙ ДИЗАЙН ✦                          ║
║                   ✦ КВАНТОВАЯ СТАТИСТИКА ✦ ГОЛОГРАФИЧЕСКИЙ UI ✦                 ║
║                                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
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
import random
import time
import hashlib
from datetime import datetime, timedelta
from functools import wraps
from collections import defaultdict
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template_string, abort, session, redirect, url_for, make_response

# Используем синхронную версию python-telegram-bot
import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaDocument, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, Filters

import PyPDF2
from docx import Document

# ========== НАСТРОЙКИ СУПЕР-ПРЕМИУМ ==========
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: TOKEN не задан в переменных окружения!")
    print("✨ Пожалуйста, добавьте TOKEN в переменные окружения Render.com")
    sys.exit(1)

# ID администратора для уведомлений
ADMIN_CHAT_ID = 483613049  # Ваш ID

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: RENDER_EXTERNAL_URL не задан!")
    print("✨ Пожалуйста, добавьте RENDER_EXTERNAL_URL в переменные окружения Render.com")
    sys.exit(1)

PORT = int(os.environ.get("PORT", 10000))
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "🚀 Самовывоз СПб | 📦 СДЭК | 🚚 Яндекс Доставка | ✈️ Почта России"

# Секретный ключ для Flask сессий
SECRET_KEY = os.environ.get("SECRET_KEY", hashlib.sha256(str(random.getrandbits(256)).encode()).hexdigest())

# ========== ПУТЬ К ПАПКЕ ЗАКАЗОВ ==========
ORDERS_FOLDER = "заказы"
ORDERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ORDERS_FOLDER)

# Создаем папку для заказов
try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"📁 Папка заказов создана: {ORDERS_PATH}")
except Exception as e:
    print(f"❌ Ошибка создания папки: {e}")
    sys.exit(1)

# ========== ФАЙЛ ДЛЯ ХРАНЕНИЯ ИСТОРИИ ЗАКАЗОВ ==========
ORDERS_DB_FILE = os.path.join(ORDERS_PATH, "orders_history.json")

# ========== НЕОНОВЫЕ СТАТУСЫ ЗАКАЗОВ ==========
ORDER_STATUSES = {
    "new": "🆕✨ НОВЫЙ",
    "processing": "🔄⚡ В ОБРАБОТКЕ",
    "printing": "🖨️🔥 В ПЕЧАТИ",
    "ready": "✅💫 ГОТОВ",
    "shipped": "📦🚀 ОТПРАВЛЕН",
    "delivered": "🏁🎯 ДОСТАВЛЕН",
    "cancelled": "❌💔 ОТМЕНЕН"
}

# ========== ЦВЕТА ДЛЯ СТАТУСОВ (HEX) ==========
STATUS_COLORS = {
    "new": "#4CAF50",
    "processing": "#FF9800",
    "printing": "#2196F3",
    "ready": "#9C27B0",
    "shipped": "#3F51B5",
    "delivered": "#009688",
    "cancelled": "#f44336"
}

def get_status_display(status):
    """Возвращает отображение статуса с неоном"""
    return ORDER_STATUSES.get(status, status)

def get_status_color(status):
    """Возвращает цвет статуса"""
    return STATUS_COLORS.get(status, "#666666")

# ========== ЗАГРУЗКА/СОХРАНЕНИЕ ИСТОРИИ ==========
def load_orders_history():
    """Загружает историю заказов из JSON файла"""
    try:
        if os.path.exists(ORDERS_DB_FILE):
            with open(ORDERS_DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки истории: {e}")
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
        logger.error(f"❌ Ошибка сохранения истории: {e}")
        return False

def update_order_status(order_id, new_status):
    """Обновляет статус заказа с отправкой уведомления"""
    try:
        history = load_orders_history()
        updated = False
        user_id = None
        
        for order in history:
            if order.get('order_id') == order_id:
                old_status = order.get('status', 'new')
                order['status'] = new_status
                order['status_updated'] = datetime.now().isoformat()
                user_id = order.get('user_id')
                updated = True
                break
        
        if updated:
            with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, ensure_ascii=False, indent=2)
            
            # Обновляем файл информации в папке заказа
            order_folder = os.path.join(ORDERS_PATH, order_id)
            info_file = os.path.join(order_folder, "💎_ИНФОРМАЦИЯ_О_ЗАКАЗЕ.txt")
            if os.path.exists(info_file):
                with open(info_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Заменяем статус в файле
                content = re.sub(r'Статус:.*\n', f'Статус: {get_status_display(new_status)}\n', content)
                
                with open(info_file, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Отправляем уведомление клиенту с супер-дизайном
            if user_id and bot:
                try:
                    status_emoji = new_status[:2] if len(new_status) > 2 else "📢"
                    status_text = get_status_display(new_status)
                    
                    # Красивое уведомление с анимацией в тексте
                    notification = (
                        "╔══════════════════════════════════════════════╗\n"
                        f"║     {status_emoji}  СТАТУС ЗАКАЗА ИЗМЕНЕН  {status_emoji}    ║\n"
                        "╚══════════════════════════════════════════════╝\n\n"
                        f"🆔 **Номер заказа:** `{order_id}`\n"
                        f"📌 **Новый статус:** {status_text}\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "✨ Спасибо, что пользуетесь нашим сервисом! ✨\n\n"
                        f"📞 Контакты: `{CONTACT_PHONE}`"
                    )
                    
                    bot.send_message(
                        chat_id=user_id,
                        text=notification,
                        parse_mode="Markdown"
                    )
                    logger.info(f"✅ Уведомление отправлено пользователю {user_id}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки уведомления: {e}")
            
            return True
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка обновления статуса: {e}")
        return False

# ========== ЛОГИРОВАНИЕ С ЦВЕТАМИ ==========
class ColoredFormatter(logging.Formatter):
    """Кастомный форматтер с цветами для логов"""
    
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    green = "\x1b[32;20m"
    cyan = "\x1b[36;20m"
    purple = "\x1b[35;20m"
    reset = "\x1b[0m"
    
    format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)"
    
    FORMATS = {
        logging.DEBUG: purple + format + reset,
        logging.INFO: cyan + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset
    }
    
    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

# Настраиваем логирование
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Создаем обработчик для stdout
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(ColoredFormatter())
logger.addHandler(console_handler)

# ========== СОСТОЯНИЯ КОНВЕРСАЦИИ ==========
(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
    WAITING_FOR_COMMENT
) = range(6)

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
user_sessions = {}
media_groups = {}
group_timers = {}
user_stats = defaultdict(lambda: {"orders": 0, "photos": 0, "pages": 0, "spent": 0})
updater = None
dispatcher = None
bot = None

# ========== ЦЕНЫ С СУПЕР-СКИДКАМИ ==========
PHOTO_PRICES = {
    "small": {(1, 9): 35, (10, 50): 28, (51, 100): 23, (101, float("inf")): 18},
    "medium": {(1, 9): 65, (10, 50): 55, (51, 100): 45, (101, float("inf")): 35},
    "large": {(1, 4): 200, (5, 20): 170, (21, 50): 150, (51, float("inf")): 120},
}

DOC_PRICES = {
    "bw": {(1, 20): 25, (21, 100): 18, (101, 300): 14, (301, float("inf")): 10},
    "color": {(1, 20): 50, (21, 100): 35, (101, 300): 25, (301, float("inf")): 20},
}

# Специальные предложения
SPECIAL_OFFERS = {
    "first_order": 0.9,  # Скидка 10% на первый заказ
    "bulk_50": 0.85,      # Скидка 15% при заказе от 50 копий
    "bulk_100": 0.8,      # Скидка 20% при заказе от 100 копий
    "loyalty": 0.95       # Скидка 5% для постоянных клиентов
}

# ========== СУПЕР-ПРЕМИУМ ФУНКЦИИ ДЛЯ ФОРМАТИРОВАНИЯ ==========

def format_file_size(size_bytes):
    """Форматирует размер файла в человеко-читаемый вид с эмодзи"""
    if size_bytes < 1024:
        return f"📦 {size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"📦 {size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"📀 {size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"💿 {size_bytes / (1024 * 1024 * 1024):.1f} GB"

def create_neon_header(title, emoji, width=50):
    """Создает неоновый заголовок с рамкой"""
    title_len = len(title) + 4  # +4 для эмодзи и пробелов
    side_len = (width - title_len) // 2
    
    return (
        "╔" + "═" * width + "╗\n"
        "║" + " " * side_len + f"{emoji}  {title.upper()}  {emoji}" + " " * (width - side_len - title_len) + "║\n"
        "╚" + "═" * width + "╝"
    )

def create_glowing_text(text, intensity=1):
    """Создает светящийся текст с разной интенсивностью"""
    glow_chars = ["░", "▒", "▓", "█"]
    if intensity <= 1:
        return f"✨ **{text}** ✨"
    else:
        glow = glow_chars[min(intensity, 3)]
        return f"{glow * 3} **{text}** {glow * 3}"

def create_progress_bar(percent, width=20):
    """Создает красивый прогресс-бар"""
    filled = int(width * percent / 100)
    empty = width - filled
    return "▓" * filled + "░" * empty + f" {percent}%"

def calculate_price_with_discounts(price_dict, quantity, user_id, is_first_order=False):
    """Рассчитывает стоимость со всеми скидками"""
    base_price = 0
    for (min_q, max_q), price in price_dict.items():
        if min_q <= quantity <= max_q:
            base_price = price * quantity
            break
    
    # Применяем скидки
    discount = 1.0
    
    if is_first_order:
        discount *= SPECIAL_OFFERS["first_order"]
    
    if quantity >= 100:
        discount *= SPECIAL_OFFERS["bulk_100"]
    elif quantity >= 50:
        discount *= SPECIAL_OFFERS["bulk_50"]
    
    # Скидка для постоянных клиентов (больше 5 заказов)
    if user_stats[user_id]["orders"] > 5:
        discount *= SPECIAL_OFFERS["loyalty"]
    
    final_price = int(base_price * discount)
    saved = base_price - final_price
    
    return final_price, saved, int(discount * 100)

def count_items_in_file(file_path, file_name):
    """Подсчет количества в файле с расширенной аналитикой"""
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                page_count = len(pdf.pages)
                
                # Дополнительная информация о PDF
                metadata = pdf.metadata if hasattr(pdf, 'metadata') else {}
                info = {
                    "pages": page_count,
                    "encrypted": pdf.is_encrypted if hasattr(pdf, 'is_encrypted') else False,
                    "metadata": metadata
                }
                logger.info(f"📄 PDF анализ: {file_name} - {page_count} стр., метаданные: {metadata}")
                return page_count, "страниц", "документ", info
                
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            
            # Подробный анализ Word документа
            paragraphs = len(doc.paragraphs)
            tables = len(doc.tables)
            sections = len(doc.sections)
            
            # Оценка количества страниц (примерно 350 слов на страницу)
            word_count = sum(len(p.text.split()) for p in doc.paragraphs)
            estimated_pages = max(1, word_count // 350)
            
            info = {
                "pages": estimated_pages,
                "paragraphs": paragraphs,
                "tables": tables,
                "sections": sections,
                "words": word_count
            }
            
            logger.info(f"📄 Word анализ: {file_name} - {word_count} слов, {estimated_pages} стр.")
            return estimated_pages, "страниц", "документ", info
            
        elif file_name.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.bmp')):
            # Анализ изображения
            import PIL.Image
            try:
                with PIL.Image.open(file_path) as img:
                    width, height = img.size
                    mode = img.mode
                    format_type = img.format
                    
                    info = {
                        "width": width,
                        "height": height,
                        "mode": mode,
                        "format": format_type,
                        "megapixels": round(width * height / 1_000_000, 2)
                    }
                    
                    logger.info(f"📸 Фото анализ: {file_name} - {width}x{height}, {info['megapixels']} МП")
                    return 1, "фото", "фото", info
            except:
                return 1, "фото", "фото", {}
            
        return 1, "единиц", "неизвестно", {}
    except Exception as e:
        logger.error(f"❌ Ошибка подсчета: {e}")
        return 1, "единиц", "неизвестно", {}

def download_file(file_obj, file_name):
    """Скачивает файл во временную папку с прогресс-баром"""
    try:
        temp_dir = tempfile.mkdtemp()
        file_path = os.path.join(temp_dir, file_name)
        
        # Определяем размер файла для прогресса
        file_size = file_obj.file_size if hasattr(file_obj, 'file_size') else 0
        
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
        
        # Проверяем размер скачанного файла
        actual_size = os.path.getsize(file_path)
        size_str = format_file_size(actual_size)
        
        logger.info(f"✅ Файл скачан: {file_name} ({size_str})")
        return file_path, temp_dir
    except Exception as e:
        logger.error(f"❌ Ошибка скачивания: {e}")
        return None, None

def save_order_to_folder(user_id, username, order_data, files_info):
    """Сохраняет заказ в папку на сервере с премиум-структурой"""
    try:
        # Создаем уникальную папку для заказа с красивым названием
        clean_name = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_id = f"{clean_name}_{timestamp}_{random.randint(100, 999)}"
        order_folder = os.path.join(ORDERS_PATH, order_id)
        os.makedirs(order_folder, exist_ok=True)
        
        # Создаем подпапки для организации
        photos_folder = os.path.join(order_folder, "📸 ФОТО")
        docs_folder = os.path.join(order_folder, "📄 ДОКУМЕНТЫ")
        preview_folder = os.path.join(order_folder, "👀 ПРЕВЬЮ")
        
        os.makedirs(photos_folder, exist_ok=True)
        os.makedirs(docs_folder, exist_ok=True)
        os.makedirs(preview_folder, exist_ok=True)
        
        logger.info(f"📁 Создана структура заказа: {order_folder}")
        
        saved_files = []
        file_details = []
        
        for i, f in enumerate(files_info, 1):
            if os.path.exists(f['path']):
                # Очищаем имя файла
                safe_name = re.sub(r'[<>:"/\\|?*]', '', f['name'])
                
                # Определяем целевую папку
                if f['type'] == 'photo':
                    target_folder = photos_folder
                else:
                    target_folder = docs_folder
                
                # Копируем файл
                new_path = os.path.join(target_folder, f"{i:02d}_{safe_name}")
                shutil.copy2(f['path'], new_path)
                saved_files.append(new_path)
                
                # Создаем превью для фото
                if f['type'] == 'photo' and f['name'].lower().endswith(('.jpg', '.jpeg', '.png')):
                    try:
                        import PIL.Image
                        with PIL.Image.open(f['path']) as img:
                            # Создаем миниатюру
                            img.thumbnail((300, 300))
                            preview_path = os.path.join(preview_folder, f"preview_{i:02d}_{safe_name}")
                            img.save(preview_path)
                    except:
                        pass
                
                # Собираем детали файла
                file_details.append({
                    "name": safe_name,
                    "type": f['type'],
                    "items": f['items'],
                    "path": new_path,
                    "size": os.path.getsize(new_path),
                    "size_str": format_file_size(os.path.getsize(new_path))
                })
                
                logger.info(f"📄 Файл {i} сохранен: {safe_name}")
            else:
                logger.error(f"❌ Файл не найден: {f['path']}")
        
        # Подсчитываем статистику
        photo_files = [ff for ff in files_info if ff['type'] == 'photo']
        doc_files = [ff for ff in files_info if ff['type'] == 'doc']
        
        total_photos_original = sum(ff['items'] for ff in photo_files)
        total_pages_original = sum(ff['items'] for ff in doc_files)
        total_photos_print = total_photos_original * order_data['quantity']
        total_pages_print = total_pages_original * order_data['quantity']
        
        # Сохраняем информацию о заказе в красивом формате
        info_file = os.path.join(order_folder, "💎_ИНФОРМАЦИЯ_О_ЗАКАЗЕ.txt")
        with open(info_file, 'w', encoding='utf-8') as f:
            f.write("╔══════════════════════════════════════════════════════════════╗\n")
            f.write("║              💎 ИНФОРМАЦИЯ О ЗАКАЗЕ 💎                       ║\n")
            f.write("╚══════════════════════════════════════════════════════════════╝\n\n")
            
            f.write(f"📅 ДАТА: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write(f"🆔 НОМЕР: {order_id}\n")
            f.write(f"{'='*60}\n\n")
            
            f.write("👤 КЛИЕНТ:\n")
            f.write(f"  • Имя: {order_data['user_info']['first_name']}\n")
            f.write(f"  • Username: @{username}\n")
            f.write(f"  • ID: {user_id}\n")
            f.write(f"  • Телефон: {CONTACT_PHONE}\n\n")
            
            f.write("📌 СТАТУС:\n")
            f.write(f"  • {get_status_display('new')}\n\n")
            
            f.write("🖨️ ПАРАМЕТРЫ ПЕЧАТИ:\n")
            if order_data['type'] == 'photo':
                format_names = {"small": "🖼️ Малый (A6/10x15)", "medium": "🖼️ Средний (13x18/15x21)", "large": "🖼️ Большой (A4/21x30)"}
                f.write(f"  • Тип: 📸 Фотографии\n")
                f.write(f"  • Формат: {format_names[order_data['format']]}\n")
            else:
                color_names = {"bw": "⚫ Черно-белая", "color": "🎨 Цветная"}
                f.write(f"  • Тип: 📄 Документы\n")
                f.write(f"  • Печать: {color_names[order_data['color']]}\n")
            
            f.write(f"  • Количество копий: {order_data['quantity']}\n")
            f.write(f"  • Скидка: {order_data.get('discount_percent', 0)}%\n")
            f.write(f"  • Экономия: {order_data.get('saved', 0)} руб.\n\n")
            
            f.write("📊 ДЕТАЛЬНАЯ СТАТИСТИКА:\n")
            if photo_files:
                f.write(f"  📸 ФОТО:\n")
                f.write(f"    • Файлов: {len(photo_files)}\n")
                f.write(f"    • Оригиналов: {total_photos_original}\n")
                f.write(f"    • К печати: {total_photos_print}\n")
            
            if doc_files:
                f.write(f"  📄 ДОКУМЕНТЫ:\n")
                f.write(f"    • Файлов: {len(doc_files)}\n")
                f.write(f"    • Страниц: {total_pages_original}\n")
                f.write(f"    • К печати: {total_pages_print}\n")
            
            f.write(f"\n💰 ФИНАНСЫ:\n")
            f.write(f"  • Сумма: {order_data['total']} руб.\n")
            f.write(f"  • Способ оплаты: {order_data.get('payment_method', 'Не указан')}\n\n")
            
            f.write(f"⏳ СРОК ВЫПОЛНЕНИЯ: {order_data['delivery']}\n")
            f.write(f"🚚 ДОСТАВКА: {order_data.get('delivery_method', DELIVERY_OPTIONS)}\n")
            f.write(f"📝 КОММЕНТАРИЙ: {order_data.get('comment', 'Нет')}\n\n")
            
            f.write("📁 ФАЙЛЫ:\n")
            for i, detail in enumerate(file_details, 1):
                icon = "📸" if detail['type'] == 'photo' else "📄"
                unit = "фото" if detail['type'] == 'photo' else "стр."
                f.write(f"  {icon} {i:02d}. {detail['name']}\n")
                f.write(f"     • {detail['items']} {unit}\n")
                f.write(f"     • {detail['size_str']}\n")
        
        # Сохраняем JSON версию для API
        json_file = os.path.join(order_folder, "order_data.json")
        json_data = {
            "order_id": order_id,
            "user_id": user_id,
            "username": username,
            "user_name": order_data['user_info']['first_name'],
            "date": datetime.now().isoformat(),
            "type": order_data['type'],
            "quantity": order_data['quantity'],
            "total_photos": total_photos_original,
            "total_pages": total_pages_original,
            "total_photos_print": total_photos_print,
            "total_pages_print": total_pages_print,
            "total_price": order_data['total'],
            "original_price": order_data.get('original_price', order_data['total']),
            "saved": order_data.get('saved', 0),
            "discount_percent": order_data.get('discount_percent', 0),
            "delivery": order_data['delivery'],
            "status": "new",
            "files": file_details,
            "payment_method": order_data.get('payment_method', ''),
            "delivery_method": order_data.get('delivery_method', ''),
            "comment": order_data.get('comment', '')
        }
        
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"📝 Информация о заказе сохранена")
        
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
            "total_photos": total_photos_original,
            "total_pages": total_pages_original,
            "total_photos_print": total_photos_print,
            "total_pages_print": total_pages_print,
            "total_price": order_data['total'],
            "original_price": order_data.get('original_price', order_data['total']),
            "saved": order_data.get('saved', 0),
            "discount_percent": order_data.get('discount_percent', 0),
            "delivery": order_data['delivery'],
            "status": "new"
        }
        save_order_to_history(history_entry)
        
        # Обновляем статистику пользователя
        user_stats[user_id]["orders"] += 1
        user_stats[user_id]["photos"] += total_photos_print
        user_stats[user_id]["pages"] += total_pages_print
        user_stats[user_id]["spent"] += order_data['total']
        
        return True, order_id, order_folder
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения: {e}")
        logger.error(traceback.format_exc())
        return False, None, None

def send_admin_notification(order_data, order_id, order_folder):
    """Отправляет уведомление админу о новом заказе с супер-дизайном"""
    try:
        order_url = f"{RENDER_URL}/orders/{order_id}/"
        
        # Подсчитываем статистику
        photo_files = [f for f in order_data['files'] if f['type'] == 'photo']
        doc_files = [f for f in order_data['files'] if f['type'] == 'doc']
        
        total_photos = sum(f['items'] for f in photo_files)
        total_pages = sum(f['items'] for f in doc_files)
        total_photos_print = total_photos * order_data['quantity']
        total_pages_print = total_pages * order_data['quantity']
        
        # Супер-премиум уведомление для админа
        admin_message = (
            "╔══════════════════════════════════════════════╗\n"
            "║     🚨  НОВЫЙ ЗАКАЗ ПОСТУПИЛ!  🚨          ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            f"🆔 **Номер:** `{order_id}`\n"
            f"👤 **Клиент:** {order_data['user_info']['first_name']} (@{order_data['user_info']['username']})\n"
            f"🆔 **ID:** `{order_data['user_info']['user_id']}`\n\n"
        )
        
        if order_data['type'] == 'photo':
            format_names = {"small": "🖼️ Малый", "medium": "🖼️ Средний", "large": "🖼️ Большой"}
            admin_message += f"📸 **Тип:** Фотографии\n"
            admin_message += f"📸 **Формат:** {format_names[order_data['format']]}\n"
        else:
            color_names = {"bw": "⚫ Ч/Б", "color": "🎨 Цветная"}
            admin_message += f"📄 **Тип:** Документы\n"
            admin_message += f"📄 **Печать:** {color_names[order_data['color']]}\n"
        
        admin_message += (
            f"📦 **Копий:** {order_data['quantity']}\n"
            f"📁 **Файлов:** {len(order_data['files'])}\n\n"
        )
        
        if photo_files:
            admin_message += f"📸 **Фото:** {len(photo_files)} файлов, {total_photos} оригиналов → {total_photos_print} к печати\n"
        if doc_files:
            admin_message += f"📄 **Документы:** {len(doc_files)} файлов, {total_pages} страниц → {total_pages_print} к печати\n"
        
        admin_message += (
            f"\n💰 **Сумма:** {order_data['total']} руб.\n"
            f"⏳ **Срок:** {order_data['delivery']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔗 **Ссылка на заказ:**\n{order_url}"
        )
        
        # Отправляем админу
        if bot:
            bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_message,
                parse_mode="Markdown"
            )
            logger.info(f"✅ Уведомление отправлено админу {ADMIN_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"❌ Ошибка отправки уведомления админу: {e}")

# ========== ИНИЦИАЛИЗАЦИЯ FLASK С СУПЕР-ДИЗАЙНОМ ==========
app = Flask(__name__)
app.secret_key = SECRET_KEY

# ========== СУПЕР-ПУПЕР МЕГА КРУТОЙ CSS ==========
PREMIUM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700;800;900&family=Rajdhani:wght@300;400;500;600;700&display=swap');
    @import url('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css');
    
    * {
        margin: 0;
        padding: 0;
        box-sizing: border-box;
    }
    
    body {
        font-family: 'Rajdhani', sans-serif;
        background: #0a0f1f;
        min-height: 100vh;
        position: relative;
        overflow-x: hidden;
    }
    
    /* Анимированный фон с частицами */
    #particles-js {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        z-index: -1;
        background: linear-gradient(135deg, #0a0f1f 0%, #1a1f3f 50%, #0a0f1f 100%);
    }
    
    /* Стеклянный эффект с неоновой подсветкой */
    .glass-card {
        background: rgba(10, 15, 31, 0.7);
        backdrop-filter: blur(20px);
        -webkit-backdrop-filter: blur(20px);
        border: 1px solid rgba(0, 255, 255, 0.2);
        border-radius: 30px;
        box-shadow: 
            0 30px 60px -20px rgba(0, 0, 0, 0.8),
            inset 0 0 30px rgba(0, 255, 255, 0.1),
            0 0 0 2px rgba(0, 255, 255, 0.05);
        transition: all 0.5s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    
    .glass-card::before {
        content: '';
        position: absolute;
        top: -50%;
        left: -50%;
        width: 200%;
        height: 200%;
        background: radial-gradient(circle, rgba(0, 255, 255, 0.1) 0%, transparent 70%);
        animation: rotate 20s linear infinite;
        opacity: 0.5;
    }
    
    .glass-card:hover {
        border-color: rgba(0, 255, 255, 0.5);
        box-shadow: 
            0 40px 80px -20px rgba(0, 0, 0, 0.9),
            inset 0 0 50px rgba(0, 255, 255, 0.2),
            0 0 0 3px rgba(0, 255, 255, 0.1);
        transform: translateY(-10px) scale(1.02);
    }
    
    /* Неоновый текст с эффектом мерцания */
    .neon-text {
        font-family: 'Orbitron', sans-serif;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 4px;
        color: #fff;
        text-shadow: 
            0 0 10px #0ff,
            0 0 20px #0ff,
            0 0 40px #0ff,
            0 0 80px #0ff,
            0 0 120px #0ff;
        animation: neonPulse 2s ease-in-out infinite;
    }
    
    @keyframes neonPulse {
        0%, 100% {
            text-shadow: 
                0 0 10px #0ff,
                0 0 20px #0ff,
                0 0 40px #0ff,
                0 0 80px #0ff;
        }
        50% {
            text-shadow: 
                0 0 20px #f0f,
                0 0 40px #f0f,
                0 0 80px #f0f,
                0 0 160px #f0f;
        }
    }
    
    /* Голографические кнопки */
    .hologram-btn {
        background: linear-gradient(135deg, rgba(0, 255, 255, 0.2), rgba(255, 0, 255, 0.2));
        border: 1px solid rgba(255, 255, 255, 0.3);
        color: white;
        padding: 15px 40px;
        font-size: 1.2em;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 2px;
        border-radius: 50px;
        cursor: pointer;
        position: relative;
        overflow: hidden;
        transition: all 0.3s;
        backdrop-filter: blur(10px);
        text-decoration: none;
        display: inline-block;
        box-shadow: 0 0 20px rgba(0, 255, 255, 0.3);
    }
    
    .hologram-btn::before {
        content: '';
        position: absolute;
        top: -50%;
        left: -50%;
        width: 200%;
        height: 200%;
        background: linear-gradient(
            45deg,
            transparent,
            rgba(255, 255, 255, 0.3),
            transparent
        );
        transform: rotate(45deg);
        animation: hologramShine 3s ease-in-out infinite;
    }
    
    @keyframes hologramShine {
        0% {
            transform: translateX(-100%) rotate(45deg);
        }
        20%, 100% {
            transform: translateX(100%) rotate(45deg);
        }
    }
    
    .hologram-btn:hover {
        transform: scale(1.1);
        box-shadow: 0 0 40px rgba(0, 255, 255, 0.5);
        border-color: rgba(255, 255, 255, 0.8);
    }
    
    /* 3D карточки с вращением */
    .card-3d {
        transform-style: preserve-3d;
        perspective: 1000px;
    }
    
    .card-3d-content {
        transition: transform 0.5s;
        transform: rotateX(0deg) rotateY(0deg);
    }
    
    .card-3d:hover .card-3d-content {
        transform: rotateX(5deg) rotateY(5deg);
    }
    
    /* Статус бейджи с неоном */
    .status-badge {
        display: inline-block;
        padding: 8px 25px;
        border-radius: 30px;
        font-weight: 600;
        font-size: 0.9em;
        text-transform: uppercase;
        letter-spacing: 1px;
        position: relative;
        overflow: hidden;
        color: white;
    }
    
    .status-badge::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.3), transparent);
        animation: shine 2s infinite;
    }
    
    @keyframes shine {
        to {
            left: 100%;
        }
    }
    
    .status-new { background: #4CAF50; box-shadow: 0 0 20px #4CAF50; }
    .status-processing { background: #FF9800; box-shadow: 0 0 20px #FF9800; }
    .status-printing { background: #2196F3; box-shadow: 0 0 20px #2196F3; }
    .status-ready { background: #9C27B0; box-shadow: 0 0 20px #9C27B0; }
    .status-shipped { background: #3F51B5; box-shadow: 0 0 20px #3F51B5; }
    .status-delivered { background: #009688; box-shadow: 0 0 20px #009688; }
    .status-cancelled { background: #f44336; box-shadow: 0 0 20px #f44336; }
    
    /* Анимированные иконки */
    .animated-icon {
        animation: float 3s ease-in-out infinite;
    }
    
    @keyframes float {
        0%, 100% { transform: translateY(0); }
        50% { transform: translateY(-20px); }
    }
    
    /* Счетчики с эффектом */
    .counter {
        font-family: 'Orbitron', monospace;
        font-size: 3em;
        font-weight: 800;
        background: linear-gradient(45deg, #0ff, #f0f);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-shadow: 0 0 30px rgba(0, 255, 255, 0.5);
    }
    
    /* Прогресс-бар с неоном */
    .neon-progress {
        height: 10px;
        background: rgba(255, 255, 255, 0.1);
        border-radius: 5px;
        overflow: hidden;
        position: relative;
    }
    
    .neon-progress-fill {
        height: 100%;
        background: linear-gradient(90deg, #0ff, #f0f);
        border-radius: 5px;
        position: relative;
        animation: progressPulse 2s ease-in-out infinite;
    }
    
    @keyframes progressPulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.7; }
    }
    
    /* Сетка для фото с 3D эффектом */
    .photo-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 25px;
        padding: 20px;
    }
    
    .photo-item {
        position: relative;
        border-radius: 20px;
        overflow: hidden;
        transform: translateZ(0);
        transition: all 0.5s;
        box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.5);
    }
    
    .photo-item:hover {
        transform: translateZ(50px) scale(1.1);
        box-shadow: 0 30px 60px -10px rgba(0, 255, 255, 0.3);
        z-index: 100;
    }
    
    .photo-item img {
        width: 100%;
        height: 200px;
        object-fit: cover;
        transition: all 0.5s;
    }
    
    .photo-item:hover img {
        transform: scale(1.2);
    }
    
    .photo-overlay {
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        background: linear-gradient(to top, rgba(0,0,0,0.8), transparent);
        color: white;
        padding: 20px;
        transform: translateY(100%);
        transition: transform 0.3s;
    }
    
    .photo-item:hover .photo-overlay {
        transform: translateY(0);
    }
    
    /* Матричный дождь для фона */
    #matrix-rain {
        position: fixed;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        pointer-events: none;
        z-index: -1;
        opacity: 0.3;
    }
    
    /* Контейнер с параллаксом */
    .parallax-container {
        perspective: 1px;
        height: 100vh;
        overflow-x: hidden;
        overflow-y: auto;
    }
    
    .parallax-layer {
        position: absolute;
        top: 0;
        right: 0;
        bottom: 0;
        left: 0;
    }
    
    .parallax-layer-base {
        transform: translateZ(0);
    }
    
    .parallax-layer-back {
        transform: translateZ(-1px) scale(2);
    }
    
    /* Неоновая линия */
    .neon-line {
        height: 2px;
        background: linear-gradient(90deg, transparent, #0ff, #f0f, #0ff, transparent);
        margin: 30px 0;
        animation: neonLinePulse 3s ease-in-out infinite;
    }
    
    @keyframes neonLinePulse {
        0%, 100% { opacity: 0.5; }
        50% { opacity: 1; }
    }
    
    /* Загрузчик с квантовым эффектом */
    .quantum-loader {
        width: 100px;
        height: 100px;
        border-radius: 50%;
        background: conic-gradient(#0ff, #f0f, #0ff);
        animation: quantumSpin 1s linear infinite;
        position: relative;
    }
    
    .quantum-loader::before {
        content: '';
        position: absolute;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        width: 80px;
        height: 80px;
        background: #0a0f1f;
        border-radius: 50%;
    }
    
    @keyframes quantumSpin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    
    /* Адаптивность */
    @media (max-width: 768px) {
        .neon-text {
            font-size: 1.5em;
            letter-spacing: 2px;
        }
        
        .hologram-btn {
            padding: 10px 20px;
            font-size: 1em;
        }
        
        .photo-grid {
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        }
    }
</style>
"""

# ========== СУПЕР-ПУПЕР МЕГА КРУТОЙ JAVASCRIPT ==========
PREMIUM_JS = """
<script src="https://cdn.jsdelivr.net/particles.js/2.0.0/particles.min.js"></script>
<script>
    // Инициализация частиц
    particlesJS('particles-js', {
        particles: {
            number: {
                value: 100,
                density: {
                    enable: true,
                    value_area: 800
                }
            },
            color: {
                value: ['#0ff', '#f0f', '#ff0']
            },
            shape: {
                type: 'circle'
            },
            opacity: {
                value: 0.5,
                random: true,
                anim: {
                    enable: true,
                    speed: 1,
                    opacity_min: 0.1,
                    sync: false
                }
            },
            size: {
                value: 3,
                random: true,
                anim: {
                    enable: true,
                    speed: 2,
                    size_min: 0.1,
                    sync: false
                }
            },
            line_linked: {
                enable: true,
                distance: 150,
                color: '#0ff',
                opacity: 0.2,
                width: 1
            },
            move: {
                enable: true,
                speed: 2,
                direction: 'none',
                random: true,
                straight: false,
                out_mode: 'out',
                bounce: false,
                attract: {
                    enable: true,
                    rotateX: 600,
                    rotateY: 1200
                }
            }
        },
        interactivity: {
            detect_on: 'canvas',
            events: {
                onhover: {
                    enable: true,
                    mode: 'grab'
                },
                onclick: {
                    enable: true,
                    mode: 'push'
                },
                resize: true
            },
            modes: {
                grab: {
                    distance: 200,
                    line_linked: {
                        opacity: 0.5
                    }
                },
                push: {
                    particles_nb: 4
                }
            }
        },
        retina_detect: true
    });

    // Матричный дождь
    function createMatrixRain() {
        const canvas = document.createElement('canvas');
        canvas.id = 'matrix-rain';
        document.body.appendChild(canvas);
        
        const ctx = canvas.getContext('2d');
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
        
        const chars = '01アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン';
        const fontSize = 14;
        const columns = canvas.width / fontSize;
        const drops = [];
        
        for (let i = 0; i < columns; i++) {
            drops[i] = 1;
        }
        
        function draw() {
            ctx.fillStyle = 'rgba(10, 15, 31, 0.05)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            
            ctx.fillStyle = '#0f0';
            ctx.font = fontSize + 'px monospace';
            
            for (let i = 0; i < drops.length; i++) {
                const text = chars.charAt(Math.floor(Math.random() * chars.length));
                ctx.fillText(text, i * fontSize, drops[i] * fontSize);
                
                if (drops[i] * fontSize > canvas.height && Math.random() > 0.975) {
                    drops[i] = 0;
                }
                drops[i]++;
            }
        }
        
        setInterval(draw, 35);
    }
    
    // Запускаем матрицу с задержкой
    setTimeout(createMatrixRain, 1000);
    
    // 3D параллакс для карточек
    document.querySelectorAll('.card-3d').forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            
            const rotateX = (y - centerY) / 20;
            const rotateY = (centerX - x) / 20;
            
            const content = card.querySelector('.card-3d-content');
            if (content) {
                content.style.transform = `rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
            }
        });
        
        card.addEventListener('mouseleave', () => {
            const content = card.querySelector('.card-3d-content');
            if (content) {
                content.style.transform = 'rotateX(0deg) rotateY(0deg)';
            }
        });
    });
    
    // Анимация счетчиков
    function animateCounter(element, target, duration = 2000) {
        const start = 0;
        const increment = target / (duration / 16);
        let current = start;
        
        function update() {
            current += increment;
            if (current < target) {
                element.textContent = Math.floor(current);
                requestAnimationFrame(update);
            } else {
                element.textContent = target;
            }
        }
        
        update();
    }
    
    document.querySelectorAll('.counter').forEach(counter => {
        const target = parseInt(counter.textContent);
        animateCounter(counter, target);
    });
    
    // Эффект печатной машинки для заголовков
    function typeWriter(element, text, speed = 50) {
        let i = 0;
        element.innerHTML = '';
        
        function type() {
            if (i < text.length) {
                element.innerHTML += text.charAt(i);
                i++;
                setTimeout(type, speed);
            }
        }
        
        type();
    }
    
    // Добавляем эффект при наведении на кнопки
    document.querySelectorAll('.hologram-btn').forEach(btn => {
        btn.addEventListener('mouseenter', (e) => {
            const rect = btn.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            
            const ripple = document.createElement('span');
            ripple.style.position = 'absolute';
            ripple.style.left = x + 'px';
            ripple.style.top = y + 'px';
            ripple.style.width = '0';
            ripple.style.height = '0';
            ripple.style.borderRadius = '50%';
            ripple.style.background = 'rgba(255, 255, 255, 0.3)';
            ripple.style.transform = 'translate(-50%, -50%)';
            ripple.style.animation = 'ripple 1s ease-out';
            
            btn.appendChild(ripple);
            
            setTimeout(() => {
                ripple.remove();
            }, 1000);
        });
    });
    
    // Добавляем стили для ripple эффекта
    const style = document.createElement('style');
    style.textContent = `
        @keyframes ripple {
            to {
                width: 200px;
                height: 200px;
                opacity: 0;
            }
        }
    `;
    document.head.appendChild(style);
    
    // Консольное приветствие
    console.log('%c🚀 PRINT BOT PREMIUM v4.20 🚀', 'font-size: 20px; color: #0ff; text-shadow: 0 0 10px #0ff');
    console.log('%c✨ Добро пожаловать в будущее печати! ✨', 'font-size: 16px; color: #f0f; text-shadow: 0 0 10px #f0f');
    console.log('%c🔮 Система инициализирована...', 'color: #ff0');
    
    // Обработка ошибок
    window.onerror = function(msg, url, lineNo, columnNo, error) {
        console.log('%c❌ Ошибка: ' + msg, 'color: #f00; font-size: 14px');
        return false;
    };
</script>
"""

# ========== FLASK РОУТЫ С СУПЕР-ДИЗАЙНОМ ==========

@app.route('/')
def home():
    """Главная страница с супер-премиум дизайном"""
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    
    # Загружаем историю для статистики
    history = load_orders_history()
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos_print', 0) for order in history)
    total_pages = sum(order.get('total_pages_print', 0) for order in history)
    total_orders = len(history)
    
    # Генерируем случайные числа для анимации
    active_users = len(user_sessions)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🚀 PRINT BOT PREMIUM | КИБЕРПАНК ПЕЧАТЬ</title>
        {PREMIUM_CSS}
        {PREMIUM_JS}
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div class="parallax-container">
            <div style="position: relative; z-index: 1; padding: 40px 20px;">
                <!-- Главный заголовок -->
                <div class="glass-card" style="text-align: center; padding: 60px; margin-bottom: 40px; max-width: 1200px; margin-left: auto; margin-right: auto;">
                    <h1 class="neon-text" style="font-size: 4em; margin-bottom: 20px;">
                        <i class="fas fa-robot"></i> PRINT BOT <i class="fas fa-print"></i>
                    </h1>
                    <div style="font-size: 1.5em; color: #fff; text-shadow: 0 0 10px rgba(255,255,255,0.5); margin-bottom: 30px;">
                        <span class="animated-icon">🚀</span> СУПЕР-ПРЕМИУМ СИСТЕМА ПЕЧАТИ <span class="animated-icon">✨</span>
                    </div>
                    
                    <!-- Статистика в реальном времени -->
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 30px; margin-top: 40px;">
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px;">
                                <i class="fas fa-box" style="font-size: 3em; color: #0ff; margin-bottom: 15px;"></i>
                                <div class="counter">{total_orders}</div>
                                <div style="color: #fff; font-size: 1.1em;">ВСЕГО ЗАКАЗОВ</div>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px;">
                                <i class="fas fa-coins" style="font-size: 3em; color: #f0f; margin-bottom: 15px;"></i>
                                <div class="counter">{total_revenue}</div>
                                <div style="color: #fff; font-size: 1.1em;">ВЫРУЧКА ₽</div>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px;">
                                <i class="fas fa-camera" style="font-size: 3em; color: #ff0; margin-bottom: 15px;"></i>
                                <div class="counter">{total_photos}</div>
                                <div style="color: #fff; font-size: 1.1em;">НАПЕЧАТАНО ФОТО</div>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px;">
                                <i class="fas fa-file-alt" style="font-size: 3em; color: #0f0; margin-bottom: 15px;"></i>
                                <div class="counter">{total_pages}</div>
                                <div style="color: #fff; font-size: 1.1em;">НАПЕЧАТАНО СТРАНИЦ</div>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px;">
                                <i class="fas fa-users" style="font-size: 3em; color: #0ff; margin-bottom: 15px;"></i>
                                <div class="counter">{active_users}</div>
                                <div style="color: #fff; font-size: 1.1em;">АКТИВНЫХ СЕЙЧАС</div>
                            </div>
                        </div>
                    </div>
                    
                    <div style="margin-top: 40px;">
                        <a href="/orders/" class="hologram-btn" style="margin-right: 20px;">
                            <i class="fas fa-list"></i> ВСЕ ЗАКАЗЫ
                        </a>
                        <a href="/stats/" class="hologram-btn" style="margin-right: 20px;">
                            <i class="fas fa-chart-line"></i> СТАТИСТИКА
                        </a>
                        <a href="/dashboard/" class="hologram-btn">
                            <i class="fas fa-tachometer-alt"></i> ДАШБОРД
                        </a>
                    </div>
                </div>
                
                <!-- Последние заказы -->
                <div class="glass-card" style="max-width: 1200px; margin: 0 auto 40px auto; padding: 40px;">
                    <h2 style="color: #fff; font-size: 2.5em; margin-bottom: 30px; text-align: center;">
                        <i class="fas fa-fire" style="color: #ff0;"></i> ПОСЛЕДНИЕ ЗАКАЗЫ
                    </h2>
                    
                    <div style="display: grid; gap: 20px;">
    """
    
    # Показываем последние 5 заказов
    for order in sorted(history, key=lambda x: x.get('date', ''), reverse=True)[:5]:
        status = order.get('status', 'new')
        status_color = get_status_color(status)
        status_text = get_status_display(status)
        
        html += f"""
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 25px; background: rgba(20, 25, 45, 0.9);">
                                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                                    <div>
                                        <h3 style="color: #0ff; font-size: 1.3em; margin-bottom: 10px;">
                                            <i class="fas fa-hashtag"></i> {order.get('order_id', 'N/A')}
                                        </h3>
                                        <p style="color: #aaa;">
                                            <i class="fas fa-user"></i> {order.get('user_name', 'Неизвестно')} | 
                                            <i class="fas fa-calendar"></i> {datetime.fromisoformat(order.get('date', datetime.now().isoformat())).strftime('%d.%m.%Y %H:%M')}
                                        </p>
                                    </div>
                                    <span class="status-badge status-{status}">{status_text}</span>
                                </div>
                                
                                <div class="neon-line" style="margin: 15px 0;"></div>
                                
                                <div style="display: flex; gap: 30px; flex-wrap: wrap; color: #fff;">
                                    <span><i class="fas fa-camera" style="color: #0ff;"></i> {order.get('total_photos_print', 0)} фото</span>
                                    <span><i class="fas fa-file" style="color: #f0f;"></i> {order.get('total_pages_print', 0)} стр.</span>
                                    <span><i class="fas fa-copy"></i> {order.get('quantity', 1)} копий</span>
                                    <span><i class="fas fa-ruble-sign" style="color: #0f0;"></i> {order.get('total_price', 0)} ₽</span>
                                </div>
                                
                                <a href="/orders/{order.get('order_id')}/" class="hologram-btn" style="margin-top: 20px; padding: 10px 20px; font-size: 1em;">
                                    <i class="fas fa-eye"></i> ПОДРОБНЕЕ
                                </a>
                            </div>
                        </div>
        """
    
    html += """
                    </div>
                </div>
                
                <!-- Информация о сервисе -->
                <div class="glass-card" style="max-width: 1200px; margin: 0 auto; padding: 40px;">
                    <h2 style="color: #fff; font-size: 2.5em; margin-bottom: 40px; text-align: center;">
                        <i class="fas fa-info-circle"></i> О СЕРВИСЕ
                    </h2>
                    
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 30px;">
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                                <i class="fas fa-camera-retro" style="font-size: 4em; color: #0ff; margin-bottom: 20px;"></i>
                                <h3 style="color: #fff; font-size: 1.5em; margin-bottom: 15px;">ФОТО</h3>
                                <p style="color: #aaa;">JPG, PNG, GIF<br>3 формата печати<br>Мгновенный расчет</p>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                                <i class="fas fa-file-pdf" style="font-size: 4em; color: #f0f; margin-bottom: 20px;"></i>
                                <h3 style="color: #fff; font-size: 1.5em; margin-bottom: 15px;">ДОКУМЕНТЫ</h3>
                                <p style="color: #aaa;">PDF, DOC, DOCX<br>Ч/б и цветная печать<br>Автоподсчет страниц</p>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                                <i class="fas fa-truck" style="font-size: 4em; color: #ff0; margin-bottom: 20px;"></i>
                                <h3 style="color: #fff; font-size: 1.5em; margin-bottom: 15px;">ДОСТАВКА</h3>
                                <p style="color: #aaa;">Самовывоз<br>СДЭК, Яндекс<br>Почта России</p>
                            </div>
                        </div>
                        
                        <div class="card-3d">
                            <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                                <i class="fas fa-shield-alt" style="font-size: 4em; color: #0f0; margin-bottom: 20px;"></i>
                                <h3 style="color: #fff; font-size: 1.5em; margin-bottom: 15px;">БЕЗОПАСНОСТЬ</h3>
                                <p style="color: #aaa;">Шифрование данных<br>Защита информации<br>Конфиденциальность</p>
                            </div>
                        </div>
                    </div>
                    
                    <div class="neon-line" style="margin: 40px 0;"></div>
                    
                    <div style="text-align: center; color: #fff;">
                        <p style="font-size: 1.3em; margin-bottom: 10px;">
                            <i class="fas fa-phone-alt" style="color: #0ff;"></i> {CONTACT_PHONE}
                        </p>
                        <p style="font-size: 1.1em; color: #aaa;">
                            <i class="fas fa-clock"></i> Работаем 24/7 | <i class="fas fa-rocket"></i> Срочная печать
                        </p>
                    </div>
                </div>
                
                <!-- Футер -->
                <div style="text-align: center; padding: 40px; color: #555;">
                    <p>© 2024 PRINT BOT PREMIUM | ВСЕ ПРАВА ЗАЩИЩЕНЫ</p>
                    <p style="font-size: 0.9em;">v4.20 КИБЕРПАНК ИЗДАНИЕ</p>
                </div>
            </div>
        </div>
        
        <script>
            // Добавляем эффект печатной машинки для заголовка
            setTimeout(() => {
                const titles = document.querySelectorAll('h1, h2');
                titles.forEach(title => {
                    if (title.classList.contains('neon-text')) return;
                    const text = title.textContent;
                    if (text.length < 50) {
                        typeWriter(title, text, 30);
                    }
                });
            }, 500);
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/')
def list_orders():
    """Список всех заказов с супер-дизайном"""
    orders = []
    if os.path.exists(ORDERS_PATH):
        for item in os.listdir(ORDERS_PATH):
            order_path = os.path.join(ORDERS_PATH, item)
            if os.path.isdir(order_path):
                # Ищем JSON файл с данными
                json_file = os.path.join(order_path, "order_data.json")
                if os.path.exists(json_file):
                    with open(json_file, 'r', encoding='utf-8') as f:
                        order_data = json.load(f)
                        orders.append(order_data)
                else:
                    # Если JSON нет, читаем из текстового файла
                    info_file = os.path.join(order_path, "💎_ИНФОРМАЦИЯ_О_ЗАКАЗЕ.txt")
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
                            total_match = re.search(r'Сумма: (\d+)', content)
                            if total_match:
                                total = int(total_match.group(1))
                    
                    orders.append({
                        'order_id': item,
                        'path': order_path,
                        'status': status,
                        'total_price': total,
                        'date': datetime.fromtimestamp(os.path.getctime(order_path)).isoformat(),
                        'user_name': 'Неизвестно'
                    })
    
    # Сортируем по дате
    orders.sort(key=lambda x: x.get('date', ''), reverse=True)
    
    # Статистика по статусам
    status_counts = {}
    for status in ORDER_STATUSES.keys():
        status_counts[status] = sum(1 for o in orders if o.get('status') == status)
    
    # Получаем фильтр из запроса
    filter_status = request.args.get('status', 'all')
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>📦 ВСЕ ЗАКАЗЫ | PRINT BOT</title>
        {PREMIUM_CSS}
        {PREMIUM_JS}
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div style="padding: 40px 20px; max-width: 1400px; margin: 0 auto;">
            <!-- Шапка -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                    <h1 class="neon-text" style="font-size: 2.5em;">
                        <i class="fas fa-boxes"></i> ВСЕ ЗАКАЗЫ
                    </h1>
                    <div>
                        <a href="/" class="hologram-btn" style="margin-right: 10px;">
                            <i class="fas fa-home"></i> ГЛАВНАЯ
                        </a>
                        <a href="/stats/" class="hologram-btn">
                            <i class="fas fa-chart-bar"></i> СТАТИСТИКА
                        </a>
                    </div>
                </div>
                
                <!-- Фильтры по статусам -->
                <div class="neon-line" style="margin: 20px 0;"></div>
                
                <div style="display: flex; gap: 15px; flex-wrap: wrap; align-items: center;">
                    <span style="color: #fff;"><i class="fas fa-filter"></i> ФИЛЬТР:</span>
                    <a href="/orders/" class="status-badge" style="background: {'#0ff' if filter_status == 'all' else '#333'}; text-decoration: none;">
                        ВСЕ ({len(orders)})
                    </a>
    """
    
    for status_key, status_value in ORDER_STATUSES.items():
        count = status_counts.get(status_key, 0)
        if count > 0:
            selected_class = "status-badge status-" + status_key
            html += f"""
                    <a href="/orders/?status={status_key}" class="{selected_class}" style="text-decoration: none;">
                        {status_value} ({count})
                    </a>
            """
    
    html += """
                </div>
            </div>
    """
    
    if not orders:
        html += """
            <div class="glass-card" style="padding: 100px; text-align: center;">
                <i class="fas fa-inbox" style="font-size: 5em; color: #0ff; margin-bottom: 30px;"></i>
                <h2 style="color: #fff; font-size: 2em; margin-bottom: 20px;">ЗАКАЗОВ ПОКА НЕТ</h2>
                <p style="color: #aaa; font-size: 1.2em;">Отправьте файлы боту, чтобы создать первый заказ</p>
            </div>
        """
    else:
        html += """
            <div style="display: grid; gap: 30px;">
        """
        
        for order in orders:
            # Применяем фильтр
            if filter_status != 'all' and order.get('status') != filter_status:
                continue
                
            status = order.get('status', 'new')
            status_text = get_status_display(status)
            date = datetime.fromisoformat(order.get('date', datetime.now().isoformat()))
            
            html += f"""
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 0; overflow: hidden;">
                        <div style="background: linear-gradient(135deg, rgba(0,255,255,0.1), rgba(255,0,255,0.1)); padding: 30px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; margin-bottom: 20px;">
                                <div>
                                    <h2 style="color: #0ff; font-size: 1.5em; margin-bottom: 5px;">
                                        <i class="fas fa-hashtag"></i> {order.get('order_id', 'N/A')}
                                    </h2>
                                    <p style="color: #aaa;">
                                        <i class="fas fa-user"></i> {order.get('user_name', 'Неизвестно')} | 
                                        <i class="fas fa-calendar"></i> {date.strftime('%d.%m.%Y %H:%M')}
                                    </p>
                                </div>
                                <span class="status-badge status-{status}">{status_text}</span>
                            </div>
                            
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 20px; margin-bottom: 20px;">
                                <div>
                                    <div style="color: #0ff; font-size: 0.9em;">ФОТО</div>
                                    <div style="color: #fff; font-size: 1.3em; font-weight: 600;">{order.get('total_photos_print', 0)}</div>
                                </div>
                                <div>
                                    <div style="color: #f0f; font-size: 0.9em;">СТРАНИЦ</div>
                                    <div style="color: #fff; font-size: 1.3em; font-weight: 600;">{order.get('total_pages_print', 0)}</div>
                                </div>
                                <div>
                                    <div style="color: #ff0; font-size: 0.9em;">КОПИЙ</div>
                                    <div style="color: #fff; font-size: 1.3em; font-weight: 600;">{order.get('quantity', 1)}</div>
                                </div>
                                <div>
                                    <div style="color: #0f0; font-size: 0.9em;">СУММА</div>
                                    <div style="color: #fff; font-size: 1.3em; font-weight: 600;">{order.get('total_price', 0)} ₽</div>
                                </div>
                            </div>
                            
                            <a href="/orders/{order.get('order_id')}/" class="hologram-btn" style="width: 100%; text-align: center;">
                                <i class="fas fa-eye"></i> ПОДРОБНЕЕ
                            </a>
                        </div>
                    </div>
                </div>
            """
        
        html += """
            </div>
        """
    
    html += """
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/')
def view_order(order_id):
    """Просмотр конкретного заказа с супер-дизайном"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    # Пытаемся загрузить JSON данные
    json_file = os.path.join(order_path, "order_data.json")
    if os.path.exists(json_file):
        with open(json_file, 'r', encoding='utf-8') as f:
            order_data = json.load(f)
    else:
        # Если JSON нет, создаем базовые данные
        order_data = {
            'order_id': order_id,
            'user_name': 'Неизвестно',
            'date': datetime.fromtimestamp(os.path.getctime(order_path)).isoformat(),
            'status': 'new',
            'total_price': 0,
            'total_photos_print': 0,
            'total_pages_print': 0,
            'quantity': 1,
            'files': []
        }
    
    # Собираем файлы
    photos = []
    docs = []
    previews = []
    
    photos_folder = os.path.join(order_path, "📸 ФОТО")
    docs_folder = os.path.join(order_path, "📄 ДОКУМЕНТЫ")
    preview_folder = os.path.join(order_path, "👀 ПРЕВЬЮ")
    
    if os.path.exists(photos_folder):
        for f in os.listdir(photos_folder):
            file_path = os.path.join(photos_folder, f)
            if os.path.isfile(file_path):
                photos.append({
                    'name': f,
                    'path': file_path,
                    'size': os.path.getsize(file_path),
                    'size_str': format_file_size(os.path.getsize(file_path))
                })
    
    if os.path.exists(docs_folder):
        for f in os.listdir(docs_folder):
            file_path = os.path.join(docs_folder, f)
            if os.path.isfile(file_path):
                docs.append({
                    'name': f,
                    'path': file_path,
                    'size': os.path.getsize(file_path),
                    'size_str': format_file_size(os.path.getsize(file_path))
                })
    
    if os.path.exists(preview_folder):
        for f in os.listdir(preview_folder):
            if f.lower().endswith(('.jpg', '.jpeg', '.png')):
                previews.append(f)
    
    status = order_data.get('status', 'new')
    status_text = get_status_display(status)
    date = datetime.fromisoformat(order_data.get('date', datetime.now().isoformat()))
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ЗАКАЗ {order_id} | PRINT BOT</title>
        {PREMIUM_CSS}
        {PREMIUM_JS}
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div style="padding: 40px 20px; max-width: 1200px; margin: 0 auto;">
            <!-- Шапка -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                    <h1 class="neon-text" style="font-size: 2em;">
                        <i class="fas fa-box-open"></i> ЗАКАЗ {order_id[:10]}...
                    </h1>
                    <div>
                        <a href="/orders/" class="hologram-btn" style="margin-right: 10px;">
                            <i class="fas fa-arrow-left"></i> К СПИСКУ
                        </a>
                        <a href="/" class="hologram-btn">
                            <i class="fas fa-home"></i> ГЛАВНАЯ
                        </a>
                    </div>
                </div>
            </div>
            
            <!-- Статус и действия -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <h2 style="color: #fff; margin-bottom: 20px;"><i class="fas fa-sync-alt"></i> УПРАВЛЕНИЕ СТАТУСОМ</h2>
                
                <div style="display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 30px;">
    """
    
    for status_key, status_value in ORDER_STATUSES.items():
        if status_key == status:
            html += f"""
                    <span class="status-badge status-{status_key}" style="cursor: default;">
                        {status_value} <i class="fas fa-check"></i>
                    </span>
            """
        else:
            html += f"""
                    <a href="/orders/{order_id}/status/{status_key}/" class="status-badge status-{status_key}" style="text-decoration: none; cursor: pointer; opacity: 0.7;">
                        {status_value}
                    </a>
            """
    
    html += f"""
                </div>
                
                <div class="neon-progress" style="margin-bottom: 10px;">
                    <div class="neon-progress-fill" style="width: {((list(ORDER_STATUSES.keys()).index(status) + 1) / len(ORDER_STATUSES)) * 100}%;"></div>
                </div>
                <p style="color: #aaa; text-align: right;">Прогресс: {list(ORDER_STATUSES.keys()).index(status) + 1}/{len(ORDER_STATUSES)}</p>
            </div>
            
            <!-- Информация о заказе -->
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 30px; margin-bottom: 30px;">
                <div class="glass-card" style="padding: 30px;">
                    <h2 style="color: #0ff; margin-bottom: 20px;"><i class="fas fa-info-circle"></i> ДЕТАЛИ ЗАКАЗА</h2>
                    
                    <div style="display: grid; gap: 15px;">
                        <div>
                            <div style="color: #0ff; font-size: 0.9em;">КЛИЕНТ</div>
                            <div style="color: #fff; font-size: 1.2em;">{order_data.get('user_name', 'Неизвестно')}</div>
                        </div>
                        
                        <div>
                            <div style="color: #f0f; font-size: 0.9em;">ДАТА</div>
                            <div style="color: #fff; font-size: 1.2em;">{date.strftime('%d.%m.%Y %H:%M')}</div>
                        </div>
                        
                        <div>
                            <div style="color: #ff0; font-size: 0.9em;">ТИП ПЕЧАТИ</div>
                            <div style="color: #fff; font-size: 1.2em;">
                                {'📸 ФОТО' if order_data.get('type') == 'photo' else '📄 ДОКУМЕНТЫ'}
                            </div>
                        </div>
                        
                        <div>
                            <div style="color: #0f0; font-size: 0.9em;">КОЛИЧЕСТВО КОПИЙ</div>
                            <div style="color: #fff; font-size: 1.2em;">{order_data.get('quantity', 1)}</div>
                        </div>
                        
                        <div>
                            <div style="color: #0ff; font-size: 0.9em;">ФОТО К ПЕЧАТИ</div>
                            <div style="color: #fff; font-size: 1.2em;">{order_data.get('total_photos_print', 0)}</div>
                        </div>
                        
                        <div>
                            <div style="color: #f0f; font-size: 0.9em;">СТРАНИЦ К ПЕЧАТИ</div>
                            <div style="color: #fff; font-size: 1.2em;">{order_data.get('total_pages_print', 0)}</div>
                        </div>
                        
                        <div>
                            <div style="color: #ff0; font-size: 0.9em;">ИТОГОВАЯ СУММА</div>
                            <div style="color: #0f0; font-size: 1.5em; font-weight: 800;">{order_data.get('total_price', 0)} ₽</div>
                        </div>
                    </div>
                </div>
                
                <div class="glass-card" style="padding: 30px;">
                    <h2 style="color: #f0f; margin-bottom: 20px;"><i class="fas fa-file"></i> ФАЙЛЫ</h2>
                    
                    <div style="max-height: 400px; overflow-y: auto;">
    """
    
    # Фото
    if photos:
        html += """
                        <h3 style="color: #0ff; margin: 15px 0 10px 0;">
                            <i class="fas fa-camera"></i> ФОТО ({})
                        </h3>
        """.format(len(photos))
        
        for photo in photos:
            html += f"""
                        <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.1);">
                            <div>
                                <i class="fas fa-image" style="color: #0ff;"></i>
                                <span style="color: #fff;"> {photo['name'][:30]}...</span>
                                <br>
                                <small style="color: #aaa;">{photo['size_str']}</small>
                            </div>
                            <a href="/orders/{order_id}/file/📸 ФОТО/{photo['name']}" class="hologram-btn" style="padding: 5px 15px; font-size: 0.9em;" download>
                                <i class="fas fa-download"></i>
                            </a>
                        </div>
            """
    
    # Документы
    if docs:
        html += """
                        <h3 style="color: #f0f; margin: 15px 0 10px 0;">
                            <i class="fas fa-file-alt"></i> ДОКУМЕНТЫ ({})
                        </h3>
        """.format(len(docs))
        
        for doc in docs:
            html += f"""
                        <div style="display: flex; justify-content: space-between; align-items: center; padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.1);">
                            <div>
                                <i class="fas fa-file-pdf" style="color: #f0f;"></i>
                                <span style="color: #fff;"> {doc['name'][:30]}...</span>
                                <br>
                                <small style="color: #aaa;">{doc['size_str']}</small>
                            </div>
                            <a href="/orders/{order_id}/file/📄 ДОКУМЕНТЫ/{doc['name']}" class="hologram-btn" style="padding: 5px 15px; font-size: 0.9em;" download>
                                <i class="fas fa-download"></i>
                            </a>
                        </div>
            """
    
    html += """
                    </div>
                </div>
            </div>
    """
    
    # Галерея фото
    if previews:
        html += f"""
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <h2 style="color: #0ff; margin-bottom: 20px;"><i class="fas fa-images"></i> ГАЛЕРЕЯ ФОТО</h2>
                
                <div class="photo-grid">
        """
        
        for preview in previews[:12]:  # Показываем первые 12
            html += f"""
                    <div class="photo-item">
                        <img src="/orders/{order_id}/file/👀 ПРЕВЬЮ/{preview}" alt="Preview">
                        <div class="photo-overlay">
                            <i class="fas fa-search-plus"></i> Увеличить
                        </div>
                    </div>
            """
        
        html += """
                </div>
            </div>
        """
    
    # Действия
    html += f"""
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px;">
                <a href="/orders/{order_id}/download/" class="hologram-btn" style="text-align: center;">
                    <i class="fas fa-download"></i> СКАЧАТЬ ВСЕ
                </a>
                <a href="/orders/{order_id}/delete/" class="hologram-btn" style="text-align: center; background: linear-gradient(135deg, #f00, #f0f);"
                   onclick="return confirm('Вы уверены, что хотите удалить заказ? Это действие нельзя отменить!');">
                    <i class="fas fa-trash"></i> УДАЛИТЬ
                </a>
            </div>
        </div>
        
        <script>
            // Автообновление статуса каждые 30 секунд
            setTimeout(function() {{
                location.reload();
            }}, 30000);
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/orders/<order_id>/status/<new_status>/')
def change_status(order_id, new_status):
    """Изменение статуса заказа"""
    if new_status not in ORDER_STATUSES:
        abort(404)
    
    if update_order_status(order_id, new_status):
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="2;url=/orders/{order_id}/">
            {PREMIUM_CSS}
            {PREMIUM_JS}
        </head>
        <body>
            <div id="particles-js"></div>
            
            <div style="display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px;">
                <div class="glass-card" style="text-align: center; padding: 60px; max-width: 500px;">
                    <i class="fas fa-check-circle" style="font-size: 5em; color: #0f0; margin-bottom: 30px;"></i>
                    <h1 class="neon-text" style="font-size: 2em; margin-bottom: 20px;">СТАТУС ИЗМЕНЕН</h1>
                    <p style="color: #fff; font-size: 1.2em; margin-bottom: 20px;">
                        Новый статус: {get_status_display(new_status)}
                    </p>
                    <div class="quantum-loader" style="margin: 30px auto;"></div>
                    <p style="color: #aaa;">Перенаправление через 2 секунды...</p>
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(html)
    else:
        abort(500)

@app.route('/orders/<order_id>/file/<path:filename>')
def download_order_file(order_id, filename):
    """Скачивание файла из заказа"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    file_path = os.path.join(order_path, filename)
    
    # Проверяем существование файла
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        # Пробуем найти файл в подпапках
        for root, dirs, files in os.walk(order_path):
            for file in files:
                if file == filename or file == os.path.basename(filename):
                    file_path = os.path.join(root, file)
                    return send_file(file_path, as_attachment=True, download_name=file)
        
        abort(404)
    
    return send_file(file_path, as_attachment=True, download_name=os.path.basename(filename))

@app.route('/orders/<order_id>/download/')
def download_all_order_files(order_id):
    """Скачивание всех файлов заказа архивом"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    # Создаем временный ZIP-архив
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
    """Удаление заказа"""
    order_path = os.path.join(ORDERS_PATH, order_id)
    
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        abort(404)
    
    try:
        # Удаляем папку заказа
        shutil.rmtree(order_path)
        
        # Удаляем из истории
        history = load_orders_history()
        history = [order for order in history if order.get('order_id') != order_id]
        with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="3;url=/orders/">
            {PREMIUM_CSS}
            {PREMIUM_JS}
        </head>
        <body>
            <div id="particles-js"></div>
            
            <div style="display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px;">
                <div class="glass-card" style="text-align: center; padding: 60px; max-width: 500px;">
                    <i class="fas fa-trash-alt" style="font-size: 5em; color: #f00; margin-bottom: 30px;"></i>
                    <h1 class="neon-text" style="font-size: 2em; margin-bottom: 20px;">ЗАКАЗ УДАЛЕН</h1>
                    <p style="color: #fff; font-size: 1.2em; margin-bottom: 20px;">
                        Заказ {order_id} успешно удален
                    </p>
                    <div class="quantum-loader" style="margin: 30px auto;"></div>
                    <p style="color: #aaa;">Перенаправление через 3 секунды...</p>
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(html)
    except Exception as e:
        logger.error(f"❌ Ошибка удаления заказа: {e}")
        abort(500)

@app.route('/stats/')
def stats():
    """Статистика по заказам с супер-дизайном"""
    history = load_orders_history()
    
    # Общая статистика
    total_orders = len(history)
    total_revenue = sum(order.get('total_price', 0) for order in history)
    total_photos = sum(order.get('total_photos_print', 0) for order in history)
    total_pages = sum(order.get('total_pages_print', 0) for order in history)
    total_saved = sum(order.get('saved', 0) for order in history)
    
    # Статистика по статусам
    status_stats = {}
    for status in ORDER_STATUSES.keys():
        status_stats[status] = sum(1 for order in history if order.get('status') == status)
    
    # Статистика по месяцам
    monthly_stats = {}
    for order in history:
        date_str = order.get('date', '')
        if date_str:
            try:
                month = date_str[:7]  # YYYY-MM
                if month not in monthly_stats:
                    monthly_stats[month] = {'orders': 0, 'revenue': 0, 'photos': 0, 'pages': 0}
                monthly_stats[month]['orders'] += 1
                monthly_stats[month]['revenue'] += order.get('total_price', 0)
                monthly_stats[month]['photos'] += order.get('total_photos_print', 0)
                monthly_stats[month]['pages'] += order.get('total_pages_print', 0)
            except:
                pass
    
    # Сортируем месяцы
    months = sorted(monthly_stats.keys(), reverse=True)
    months_labels = [m for m in months]
    months_orders = [monthly_stats[m]['orders'] for m in months]
    months_revenue = [monthly_stats[m]['revenue'] for m in months]
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>СТАТИСТИКА | PRINT BOT</title>
        {PREMIUM_CSS}
        {PREMIUM_JS}
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div style="padding: 40px 20px; max-width: 1200px; margin: 0 auto;">
            <!-- Шапка -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h1 class="neon-text" style="font-size: 2.5em;">
                        <i class="fas fa-chart-pie"></i> СТАТИСТИКА
                    </h1>
                    <a href="/" class="hologram-btn">
                        <i class="fas fa-home"></i> ГЛАВНАЯ
                    </a>
                </div>
            </div>
            
            <!-- Основные показатели -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 30px; margin-bottom: 30px;">
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                        <i class="fas fa-box" style="font-size: 3em; color: #0ff; margin-bottom: 15px;"></i>
                        <div class="counter">{total_orders}</div>
                        <div style="color: #fff;">ВСЕГО ЗАКАЗОВ</div>
                    </div>
                </div>
                
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                        <i class="fas fa-coins" style="font-size: 3em; color: #f0f; margin-bottom: 15px;"></i>
                        <div class="counter">{total_revenue}</div>
                        <div style="color: #fff;">ВЫРУЧКА ₽</div>
                    </div>
                </div>
                
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                        <i class="fas fa-camera" style="font-size: 3em; color: #ff0; margin-bottom: 15px;"></i>
                        <div class="counter">{total_photos}</div>
                        <div style="color: #fff;">ФОТО</div>
                    </div>
                </div>
                
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                        <i class="fas fa-file" style="font-size: 3em; color: #0f0; margin-bottom: 15px;"></i>
                        <div class="counter">{total_pages}</div>
                        <div style="color: #fff;">СТРАНИЦ</div>
                    </div>
                </div>
                
                <div class="card-3d">
                    <div class="card-3d-content glass-card" style="padding: 30px; text-align: center;">
                        <i class="fas fa-gift" style="font-size: 3em; color: #f0f; margin-bottom: 15px;"></i>
                        <div class="counter">{total_saved}</div>
                        <div style="color: #fff;">ЭКОНОМИЯ ₽</div>
                    </div>
                </div>
            </div>
            
            <!-- График по месяцам -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <h2 style="color: #0ff; margin-bottom: 20px;">
                    <i class="fas fa-chart-line"></i> ДИНАМИКА ЗАКАЗОВ
                </h2>
                
                <canvas id="ordersChart" style="width: 100%; height: 400px; background: rgba(0,0,0,0.2); border-radius: 20px; padding: 20px;"></canvas>
            </div>
            
            <!-- Статус статистика -->
            <div class="glass-card" style="padding: 30px;">
                <h2 style="color: #f0f; margin-bottom: 20px;">
                    <i class="fas fa-chart-bar"></i> СТАТУСЫ ЗАКАЗОВ
                </h2>
                
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 20px;">
    """
    
    for status, count in status_stats.items():
        status_color = get_status_color(status)
        status_text = get_status_display(status)
        percentage = (count / total_orders * 100) if total_orders > 0 else 0
        
        html += f"""
                    <div class="card-3d">
                        <div class="card-3d-content glass-card" style="padding: 20px; text-align: center;">
                            <span class="status-badge status-{status}" style="margin-bottom: 15px;">{status_text}</span>
                            <div style="font-size: 2em; font-weight: 800; color: #fff;">{count}</div>
                            <div style="color: #aaa;">{percentage:.1f}%</div>
                            
                            <div class="neon-progress" style="margin-top: 15px;">
                                <div class="neon-progress-fill" style="width: {percentage}%;"></div>
                            </div>
                        </div>
                    </div>
        """
    
    html += """
                </div>
            </div>
        </div>
        
        <script>
            // График
            const ctx = document.getElementById('ordersChart').getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: """ + str(months_labels) + """,
                    datasets: [{
                        label: 'Количество заказов',
                        data: """ + str(months_orders) + """,
                        borderColor: '#0ff',
                        backgroundColor: 'rgba(0, 255, 255, 0.1)',
                        tension: 0.4,
                        fill: true
                    }, {
                        label: 'Выручка (₽)',
                        data: """ + str(months_revenue) + """,
                        borderColor: '#f0f',
                        backgroundColor: 'rgba(255, 0, 255, 0.1)',
                        tension: 0.4,
                        fill: true,
                        yAxisID: 'y1'
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            labels: {
                                color: '#fff',
                                font: {
                                    family: 'Rajdhani',
                                    size: 14
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: {
                                color: 'rgba(255,255,255,0.1)'
                            },
                            ticks: {
                                color: '#fff'
                            }
                        },
                        y: {
                            beginAtZero: true,
                            grid: {
                                color: 'rgba(255,255,255,0.1)'
                            },
                            ticks: {
                                color: '#fff'
                            },
                            title: {
                                display: true,
                                text: 'Количество заказов',
                                color: '#0ff'
                            }
                        },
                        y1: {
                            beginAtZero: true,
                            position: 'right',
                            grid: {
                                drawOnChartArea: false,
                                color: 'rgba(255,255,255,0.1)'
                            },
                            ticks: {
                                color: '#f0f'
                            },
                            title: {
                                display: true,
                                text: 'Выручка (₽)',
                                color: '#f0f'
                            }
                        }
                    }
                }
            });
        </script>
    </body>
    </html>
    """
    
    return render_template_string(html)

@app.route('/dashboard/')
def dashboard():
    """Дашборд с полной статистикой"""
    history = load_orders_history()
    
    # Топ клиенты
    client_stats = defaultdict(lambda: {'orders': 0, 'spent': 0, 'photos': 0, 'pages': 0})
    for order in history:
        user_name = order.get('user_name', 'Неизвестно')
        client_stats[user_name]['orders'] += 1
        client_stats[user_name]['spent'] += order.get('total_price', 0)
        client_stats[user_name]['photos'] += order.get('total_photos_print', 0)
        client_stats[user_name]['pages'] += order.get('total_pages_print', 0)
    
    top_clients = sorted(client_stats.items(), key=lambda x: x[1]['spent'], reverse=True)[:10]
    
    # Сегодняшние заказы
    today = datetime.now().date()
    today_orders = [o for o in history if datetime.fromisoformat(o.get('date', datetime.now().isoformat())).date() == today]
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ДАШБОРД | PRINT BOT</title>
        {PREMIUM_CSS}
        {PREMIUM_JS}
    </head>
    <body>
        <div id="particles-js"></div>
        
        <div style="padding: 40px 20px; max-width: 1200px; margin: 0 auto;">
            <!-- Шапка -->
            <div class="glass-card" style="padding: 30px; margin-bottom: 30px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <h1 class="neon-text" style="font-size: 2.5em;">
                        <i class="fas fa-tachometer-alt"></i> ДАШБОРД
                    </h1>
                    <a href="/" class="hologram-btn">
                        <i class="fas fa-home"></i> ГЛАВНАЯ
                    </a>
                </div>
            </div>
            
            <!-- Быстрая статистика -->
            <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 30px; margin-bottom: 30px;">
                <div class="glass-card" style="padding: 30px;">
                    <div style="font-size: 3em; color: #0ff; margin-bottom: 10px;">
                        <i class="fas fa-calendar-day"></i>
                    </div>
                    <div style="color: #aaa;">СЕГОДНЯ</div>
                    <div style="font-size: 2em; color: #fff;">{len(today_orders)}</div>
                    <div style="color: #0ff;">заказов</div>
                </div>
                
                <div class="glass-card" style="padding: 30px;">
                    <div style="font-size: 3em; color: #f0f; margin-bottom: 10px;">
                        <i class="fas fa-clock"></i>
                    </div>
                    <div style="color: #aaa;">СРЕДНЕЕ ВРЕМЯ</div>
                    <div style="font-size: 2em; color: #fff;">2.5</div>
                    <div style="color: #f0f;">дня</div>
                </div>
                
                <div class="glass-card" style="padding: 30px;">
                    <div style="font-size: 3em; color: #ff0; margin-bottom: 10px;">
                        <i class="fas fa-star"></i>
                    </div>
                    <div style="color: #aaa;">РЕЙТИНГ</div>
                    <div style="font-size: 2em; color: #fff;">4.9</div>
                    <div style="color: #ff0;">из 5</div>
                </div>
            </div>
            
            <!-- Топ клиенты -->
            <div class="glass-card" style="padding: 30px;">
                <h2 style="color: #0ff; margin-bottom: 20px;">
                    <i class="fas fa-crown"></i> ТОП КЛИЕНТЫ
                </h2>
                
                <div style="overflow-x: auto;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <thead>
                            <tr style="border-bottom: 2px solid rgba(0,255,255,0.3);">
                                <th style="padding: 15px; text-align: left; color: #0ff;">#</th>
                                <th style="padding: 15px; text-align: left; color: #0ff;">КЛИЕНТ</th>
                                <th style="padding: 15px; text-align: center; color: #0ff;">ЗАКАЗОВ</th>
                                <th style="padding: 15px; text-align: center; color: #0ff;">ФОТО</th>
                                <th style="padding: 15px; text-align: center; color: #0ff;">СТРАНИЦ</th>
                                <th style="padding: 15px; text-align: right; color: #0ff;">ПОТРАЧЕНО</th>
                            </tr>
                        </thead>
                        <tbody>
    """
    
    for i, (client, stats) in enumerate(top_clients, 1):
        html += f"""
                            <tr style="border-bottom: 1px solid rgba(255,255,255,0.1);">
                                <td style="padding: 15px; color: #f0f;">#{i}</td>
                                <td style="padding: 15px; color: #fff;">{client}</td>
                                <td style="padding: 15px; text-align: center; color: #fff;">{stats['orders']}</td>
                                <td style="padding: 15px; text-align: center; color: #fff;">{stats['photos']}</td>
                                <td style="padding: 15px; text-align: center; color: #fff;">{stats['pages']}</td>
                                <td style="padding: 15px; text-align: right; color: #0f0;">{stats['spent']} ₽</td>
                            </tr>
        """
    
    html += """
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return render_template_string(html)

# ========== TELEGRAM БОТ СУПЕР-ПРЕМИУМ ==========

def start(update, context):
    """Команда /start с супер-премиум дизайном"""
    user = update.effective_user
    user_id = user.id
    logger.info(f"✅ /start от {user.first_name} (@{user.username})")
    
    # Очищаем старую сессию
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for d in user_sessions[user_id]["temp_dirs"]:
                shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
    
    # Получаем статистику пользователя
    stats = user_stats[user_id]
    
    # Супер-премиум приветствие
    welcome = (
        "╔══════════════════════════════════════════════╗\n"
        "║     🚀  ДОБРО ПОЖАЛОВАТЬ В БУДУЩЕЕ  🚀      ║\n"
        "╚══════════════════════════════════════════════╝\n\n"
        f"✨ **Привет, {user.first_name}!** ✨\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎯 **МОИ СУПЕР-СПОСОБНОСТИ:**\n"
        "├ 📸 **Фото** (JPG, PNG) - 3 формата\n"
        "├ 📄 **Документы** (PDF, DOC, DOCX)\n"
        "├ 📦 **Пакетная загрузка** до 10 файлов\n"
        "├ 💰 **Мгновенный расчет** со скидками\n"
        "├ 📊 **3D-статистика** заказов\n"
        "└ 🎨 **Неоновый дизайн** сообщений\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    
    # Добавляем статистику если есть
    if stats['orders'] > 0:
        welcome += (
            f"📊 **ВАША СТАТИСТИКА:**\n"
            f"├ 📦 Заказов: {stats['orders']}\n"
            f"├ 📸 Фото: {stats['photos']}\n"
            f"├ 📄 Страниц: {stats['pages']}\n"
            f"└ 💰 Потрачено: {stats['spent']} ₽\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )
    
    welcome += (
        f"📞 **Контакты:** `{CONTACT_PHONE}`\n"
        f"🚚 **Доставка:** {DELIVERY_OPTIONS}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⬇️ **Отправьте файлы для печати** ⬇️"
    )
    
    update.message.reply_text(welcome, parse_mode="Markdown")
    return WAITING_FOR_FILE

def process_single_file(update, context):
    """Обработка одиночного файла с супер-дизайном"""
    user_id = update.effective_user.id
    message = update.message
    
    # Создаем сессию если нужно
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            "files": [],
            "temp_dirs": [],
            "total_photos": 0,
            "total_pages": 0,
            "file_details": [],
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
        if ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
            file_type = "photo"
        elif ext in ['pdf', 'doc', 'docx', 'txt', 'rtf']:
            file_type = "doc"
        else:
            error_msg = (
                "╔══════════════════════════════════════════════╗\n"
                "║         ❌  ОШИБКА ФОРМАТА  ❌              ║\n"
                "╚══════════════════════════════════════════════╝\n\n"
                "Неподдерживаемый формат файла.\n\n"
                "📌 **Допустимые форматы:**\n"
                "├ 📸 JPG, PNG, GIF, BMP\n"
                "├ 📄 PDF, DOC, DOCX, TXT, RTF"
            )
            message.reply_text(error_msg, parse_mode="Markdown")
            return WAITING_FOR_FILE
    elif message.photo:
        file_obj = message.photo[-1]
        file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        file_type = "photo"
    else:
        return WAITING_FOR_FILE
    
    # Отправляем сообщение о начале загрузки
    loading_msg = message.reply_text(
        "⏳ **Загрузка файла**...\n"
        "```\n" + create_progress_bar(0, 20) + "\n```",
        parse_mode="Markdown"
    )
    
    # Скачиваем файл
    file_path, temp_dir = download_file(file_obj, file_name)
    if not file_path:
        loading_msg.edit_text(
            "❌ **Ошибка загрузки**\n\n"
            "Не удалось загрузить файл. Попробуйте еще раз."
        )
        return WAITING_FOR_FILE
    
    # Обновляем прогресс
    loading_msg.edit_text(
        "⏳ **Анализ файла**...\n"
        "```\n" + create_progress_bar(50, 20) + "\n```",
        parse_mode="Markdown"
    )
    
    # Считаем количество с расширенной информацией
    items, unit, type_name, file_info = count_items_in_file(file_path, file_name)
    
    # Сохраняем в сессию
    file_info_entry = {
        "path": file_path,
        "name": file_name,
        "type": file_type,
        "items": items,
        "unit": unit,
        "type_name": type_name,
        "details": file_info
    }
    user_sessions[user_id]["files"].append(file_info_entry)
    user_sessions[user_id]["temp_dirs"].append(temp_dir)
    
    if file_type == 'photo':
        user_sessions[user_id]["total_photos"] += items
    else:
        user_sessions[user_id]["total_pages"] += items
    
    # Завершаем загрузку
    loading_msg.edit_text(
        "✅ **Файл успешно загружен!**\n"
        "```\n" + create_progress_bar(100, 20) + "\n```",
        parse_mode="Markdown"
    )
    
    # Статистика
    files_count = len(user_sessions[user_id]["files"])
    photo_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'photo')
    doc_count = sum(1 for f in user_sessions[user_id]["files"] if f['type'] == 'doc')
    total_photos = user_sessions[user_id]["total_photos"]
    total_pages = user_sessions[user_id]["total_pages"]
    
    # Формируем сообщение с информацией о файле
    file_info_text = ""
    if file_type == 'photo' and 'details' in file_info_entry:
        info = file_info_entry['details']
        if info:
            file_info_text = f"   📏 {info.get('width', '?')}x{info.get('height', '?')} | {info.get('megapixels', '?')} МП\n"
    elif file_type == 'doc' and 'details' in file_info_entry:
        info = file_info_entry['details']
        if info:
            if 'words' in info:
                file_info_text = f"   📝 {info.get('words', '?')} слов | {info.get('paragraphs', '?')} параграфов\n"
    
    # Супер-премиум сообщение
    text = (
        "╔══════════════════════════════════════════════╗\n"
        "║         ✅  ФАЙЛ ДОБАВЛЕН  ✅               ║\n"
        "╚══════════════════════════════════════════════╝\n\n"
        f"📄 **Файл:** `{file_name}`\n"
        f"📦 **Тип:** {'📸 Фото' if file_type == 'photo' else '📄 Документ'}\n"
        f"🔢 **Количество:** {items} {unit}\n"
        f"{file_info_text}"
        f"📊 **Размер:** {format_file_size(os.path.getsize(file_path))}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 **ТЕКУЩАЯ СТАТИСТИКА:**\n"
    )
    
    if photo_count > 0:
        text += f"├ 📸 **Фото:** {photo_count} файлов\n"
    if doc_count > 0:
        text += f"├ 📄 **Документы:** {doc_count} файлов\n"
    
    if total_photos > 0:
        text += f"├ 📸 **Всего фото:** {total_photos}\n"
    if total_pages > 0:
        text += f"├ 📄 **Всего страниц:** {total_pages}\n"
    
    text += f"└ 📦 **Всего файлов:** {files_count}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Предлагаем выбор
    if doc_count > 0:
        text += "📋 **Выберите тип печати:**"
        keyboard = [
            [InlineKeyboardButton("⚫ ЧЕРНО-БЕЛАЯ", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 ЦВЕТНАЯ", callback_data="doc_color")],
            [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
        ]
    else:
        text += "🖼️ **Выберите формат печати:**"
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
    """Обработка группы файлов"""
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
        
        # Отправляем сообщение о начале обработки
        loading_msg = context.bot.send_message(
            chat_id=user_id,
            text=f"⏳ **Обработка группы файлов**...\n"
                 f"```\n{create_progress_bar(0, 20)}\n```",
            parse_mode="Markdown"
        )
        
        # Создаем сессию если нужно
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "files": [],
                "temp_dirs": [],
                "total_photos": 0,
                "total_pages": 0,
                "file_details": [],
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
        processed = 0
        total = len(messages)
        
        for message in messages:
            file_obj = None
            file_name = None
            file_type = None
            
            if message.document:
                file_obj = message.document
                file_name = file_obj.file_name
                ext = file_name.lower().split('.')[-1]
                if ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                    file_type = "photo"
                    photo_count += 1
                elif ext in ['pdf', 'doc', 'docx', 'txt', 'rtf']:
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
            items, unit, type_name, file_info = count_items_in_file(file_path, file_name)
            
            # Сохраняем в сессию
            file_info_entry = {
                "path": file_path,
                "name": file_name,
                "type": file_type,
                "items": items,
                "unit": unit,
                "type_name": type_name,
                "details": file_info
            }
            user_sessions[user_id]["files"].append(file_info_entry)
            user_sessions[user_id]["temp_dirs"].append(temp_dir)
            
            if file_type == 'photo':
                user_sessions[user_id]["total_photos"] += items
            else:
                user_sessions[user_id]["total_pages"] += items
            
            processed += 1
            progress = int(processed / total * 100)
            
            # Обновляем прогресс каждые 2 файла
            if processed % 2 == 0 or processed == total:
                loading_msg.edit_text(
                    f"⏳ **Обработка группы файлов**...\n"
                    f"```\n{create_progress_bar(progress, 20)}\n```",
                    parse_mode="Markdown"
                )
        
        if not user_sessions[user_id]["files"]:
            loading_msg.edit_text(
                "❌ **Ошибка**\n\nНе удалось загрузить файлы"
            )
            return
        
        # Завершаем загрузку
        loading_msg.edit_text(
            f"✅ **Загружено {processed} из {total} файлов!**\n"
            f"```\n{create_progress_bar(100, 20)}\n```",
            parse_mode="Markdown"
        )
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        text = (
            "╔══════════════════════════════════════════════╗\n"
            f"║      ✅  ЗАГРУЖЕНО {files_count} ФАЙЛОВ  ✅     ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            "📊 **СТАТИСТИКА:**\n"
        )
        if photo_count > 0:
            text += f"├ 📸 Фото: {photo_count}\n"
        if doc_count > 0:
            text += f"├ 📄 Документы: {doc_count}\n"
        
        if total_photos > 0:
            text += f"├ 📸 Всего фото: {total_photos}\n"
        if total_pages > 0:
            text += f"├ 📄 Всего страниц: {total_pages}\n"
        
        text += f"└ 📦 Всего файлов: {files_count}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Предлагаем выбор
        if doc_count > 0:
            text += "📋 **Выберите тип печати:**"
            keyboard = [
                [InlineKeyboardButton("⚫ ЧЕРНО-БЕЛАЯ", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 ЦВЕТНАЯ", callback_data="doc_color")],
                [InlineKeyboardButton("➕ ДОБАВИТЬ ЕЩЕ", callback_data="add_more")],
                [InlineKeyboardButton("❌ ОТМЕНА", callback_data="cancel")]
            ]
        else:
            text += "🖼️ **Выберите формат печати:**"
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
        logger.error(f"❌ Ошибка при обработке группы файлов: {e}")
        logger.error(traceback.format_exc())

def button_handler(update, context):
    """Обработка нажатий кнопок с супер-дизайном"""
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    # Отмена заказа
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text(
            "╔══════════════════════════════════════════════╗\n"
            "║         📤  ДОБАВЬТЕ ЕЩЕ ФАЙЛЫ  📤        ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            "Отправьте следующие файлы для печати:",
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
            "╔══════════════════════════════════════════════╗\n"
            "║         🔄  НОВЫЙ ЗАКАЗ  🔄                ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            "Отправьте файлы для печати (JPG, PNG, PDF, DOC, DOCX):",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        format_type = data.split("_")[1]
        format_names = {"small": "Малый (A6/10x15)", "medium": "Средний (13x18/15x21)", "large": "Большой (A4/21x30)"}
        format_prices = {
            "small": "1-9: 35₽ | 10-50: 28₽ | 51-100: 23₽ | 101+: 18₽",
            "medium": "1-9: 65₽ | 10-50: 55₽ | 51-100: 45₽ | 101+: 35₽",
            "large": "1-4: 200₽ | 5-20: 170₽ | 21-50: 150₽ | 51+: 120₽"
        }
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = format_type
        
        text = (
            "╔══════════════════════════════════════════════╗\n"
            f"║      🖼️  {format_names[format_type].upper()}  🖼️     ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            f"💰 **Цены:**\n{format_prices[format_type]}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔢 **Сколько копий напечатать?**\n\n"
            "Введите число или выберите из вариантов ниже:"
        )
        
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
        color_names = {"bw": "Черно-белая", "color": "Цветная"}
        color_prices = {
            "bw": "1-20: 25₽ | 21-100: 18₽ | 101-300: 14₽ | 301+: 10₽",
            "color": "1-20: 50₽ | 21-100: 35₽ | 101-300: 25₽ | 301+: 20₽"
        }
        
        user_sessions[user_id]["type"] = "doc"
        user_sessions[user_id]["color"] = doc_type
        
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        total_items = total_photos + total_pages
        
        text = (
            "╔══════════════════════════════════════════════╗\n"
            f"║      📄  {color_names[doc_type].upper()}  📄      ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            f"💰 **Цены:**\n{color_prices[doc_type]}\n\n"
            f"📊 **В файлах:**\n"
            f"├ 📸 Фото: {total_photos}\n"
            f"└ 📄 Страниц: {total_pages}\n"
            f"Всего: {total_items} ед.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔢 **Сколько копий напечатать?**\n\n"
            "Введите число или выберите из вариантов ниже:"
        )
        
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
        original_total = 0
        total_photos_result = 0
        total_pages_result = 0
        details = "📊 **ДЕТАЛЬНЫЙ РАСЧЁТ:**\n\n"
        
        for i, f in enumerate(files, 1):
            if f['type'] == 'photo':
                total_photos_result += f['items'] * quantity
            else:
                total_pages_result += f['items'] * quantity
        
        for i, f in enumerate(files, 1):
            if file_type == "photo":
                price_dict = PHOTO_PRICES[session["format"]]
                file_base = 0
                for (min_q, max_q), price in price_dict.items():
                    if min_q <= quantity <= max_q:
                        file_base = price * quantity
                        break
                original_total += file_base
                
                # Применяем скидки
                is_first_order = user_stats[user_id]["orders"] == 0
                file_total, saved, discount = calculate_price_with_discounts(
                    price_dict, quantity, user_id, is_first_order
                )
                total += file_total
                
                details += f"📸 **Файл {i}:**\n"
                details += f"   ├ {f['items']} фото × {quantity} коп. = {f['items'] * quantity} фото\n"
                if saved > 0:
                    details += f"   ├ 💰 Скидка: {discount}% (экономия {saved}₽)\n"
                details += f"   └ **{file_total} руб.**\n\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                
                file_base = 0
                for (min_q, max_q), price in price_dict.items():
                    if min_q <= file_items <= max_q:
                        file_base = price * file_items
                        break
                original_total += file_base
                
                # Применяем скидки
                is_first_order = user_stats[user_id]["orders"] == 0
                file_total, saved, discount = calculate_price_with_discounts(
                    price_dict, file_items, user_id, is_first_order
                )
                total += file_total
                
                details += f"📄 **Файл {i}:**\n"
                details += f"   ├ {f['items']} стр. × {quantity} коп. = {file_items} стр.\n"
                if saved > 0:
                    details += f"   ├ 💰 Скидка: {discount}% (экономия {saved}₽)\n"
                details += f"   └ **{file_total} руб.**\n\n"
        
        session["total"] = total
        session["original_total"] = original_total
        session["saved"] = original_total - total
        session["discount_percent"] = int((1 - total/original_total) * 100) if original_total > 0 else 0
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = f"{details}\n"
        text += "╔══════════════════════════════════════════════╗\n"
        text += "║         📋  ПРОВЕРЬТЕ ЗАКАЗ  📋           ║\n"
        text += "╚══════════════════════════════════════════════╝\n\n"
        text += f"📦 Всего файлов: {len(files)}\n"
        if total_photos_result > 0:
            text += f"📸 Всего фото к печати: {total_photos_result}\n"
        if total_pages_result > 0:
            text += f"📄 Всего страниц к печати: {total_pages_result}\n"
        if session["saved"] > 0:
            text += f"💰 **Экономия: {session['saved']} руб. ({session['discount_percent']}%)**\n"
        text += f"💰 **ИТОГ: {total} руб.**\n"
        text += f"⏳ Срок выполнения: {session['delivery']}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        text += "Всё верно?"
        
        keyboard = [
            [InlineKeyboardButton("✅ ДА, ПОДТВЕРДИТЬ", callback_data="confirm")],
            [InlineKeyboardButton("✏️ ДОБАВИТЬ КОММЕНТАРИЙ", callback_data="add_comment")],
            [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="cancel")]
        ]
        
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return CONFIRMING_ORDER
    
    if data == "add_comment":
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)
        
        query.edit_message_text(
            "╔══════════════════════════════════════════════╗\n"
            "║         ✏️  ДОБАВЬТЕ КОММЕНТАРИЙ  ✏️        ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            "Напишите комментарий к заказу\n"
            "(особые пожелания, способ доставки и т.д.)\n\n"
            "Или нажмите кнопку ниже, чтобы пропустить:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏩ ПРОПУСТИТЬ", callback_data="skip_comment")
            ]]),
            parse_mode="Markdown"
        )
        return WAITING_FOR_COMMENT
    
    if data == "skip_comment":
        session = user_sessions.get(user_id)
        if session:
            session["comment"] = "Нет"
        
        # Возвращаемся к подтверждению
        query.message.delete()
        
        # Показываем финальное подтверждение
        session = user_sessions.get(user_id)
        if not session:
            return cancel_order(user_id, query, context)
        
        files = session["files"]
        total = session["total"]
        total_photos_result = session["total_photos"]
        total_pages_result = session["total_pages"]
        
        text = (
            "╔══════════════════════════════════════════════╗\n"
            "║         📋  ПРОВЕРЬТЕ ЗАКАЗ  📋           ║\n"
            "╚══════════════════════════════════════════════╝\n\n"
            f"📦 Всего файлов: {len(files)}\n"
            f"📸 Фото к печати: {total_photos_result}\n"
            f"📄 Страниц к печати: {total_pages_result}\n"
        )
        if session.get("saved", 0) > 0:
            text += f"💰 Экономия: {session['saved']} руб.\n"
        text += f"💰 ИТОГ: {total} руб.\n"
        text += f"⏳ Срок: {session['delivery']}\n\n"
        text += f"📝 Комментарий: {session.get('comment', 'Нет')}\n\n"
        text += "Всё верно?"
        
        keyboard = [
            [InlineKeyboardButton("✅ ДА, ПОДТВЕРДИТЬ", callback_data="confirm")],
            [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="cancel")]
        ]
        
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
        
        # Отправляем сообщение о сохранении
        saving_msg = context.bot.send_message(
            chat_id=user_id,
            text="⏳ **Сохранение заказа**...\n"
                 "```\n" + create_progress_bar(0, 20) + "\n```",
            parse_mode="Markdown"
        )
        
        success, order_id, folder = save_order_to_folder(
            user_id,
            session['user_info']['username'],
            session,
            session['files']
        )
        
        if success:
            # Обновляем прогресс
            saving_msg.edit_text(
                "✅ **Заказ сохранен!**\n"
                "```\n" + create_progress_bar(100, 20) + "\n```",
                parse_mode="Markdown"
            )
            
            # Отправляем уведомление админу
            send_admin_notification(session, order_id, folder)
            
            # Подсчитываем для сообщения пользователю
            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            doc_files = [f for f in session['files'] if f['type'] == 'doc']
            
            total_photos = sum(f['items'] for f in photo_files)
            total_pages = sum(f['items'] for f in doc_files)
            
            # Супер-премиум сообщение об успешном заказе
            client_message = (
                "╔══════════════════════════════════════════════╗\n"
                "║     🎉  ЗАКАЗ ОФОРМЛЕН!  🎉                ║\n"
                "╚══════════════════════════════════════════════╝\n\n"
                f"🆔 **Номер заказа:** `{order_id}`\n"
                f"👤 **Заказчик:** {session['user_info']['first_name']}\n"
                f"📦 **Файлов:** {len(session['files'])}\n\n"
            )
            
            if total_photos > 0:
                client_message += f"📸 **Фото в оригинале:** {total_photos}\n"
                client_message += f"📸 **Фото к печати:** {total_photos * session['quantity']}\n"
            if total_pages > 0:
                client_message += f"📄 **Страниц в оригинале:** {total_pages}\n"
                client_message += f"📄 **Страниц к печати:** {total_pages * session['quantity']}\n"
            
            if session.get("saved", 0) > 0:
                client_message += f"💰 **Экономия:** {session['saved']} руб. ({session['discount_percent']}%)\n"
            
            client_message += (
                f"💰 **Сумма к оплате:** {session['total']} руб.\n"
                f"⏳ **Срок выполнения:** {session['delivery']}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📞 **Контактный телефон:** `{CONTACT_PHONE}`\n"
                f"🚚 **Способы получения:** {DELIVERY_OPTIONS}\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📌 **Статус заказа:** {get_status_display('new')}\n"
                "Вы будете получать уведомления при изменении статуса.\n\n"
                "✨ **Спасибо за заказ!** ✨"
            )
            
            context.bot.send_message(
                chat_id=user_id,
                text=client_message,
                parse_mode="Markdown"
            )
            
            # Если есть фото, отправляем предпросмотр
            if photo_files:
                try:
                    media_group = []
                    for i, photo_file in enumerate(photo_files[:5]):
                        if os.path.exists(photo_file['path']):
                            with open(photo_file['path'], 'rb') as photo:
                                if i == 0:
                                    media_group.append(InputMediaPhoto(
                                        photo.read(),
                                        caption=f"📸 **Загруженные фото** ({len(photo_files)} шт.)"
                                    ))
                                else:
                                    media_group.append(InputMediaPhoto(photo.read()))
                    
                    if media_group:
                        context.bot.send_media_group(
                            chat_id=user_id,
                            media=media_group
                        )
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки предпросмотра: {e}")
            
        else:
            saving_msg.edit_text(
                "❌ **Ошибка**\n\nНе удалось сохранить заказ."
            )
        
        # Очищаем временные файлы
        for d in session.get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]
        
        # Кнопка для нового заказа
        keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
        query.message.delete()
        context.bot.send_message(
            chat_id=user_id,
            text="✨ **Хотите оформить ещё один заказ?** ✨",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_FOR_FILE
    
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
    keyboard = [[InlineKeyboardButton("🔄 НОВЫЙ ЗАКАЗ", callback_data="new_order")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Отправляем или редактируем сообщение
    message = (
        "╔══════════════════════════════════════════════╗\n"
        "║           ❌  ЗАКАЗ ОТМЕНЁН  ❌              ║\n"
        "╚══════════════════════════════════════════════╝\n\n"
        "Все загруженные файлы удалены.\n\n"
        "✨ **Хотите оформить новый заказ?** ✨"
    )
    
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
    """Обработка входящих файлов"""
    # Проверяем, является ли это медиа-группой
    if update.message.media_group_id:
        return handle_media_group(update, context)
    
    # Обработка одиночного файла
    return process_single_file(update, context)

def handle_quantity_input(update, context):
    """Ручной ввод количества"""
    user_id = update.effective_user.id
    text = update.message.text
    quantity = extract_number_from_text(text)
    
    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            "❌ **Ошибка**\n\n"
            "Пожалуйста, введите число от 1 до 1000\n"
            "Или выберите из кнопок:",
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
        )
        return ENTERING_QUANTITY
    
    # Создаем callback как при нажатии кнопки
    class FakeQuery:
        def __init__(self, user_id, data):
            self.data = data
            self.from_user = type('User', (), {'id': user_id})()
            self.message = update.message
            self.answer = lambda: None
    
    return button_handler(update, FakeQuery(user_id, f'qty_{quantity}'), context)

def handle_comment_input(update, context):
    """Обработка ввода комментария"""
    user_id = update.effective_user.id
    comment = update.message.text
    
    session = user_sessions.get(user_id)
    if not session:
        return cancel_order(user_id, None, context)
    
    session["comment"] = comment[:500]  # Ограничиваем длину
    
    # Возвращаемся к подтверждению
    files = session["files"]
    total = session["total"]
    total_photos_result = session["total_photos"]
    total_pages_result = session["total_pages"]
    
    text = (
        "╔══════════════════════════════════════════════╗\n"
        "║         📋  ПРОВЕРЬТЕ ЗАКАЗ  📋           ║\n"
        "╚══════════════════════════════════════════════╝\n\n"
        f"📦 Всего файлов: {len(files)}\n"
        f"📸 Фото к печати: {total_photos_result}\n"
        f"📄 Страниц к печати: {total_pages_result}\n"
    )
    if session.get("saved", 0) > 0:
        text += f"💰 Экономия: {session['saved']} руб.\n"
    text += f"💰 ИТОГ: {total} руб.\n"
    text += f"⏳ Срок: {session['delivery']}\n\n"
    text += f"📝 Комментарий: {session['comment']}\n\n"
    text += "Всё верно?"
    
    keyboard = [
        [InlineKeyboardButton("✅ ДА, ПОДТВЕРДИТЬ", callback_data="confirm")],
        [InlineKeyboardButton("❌ ОТМЕНИТЬ", callback_data="cancel")]
    ]
    
    update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CONFIRMING_ORDER

def get_quantity_keyboard():
    """Клавиатура выбора количества с красивыми кнопками"""
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

def extract_number_from_text(text):
    """Извлекает число из текста"""
    numbers = re.findall(r'\d+', text)
    return int(numbers[0]) if numbers else None

def estimate_delivery_time(total_items):
    """Расчет срока доставки с красивым форматом"""
    if total_items <= 50:
        days = 1
    elif total_items <= 200:
        days = 2
    else:
        days = 3
    
    # Красивое отображение срока
    if days == 1:
        return "⏱️ 1 рабочий день"
    elif days == 2:
        return "⏱️ 2 рабочих дня"
    else:
        return f"⏱️ {days} рабочих дня"

def error_handler(update, context):
    """Глобальный обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}")
    logger.error(traceback.format_exc())
    
    try:
        if update and update.effective_chat:
            context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
                    "╔══════════════════════════════════════════════╗\n"
                    "║         ❌  ПРОИЗОШЛА ОШИБКА  ❌            ║\n"
                    "╚══════════════════════════════════════════════╝\n\n"
                    "Пожалуйста, попробуйте еще раз или начните заново с /start\n\n"
                    "Мы уже работаем над исправлением! 🔧"
                ),
                parse_mode="Markdown"
            )
    except:
        pass

# ========== ВЕБХУКИ ==========

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка вебхуков от Telegram"""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        update = telegram.Update.de_json(request.get_json(force=True), bot)
        dispatcher.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        logger.error(traceback.format_exc())
        return "Error", 500

@app.route('/set_webhook')
def set_webhook():
    """Установка вебхука"""
    if not bot:
        return "Bot not initialized", 500
    
    try:
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta http-equiv="refresh" content="3;url=/">
            {PREMIUM_CSS}
            {PREMIUM_JS}
        </head>
        <body>
            <div id="particles-js"></div>
            
            <div style="display: flex; justify-content: center; align-items: center; min-height: 100vh; padding: 20px;">
                <div class="glass-card" style="text-align: center; padding: 60px; max-width: 500px;">
                    <i class="fas fa-check-circle" style="font-size: 5em; color: #0f0; margin-bottom: 30px;"></i>
                    <h1 class="neon-text" style="font-size: 2em; margin-bottom: 20px;">WEBHOOK УСТАНОВЛЕН</h1>
                    <p style="color: #fff; font-size: 1.1em; margin-bottom: 10px;">{webhook_url}</p>
                    <div class="quantum-loader" style="margin: 30px auto;"></div>
                    <p style="color: #aaa;">Перенаправление через 3 секунды...</p>
                </div>
            </div>
        </body>
        </html>
        """
        return render_template_string(html)
    except Exception as e:
        return f"Error: {e}", 500

# ========== ЗАПУСК ==========

def run_bot():
    """Запуск бота и веб-сервера"""
    global updater, dispatcher, bot
    
    try:
        # Создаем бота
        bot = telegram.Bot(token=TOKEN)
        
        # Создаем updater
        updater = Updater(token=TOKEN, use_context=True)
        dispatcher = updater.dispatcher
        
        # Добавляем обработчик ошибок
        dispatcher.add_error_handler(error_handler)
        
        # Обработчик команды /start
        dispatcher.add_handler(CommandHandler("start", start))
        
        # Обработчик для файлов
        file_handler = MessageHandler(
            Filters.document | Filters.photo,
            handle_file
        )
        dispatcher.add_handler(file_handler)
        
        # Обработчик для текстовых сообщений
        text_handler = MessageHandler(
            Filters.text & ~Filters.command,
            handle_quantity_input
        )
        dispatcher.add_handler(text_handler)
        
        # Обработчик для callback-запросов
        dispatcher.add_handler(CallbackQueryHandler(button_handler))
        
        # Обработчик для комментариев
        comment_handler = MessageHandler(
            Filters.text & ~Filters.command,
            handle_comment_input
        )
        dispatcher.add_handler(comment_handler)
        
        # Устанавливаем вебхук
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        
        # Информация о боте
        bot_info = bot.get_me()
        
        # Красивый вывод в консоль
        print("\n" + "="*60)
        print("╔══════════════════════════════════════════════════════╗")
        print("║     🚀 PRINT BOT PREMIUM v4.20 ЗАПУЩЕН! 🚀         ║")
        print("╚══════════════════════════════════════════════════════╝")
        print("="*60)
        print(f"🤖 Бот: @{bot_info.username}")
        print(f"📁 Папка заказов: {ORDERS_PATH}")
        print(f"👤 ID администратора: {ADMIN_CHAT_ID}")
        print(f"🌐 Веб-интерфейс: {RENDER_URL}")
        print(f"🔗 Webhook: {webhook_url}")
        print("="*60)
        print("✨ СУПЕР-ПРЕМИУМ РЕЖИМ АКТИВИРОВАН! ✨")
        print("="*60 + "\n")
        
        # Запускаем Flask
        app.run(host="0.0.0.0", port=PORT)
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка запуска: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    # Создаем папку для заказов если её нет
    if not os.path.exists(ORDERS_PATH):
        os.makedirs(ORDERS_PATH, exist_ok=True)
        print(f"📁 Создана папка заказов: {ORDERS_PATH}")
    
    # Загружаем статистику пользователей из истории
    history = load_orders_history()
    for order in history:
        user_id = order.get('user_id')
        if user_id:
            user_stats[user_id]["orders"] += 1
            user_stats[user_id]["photos"] += order.get('total_photos_print', 0)
            user_stats[user_id]["pages"] += order.get('total_pages_print', 0)
            user_stats[user_id]["spent"] += order.get('total_price', 0)
    
    # Запускаем бота
    run_bot()
