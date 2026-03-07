#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
С функциями: статусы заказов, уведомления, предпросмотр фото, дизайн
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
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template_string

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
    "new": {"emoji": "🆕", "text": "Новый", "color": "#e3f2fd"},
    "processing": {"emoji": "🔄", "text": "В обработке", "color": "#fff3e0"},
    "printing": {"emoji": "🖨️", "text": "В печати", "color": "#e8f5e8"},
    "ready": {"emoji": "✅", "text": "Готов", "color": "#e8e8f5"},
    "shipped": {"emoji": "📦", "text": "Отправлен", "color": "#f3e5f5"},
    "delivered": {"emoji": "🏁", "text": "Доставлен", "color": "#e8f0fe"},
    "cancelled": {"emoji": "❌", "text": "Отменен", "color": "#ffebee"}
}

def get_status_text(status_code):
    """Возвращает текст статуса"""
    return ORDER_STATUSES.get(status_code, {}).get("text", status_code)

def get_status_emoji(status_code):
    """Возвращает эмодзи статуса"""
    return ORDER_STATUSES.get(status_code, {}).get("emoji", "❓")

def get_status_display(status_code):
    """Возвращает полное отображение статуса (эмодзи + текст)"""
    status = ORDER_STATUSES.get(status_code, {})
    return f"{status.get('emoji', '❓')} {status.get('text', status_code)}"

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
        
        # Добавляем статус по умолчанию
        order_data["status"] = "new"
        order_data["status_history"] = [{
            "status": "new",
            "timestamp": datetime.now().isoformat(),
            "note": "Заказ создан"
        }]
        
        history.append(order_data)
        
        with open(ORDERS_DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
            
        logger.info(f"✅ Заказ сохранен в историю: {order_data.get('order_id', 'unknown')}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения истории: {e}")
        return False

def update_order_status(order_id, new_status, note=""):
    """Обновляет статус заказа в истории"""
    try:
        history = load_orders_history()
        updated = False
        
        for order in history:
            if order.get('order_id') == order_id or order.get('folder', '').endswith(order_id):
                old_status = order.get('status', 'new')
                order['status'] = new_status
                
                if 'status_history' not in order:
                    order['status_history'] = []
                
                order['status_history'].append({
                    "status": new_status,
                    "timestamp": datetime.now().isoformat(),
                    "note": note
                })
                
                updated = True
                logger.info(f"✅ Статус заказа {order_id} изменен: {old_status} -> {new_status}")
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
                
                # Заменяем или добавляем строку со статусом
                status_line = f"Статус: {get_status_display(new_status)}"
                if "Статус:" in content:
                    import re
                    content = re.sub(r'Статус:.*\n', status_line + '\n', content)
                else:
                    content += f"\n{status_line}\n"
                
                with open(info_file, 'w', encoding='utf-8') as f:
                    f.write(content)
            
            # Отправляем уведомление клиенту об изменении статуса
            if bot and order.get('user_id'):
                try:
                    client_message = (
                        f"📢 **Обновление статуса вашего заказа**\n\n"
                        f"🆔 Заказ: {order_id}\n"
                        f"📌 Новый статус: {get_status_display(new_status)}\n"
                    )
                    if note:
                        client_message += f"📝 Комментарий: {note}\n"
                    
                    bot.send_message(
                        chat_id=order['user_id'],
                        text=client_message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"✅ Уведомление об изменении статуса отправлено клиенту {order['user_id']}")
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки уведомления клиенту: {e}")
        
        return updated
    except Exception as e:
        logger.error(f"❌ Ошибка обновления статуса: {e}")
        return False

# ========== ФУНКЦИЯ ДЛЯ ОЧИСТКИ СТАРЫХ ЗАКАЗОВ ==========
def clean_old_orders(days=30):
    """Удаляет заказы старше указанного количества дней"""
    try:
        now = datetime.now()
        count = 0
        for item in os.listdir(ORDERS_PATH):
            item_path = os.path.join(ORDERS_PATH, item)
            if os.path.isdir(item_path) and item != "orders_history.json":
                # Получаем время создания папки
                created = datetime.fromtimestamp(os.path.getctime(item_path))
                age = now - created
                
                # Если папка старше days дней, удаляем
                if age.days > days:
                    shutil.rmtree(item_path, ignore_errors=True)
                    count += 1
                    print(f"🗑️ Удален старый заказ: {item} (возраст {age.days} дней)")
        
        if count > 0:
            print(f"✅ Очистка завершена. Удалено {count} старых заказов")
    except Exception as e:
        print(f"❌ Ошибка при очистке старых заказов: {e}")

# Запускаем очистку при старте (удаляем заказы старше 30 дней)
clean_old_orders(30)

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
            "total_items": total_photos + total_pages,
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
            admin_message += f"📸 Фото: {len(photo_files)} файлов, {total_photos} фото в оригинале\n"
        if doc_files:
            admin_message += f"📄 Документы: {len(doc_files)} файлов, {total_pages} страниц в оригинале\n"
        
        admin_message += f"💰 Сумма: {order_data['total']} руб.\n"
        admin_message += f"⏳ Срок: {order_data['delivery']}\n\n"
        admin_message += f"🔗 Ссылка для управления:\n{order_url}\n\n"
        admin_message += f"📁 Папка на сервере:\n{order_folder}"
        
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
            
            # Сохраняем в сессию с правильным типом
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
        total_photos = user_sessions[user_id]["total_photos"]
        total_pages = user_sessions[user_id]["total_pages"]
        total_items = total_photos + total_pages
        query.edit_message_text(
            f"🔢 В файлах всего {total_items} единиц.\n"
            f"📸 Фото: {total_photos}\n"
            f"📄 Страниц: {total_pages}\n\n"
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
            query.edit_message_text("❌ Ошибка: сессия не найдена")
            return WAITING_FOR_FILE
        
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
            
            # Уведомление клиенту с предпросмотром (если есть фото)
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
                "Мы будем уведомлять вас об изменениях статуса!\n\n"
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
                    media_group = []
                    for i, photo_file in enumerate(photo_files[:10]):  # Максимум 10 фото
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

# ========== ФУНКЦИЯ ДЛЯ ПЕРИОДИЧЕСКОЙ ОЧИСТКИ ==========
def scheduled_cleanup():
    """Запускает очистку старых заказов раз в день"""
    clean_old_orders(30)
    # Запускаем снова через 24 часа
    threading.Timer(86400, scheduled_cleanup).start()

# Запускаем периодическую очистку
threading.Timer(86400, scheduled_cleanup).start()

# ========== ВЕБ-ИНТЕРФЕЙС С НОВЫМ ДИЗАЙНОМ ==========
app = Flask(__name__)

# HTML шаблон для главной страницы
HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Print Bot - Главная</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            width: 100%;
        }
        
        .hero {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            padding: 60px 40px;
            color: white;
            text-align: center;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.2);
            margin-bottom: 30px;
        }
        
        .hero h1 {
            font-size: 3.5em;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
        }
        
        .hero h1 i {
            font-size: 1.2em;
        }
        
        .hero p {
            font-size: 1.2em;
            opacity: 0.9;
            max-width: 600px;
            margin: 0 auto 30px;
        }
        
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 40px;
        }
        
        .stat-card {
            background: rgba(255, 255, 255, 0.15);
            border-radius: 20px;
            padding: 30px 20px;
            text-align: center;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .stat-card:hover {
            background: rgba(255, 255, 255, 0.2);
            transform: translateY(-5px);
        }
        
        .stat-icon {
            font-size: 2.5em;
            margin-bottom: 15px;
        }
        
        .stat-value {
            font-size: 2.5em;
            font-weight: bold;
            margin-bottom: 5px;
        }
        
        .stat-label {
            font-size: 0.9em;
            opacity: 0.8;
        }
        
        .actions-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-top: 30px;
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
            transform: translateY(-5px);
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.15);
        }
        
        .action-icon {
            font-size: 3em;
            margin-bottom: 15px;
        }
        
        .action-title {
            font-size: 1.3em;
            font-weight: bold;
            margin-bottom: 10px;
        }
        
        .action-desc {
            font-size: 0.9em;
            color: #666;
        }
        
        .info-section {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 20px;
            padding: 30px;
            margin-top: 30px;
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
        }
        
        .contact-item {
            background: rgba(255, 255, 255, 0.1);
            padding: 15px 25px;
            border-radius: 15px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        @media (max-width: 768px) {
            .hero h1 {
                font-size: 2.5em;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="hero">
            <h1>
                <span>🤖</span> Print Bot
            </h1>
            <p>Сервис для печати фото и документов через Telegram</p>
            
            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-icon">📦</div>
                    <div class="stat-value">{orders_count}</div>
                    <div class="stat-label">активных заказов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">📊</div>
                    <div class="stat-value">{total_orders}</div>
                    <div class="stat-label">всего заказов</div>
                </div>
                <div class="stat-card">
                    <div class="stat-icon">💰</div>
                    <div class="stat-value">{total_revenue}</div>
                    <div class="stat-label">руб. прибыль</div>
                </div>
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

# HTML шаблон для списка заказов
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        .header {
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 30px;
            color: white;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
        }
        
        .header h1 {
            font-size: 2.5em;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .stats {
            display: flex;
            gap: 20px;
            margin-top: 20px;
            flex-wrap: wrap;
        }
        
        .stat-card {
            background: rgba(255, 255, 255, 0.2);
            border-radius: 15px;
            padding: 15px 25px;
            display: flex;
            align-items: center;
            gap: 15px;
        }
        
        .stat-icon {
            font-size: 2em;
        }
        
        .stat-info h3 {
            font-size: 0.9em;
            opacity: 0.9;
            margin-bottom: 5px;
        }
        
        .stat-info p {
            font-size: 1.5em;
            font-weight: bold;
        }
        
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
            border-radius: 12px;
            display: inline-flex;
            align-items: center;
            gap: 10px;
            transition: all 0.3s ease;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .nav-btn:hover {
            background: rgba(255, 255, 255, 0.25);
            transform: translateY(-2px);
        }
        
        .orders-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
            gap: 25px;
        }
        
        .order-card {
            background: white;
            border-radius: 20px;
            overflow: hidden;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
            transition: all 0.3s ease;
        }
        
        .order-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 15px 40px rgba(0, 0, 0, 0.2);
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
            border-radius: 20px;
            font-size: 0.9em;
            backdrop-filter: blur(5px);
            font-weight: 500;
        }
        
        .order-content {
            padding: 20px;
        }
        
        .order-stats {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            margin-bottom: 15px;
            padding-bottom: 15px;
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
        
        .status-menu {
            margin: 15px 0;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 10px;
        }
        
        .status-menu h4 {
            margin-bottom: 10px;
            color: #333;
        }
        
        .status-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        
        .status-btn {
            padding: 8px 12px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 0.85em;
            transition: all 0.2s ease;
            background: white;
            border: 1px solid #dee2e6;
        }
        
        .status-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        
        {% for status, data in statuses.items() %}
        .status-btn.{{ status }} { background: {{ data.color }}; color: #333; }
        {% endfor %}
        
        .files-list {
            margin: 15px 0;
            max-height: 200px;
            overflow-y: auto;
        }
        
        .file-item {
            display: flex;
            align-items: center;
            padding: 8px;
            background: #f8f9fa;
            border-radius: 8px;
            margin-bottom: 5px;
            transition: all 0.2s ease;
        }
        
        .file-item:hover {
            background: #e9ecef;
        }
        
        .file-icon {
            font-size: 1.2em;
            margin-right: 10px;
        }
        
        .file-info {
            flex: 1;
        }
        
        .file-name {
            font-size: 0.9em;
            color: #333;
            word-break: break-all;
        }
        
        .file-size {
            font-size: 0.8em;
            color: #666;
        }
        
        .file-download {
            color: #667eea;
            text-decoration: none;
            padding: 5px 10px;
            border-radius: 5px;
            transition: all 0.2s ease;
        }
        
        .file-download:hover {
            background: #e9ecef;
        }
        
        .photo-preview {
            max-width: 100px;
            max-height: 100px;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            object-fit: cover;
        }
        
        .photo-preview:hover {
            transform: scale(1.1);
            box-shadow: 0 5px 15px rgba(0,0,0,0.2);
        }
        
        .photo-gallery {
            display: flex;
            gap: 10px;
            overflow-x: auto;
            padding: 15px 0;
            margin-bottom: 15px;
        }
        
        .order-actions {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        
        .action-btn {
            flex: 1;
            padding: 12px;
            border: none;
            border-radius: 10px;
            font-size: 0.9em;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            text-decoration: none;
            transition: all 0.3s ease;
        }
        
        .btn-download-all {
            background: #28a745;
            color: white;
        }
        
        .btn-download-all:hover {
            background: #218838;
            transform: translateY(-2px);
        }
        
        .btn-view {
            background: #667eea;
            color: white;
        }
        
        .btn-view:hover {
            background: #5a67d8;
            transform: translateY(-2px);
        }
        
        .order-info {
            background: #f8f9fa;
            border-radius: 10px;
            padding: 15px;
            margin: 15px 0;
            font-size: 0.9em;
            white-space: pre-wrap;
            max-height: 200px;
            overflow-y: auto;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            background: rgba(255, 255, 255, 0.1);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            color: white;
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
        
        @media (max-width: 768px) {
            .orders-grid {
                grid-template-columns: 1fr;
            }
            
            .header h1 {
                font-size: 2em;
            }
            
            .stats {
                flex-direction: column;
            }
        }
    </style>
    <script>
        function updateStatus(orderId, status) {
            fetch(`/orders/${orderId}/status`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({status: status})
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    alert('Ошибка при обновлении статуса: ' + (data.error || 'неизвестная ошибка'));
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Ошибка при обновлении статуса');
            });
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                <span>📦</span> Заказы на печать
            </h1>
            <p>Управление заказами и файлами</p>
            
            <div class="stats">
                <div class="stat-card">
                    <span class="stat-icon">📊</span>
                    <div class="stat-info">
                        <h3>Всего заказов</h3>
                        <p>{orders_count}</p>
                    </div>
                </div>
                <div class="stat-card">
                    <span class="stat-icon">📁</span>
                    <div class="stat-info">
                        <h3>Всего файлов</h3>
                        <p>{total_files}</p>
                    </div>
                </div>
                <div class="stat-card">
                    <span class="stat-icon">💰</span>
                    <div class="stat-info">
                        <h3>Общая сумма</h3>
                        <p>{total_sum} руб.</p>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="nav-links">
            <a href="/" class="nav-btn">
                <span>🏠</span> На главную
            </a>
            <a href="/stats" class="nav-btn">
                <span>📊</span> Статистика
            </a>
            <a href="/health" class="nav-btn">
                <span>❤️</span> Здоровье
            </a>
        </div>
        
        {% if orders %}
        <div class="orders-grid">
            {% for order in orders %}
            <div class="order-card" id="order-{{ order.id }}">
                <div class="order-header">
                    <h2>{{ order.id }}</h2>
                    <div class="order-date">
                        <span>📅</span> {{ order.created }}
                    </div>
                    <div class="order-status-badge" style="background: {{ order.status_color }}">
                        {{ order.status_display }}
                    </div>
                </div>
                
                <div class="order-content">
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
                    
                    <div class="status-menu">
                        <h4>📌 Изменить статус:</h4>
                        <div class="status-buttons">
                            {% for status, data in statuses.items() %}
                            <button class="status-btn {{ status }}" onclick="updateStatus('{{ order.id }}', '{{ status }}')">
                                {{ data.emoji }} {{ data.text }}
                            </button>
                            {% endfor %}
                        </div>
                    </div>
                    
                    {% if order.info %}
                    <div class="order-info">{{ order.info }}</div>
                    {% endif %}
                    
                    {% if order.photos %}
                    <div class="photo-gallery">
                        {% for photo in order.photos %}
                        <img src="{{ photo.url }}" class="photo-preview" alt="{{ photo.name }}" onclick="window.open('{{ photo.url }}', '_blank')">
                        {% endfor %}
                    </div>
                    {% endif %}
                    
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
        <div class="empty-state">
            <div class="empty-icon">📭</div>
            <div class="empty-text">Заказов пока нет</div>
        </div>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    """Главная страница"""
    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    
    # Считаем статистику
    orders_count = 0
    total_files = 0
    total_sum = 0
    
    if os.path.exists(ORDERS_PATH):
        for item in os.listdir(ORDERS_PATH):
            item_path = os.path.join(ORDERS_PATH, item)
            if os.path.isdir(item_path) and item != "orders_history.json":
                orders_count += 1
    
    history = load_orders_history()
    total_orders = len(history)
    total_revenue = sum(order.get('total_price', 0) for order in history)
    
    return render_template_string(
        HOME_TEMPLATE,
        orders_count=orders_count,
        total_orders=total_orders,
        total_revenue=total_revenue,
        phone=CONTACT_PHONE,
        delivery=DELIVERY_OPTIONS,
        time=current_time
    )

@app.route('/orders/')
def list_orders():
    """Список всех заказов"""
    try:
        orders = []
        if os.path.exists(ORDERS_PATH):
            for item in os.listdir(ORDERS_PATH):
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
                    
                    for f in sorted(os.listdir(item_path)):
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
                    
                    # Получаем время создания
                    created = datetime.fromtimestamp(os.path.getctime(item_path))
                    age = datetime.now() - created
                    
                    orders.append({
                        'id': item,
                        'name': item,
                        'info': info_text.replace('\n', '<br>'),
                        'files': files,
                        'photos': photos[:5],  # Первые 5 фото для галереи
                        'file_count': file_count,
                        'total_size': format_file_size(total_size),
                        'created': created.strftime('%d.%m.%Y %H:%M'),
                        'age_days': age.days,
                        'status': status,
                        'status_display': get_status_display(status),
                        'status_color': ORDER_STATUSES.get(status, {}).get('color', '#f8f9fa')
                    })
        
        # Сортируем по дате (новые сверху)
        orders.sort(key=lambda x: x['created'], reverse=True)
        
        # Общая статистика
        total_files = sum(o['file_count'] for o in orders)
        total_sum = 0
        
        return render_template_string(
            ORDERS_TEMPLATE,
            orders=orders,
            orders_count=len(orders),
            total_files=total_files,
            total_sum=total_sum,
            statuses=ORDER_STATUSES
        )
    except Exception as e:
        logger.error(f"Ошибка при отображении заказов: {e}")
        return f"Ошибка: {e}", 500

@app.route('/orders/<path:order_name>/')
def view_order(order_name):
    """Просмотр конкретного заказа"""
    order_path = os.path.join(ORDERS_PATH, order_name)
    if not os.path.exists(order_path) or not os.path.isdir(order_path):
        return "Заказ не найден", 404
    
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
                'url': f'/orders/{order_name}/{f}',
                'is_photo': is_photo
            }
            files.append(file_info)
            if is_photo:
                photos.append(file_info)
    
    # Читаем информацию о заказе
    info = ""
    info_file = os.path.join(order_path, "информация_о_заказе.txt")
    if os.path.exists(info_file):
        with open(info_file, 'r', encoding='utf-8') as f:
            info = f.read()
    
    # Получаем статус из истории
    status = "new"
    history = load_orders_history()
    for h in history:
        if h.get('order_id') == order_name:
            status = h.get('status', 'new')
            break
    
    created = datetime.fromtimestamp(os.path.getctime(order_path))
    
    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Заказ {order_name}</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }}
            
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            
            .header {{
                background: rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(10px);
                border-radius: 20px;
                padding: 30px;
                margin-bottom: 30px;
                color: white;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
            }}
            
            .header h1 {{
                font-size: 2em;
                margin-bottom: 10px;
                display: flex;
                align-items: center;
                gap: 10px;
                word-break: break-all;
            }}
            
            .order-meta {{
                display: flex;
                gap: 20px;
                margin-top: 15px;
                flex-wrap: wrap;
            }}
            
            .meta-item {{
                background: rgba(255, 255, 255, 0.2);
                padding: 10px 15px;
                border-radius: 10px;
                font-size: 0.9em;
            }}
            
            .nav-links {{
                display: flex;
                gap: 15px;
                margin-bottom: 30px;
                flex-wrap: wrap;
            }}
            
            .nav-btn {{
                background: rgba(255, 255, 255, 0.15);
                backdrop-filter: blur(10px);
                color: white;
                text-decoration: none;
                padding: 12px 25px;
                border-radius: 12px;
                display: inline-flex;
                align-items: center;
                gap: 10px;
                transition: all 0.3s ease;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
            
            .nav-btn:hover {{
                background: rgba(255, 255, 255, 0.25);
                transform: translateY(-2px);
            }}
            
            .content {{
                background: white;
                border-radius: 20px;
                padding: 30px;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.15);
            }}
            
            .status-section {{
                margin-bottom: 30px;
                padding: 20px;
                background: #f8f9fa;
                border-radius: 15px;
            }}
            
            .status-section h2 {{
                margin-bottom: 15px;
                color: #333;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            
            .status-buttons {{
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
            }}
            
            .status-btn {{
                padding: 10px 20px;
                border: none;
                border-radius: 10px;
                cursor: pointer;
                font-size: 0.95em;
                transition: all 0.2s ease;
                background: white;
                border: 1px solid #dee2e6;
            }}
            
            .status-btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 2px 5px rgba(0,0,0,0.1);
            }}
            
            .status-btn.new {{ background: #e3f2fd; }}
            .status-btn.processing {{ background: #fff3e0; }}
            .status-btn.printing {{ background: #e8f5e8; }}
            .status-btn.ready {{ background: #e8e8f5; }}
            .status-btn.shipped {{ background: #f3e5f5; }}
            .status-btn.delivered {{ background: #e8f0fe; }}
            .status-btn.cancelled {{ background: #ffebee; }}
            
            .info-section {{
                background: #f8f9fa;
                border-radius: 15px;
                padding: 20px;
                margin-bottom: 30px;
            }}
            
            .info-section h2 {{
                color: #333;
                margin-bottom: 15px;
                display: flex;
                align-items: center;
                gap: 10px;
                font-size: 1.3em;
            }}
            
            .info-content {{
                white-space: pre-wrap;
                font-family: monospace;
                background: white;
                padding: 15px;
                border-radius: 10px;
                border: 1px solid #eee;
                max-height: 400px;
                overflow-y: auto;
            }}
            
            .photos-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                gap: 15px;
                margin: 20px 0;
            }}
            
            .photo-card {{
                background: #f8f9fa;
                border-radius: 10px;
                padding: 10px;
                text-align: center;
                transition: all 0.3s ease;
            }}
            
            .photo-card:hover {{
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            }}
            
            .photo-img {{
                max-width: 100%;
                max-height: 150px;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.2s ease;
                object-fit: cover;
            }}
            
            .photo-img:hover {{
                transform: scale(1.05);
            }}
            
            .photo-name {{
                margin-top: 10px;
                font-size: 0.85em;
                color: #666;
                word-break: break-all;
            }}
            
            .files-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
                gap: 15px;
                margin-top: 20px;
            }}
            
            .file-card {{
                background: #f8f9fa;
                border-radius: 12px;
                padding: 15px;
                display: flex;
                align-items: center;
                gap: 15px;
                transition: all 0.3s ease;
                border: 1px solid #eee;
                text-decoration: none;
                color: inherit;
            }}
            
            .file-card:hover {{
                background: #e9ecef;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.1);
            }}
            
            .file-icon {{
                font-size: 2em;
                min-width: 50px;
                text-align: center;
            }}
            
            .file-details {{
                flex: 1;
            }}
            
            .file-name {{
                font-weight: 500;
                color: #333;
                word-break: break-all;
                margin-bottom: 5px;
            }}
            
            .file-size {{
                font-size: 0.85em;
                color: #666;
            }}
            
            .download-all {{
                display: inline-block;
                background: #28a745;
                color: white;
                text-decoration: none;
                padding: 15px 30px;
                border-radius: 12px;
                font-size: 1.1em;
                margin-top: 30px;
                transition: all 0.3s ease;
                border: none;
                cursor: pointer;
            }}
            
            .download-all:hover {{
                background: #218838;
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(0, 0, 0, 0.2);
            }}
        </style>
        <script>
            function updateStatus(status) {{
                fetch(`/orders/{order_name}/status`, {{
                    method: 'POST',
                    headers: {{
                        'Content-Type': 'application/json',
                    }},
                    body: JSON.stringify({{status: status, note: 'Обновлено через веб-интерфейс'}})
                }})
                .then(response => response.json())
                .then(data => {{
                    if (data.success) {{
                        location.reload();
                    }} else {{
                        alert('Ошибка при обновлении статуса: ' + (data.error || 'неизвестная ошибка'));
                    }}
                }})
                .catch(error => {{
                    console.error('Error:', error);
                    alert('Ошибка при обновлении статуса');
                }});
            }}
        </script>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>
                    <span>📁</span> Заказ: {order_name}
                </h1>
                <p>Информация о заказе и файлы</p>
                <div class="order-meta">
                    <span class="meta-item">📅 Создан: {created.strftime('%d.%m.%Y %H:%M')}</span>
                    <span class="meta-item">📦 Файлов: {len(files)}</span>
                    <span class="meta-item">💾 Размер: {format_file_size(total_size)}</span>
                    <span class="meta-item">📌 Статус: {get_status_display(status)}</span>
                </div>
            </div>
            
            <div class="nav-links">
                <a href="/orders/" class="nav-btn">
                    <span>←</span> К списку заказов
                </a>
                <a href="/" class="nav-btn">
                    <span>🏠</span> На главную
                </a>
            </div>
            
            <div class="content">
                <div class="status-section">
                    <h2>
                        <span>📌</span> Управление статусом заказа
                    </h2>
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
                
                <div class="info-section">
                    <h2>
                        <span>📋</span> Информация о заказе
                    </h2>
                    <div class="info-content">{info.replace(chr(10), '<br>')}</div>
                </div>
                
                {f'''
                <h2 style="margin-bottom: 20px;">📸 Предпросмотр фото</h2>
                <div class="photos-grid">
                    {''.join([f'''
                    <div class="photo-card">
                        <img src="{p['url']}" class="photo-img" alt="{p['name']}" onclick="window.open('{p['url']}', '_blank')">
                        <div class="photo-name">{p['name']}</div>
                    </div>
                    ''' for p in photos])}
                </div>
                ''' if photos else ''}
                
                <h2 style="margin: 30px 0 20px 0;">📄 Все файлы</h2>
                
                <div class="files-grid">
                    {''.join([f'''
                    <a href="{f['url']}" class="file-card" download>
                        <span class="file-icon">{'📸' if f['is_photo'] else '📄'}</span>
                        <div class="file-details">
                            <div class="file-name">{f['name']}</div>
                            <div class="file-size">{f['size_formatted']}</div>
                        </div>
                    </a>
                    ''' for f in files])}
                </div>
                
                <div style="text-align: center; margin-top: 40px;">
                    <a href="/orders/{order_name}/download" class="download-all">
                        ⬇️ Скачать все файлы (ZIP)
                    </a>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.route('/orders/<path:order_name>/status', methods=['POST'])
def update_order_status_route(order_name):
    """Обновление статуса заказа"""
    try:
        data = request.get_json()
        new_status = data.get('status')
        note = data.get('note', '')
        
        if not new_status:
            return jsonify({"success": False, "error": "Не указан статус"}), 400
        
        if new_status not in ORDER_STATUSES:
            return jsonify({"success": False, "error": "Некорректный статус"}), 400
        
        success = update_order_status(order_name, new_status, note)
        
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Заказ не найден"}), 404
            
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

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
        total_files = 0
        total_size = 0
        
        if os.path.exists(ORDERS_PATH):
            for item in os.listdir(ORDERS_PATH):
                item_path = os.path.join(ORDERS_PATH, item)
                if os.path.isdir(item_path) and item != "orders_history.json":
                    orders_count += 1
                    for f in os.listdir(item_path):
                        if f != "информация_о_заказе.txt":
                            total_files += 1
                            total_size += os.path.getsize(os.path.join(item_path, f))
        
        # Загружаем историю
        history = load_orders_history()
        
        # Статистика по статусам
        status_stats = {}
        for order in history:
            status = order.get('status', 'new')
            status_stats[status] = status_stats.get(status, 0) + 1
        
        return jsonify({
            "status": "ok",
            "orders_count": orders_count,
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "active_sessions": len(user_sessions),
            "bot_ready": dispatcher is not None,
            "orders_folder": ORDERS_PATH,
            "orders_url": f"{RENDER_URL}/orders/",
            "status_stats": status_stats,
            "history_count": len(history)
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

# ========== ИНИЦИАЛИЗАЦИЯ ==========
print("=" * 60)
print("🚀 ЗАПУСК БОТА")
print("=" * 60)
print(f"📁 Папка для заказов: {ORDERS_PATH}")
print(f"📁 URL для просмотра заказов: {RENDER_URL}/orders/")
print(f"👤 ID администратора: {ADMIN_CHAT_ID}")
print(f"📊 Статусы заказов: {len(ORDER_STATUSES)} шт.")
print(f"🗑️ Автоматическая очистка: 30 дней")

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
