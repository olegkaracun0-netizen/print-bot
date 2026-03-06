import logging
from datetime import datetime
import tempfile
import json
import re
import os
import shutil
import traceback
import asyncio
import sys
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
import PyPDF2
from docx import Document

# ========== ИМПОРТЫ ДЛЯ ВЕБ-СЕРВЕРА ==========
from flask import Flask, request
import threading
import time
# =============================================

# ========== НАСТРОЙКИ ==========
# Берем токен из переменных окружения Render
TOKEN = os.environ.get("TOKEN", "8238978593:AAG-rgNUQXF8_MAkLjBgeON2FGUfHhm7YO0")
ORDERS_FOLDER = "заказы"
PORT = int(os.environ.get("PORT", 10000))

# Контактная информация (только для сообщений пользователю)
CONTACT_PHONE = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб, СДЭК, Яндекс Доставка"

# Создаем папку для заказов
if not os.path.exists(ORDERS_FOLDER):
    os.makedirs(ORDERS_FOLDER)
    print(f"📁 Создана папка: {ORDERS_FOLDER}")

# Состояния для разговора
(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
) = range(5)

# Хранилище для медиа-групп
media_groups = {}
user_sessions = {}

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

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", 
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# ========== FLASK ПРИЛОЖЕНИЕ ДЛЯ RENDER ==========
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return """
    <html>
        <head>
            <title>Print Bot</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
                .container { max-width: 800px; margin: 0 auto; background: rgba(255,255,255,0.1); padding: 30px; border-radius: 15px; backdrop-filter: blur(10px); }
                h1 { text-align: center; }
                .status { background: rgba(0,0,0,0.3); padding: 20px; border-radius: 10px; margin: 20px 0; }
                .info { margin: 10px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 Print Bot</h1>
                <div class="status">
                    <h2>✅ Бот работает 24/7!</h2>
                    <p class="info">📁 Папка заказов: <strong>заказы/</strong></p>
                    <p class="info">📞 Контакт: <strong>89219805705</strong></p>
                    <p class="info">🚚 Доставка: Самовывоз СПб, СДЭК, Яндекс</p>
                    <p class="info">⏰ Время сервера: <strong>{}</strong></p>
                </div>
                <p>Бот активен и принимает заказы в Telegram!</p>
            </div>
        </body>
    </html>
    """.format(datetime.now().strftime("%d.%m.%Y %H:%M:%S"))

@flask_app.route('/health')
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}, 200

@flask_app.route('/stats')
def stats():
    """Статистика работы бота"""
    try:
        orders_count = len([d for d in os.listdir(ORDERS_FOLDER) if os.path.isdir(os.path.join(ORDERS_FOLDER, d))]) if os.path.exists(ORDERS_FOLDER) else 0
        return {
            "status": "ok",
            "orders_count": orders_count,
            "active_sessions": len(user_sessions),
            "uptime": "24/7"
        }, 200
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500

def run_flask():
    """Запускает Flask-сервер в отдельном потоке"""
    flask_app.run(host='0.0.0.0', port=PORT)
# =================================================

def save_order_to_files(user_id, username, order_data, file_paths=None):
    """Сохранение заказа в папку с файлами (без контактов и доставки)."""
    try:
        # Создаем уникальное имя папки для заказа
        clean_username = re.sub(r'[^\w\s-]', '', username) or f"user_{user_id}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        order_folder_name = f"{clean_username}_{timestamp}"
        order_folder = os.path.join(ORDERS_FOLDER, order_folder_name)
        
        # Создаем папку для заказа
        os.makedirs(order_folder, exist_ok=True)
        logger.info(f"📁 Создана папка заказа: {order_folder}")
        
        saved_files = []
        
        # Сохраняем файлы, если они есть
        if file_paths and isinstance(file_paths, list):
            for i, file_info in enumerate(file_paths, 1):
                temp_file = file_info.get('path')
                original_filename = file_info.get('name', f'file_{i}')
                
                if temp_file and os.path.exists(temp_file):
                    # Получаем расширение файла
                    file_ext = os.path.splitext(original_filename)[1]
                    if not file_ext:
                        file_ext = ".bin"
                    
                    # Новое имя файла в папке заказа
                    new_filename = f"{i}_{original_filename}"
                    new_file_path = os.path.join(order_folder, new_filename)
                    
                    # Копируем файл
                    shutil.copy2(temp_file, new_file_path)
                    saved_files.append({
                        'original_name': original_filename,
                        'saved_name': new_filename,
                        'path': new_file_path,
                        'pages': file_info.get('page_count', 1)
                    })
                    logger.info(f"📄 Файл {i} скопирован: {new_file_path}")
        
        # Сохраняем информацию о заказе (БЕЗ КОНТАКТОВ И ДОСТАВКИ)
        info_filename = os.path.join(order_folder, "информация_о_заказе.txt")
        
        total_pages_all = order_data.get('quantity', 1) * order_data.get('total_page_count', 1)
        
        if order_data.get('file_type') == 'photo':
            format_names = {"small": "Малый (A6/10x15)", "medium": "Средний (13x18/15x21)", "large": "Большой (A4/21x30)"}
            type_text = f"Формат печати: {format_names[order_data.get('format', 'small')]}"
        else:
            color_names = {"bw": "Черно-белая", "color": "Цветная"}
            type_text = f"Тип печати: {color_names[order_data.get('color', 'bw')]}"
        
        # Формируем список сохраненных файлов
        files_list = ""
        for i, f in enumerate(saved_files, 1):
            file_icon = "📸" if f['original_name'].lower().endswith(('.jpg', '.jpeg', '.png')) else "📄"
            files_list += f"   {file_icon} Файл {i}: {f['original_name']} ({f['pages']} листов)\n"
        
        # ВНИМАНИЕ: Здесь убраны контакты и доставка!
        order_content = f"""
╔══════════════════════════════════════════════╗
║           ЗАКАЗ НА ПЕЧАТЬ                    ║
╚══════════════════════════════════════════════╝

📅 Дата: {datetime.now().strftime("%d.%m.%Y %H:%M:%S")}

👤 ИНФОРМАЦИЯ О КЛИЕНТЕ:
   • ID Telegram: {user_id}
   • Username: @{username}
   • Имя: {order_data['user_info']['first_name']}
   • Фамилия: {order_data['user_info'].get('last_name', '')}

📋 ОБЩАЯ ИНФОРМАЦИЯ:
   • Тип заказа: {'Фото' if order_data.get('file_type') == 'photo' else 'Документы'}
   • Количество файлов: {len(file_paths) if file_paths else 0}
   • Всего листов в оригинале: {order_data.get('total_page_count', 1)}
   • {type_text}
   • Количество копий: {order_data.get('quantity', 1)}
   • Всего листов к печати: {total_pages_all}
   • Стоимость: {order_data.get('total', 0)} руб.
   • Срок выполнения: {order_data.get('delivery', '1 день')}

📁 СОХРАНЕННЫЕ ФАЙЛЫ:
{files_list}
📌 СТАТУС: НОВЫЙ ЗАКАЗ
═══════════════════════════════════════════════
        """
        
        with open(info_filename, "w", encoding="utf-8") as f:
            f.write(order_content)
        
        logger.info(f"📝 Информация о заказе сохранена в {info_filename}")
        
        return True, order_folder, saved_files
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения заказа: {e}")
        logger.error(traceback.format_exc())
        return False, None, None

def calculate_price(price_dict, total_pages):
    """Рассчитывает стоимость на основе количества листов."""
    for (min_q, max_q), price in price_dict.items():
        if min_q <= total_pages <= max_q:
            return price * total_pages
    return 0

def estimate_delivery_time(total_items):
    if total_items <= 50:
        return "1 день"
    elif total_items <= 200:
        return "2 дня"
    else:
        return "3 дня"

async def download_file(file, file_name, context):
    """Скачивает файл и возвращает путь к нему."""
    try:
        temp_dir = tempfile.mkdtemp()
        temp_file_path = os.path.join(temp_dir, file_name)
        
        file_obj = await file.get_file()
        await file_obj.download_to_drive(temp_file_path)
        
        return temp_file_path, temp_dir
    except Exception as e:
        logger.error(f"Ошибка скачивания файла: {e}")
        return None, None

async def count_pages_in_file(file_path, file_name):
    """Подсчет листов в файле."""
    try:
        if file_name.lower().endswith('.pdf'):
            with open(file_path, 'rb') as pdf_file:
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                return len(pdf_reader.pages)
        elif file_name.lower().endswith(('.docx', '.doc')):
            doc = Document(file_path)
            # Подсчет страниц в Word документе
            try:
                # Пытаемся получить количество страниц через свойства документа
                core_properties = doc.core_properties
                if hasattr(core_properties, 'pages'):
                    return core_properties.pages
            except:
                pass
            
            # Если не удалось получить через свойства, используем приблизительный подсчет
            text = ""
            for paragraph in doc.paragraphs:
                text += paragraph.text + " "
            
            # Подсчет символов (приблизительно 2000 символов на страницу)
            chars = len(text)
            estimated_pages = max(1, chars // 2000)
            
            # Также учитываем таблицы
            tables_count = len(doc.tables)
            if tables_count > 0:
                estimated_pages += tables_count // 2
            
            logger.info(f"📄 Word документ: {file_name}, примерно {estimated_pages} листов")
            return estimated_pages
        else:
            return 1
    except Exception as e:
        logger.error(f"Ошибка подсчета листов: {e}")
        return 1

def extract_number_from_text(text):
    text = text.lower().strip()
    numbers = re.findall(r'\d+', text)
    if numbers:
        return int(numbers[0])
    return None

# ========== ПРИВЕТСТВИЕ ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    if user_id in user_sessions:
        if "temp_dirs" in user_sessions[user_id]:
            for temp_dir in user_sessions[user_id]["temp_dirs"]:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass
        del user_sessions[user_id]
    
    if user_id in media_groups:
        del media_groups[user_id]
    
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я твой бот-помощник по печати фото и документов. 📸🖨️\n\n"
        "✨ **Что я умею:**\n"
        "• Считать количество листов в PDF и Word документах\n"
        "• Принимать несколько файлов одним сообщением\n"
        "• Рассчитывать стоимость за каждый лист\n\n"
        "📎 Поддерживаемые форматы: JPG, PNG, PDF, DOC, DOCX\n\n"
        "📦 **Доставка:**\n"
        f"• Самовывоз СПб\n"
        f"• СДЭК\n"
        f"• Яндекс Доставка\n\n"
        f"📞 **Контактный телефон:** {CONTACT_PHONE}\n\n"
        "Отправляй файлы, и я помогу с заказом! 😊"
    )
    
    await update.message.reply_text(welcome_text, parse_mode="Markdown")
    return WAITING_FOR_FILE

# ========== ОБРАБОТКА ФАЙЛОВ ==========
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик файлов."""
    user_id = update.effective_user.id
    
    if update.message.media_group_id:
        return await handle_media_group(update, context)
    else:
        return await handle_single_file(update, context)

async def handle_media_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает группу медиафайлов (несколько файлов в одном сообщении)."""
    user_id = update.effective_user.id
    media_group_id = update.message.media_group_id
    
    if user_id not in media_groups:
        media_groups[user_id] = {}
    
    if media_group_id not in media_groups[user_id]:
        media_groups[user_id][media_group_id] = []
    
    media_groups[user_id][media_group_id].append(update.message)
    
    async def process_group():
        await asyncio.sleep(2)
        if user_id in media_groups and media_group_id in media_groups[user_id]:
            messages = media_groups[user_id].pop(media_group_id)
            if len(messages) > 0:
                await process_multiple_files(user_id, messages, context)
    
    asyncio.create_task(process_group())
    return WAITING_FOR_FILE

async def process_multiple_files(user_id, messages, context):
    """Обрабатывает несколько файлов из медиа-группы."""
    try:
        username = messages[0].from_user.username or messages[0].from_user.first_name
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "temp_files": [],
                "temp_dirs": [],
                "total_page_count": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": messages[0].from_user.first_name,
                    "last_name": messages[0].from_user.last_name or ""
                }
            }
        
        file_types = []
        doc_count = 0
        photo_count = 0
        
        for message in messages:
            if message.document:
                file = message.document
                file_name = file.file_name
                file_name_lower = file_name.lower()
                
                if file_name_lower.endswith(('.jpg', '.jpeg', '.png')):
                    file_type = "photo"
                    photo_count += 1
                elif file_name_lower.endswith('.pdf'):
                    file_type = "doc"
                    doc_count += 1
                elif file_name_lower.endswith(('.docx', '.doc')):
                    file_type = "doc"
                    doc_count += 1
                else:
                    continue
            elif message.photo:
                file = message.photo[-1]
                file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                file_type = "photo"
                photo_count += 1
            else:
                continue
            
            file_path, temp_dir = await download_file(file, file_name, context)
            if not file_path:
                continue
            
            page_count = await count_pages_in_file(file_path, file_name)
            
            user_sessions[user_id]["temp_files"].append({
                "path": file_path,
                "name": file_name,
                "type": file_type,
                "page_count": page_count
            })
            user_sessions[user_id]["temp_dirs"].append(temp_dir)
            user_sessions[user_id]["total_page_count"] += page_count
            file_types.append(file_type)
            
            logger.info(f"📊 Файл {file_name}: {page_count} листов")
        
        if doc_count > 0:
            main_type = "doc"
        else:
            main_type = "photo"
        
        user_sessions[user_id]["file_type"] = main_type
        
        files_count = len(user_sessions[user_id]["temp_files"])
        total_pages = user_sessions[user_id]["total_page_count"]
        
        if main_type == "doc":
            text = (f"📄 Загружено **{files_count}** документов!\n"
                   f"📊 Всего листов: **{total_pages}**\n\n"
                   f"Выберите тип печати для всех документов:")
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        else:
            text = (f"📸 Загружено **{files_count}** фото!\n\n"
                   f"Выберите формат печати для всех фото:")
            keyboard = [
                [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        
        await messages[0].reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
        if main_type == "doc":
            return SELECTING_DOC_TYPE
        else:
            return SELECTING_PHOTO_FORMAT
        
    except Exception as e:
        logger.error(f"Ошибка в process_multiple_files: {e}")
        logger.error(traceback.format_exc())
        await messages[0].reply_text("Произошла ошибка при обработке файлов. Попробуйте еще раз.")
        return WAITING_FOR_FILE

async def handle_single_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает одиночный файл."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    
    try:
        if update.message.document:
            file = update.message.document
            file_name = file.file_name
            file_name_lower = file_name.lower()
            
            if file_name_lower.endswith(('.jpg', '.jpeg', '.png')):
                file_type = "photo"
            elif file_name_lower.endswith('.pdf'):
                file_type = "doc"
            elif file_name_lower.endswith(('.docx', '.doc')):
                file_type = "doc"
            else:
                await update.message.reply_text("❌ Неподдерживаемый формат. Отправьте JPG, PNG, PDF или DOC/DOCX.")
                return WAITING_FOR_FILE
        elif update.message.photo:
            file = update.message.photo[-1]
            file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            file_type = "photo"
        else:
            return WAITING_FOR_FILE
        
        file_path, temp_dir = await download_file(file, file_name, context)
        if not file_path:
            await update.message.reply_text("❌ Ошибка при загрузке файла. Попробуйте еще раз.")
            return WAITING_FOR_FILE
        
        page_count = await count_pages_in_file(file_path, file_name)
        logger.info(f"📊 Файл {file_name}: {page_count} листов")
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {
                "temp_files": [],
                "temp_dirs": [],
                "total_page_count": 0,
                "user_info": {
                    "user_id": user_id,
                    "username": username,
                    "first_name": update.effective_user.first_name,
                    "last_name": update.effective_user.last_name or ""
                }
            }
        
        user_sessions[user_id]["temp_files"] = [{
            "path": file_path,
            "name": file_name,
            "type": file_type,
            "page_count": page_count
        }]
        user_sessions[user_id]["temp_dirs"] = [temp_dir]
        user_sessions[user_id]["total_page_count"] = page_count
        user_sessions[user_id]["file_type"] = file_type
        
        if file_type == "photo":
            text = f"📸 Загружено фото. Выберите формат печати:"
            keyboard = [
                [InlineKeyboardButton("🖼 Малый (A6/10x15)", callback_data="photo_small")],
                [InlineKeyboardButton("🖼 Средний (13x18/15x21)", callback_data="photo_medium")],
                [InlineKeyboardButton("🖼 Большой (A4/21x30)", callback_data="photo_large")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        else:
            text = f"📄 Загружен документ ({page_count} листов). Выберите тип печати:"
            keyboard = [
                [InlineKeyboardButton("⚫ Черно-белая", callback_data="doc_bw")],
                [InlineKeyboardButton("🎨 Цветная", callback_data="doc_color")],
                [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order")]
            ]
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        
        if file_type == "doc":
            return SELECTING_DOC_TYPE
        return SELECTING_PHOTO_FORMAT
        
    except Exception as e:
        logger.error(f"Ошибка в handle_single_file: {e}")
        logger.error(traceback.format_exc())
        await update.message.reply_text("Произошла ошибка. Попробуйте еще раз.")
        return WAITING_FOR_FILE

# ========== ОБРАБОТКА КНОПОК ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    
    logger.info("=" * 50)
    logger.info(f"🔘 Получен callback: {data}")
    logger.info(f"👤 От пользователя: {user_id}")
    logger.info("=" * 50)
    
    if data == "new_order":
        logger.info("🔄 Нажата кнопка НОВЫЙ ЗАКАЗ")
        try:
            if user_id in user_sessions:
                if "temp_dirs" in user_sessions[user_id]:
                    for temp_dir in user_sessions[user_id]["temp_dirs"]:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                del user_sessions[user_id]
            
            if user_id in media_groups:
                del media_groups[user_id]
            
            try:
                await query.message.delete()
            except:
                pass
            
            await context.bot.send_message(
                chat_id=user_id,
                text="🔄 **НОВЫЙ ЗАКАЗ**\n\nОтправьте файл(ы) для печати:",
                parse_mode="Markdown"
            )
            
            return WAITING_FOR_FILE
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return WAITING_FOR_FILE
    
    if data == "cancel_order":
        logger.info("❌ Отмена заказа")
        try:
            if user_id in user_sessions:
                if "temp_dirs" in user_sessions[user_id]:
                    for temp_dir in user_sessions[user_id]["temp_dirs"]:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                del user_sessions[user_id]
            
            if user_id in media_groups:
                del media_groups[user_id]
            
            await query.message.delete()
        except:
            pass
        
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Заказ отменен.\nЧтобы начать новый заказ, отправьте /start"
        )
        return WAITING_FOR_FILE
    
    if data.startswith("photo_"):
        format_type = data.split("_")[1]
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {}
        
        user_sessions[user_id]["format"] = format_type
        
        files_count = len(user_sessions[user_id].get("temp_files", []))
        format_names = {"small": "Малый", "medium": "Средний", "large": "Большой"}
        
        text = (f"Вы выбрали: {format_names[format_type]} формат для {files_count} файлов\n\n"
                f"Сколько копий каждого файла напечатать?\n"
                f"Введите число или выберите из вариантов:")
        
        await query.message.edit_text(text, reply_markup=get_quantity_keyboard())
        return ENTERING_QUANTITY
    
    if data.startswith("doc_"):
        doc_type = data.split("_")[1]
        
        if user_id not in user_sessions:
            user_sessions[user_id] = {}
        
        user_sessions[user_id]["color"] = doc_type
        
        total_pages = user_sessions[user_id].get("total_page_count", 1)
        files_count = len(user_sessions[user_id].get("temp_files", []))
        
        color_names = {"bw": "Черно-белая", "color": "Цветная"}
        
        text = (f"Вы выбрали: {color_names[doc_type]} печать\n"
                f"Всего листов во всех файлах: {total_pages}\n\n"
                f"Сколько копий каждого документа напечатать?\n"
                f"Введите число или выберите из вариантов:")
        
        await query.message.edit_text(text, reply_markup=get_quantity_keyboard())
        return ENTERING_QUANTITY
    
    if data.startswith("qty_"):
        try:
            quantity = int(data.split("_")[1])
            
            if user_id not in user_sessions:
                user_sessions[user_id] = {}
            
            user_sessions[user_id]["quantity"] = quantity
            session = user_sessions[user_id]
            
            files = session.get("temp_files", [])
            file_type = session.get("file_type")
            
            total_sum = 0
            total_pages_all = 0
            detailed_text = "📊 **ДЕТАЛЬНЫЙ РАСЧЕТ:**\n\n"
            
            for i, file in enumerate(files, 1):
                if file_type == "photo":
                    format_key = session.get("format", "small")
                    price_dict = PHOTO_PRICES[format_key]
                    file_total = calculate_price(price_dict, quantity)
                    total_sum += file_total
                    total_pages_all += file['page_count'] * quantity
                    
                    detailed_text += f"📸 Файл {i}: `{file['name'][:30]}...`\n"
                    detailed_text += f"   • {file['page_count']} листов × {quantity} копий = {file['page_count'] * quantity} листов\n"
                    detailed_text += f"   • {file_total // quantity} руб./копия\n"
                    detailed_text += f"   • **{file_total} руб.**\n\n"
                else:
                    color_key = session.get("color", "bw")
                    price_dict = DOC_PRICES[color_key]
                    file_pages = file['page_count'] * quantity
                    file_total = calculate_price(price_dict, file_pages)
                    total_sum += file_total
                    total_pages_all += file_pages
                    
                    price_per_page = file_total // file_pages if file_pages > 0 else 0
                    
                    detailed_text += f"📄 Файл {i}: `{file['name'][:30]}...`\n"
                    detailed_text += f"   • {file['page_count']} листов × {quantity} копий = {file_pages} листов\n"
                    detailed_text += f"   • {price_per_page} руб./лист\n"
                    detailed_text += f"   • **{file_total} руб.**\n\n"
            
            session["total"] = total_sum
            session["total_pages_all"] = total_pages_all
            session["delivery"] = estimate_delivery_time(total_pages_all)
            
            await query.message.delete()
            
            text = (f"{detailed_text}\n"
                   f"📋 **ПРОВЕРЬТЕ ЗАКАЗ:**\n\n"
                   f"📦 Всего файлов: {len(files)}\n"
                   f"📊 Всего листов к печати: {total_pages_all}\n"
                   f"💰 **ИТОГОВАЯ СУММА: {total_sum} руб.**\n"
                   f"⏳ **Срок: {session['delivery']}**\n\n"
                   "Всё верно?")
            
            keyboard = [
                [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order"),
                 InlineKeyboardButton("❌ Отменить", callback_data="cancel_order")]
            ]
            
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return CONFIRMING_ORDER
            
        except Exception as e:
            logger.error(f"Ошибка в qty: {e}")
            return ENTERING_QUANTITY
    
    if data == "confirm_order":
        try:
            session = user_sessions.get(user_id, {})
            
            if not session:
                await query.edit_message_text("Ошибка: сессия не найдена")
                return WAITING_FOR_FILE
            
            if "temp_files" not in session or not session["temp_files"]:
                await query.edit_message_text("Ошибка: файлы не найдены. Начните заново.")
                return WAITING_FOR_FILE
            
            success, order_folder, saved_files = save_order_to_files(
                user_id, 
                session['user_info']['username'], 
                session,
                session["temp_files"]
            )
            
            files_count = len(session["temp_files"])
            total_pages = session.get('total_pages_all', 0)
            total_sum = session.get('total', 0)
            
            if success and saved_files:
                folder_message = f"\n📁 Папка заказа: `{order_folder}`"
                files_message = f"\n📄 Сохранено файлов: {len(saved_files)}"
            else:
                folder_message = "\n⚠️ Не удалось сохранить файлы"
                files_message = ""
            
            text = (
                "✅ **ЗАКАЗ УСПЕШНО ОФОРМЛЕН!**\n\n"
                f"👤 Заказчик: {session['user_info']['first_name']}\n"
                f"📦 Файлов: {files_count}\n"
                f"📊 Всего листов к печати: {total_pages}\n"
                f"💰 Сумма к оплате: {total_sum} руб.\n"
                f"⏳ Срок выполнения: {session['delivery']}\n"
                f"{files_message}"
                f"{folder_message}\n\n"
                "📞 **Контактный телефон:**\n"
                f"`{CONTACT_PHONE}`\n\n"
                "🚚 **Способы получения:**\n"
                f"• Самовывоз СПб\n"
                "• СДЭК\n"
                "• Яндекс Доставка\n\n"
                "Свяжитесь с нами для уточнения деталей доставки!\n"
                "Спасибо за заказ! 😊"
            )
            
            keyboard = [[InlineKeyboardButton("🔄 Сделать новый заказ", callback_data="new_order")]]
            
            try:
                await query.message.delete()
            except:
                pass
            
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            if "temp_dirs" in session:
                for temp_dir in session["temp_dirs"]:
                    shutil.rmtree(temp_dir, ignore_errors=True)
            
            if user_id in user_sessions:
                del user_sessions[user_id]
            
            return WAITING_FOR_FILE
                
        except Exception as e:
            logger.error(f"Ошибка в confirm_order: {e}")
            logger.error(traceback.format_exc())
            await context.bot.send_message(
                chat_id=user_id,
                text="Произошла ошибка при сохранении заказа."
            )
            return WAITING_FOR_FILE
    
    return WAITING_FOR_FILE

def get_quantity_keyboard():
    keyboard = [
        [InlineKeyboardButton("1", callback_data="qty_1"), InlineKeyboardButton("2", callback_data="qty_2"),
         InlineKeyboardButton("3", callback_data="qty_3"), InlineKeyboardButton("4", callback_data="qty_4"),
         InlineKeyboardButton("5", callback_data="qty_5")],
        [InlineKeyboardButton("10", callback_data="qty_10"), InlineKeyboardButton("20", callback_data="qty_20"),
         InlineKeyboardButton("30", callback_data="qty_30"), InlineKeyboardButton("50", callback_data="qty_50"),
         InlineKeyboardButton("100", callback_data="qty_100")],
        [InlineKeyboardButton("200", callback_data="qty_200"), InlineKeyboardButton("300", callback_data="qty_300"),
         InlineKeyboardButton("400", callback_data="qty_400"), InlineKeyboardButton("500", callback_data="qty_500")],
        [InlineKeyboardButton("❌ Отмена", callback_data="cancel_order"),
         InlineKeyboardButton("⬅️ Назад", callback_data="back_to_format")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    quantity = extract_number_from_text(text)
    
    if quantity is None or quantity < 1 or quantity > 1000:
        await update.message.reply_text(
            "Пожалуйста, введите число от 1 до 1000\n"
            "Или выберите из кнопок:",
            reply_markup=get_quantity_keyboard()
        )
        return ENTERING_QUANTITY
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {}
    
    user_sessions[user_id]["quantity"] = quantity
    session = user_sessions[user_id]
    
    files = session.get("temp_files", [])
    file_type = session.get("file_type")
    
    total_sum = 0
    total_pages_all = 0
    detailed_text = "📊 **ДЕТАЛЬНЫЙ РАСЧЕТ:**\n\n"
    
    for i, file in enumerate(files, 1):
        if file_type == "photo":
            format_key = session.get("format", "small")
            price_dict = PHOTO_PRICES[format_key]
            file_total = calculate_price(price_dict, quantity)
            total_sum += file_total
            total_pages_all += file['page_count'] * quantity
            
            detailed_text += f"📸 Файл {i}: `{file['name'][:30]}...`\n"
            detailed_text += f"   • {file['page_count']} листов × {quantity} копий = {file['page_count'] * quantity} листов\n"
            detailed_text += f"   • {file_total // quantity} руб./копия\n"
            detailed_text += f"   • **{file_total} руб.**\n\n"
        else:
            color_key = session.get("color", "bw")
            price_dict = DOC_PRICES[color_key]
            file_pages = file['page_count'] * quantity
            file_total = calculate_price(price_dict, file_pages)
            total_sum += file_total
            total_pages_all += file_pages
            
            price_per_page = file_total // file_pages if file_pages > 0 else 0
            
            detailed_text += f"📄 Файл {i}: `{file['name'][:30]}...`\n"
            detailed_text += f"   • {file['page_count']} листов × {quantity} копий = {file_pages} листов\n"
            detailed_text += f"   • {price_per_page} руб./лист\n"
            detailed_text += f"   • **{file_total} руб.**\n\n"
    
    session["total"] = total_sum
    session["total_pages_all"] = total_pages_all
    session["delivery"] = estimate_delivery_time(total_pages_all)
    
    text = (f"{detailed_text}\n"
           f"📋 **ПРОВЕРЬТЕ ЗАКАЗ:**\n\n"
           f"📦 Всего файлов: {len(files)}\n"
           f"📊 Всего листов к печати: {total_pages_all}\n"
           f"💰 **ИТОГОВАЯ СУММА: {total_sum} руб.**\n"
           f"⏳ **Срок: {session['delivery']}**\n\n"
           "Всё верно?")
    
    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить заказ", callback_data="confirm_order"),
         InlineKeyboardButton("❌ Отменить", callback_data="cancel_order")]
    ]
    
    await update.message.reply_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return CONFIRMING_ORDER

# ========== ОБЩЕНИЕ ==========
async def chat_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    text = update.message.text.lower()
    
    if 'цена' in text or 'price' in text or 'стоимость' in text:
        await update.message.reply_text(
            "💰 **Наши цены:**\n\n"
            "📷 **Фото:**\n"
            "• Малый формат (A6/10x15): 18-35₽/шт\n"
            "• Средний формат (13x18/15x21): 35-65₽/шт\n"
            "• Большой формат (A4/21x30): 120-200₽/шт\n\n"
            "📄 **Документы (A4):**\n"
            "• Черно-белые: 10-25₽/лист\n"
            "• Цветные: 20-50₽/лист\n\n"
            "Цена зависит от количества листов!"
        )
    elif 'срок' in text or 'time' in text or 'доставк' in text:
        await update.message.reply_text(
            f"⏳ Срок выполнения: 1-3 дня в зависимости от количества листов\n\n"
            f"🚚 **Доставка:**\n"
            f"• Самовывоз СПб\n"
            f"• СДЭК\n"
            f"• Яндекс Доставка\n\n"
            f"📞 Контактный телефон: {CONTACT_PHONE}"
        )
    elif 'контакт' in text or 'телефон' in text or 'phone' in text:
        await update.message.reply_text(f"📞 Контактный телефон: `{CONTACT_PHONE}`")
    elif 'доставк' in text or 'самовывоз' in text or 'delivery' in text:
        await update.message.reply_text(
            f"🚚 **Способы получения:**\n"
            f"• Самовывоз СПб\n"
            f"• СДЭК\n"
            f"• Яндекс Доставка"
        )
    elif 'привет' in text or 'hello' in text or 'здравств' in text:
        await update.message.reply_text(f"Привет! 👋 Отправьте файл для печати")
    elif 'несколько' in text or 'много' in text:
        await update.message.reply_text(
            "✨ Да, вы можете отправить **несколько файлов** одним сообщением!\n"
            "Я посчитаю листы в каждом документе и рассчитаю общую стоимость."
        )
    else:
        await update.message.reply_text(
            "Я бот для печати. 📸🖨️\n\n"
            "📌 **Что я умею:**\n"
            "• Считать листы в PDF и Word\n"
            "• Принимать несколько файлов сразу\n"
            "• Рассчитывать стоимость за листы\n\n"
            f"📞 Контакт: `{CONTACT_PHONE}`\n"
            f"🚚 Доставка: Самовывоз СПб, СДЭК, Яндекс\n\n"
            "Отправьте файл(ы) для заказа!"
        )

# ========== ОШИБКИ ==========
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"❌ Ошибка: {context.error}")
    logger.error(traceback.format_exc())
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "Извините, произошла ошибка. Попробуйте позже или начните заново с /start"
            )
    except:
        pass

# ========== ЗАПУСК БОТА ==========
async def run_bot():
    """Запускает Telegram бота"""
    try:
        # Создаем приложение бота
        app = Application.builder().token(TOKEN).build()
        
        # Добавляем обработчики
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file),
                CommandHandler("start", start),
            ],
            states={
                WAITING_FOR_FILE: [
                    MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, chat_response),
                    CallbackQueryHandler(button_handler),
                ],
                SELECTING_PHOTO_FORMAT: [
                    CallbackQueryHandler(button_handler),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, chat_response),
                ],
                SELECTING_DOC_TYPE: [
                    CallbackQueryHandler(button_handler),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, chat_response),
                ],
                ENTERING_QUANTITY: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quantity_input),
                    CallbackQueryHandler(button_handler),
                ],
                CONFIRMING_ORDER: [
                    CallbackQueryHandler(button_handler),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, chat_response),
                ],
            },
            fallbacks=[CommandHandler("start", start)],
        )
        
        app.add_handler(conv_handler)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_response))
        app.add_error_handler(error_handler)
        
        # Информация о запуске
        print("=" * 60)
        print("✅ БОТ ЗАПУЩЕН!")
        print(f"📁 Заказы сохраняются в папку: {ORDERS_FOLDER}")
        print(f"📞 Контактный телефон: {CONTACT_PHONE}")
        print(f"🚚 Доставка: {DELIVERY_OPTIONS}")
        print(f"🌐 Веб-интерфейс: http://localhost:{PORT}")
        print("=" * 60)
        print("🔄 Бот работает в режиме long polling...")
        print("=" * 60)
        
        # Запускаем бота
        await app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
            close_loop=False
        )
    except Exception as e:
        logger.error(f"Критическая ошибка в run_bot: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

# ========== ТОЧКА ВХОДА ==========
def main():
    """Главная функция"""
    try:
        # Запускаем Flask в отдельном потоке
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        print(f"🌐 Flask сервер запущен на порту {PORT}")
        
        # Запускаем бота в основном потоке
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()