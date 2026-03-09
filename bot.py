#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
Современный минималистичный дизайн
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

# ========== НОВЫЙ МИНИМАЛИСТИЧНЫЙ ДИЗАЙН ДЛЯ TELEGRAM ==========

def format_bold(text):
    """Форматирует жирный текст"""
    return f"**{text}**"

def format_italic(text):
    """Форматирует курсив"""
    return f"*{text}*"

def format_code(text):
    """Форматирует моноширинный текст"""
    return f"`{text}`"

def section(title, emoji="📌"):
    """Создает заголовок раздела"""
    return f"\n{emoji} {format_bold(title)}\n"

def bullet(text, emoji="•"):
    """Создает пункт списка"""
    return f"{emoji} {text}"

def stat_item(label, value):
    """Создает элемент статистики"""
    return f"  {label}: {value}"

def price_tag(amount):
    """Форматирует цену"""
    return f"💰 {format_bold(str(amount))} ₽"

def file_info(name, items, unit):
    """Форматирует информацию о файле"""
    return f"📄 {format_code(name[:30])}... — {items} {unit}"

def order_summary(order_id, total):
    """Форматирует сводку заказа"""
    return f"🆔 {format_code(order_id)} — {price_tag(total)}"

def success_message(text):
    """Форматирует сообщение об успехе"""
    return f"✅ {text}"

def error_message(text):
    """Форматирует сообщение об ошибке"""
    return f"❌ {text}"

def warning_message(text):
    """Форматирует предупреждение"""
    return f"⚠️ {text}"

def divider():
    """Создает разделитель"""
    return "──────────"

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
        admin_message = f"🆕 {format_bold('НОВЫЙ ЗАКАЗ!')}\n\n"
        admin_message += f"👤 Клиент: {order_data['user_info']['first_name']} (@{order_data['user_info']['username']})\n"
        admin_message += f"🆔 ID: {format_code(str(order_data['user_info']['user_id']))}\n\n"
        
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
        
        admin_message += f"💰 Сумма: {price_tag(order_data['total'])}\n"
        admin_message += f"⏳ Срок: {order_data['delivery']}\n\n"
        admin_message += f"🔗 Ссылка: {order_url}"
        
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

# ========== ОБНОВЛЕННЫЕ ОБРАБОТЧИКИ КОМАНД С НОВЫМ ДИЗАЙНОМ ==========
def start(update, context):
    """Команда /start с новым дизайном"""
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
        f"👋 {format_bold(f'Привет, {user.first_name}!')}\n\n"
        f"📸🖨️ Я помогу распечатать фото и документы.\n\n"
        f"{section('Что я умею', '✨')}"
        f"{bullet('📷 Фото (JPG, PNG) — 3 формата печати')}\n"
        f"{bullet('📄 Документы (PDF, DOC, DOCX) — ч/б и цветная')}\n"
        f"{bullet('📦 Пакетная загрузка — до 10 файлов за раз')}\n"
        f"{bullet('💰 Мгновенный расчет стоимости со скидками')}\n"
        f"{bullet('📊 Отслеживание статуса заказа')}\n\n"
        f"{divider()}\n\n"
        f"📞 {format_bold('Контакты:')} {format_code(CONTACT_PHONE)}\n"
        f"🚚 {format_bold('Доставка:')} {DELIVERY_OPTIONS}\n\n"
        f"{divider()}\n\n"
        f"⬇️ {format_bold('Отправляйте файлы, и я начну расчет!')} ⬇️"
    )
    
    update.message.reply_text(welcome, parse_mode="Markdown")
    return WAITING_FOR_FILE

def process_single_file(update, context):
    """Обработка одиночного файла с новым дизайном"""
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
            error_msg = error_message("Неподдерживаемый формат файла") + "\n\n"
            error_msg += f"{section('Допустимые форматы', '📌')}"
            error_msg += f"{bullet('📸 JPG, PNG')}\n"
            error_msg += f"{bullet('📄 PDF, DOC, DOCX')}"
            message.reply_text(error_msg, parse_mode="Markdown")
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
        message.reply_text(error_message("Ошибка загрузки файла"))
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
    
    text = success_message("Файл успешно добавлен!") + "\n\n"
    text += f"{section('Текущая статистика', '📊')}"
    
    if photo_count > 0:
        text += f"{stat_item('📸 Фото', f'{photo_count} файлов')}\n"
    if doc_count > 0:
        text += f"{stat_item('📄 Документы', f'{doc_count} файлов')}\n"
    
    if total_photos > 0:
        text += f"{stat_item('📸 Всего фото', total_photos)}\n"
    if total_pages > 0:
        text += f"{stat_item('📄 Всего страниц', total_pages)}\n"
    text += f"{stat_item('📦 Всего файлов', files_count)}\n\n"
    text += divider() + "\n\n"
    
    # Предлагаем выбор
    if doc_count > 0:
        text += f"{section('Выберите тип печати для документов', '📋')}"
        keyboard = [
            [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
            [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
            [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
        ]
    else:
        text += f"{section('Выберите формат печати для фото', '🖼️')}"
        keyboard = [
            [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
            [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
            [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
            [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
            [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
        ]
    
    message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return WAITING_FOR_FILE

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
        success_count = 0
        
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
            
            success_count += 1
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
                text=error_message("Не удалось загрузить файлы")
            )
            return
        
        files_count = len(user_sessions[user_id]["files"])
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        
        text = success_message(f"Загружено {files_count} файлов!") + "\n\n"
        text += f"{section('Статистика', '📊')}"
        
        if photo_count > 0:
            text += f"{stat_item('📸 Фото', photo_count)}\n"
        if doc_count > 0:
            text += f"{stat_item('📄 Документы', doc_count)}\n"
        
        if total_photos > 0:
            text += f"{stat_item('📸 Всего фото', total_photos)}\n"
        if total_pages > 0:
            text += f"{stat_item('📄 Всего страниц', total_pages)}\n"
        
        text += f"\n{divider()}\n\n"
        
        # Предлагаем выбор (если есть документы, приоритет у них)
        if doc_count > 0:
            text += f"{section('Выберите тип печати для документов', '📋')}"
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("➕ Добавить ещё файлы", callback_data="add_more")],
                [InlineKeyboardButton("❌ Отмена заказа", callback_data="cancel")]
            ]
        else:
            text += f"{section('Выберите формат печати для фото', '🖼️')}"
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
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке группы файлов: {e}")
        logger.error(traceback.format_exc())

def button_handler(update, context):
    """Обработка нажатий кнопок с новым дизайном"""
    query = update.callback_query
    query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info(f"🔘 Callback: {data} от {user_id}")
    
    # Отмена заказа - обрабатываем ВСЕГДА в первую очередь
    if data == "cancel":
        return cancel_order(user_id, query, context)
    
    if data == "add_more":
        query.edit_message_text(
            f"📤 {format_bold('Отправьте следующие файлы')}\n\n"
            f"{divider()}\n\n"
            f"{bullet('📸 JPG, PNG')}\n"
            f"{bullet('📄 PDF, DOC, DOCX')}",
            parse_mode="Markdown"
        )
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
            f"🔄 {format_bold('НОВЫЙ ЗАКАЗ')}\n\n"
            f"{divider()}\n\n"
            f"{bullet('📸 JPG, PNG')}\n"
            f"{bullet('📄 PDF, DOC, DOCX')}\n\n"
            f"{divider()}\n\n"
            f"Отправьте файлы для печати:",
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        if user_id not in user_sessions:
            return cancel_order(user_id, query, context)
        
        format_type = data.split("_")[1]
        format_names = {
            "small": "🖼 Малый (A6/10x15)",
            "medium": "🖼 Средний (13x18/15x21)",
            "large": "🖼 Большой (A4/21x30)"
        }
        format_prices = {
            "small": "1-9: 35₽ | 10-50: 28₽ | 51-100: 23₽ | 101+: 18₽",
            "medium": "1-9: 65₽ | 10-50: 55₽ | 51-100: 45₽ | 101+: 35₽",
            "large": "1-4: 200₽ | 5-20: 170₽ | 21-50: 150₽ | 51+: 120₽"
        }
        
        user_sessions[user_id]["type"] = "photo"
        user_sessions[user_id]["format"] = format_type
        
        text = (
            f"{format_names[format_type]}\n\n"
            f"{section('Цены', '💰')}"
            f"{bullet(format_prices[format_type])}\n\n"
            f"{divider()}\n\n"
            f"🔢 {format_bold('Сколько копий напечатать?')}\n\n"
            f"Введите число или выберите вариант:"
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
        color_names = {"bw": "⚫ Черно-белая", "color": "🎨 Цветная"}
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
            f"{color_names[doc_type]}\n\n"
            f"{section('Цены', '💰')}"
            f"{bullet(color_prices[doc_type])}\n\n"
            f"{section('В файлах', '📊')}"
            f"{bullet(f'📸 Фото: {total_photos}')}\n"
            f"{bullet(f'📄 Страниц: {total_pages}')}\n"
            f"{bullet(f'📦 Всего: {total_items} ед.')}\n\n"
            f"{divider()}\n\n"
            f"🔢 {format_bold('Сколько копий напечатать?')}\n\n"
            f"Введите число или выберите вариант:"
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
        total_photos_result = 0
        total_pages_result = 0
        details = f"{section('Детальный расчет', '🧮')}\n"
        
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
                details += f"{bullet(f'Файл {i}: {f["items"]} фото × {quantity} коп. = {f["items"] * quantity} фото → {file_total}₽')}\n"
            else:
                price_dict = DOC_PRICES[session["color"]]
                file_items = f['items'] * quantity
                file_total = calculate_price(price_dict, file_items)
                total += file_total
                details += f"{bullet(f'Файл {i}: {f["items"]} стр. × {quantity} коп. = {file_items} стр. → {file_total}₽')}\n"
        
        session["total"] = total
        session["total_photos"] = total_photos_result
        session["total_pages"] = total_pages_result
        session["delivery"] = estimate_delivery_time(total_photos_result + total_pages_result)
        
        text = (
            f"{details}\n"
            f"{section('Проверьте заказ', '📋')}\n"
            f"{bullet(f'📦 Файлов: {len(files)}')}\n"
        )
        if total_photos_result > 0:
            text += f"{bullet(f'📸 Фото к печати: {total_photos_result}')}\n"
        if total_pages_result > 0:
            text += f"{bullet(f'📄 Страниц к печати: {total_pages_result}')}\n"
        
        text += (
            f"\n{divider()}\n\n"
            f"💰 {format_bold('ИТОГО:')} {price_tag(total)}\n"
            f"⏳ {format_bold('Срок:')} {session['delivery']}\n\n"
            f"{divider()}\n\n"
            f"❓ {format_bold('Всё верно?')}"
        )
        
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
            # Отправляем уведомление админу
            send_admin_notification(session, order_id, folder)
            
            # Подсчитываем для сообщения пользователю
            photo_files = [f for f in session['files'] if f['type'] == 'photo']
            doc_files = [f for f in session['files'] if f['type'] == 'doc']
            
            total_photos = sum(f['items'] for f in photo_files)
            total_pages = sum(f['items'] for f in doc_files)
            
            # Уведомление клиенту
            client_message = (
                f"✅ {format_bold('ЗАКАЗ УСПЕШНО ОФОРМЛЕН!')}\n\n"
                f"{section('Детали заказа', '📋')}\n"
                f"{bullet(f'🆔 Номер: {format_code(order_id)}')}\n"
                f"{bullet(f'👤 Заказчик: {session["user_info"]["first_name"]}')}\n"
                f"{bullet(f'📦 Файлов: {len(session["files"])}')}\n"
            )
            
            if total_photos > 0:
                client_message += f"{bullet(f'📸 Фото в оригинале: {total_photos}')}\n"
                client_message += f"{bullet(f'📸 Фото к печати: {total_photos * session["quantity"]}')}\n"
            if total_pages > 0:
                client_message += f"{bullet(f'📄 Страниц в оригинале: {total_pages}')}\n"
                client_message += f"{bullet(f'📄 Страниц к печати: {total_pages * session["quantity"]}')}\n"
            
            client_message += (
                f"\n{divider()}\n\n"
                f"💰 {price_tag(session['total'])}\n"
                f"⏳ Срок: {session['delivery']}\n\n"
                f"{divider()}\n\n"
                f"📞 {format_code(CONTACT_PHONE)}\n"
                f"🚚 {DELIVERY_OPTIONS}\n\n"
                f"{divider()}\n\n"
                f"📌 {format_bold('Статус:')} {get_status_display('new')}\n"
                f"{bullet('Вы будете получать уведомления при изменении статуса')}\n\n"
                f"✨ {format_bold('Спасибо за заказ!')} ✨"
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
                text=error_message("Ошибка при сохранении заказа")
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
            text=f"✨ {format_bold('Хотите оформить ещё один заказ?')}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

def cancel_order(user_id, query=None, context=None):
    """Общая функция для отмены заказа с новым дизайном"""
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
    text = (
        f"❌ {format_bold('Заказ отменён')}\n\n"
        f"{bullet('Все загруженные файлы удалены')}\n\n"
        f"{divider()}\n\n"
        f"✨ {format_bold('Хотите оформить новый заказ?')}"
    )
    
    if query:
        try:
            query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        except:
            if context:
                context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
    elif context:
        context.bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    
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
            f"❌ {format_bold('Ошибка ввода')}\n\n"
            f"{bullet('Введите число от 1 до 1000')}\n\n"
            f"{divider()}\n\n"
            f"Или выберите из кнопок:",
            reply_markup=get_quantity_keyboard(),
            parse_mode="Markdown"
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

# Простой health check для Render
@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/healthz')
def healthz():
    return "OK", 200

@app.route('/ping')
def ping():
    return "pong", 200

@app.route('/')
def home():
    orders_count = len([d for d in os.listdir(ORDERS_PATH) if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Print Bot</title>
        <style>
            body {{ font-family: Arial; text-align: center; padding: 50px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
            .container {{ max-width: 600px; margin: 0 auto; background: rgba(255,255,255,0.1); padding: 30px; border-radius: 20px; }}
            h1 {{ font-size: 3em; }}
            .stats {{ font-size: 1.5em; margin: 20px 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Print Bot</h1>
            <p>Telegram бот для печати фото и документов</p>
            <div class="stats">📦 Заказов: {orders_count}</div>
            <div class="stats">📞 Контакт: {CONTACT_PHONE}</div>
            <div class="stats">⏰ {current_time}</div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html)

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

@app.route('/set_webhook')
def set_webhook():
    try:
        webhook_url = f"{RENDER_URL}/webhook"
        bot.set_webhook(url=webhook_url)
        return f"✅ Webhook установлен: {webhook_url}"
    except Exception as e:
        return f"❌ Ошибка: {e}"

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
