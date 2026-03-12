#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram бот для печати фото и документов
v4.0 — новые цены, ИИ-ассистент, красивый дизайн
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
import urllib.request
import urllib.error
from datetime import datetime
from flask import Flask, request, jsonify, send_file, send_from_directory, render_template_string, abort

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (Updater, CommandHandler, MessageHandler,
                           CallbackQueryHandler, ConversationHandler, Filters)
import PyPDF2
from docx import Document

# ══════════════════════════════════════════
#  НАСТРОЙКИ
# ══════════════════════════════════════════
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    print("❌ ОШИБКА: TOKEN не задан!")
    sys.exit(1)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")   # для ИИ-ассистента (бесплатно)

ADMIN_CHAT_ID = 483613049

RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL")
if not RENDER_URL:
    print("❌ ОШИБКА: RENDER_EXTERNAL_URL не задан!")
    sys.exit(1)

PORT             = int(os.environ.get("PORT", 10000))
CONTACT_PHONE    = "89219805705"
DELIVERY_OPTIONS = "Самовывоз СПб, СДЭК, Яндекс Доставка"

ORDERS_FOLDER = "заказы"
ORDERS_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ORDERS_FOLDER)

try:
    os.makedirs(ORDERS_PATH, exist_ok=True)
    print(f"📁 Папка заказов: {ORDERS_PATH}")
except Exception as e:
    print(f"❌ Ошибка создания папки: {e}")
    sys.exit(1)

ORDERS_DB_FILE = os.path.join(ORDERS_PATH, "orders_history.json")

ORDER_STATUSES = {
    "new":        "🆕 Новый",
    "processing": "🔄 В обработке",
    "printing":   "🖨 В печати",
    "ready":      "✅ Готов",
    "shipped":    "📦 Отправлен",
    "delivered":  "🏁 Доставлен",
    "cancelled":  "❌ Отменён",
}

# ══════════════════════════════════════════
#  ПРАЙС-ЛИСТ (новые цены)
# ══════════════════════════════════════════

# Документы A4, 80 г/м²
DOC_PRICES = {
    "bw":    {(1, 19): 20,  (20, 49): 20,  (50, 99): 15,  (100, 299): 10, (300, float("inf")): 8},
    "color": {(1, 19): 40,  (20, 49): 40,  (50, 99): 30,  (100, 299): 20, (300, float("inf")): 16},
}

# Фото
PHOTO_PRICES = {
    "small":  {(1, 8): 30,   (9, 49): 30,   (50, 99): 25,  (100, float("inf")): 15},
    "medium": {(1, 8): 60,   (9, 49): 60,   (50, 99): 50,  (100, float("inf")): 27},
    "large":  {(1, 3): 170,  (4, 19): 170,  (20, 49): 150, (50, float("inf")): 110},
}

# Текстовый прайс для /prices и ИИ-ассистента
PRICE_TEXT = """📌 *Наши услуги и цены*

💰 *Печать документов* (А4, бумага 80 г/м²)

🖨 *Чёрно-белая:*
от 20 листов — 20₽ / лист
от 50 — 15₽ / лист
от 100 — 10₽ / лист
от 300 — 8₽ / лист

🎨 *Цветная:*
от 20 листов — 40₽ / лист
от 50 — 30₽ / лист
от 100 — 20₽ / лист
от 300 — 16₽ / лист

· · · · · · · · · · · · · · ·

💰 *Печать фотографий*

🖼 *Малый формат* (A6 / 10×15 см)
от 9 шт — 30₽ / шт
от 50 — 25₽ / шт
от 100 — 20₽ / шт
от 101 — 15₽ / шт

🖼 *Средний формат* (13×18 / 15×21 см)
от 9 шт — 60₽ / шт
от 50 — 50₽ / шт
от 100 — 35₽ / шт
от 101 — 27₽ / шт

🖼 *Большой формат* (A4 / 21×30 см)
от 4 шт — 170₽ / шт
от 20 — 150₽ / шт
от 50 — 130₽ / шт
от 51 — 110₽ / шт

· · · · · · · · · · · · · · ·

📂 *Копии и сканы* — от 3₽ за шт

💛 При больших заказах — специальные условия!"""

# Системный промпт для ИИ-ассистента
AI_SYSTEM_PROMPT = f"""Ты вежливый и дружелюбный ИИ-ассистент сервиса печати фото и документов.
Ты помогаешь клиентам отвечать на вопросы о ценах, услугах, сроках и процессе заказа.
Отвечай кратко и по делу. Используй эмодзи. Отвечай на русском языке.

Информация о сервисе:
- Телефон: {CONTACT_PHONE}
- Доставка: {DELIVERY_OPTIONS}
- Принимаем файлы: JPG, PNG, PDF, DOC, DOCX
- Срок выполнения: 1-3 дня в зависимости от объёма

Прайс-лист:
ДОКУМЕНТЫ (А4, 80г/м²):
Чёрно-белая: от 20л — 20₽/л, от 50л — 15₽/л, от 100л — 10₽/л, от 300л — 8₽/л
Цветная: от 20л — 40₽/л, от 50л — 30₽/л, от 100л — 20₽/л, от 300л — 16₽/л

ФОТО:
Малый (10×15/A6): от 9шт — 30₽, от 50 — 25₽, от 100 — 20₽, от 101 — 15₽
Средний (13×18): от 9шт — 60₽, от 50 — 50₽, от 100 — 35₽, от 101 — 27₽
Большой (A4): от 4шт — 170₽, от 20 — 150₽, от 50 — 130₽, от 51 — 110₽
Копии/сканы: от 3₽/шт

Как сделать заказ: просто отправить файл в бот, выбрать формат и количество.
При вопросах вне темы печати — вежливо переведи разговор обратно на услуги сервиса.
Никогда не придумывай цены — используй только данные выше."""

# ══════════════════════════════════════════
#  ЛОГИРОВАНИЕ
# ══════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════
#  СОСТОЯНИЯ
# ══════════════════════════════════════════
(
    WAITING_FOR_FILE,
    SELECTING_PHOTO_FORMAT,
    SELECTING_DOC_TYPE,
    ENTERING_QUANTITY,
    CONFIRMING_ORDER,
    AI_CHAT,
) = range(6)

# ══════════════════════════════════════════
#  ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# ══════════════════════════════════════════
user_sessions  = {}
ai_histories   = {}   # история диалога с ИИ для каждого юзера
media_groups   = {}
group_timers   = {}
updater    = None
dispatcher = None
bot        = None

# ══════════════════════════════════════════
#  ЭМОДЗИ-КОНСТАНТЫ
# ══════════════════════════════════════════
SPARK  = "✨"
FIRE   = "🔥"
STAR   = "⭐️"
ROCKET = "🚀"
PARTY  = "🎉"
CAMERA = "📸"
PRINT  = "🖨"
DOC    = "📄"
MONEY  = "💰"
CLOCK  = "⏰"
CHECK  = "✅"
BELL   = "🔔"
HEART  = "💙"
BOT_IC = "🤖"
BRAIN  = "🧠"

def _sep():
    return "· · · · · · · · · · · · · · ·"

# ══════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════
def get_status_display(status):
    return ORDER_STATUSES.get(status, status)

def calculate_price(price_dict, quantity):
    for (mn, mx), price in price_dict.items():
        if mn <= quantity <= mx:
            return price * quantity
    return 0

def estimate_delivery(total_items):
    if total_items <= 50:    return "1 день"
    elif total_items <= 200: return "2 дня"
    return "3 дня"

def extract_number(text):
    nums = re.findall(r"\d+", text)
    return int(nums[0]) if nums else None

def format_size(b):
    if b < 1024:        return f"{b} B"
    elif b < 1024**2:   return f"{b/1024:.1f} KB"
    return f"{b/1024**2:.1f} MB"

def count_items(file_path, file_name):
    try:
        if file_name.lower().endswith(".pdf"):
            with open(file_path, "rb") as f:
                return len(PyPDF2.PdfReader(f).pages), "страниц", "документ"
        elif file_name.lower().endswith((".docx", ".doc")):
            doc   = Document(file_path)
            pages = max(1, len(doc.paragraphs) // 35)
            pages += len(doc.tables) // 2
            return pages, "страниц", "документ"
        elif file_name.lower().endswith((".jpg", ".jpeg", ".png")):
            return 1, "фото", "фото"
    except Exception as e:
        logger.error(f"Ошибка подсчёта: {e}")
    return 1, "единиц", "неизвестно"

def download_file(file_obj, file_name):
    try:
        tmp  = tempfile.mkdtemp()
        path = os.path.join(tmp, file_name)
        if hasattr(file_obj, "get_file"):
            file_obj.get_file().download(custom_path=path)
        elif hasattr(file_obj, "download"):
            file_obj.download(custom_path=path)
        else:
            with open(path, "wb") as f:
                f.write(file_obj.download_as_bytearray())
        return path, tmp
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
        return None, None

# ══════════════════════════════════════════
#  ИИ-АССИСТЕНТ (OpenRouter — есть бесплатные модели)
# ══════════════════════════════════════════
def ask_ai(user_id, user_message):
    """Отправляет сообщение через OpenRouter API и возвращает ответ."""
    if not OPENROUTER_API_KEY:
        logger.error("AI: OPENROUTER_API_KEY не задан!")
        return (
            f"{BOT_IC} ИИ-ассистент не настроен.\n\n"
            f"Администратору нужно добавить OPENROUTER_API_KEY в переменные окружения.\n\n"
            f"По вопросам пишите: {CONTACT_PHONE}"
        )

    if user_id not in ai_histories:
        ai_histories[user_id] = []

    ai_histories[user_id].append({
        "role": "user",
        "content": user_message,
    })

    if len(ai_histories[user_id]) > 20:
        ai_histories[user_id] = ai_histories[user_id][-20:]

    messages = [{"role": "system", "content": AI_SYSTEM_PROMPT}] + ai_histories[user_id]

    try:
        payload = json.dumps({
            "model": "meta-llama/llama-3.3-8b-instruct:free",
            "messages": messages,
            "max_tokens": 800,
            "temperature": 0.7,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer":  RENDER_URL or "https://printbot.onrender.com",
                "X-Title":       "Print Bot",
            },
            method="POST",
        )

        logger.info(f"OpenRouter запрос от {user_id}: {user_message[:50]}")

        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        # Проверяем наличие ответа
        choices = data.get("choices", [])
        if not choices:
            logger.error(f"OpenRouter пустой ответ: {data}")
            return f"😔 ИИ не ответил. Попробуй ещё раз!\n\nПо вопросам: {CONTACT_PHONE}"

        reply = choices[0]["message"]["content"]
        logger.info(f"OpenRouter ответ получен для {user_id}")

        ai_histories[user_id].append({
            "role": "assistant",
            "content": reply,
        })

        return reply

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        logger.error(f"OpenRouter HTTP {e.code} BODY: {body}")
        try:
            err_msg = json.loads(body).get("error", {}).get("message", body)
        except Exception:
            err_msg = body[:200]
        logger.error(f"OpenRouter детали ошибки: {err_msg}")
        return (
            f"😔 Ошибка ИИ ({e.code}): {err_msg[:120]}\n\n"
            f"По вопросам пишите: {CONTACT_PHONE}"
        )
    except urllib.error.URLError as e:
        logger.error(f"OpenRouter URLError: {e.reason}")
        return (
            f"😔 Нет связи с ИИ-сервером.\n\n"
            f"По вопросам пишите: {CONTACT_PHONE}"
        )
    except Exception as e:
        logger.error(f"OpenRouter ошибка: {e}\n{traceback.format_exc()}")
        return (
            f"😔 Не смог получить ответ от ИИ.\n\n"
            f"По вопросам пишите: {CONTACT_PHONE}"
        )

# ══════════════════════════════════════════
#  ИСТОРИЯ ЗАКАЗОВ
# ══════════════════════════════════════════
def load_history():
    try:
        if os.path.exists(ORDERS_DB_FILE):
            with open(ORDERS_DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки истории: {e}")
    return []

def save_history(entry):
    try:
        h = load_history()
        h.append(entry)
        with open(ORDERS_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return False

def update_order_status(order_id, new_status):
    try:
        history = load_history()
        user_id = None
        updated = False
        for o in history:
            if o.get("order_id") == order_id:
                o["status"] = new_status
                user_id     = o.get("user_id")
                updated     = True
                break
        if updated:
            with open(ORDERS_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, ensure_ascii=False, indent=2)

            info = os.path.join(ORDERS_PATH, order_id, "информация_о_заказе.txt")
            if os.path.exists(info):
                with open(info, "r", encoding="utf-8") as f:
                    txt = f.read()
                txt = re.sub(r"Статус:.*\n",
                             f"Статус: {get_status_display(new_status)}\n", txt)
                with open(info, "w", encoding="utf-8") as f:
                    f.write(txt)

            if user_id and bot:
                try:
                    bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"{BELL} *Статус заказа обновлён!*\n\n"
                            f"🆔 `{order_id}`\n"
                            f"📌 *{get_status_display(new_status)}*"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления: {e}")
        return updated
    except Exception as e:
        logger.error(f"update_order_status: {e}")
        return False

# ══════════════════════════════════════════
#  СОХРАНЕНИЕ ЗАКАЗА
# ══════════════════════════════════════════
def save_order_to_folder(user_id, username, order_data, files_info):
    try:
        clean  = re.sub(r"[^\w\s-]", "", username) or f"user_{user_id}"
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        oid    = f"{clean}_{ts}"
        folder = os.path.join(ORDERS_PATH, oid)
        os.makedirs(folder, exist_ok=True)

        for i, f in enumerate(files_info, 1):
            if os.path.exists(f["path"]):
                safe = re.sub(r'[<>:"/\\|?*]', "", f["name"])
                shutil.copy2(f["path"], os.path.join(folder, f"{i}_{safe}"))

        ph = [x for x in files_info if x["type"] == "photo"]
        dc = [x for x in files_info if x["type"] == "doc"]
        tp = sum(x["items"] for x in ph)
        td = sum(x["items"] for x in dc)

        info_path = os.path.join(folder, "информация_о_заказе.txt")
        with open(info_path, "w", encoding="utf-8") as f:
            f.write(f"ЗАКАЗ ОТ {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
            f.write("=" * 50 + "\n\n")
            f.write(f"Клиент: {order_data['user_info']['first_name']} (@{username})\n")
            f.write(f"ID: {user_id}\n")
            f.write(f"Телефон: {CONTACT_PHONE}\n")
            f.write(f"Статус: {get_status_display('new')}\n\n")
            if order_data["type"] == "photo":
                fn = {"small": "Малый (A6/10×15)", "medium": "Средний (13×18)", "large": "Большой (A4)"}
                f.write(f"Тип: Фото\nФормат: {fn[order_data['format']]}\n")
            else:
                cn = {"bw": "Черно-белая", "color": "Цветная"}
                f.write(f"Тип: Документы\nПечать: {cn[order_data['color']]}\n")
            f.write(f"Копий: {order_data['quantity']}\n\n")
            if ph:
                f.write(f"ФОТО: {len(ph)} файлов, {tp} шт. → к печати: {tp * order_data['quantity']}\n\n")
            if dc:
                f.write(f"ДОКУМЕНТЫ: {len(dc)} файлов, {td} стр. → к печати: {td * order_data['quantity']}\n\n")
            f.write(f"ИТОГО: {order_data['total']} руб.\n")
            f.write(f"Срок: {order_data['delivery']}\n\nФАЙЛЫ:\n")
            for i, fi in enumerate(files_info, 1):
                ico  = "📸" if fi["type"] == "photo" else "📄"
                unit = "фото" if fi["type"] == "photo" else "страниц"
                f.write(f"{ico} {i}. {fi['name']} — {fi['items']} {unit}\n")
            f.write(f"\nВсего файлов: {len(files_info)}")

        save_history({
            "order_id": oid, "folder": folder,
            "user_id": user_id, "username": username,
            "user_name": order_data["user_info"]["first_name"],
            "date": datetime.now().isoformat(),
            "type": order_data["type"], "quantity": order_data["quantity"],
            "total_photos": tp, "total_pages": td,
            "total_price": order_data["total"], "delivery": order_data["delivery"],
            "status": "new",
        })
        return True, oid, folder
    except Exception as e:
        logger.error(f"save_order_to_folder: {e}\n{traceback.format_exc()}")
        return False, None, None

# ══════════════════════════════════════════
#  УВЕДОМЛЕНИЕ АДМИНИСТРАТОРУ
# ══════════════════════════════════════════
def notify_admin(order_data, order_id):
    try:
        url = f"{RENDER_URL}/orders/{order_id}/"
        ph  = [f for f in order_data["files"] if f["type"] == "photo"]
        dc  = [f for f in order_data["files"] if f["type"] == "doc"]
        tp  = sum(f["items"] for f in ph)
        td  = sum(f["items"] for f in dc)

        if order_data["type"] == "photo":
            fn  = {"small": "10×15/A6", "medium": "13×18", "large": "A4"}
            typ = f"🖼 Фото — формат {fn[order_data['format']]}"
        else:
            cn  = {"bw": "⚫️ Ч/Б", "color": "🌈 Цвет"}
            typ = f"📄 Документы — {cn[order_data['color']]}"

        lines = [
            f"{BELL} *НОВЫЙ ЗАКАЗ*",
            "",
            f"👤 *{order_data['user_info']['first_name']}*  @{order_data['user_info']['username']}",
            f"🆔 `{order_data['user_info']['user_id']}`",
            "",
            typ,
            f"📦 Копий: *{order_data['quantity']}*   Файлов: *{len(order_data['files'])}*",
        ]
        if ph: lines.append(f"📸 Фото: {len(ph)} файл(а) → {tp} шт.")
        if dc: lines.append(f"📄 Документы: {len(dc)} файл(а) → {td} стр.")
        lines += [
            "",
            f"{MONEY} *{order_data['total']} руб.*",
            f"{CLOCK} Срок: *{order_data['delivery']}*",
            "",
            f"🔗 [Открыть заказ]({url})",
        ]
        if bot:
            bot.send_message(chat_id=ADMIN_CHAT_ID,
                             text="\n".join(lines),
                             parse_mode="Markdown")
    except Exception as e:
        logger.error(f"notify_admin: {e}")

# ══════════════════════════════════════════
#  СЕССИИ
# ══════════════════════════════════════════
def _init_session(user_id, user):
    user_sessions[user_id] = {
        "files": [], "temp_dirs": [],
        "total_photos": 0, "total_pages": 0,
        "user_info": {
            "user_id":    user_id,
            "username":   user.username or user.first_name,
            "first_name": user.first_name,
            "last_name":  user.last_name or "",
        },
    }

def _cleanup(user_id):
    if user_id in user_sessions:
        for d in user_sessions[user_id].get("temp_dirs", []):
            shutil.rmtree(d, ignore_errors=True)
        del user_sessions[user_id]

# ══════════════════════════════════════════
#  КЛАВИАТУРЫ
# ══════════════════════════════════════════
def _kbd_main():
    """Главное меню — показывается после /start."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{CAMERA}  Заказать печать",    callback_data="start_order")],
        [InlineKeyboardButton(f"{MONEY}  Цены и услуги",       callback_data="show_prices")],
        [InlineKeyboardButton(f"{BOT_IC}  Задать вопрос ИИ",   callback_data="start_ai")],
        [InlineKeyboardButton(f"📞  Контакты",                  callback_data="show_contacts")],
    ])

def _kbd_format(doc_count):
    if doc_count > 0:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚫️  Чёрно-белая печать",  callback_data="doc_bw")],
            [InlineKeyboardButton("🌈  Цветная печать",       callback_data="doc_color")],
            [InlineKeyboardButton("➕  Добавить ещё файлы",   callback_data="add_more")],
            [InlineKeyboardButton("🗑  Отменить заказ",       callback_data="cancel")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔹  Малый  —  10×15 / A6",      callback_data="photo_small")],
        [InlineKeyboardButton("🔷  Средний  —  13×18 / 15×21", callback_data="photo_medium")],
        [InlineKeyboardButton("🟦  Большой  —  A4 / 21×30",    callback_data="photo_large")],
        [InlineKeyboardButton("➕  Добавить ещё файлы",         callback_data="add_more")],
        [InlineKeyboardButton("🗑  Отменить заказ",             callback_data="cancel")],
    ])

def _kbd_qty():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣", callback_data="qty_1"),
         InlineKeyboardButton("2️⃣", callback_data="qty_2"),
         InlineKeyboardButton("3️⃣", callback_data="qty_3"),
         InlineKeyboardButton("4️⃣", callback_data="qty_4"),
         InlineKeyboardButton("5️⃣", callback_data="qty_5")],
        [InlineKeyboardButton("🔟",  callback_data="qty_10"),
         InlineKeyboardButton("20",  callback_data="qty_20"),
         InlineKeyboardButton("30",  callback_data="qty_30"),
         InlineKeyboardButton("50",  callback_data="qty_50"),
         InlineKeyboardButton("💯",  callback_data="qty_100")],
        [InlineKeyboardButton("200", callback_data="qty_200"),
         InlineKeyboardButton("300", callback_data="qty_300"),
         InlineKeyboardButton("400", callback_data="qty_400"),
         InlineKeyboardButton("500", callback_data="qty_500")],
        [InlineKeyboardButton("✏️  Ввести своё число", callback_data="qty_hint")],
        [InlineKeyboardButton("🗑  Отменить заказ",    callback_data="cancel")],
    ])

def _kbd_confirm():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{CHECK}  Да, оформить заказ!", callback_data="confirm")],
        [InlineKeyboardButton("🗑  Нет, отменить",             callback_data="cancel")],
    ])

def _kbd_new():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{ROCKET}  Новый заказ",       callback_data="new_order")],
        [InlineKeyboardButton(f"{BOT_IC}  Задать вопрос ИИ",  callback_data="start_ai")],
    ])

def _kbd_ai():
    """Клавиатура в режиме ИИ-чата."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{CAMERA}  Сделать заказ",      callback_data="start_order")],
        [InlineKeyboardButton(f"{MONEY}  Показать цены",        callback_data="show_prices")],
        [InlineKeyboardButton("🔄  Сбросить диалог с ИИ",       callback_data="reset_ai")],
        [InlineKeyboardButton("🏠  Главное меню",               callback_data="main_menu")],
    ])

# ══════════════════════════════════════════
#  ОБРАБОТКА ФАЙЛОВ
# ══════════════════════════════════════════
def handle_file(update, context):
    if update.message.media_group_id:
        return handle_media_group(update, context)
    return process_single_file(update, context)

def handle_media_group(update, context):
    uid  = update.effective_user.id
    mgid = update.message.media_group_id
    media_groups.setdefault(uid, {}).setdefault(mgid, []).append(update.message)

    key = f"{uid}_{mgid}"
    if key in group_timers:
        group_timers[key].cancel()
    t = threading.Timer(2.0, _flush_group, args=[uid, mgid, context])
    t.daemon = True
    t.start()
    group_timers[key] = t
    return WAITING_FOR_FILE

def _flush_group(uid, mgid, context):
    try:
        if uid not in media_groups or mgid not in media_groups[uid]:
            return
        messages = media_groups[uid].pop(mgid)
        group_timers.pop(f"{uid}_{mgid}", None)

        if uid not in user_sessions:
            _init_session(uid, messages[0].from_user)
        else:
            user_sessions[uid].setdefault("total_photos", 0)
            user_sessions[uid].setdefault("total_pages",  0)

        ph_cnt = doc_cnt = 0
        for msg in messages:
            fobj = fname = ftype = None
            if msg.document:
                fobj  = msg.document
                fname = fobj.file_name
                ext   = fname.lower().split(".")[-1]
                if ext in ("jpg", "jpeg", "png"):
                    ftype = "photo"; ph_cnt += 1
                elif ext in ("pdf", "doc", "docx"):
                    ftype = "doc";   doc_cnt += 1
                else:
                    continue
            elif msg.photo:
                fobj  = msg.photo[-1]
                fname = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
                ftype = "photo"; ph_cnt += 1
            else:
                continue

            path, tmp = download_file(fobj, fname)
            if not path:
                continue
            items, unit, tname = count_items(path, fname)
            user_sessions[uid]["files"].append({
                "path": path, "name": fname, "type": ftype,
                "items": items, "unit": unit, "type_name": tname,
            })
            user_sessions[uid]["temp_dirs"].append(tmp)
            if ftype == "photo": user_sessions[uid]["total_photos"] += items
            else:                user_sessions[uid]["total_pages"]  += items

        if not user_sessions[uid]["files"]:
            context.bot.send_message(chat_id=uid,
                                     text=f"{FIRE} Не удалось загрузить файлы. Попробуй ещё раз!")
            return

        _send_format_menu(uid, context, ph_cnt, doc_cnt,
                          user_sessions[uid]["total_photos"],
                          user_sessions[uid]["total_pages"])
    except Exception as e:
        logger.error(f"_flush_group: {e}\n{traceback.format_exc()}")

def process_single_file(update, context):
    uid = update.effective_user.id
    msg = update.message

    if uid not in user_sessions:
        _init_session(uid, update.effective_user)
    else:
        user_sessions[uid].setdefault("total_photos", 0)
        user_sessions[uid].setdefault("total_pages",  0)

    fobj = fname = ftype = None
    if msg.document:
        fobj  = msg.document
        fname = fobj.file_name
        ext   = fname.lower().split(".")[-1]
        if ext in ("jpg", "jpeg", "png"):    ftype = "photo"
        elif ext in ("pdf", "doc", "docx"):  ftype = "doc"
        else:
            msg.reply_text(
                f"😅 Формат *{ext}* не поддерживается.\n"
                "Отправь JPG, PNG, PDF, DOC или DOCX.",
                parse_mode="Markdown"
            )
            return WAITING_FOR_FILE
    elif msg.photo:
        fobj  = msg.photo[-1]
        fname = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"
        ftype = "photo"
    else:
        return WAITING_FOR_FILE

    path, tmp = download_file(fobj, fname)
    if not path:
        msg.reply_text("😔 Не удалось загрузить файл. Попробуй ещё раз!")
        return WAITING_FOR_FILE

    items, unit, tname = count_items(path, fname)
    user_sessions[uid]["files"].append({
        "path": path, "name": fname, "type": ftype,
        "items": items, "unit": unit, "type_name": tname,
    })
    user_sessions[uid]["temp_dirs"].append(tmp)
    if ftype == "photo": user_sessions[uid]["total_photos"] += items
    else:                user_sessions[uid]["total_pages"]  += items

    ph_cnt  = sum(1 for f in user_sessions[uid]["files"] if f["type"] == "photo")
    doc_cnt = sum(1 for f in user_sessions[uid]["files"] if f["type"] == "doc")

    _send_format_menu(uid, context, ph_cnt, doc_cnt,
                      user_sessions[uid]["total_photos"],
                      user_sessions[uid]["total_pages"],
                      reply_fn=msg.reply_text)
    return WAITING_FOR_FILE

def _send_format_menu(uid, context, ph_cnt, doc_cnt, tot_ph, tot_pg, reply_fn=None):
    total = ph_cnt + doc_cnt
    lines = [
        f"{SPARK}{SPARK}{SPARK}",
        "",
        f"{CHECK} *{'Файлы загружены' if total > 1 else 'Файл загружен'}!*",
        "",
    ]
    if ph_cnt:  lines.append(f"{CAMERA} Фото-файлов — *{ph_cnt}*")
    if doc_cnt: lines.append(f"{DOC} Документов — *{doc_cnt}*")
    if tot_ph:  lines.append(f"🖼 Всего фото — *{tot_ph} шт.*")
    if tot_pg:  lines.append(f"📃 Всего страниц — *{tot_pg} шт.*")
    lines += [
        "",
        _sep(),
        "",
        f"{'🖨 *Выбери тип печати:*' if doc_cnt else '🖼 *Выбери формат фото:*'}",
    ]
    text = "\n".join(lines)
    kbd  = _kbd_format(doc_cnt)

    if reply_fn:
        reply_fn(text, reply_markup=kbd, parse_mode="Markdown")
    else:
        context.bot.send_message(chat_id=uid, text=text,
                                 reply_markup=kbd, parse_mode="Markdown")

# ══════════════════════════════════════════
#  CANCEL
# ══════════════════════════════════════════
def cancel_order(uid, query=None, context=None):
    _cleanup(uid)
    text = f"🗑 *Заказ отменён*\n\nВсе файлы удалены 🧹\n\nЧем могу помочь?"
    if query:
        try:
            query.edit_message_text(text, reply_markup=_kbd_new(), parse_mode="Markdown")
        except Exception:
            if context:
                context.bot.send_message(chat_id=uid, text=text,
                                         reply_markup=_kbd_new(), parse_mode="Markdown")
    elif context:
        context.bot.send_message(chat_id=uid, text=text,
                                 reply_markup=_kbd_new(), parse_mode="Markdown")
    return WAITING_FOR_FILE

# ══════════════════════════════════════════
#  РАСЧЁТ СТОИМОСТИ
# ══════════════════════════════════════════
def _do_calc(quantity, uid):
    s = user_sessions.get(uid)
    if not s:
        return None

    s["quantity"] = quantity
    files  = s["files"]
    ftype  = s["type"]
    total  = 0
    tot_ph = 0
    tot_pg = 0

    lines = [f"{FIRE}{FIRE} *Детали заказа* {FIRE}{FIRE}", ""]

    for i, f in enumerate(files, 1):
        if ftype == "photo":
            pd        = PHOTO_PRICES[s["format"]]
            ftot      = calculate_price(pd, quantity)
            total    += ftot
            tot_ph   += f["items"] * quantity
            price_per = ftot // quantity if quantity else 0
            lines += [
                f"{CAMERA} *Файл {i}*  `{f['name'][:38]}`",
                f"   {f['items']} фото × {quantity} коп. = *{f['items'] * quantity} шт.*",
                f"   {price_per} руб/коп. → *{ftot} руб.*",
                "",
            ]
        else:
            pd     = DOC_PRICES[s["color"]]
            fitems = f["items"] * quantity
            ftot   = calculate_price(pd, fitems)
            total += ftot
            tot_pg += fitems
            ppp = ftot // fitems if fitems else 0
            lines += [
                f"{DOC} *Файл {i}*  `{f['name'][:38]}`",
                f"   {f['items']} стр. × {quantity} коп. = *{fitems} стр.*",
                f"   {ppp} руб/стр. → *{ftot} руб.*",
                "",
            ]

    delivery = estimate_delivery(tot_ph + tot_pg)
    s["total"]        = total
    s["total_photos"] = tot_ph
    s["total_pages"]  = tot_pg
    s["delivery"]     = delivery

    lines += [_sep(), ""]
    lines.append(f"📦 Файлов: *{len(files)}*")
    if tot_ph: lines.append(f"{CAMERA} Фото к печати: *{tot_ph} шт.*")
    if tot_pg: lines.append(f"📃 Страниц к печати: *{tot_pg} шт.*")
    lines += [
        f"{CLOCK} Срок: *{delivery}*",
        "",
        _sep(),
        "",
        f"{MONEY}{MONEY} *ИТОГО: {total} руб.* {MONEY}{MONEY}",
        "",
        f"_{STAR} Всё верно? Подтверждаем?_",
    ]
    return "\n".join(lines)

# ══════════════════════════════════════════
#  КОМАНДЫ
# ══════════════════════════════════════════
def start(update, context):
    user = update.effective_user
    uid  = user.id
    _cleanup(uid)

    text = (
        f"👋 *Привет, {user.first_name}!*\n\n"
        f"Вы на канале быстрой и качественной печати {CAMERA}{DOC}\n\n"
        f"Мы работаем для тех, кому важно:\n"
        f"✔ Чёткое качество\n"
        f"✔ Быстрое выполнение\n"
        f"✔ Удобная отправка файлов онлайн\n"
        f"✔ Адекватные цены\n\n"
        f"Что можно заказать у нас:\n"
        f"{CAMERA} Печать фотографий (разные форматы)\n"
        f"📑 Чёрно-белая и цветная печать документов\n"
        f"📚 Курсовые, дипломы, рефераты\n"
        f"📂 Копии и сканирование\n\n"
        f"{_sep()}\n\n"
        f"Просто отправь файл — и я всё посчитаю {SPARK}\n"
        f"Или выбери действие ниже 👇"
    )
    update.message.reply_text(text, reply_markup=_kbd_main(), parse_mode="Markdown")
    return WAITING_FOR_FILE

def cmd_prices(update, context):
    update.message.reply_text(PRICE_TEXT, parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup([
                                  [InlineKeyboardButton(f"{CAMERA}  Сделать заказ", callback_data="start_order")],
                                  [InlineKeyboardButton(f"{BOT_IC}  Спросить ИИ",  callback_data="start_ai")],
                              ]))
    return WAITING_FOR_FILE

def cmd_ai(update, context):
    uid = update.effective_user.id
    update.message.reply_text(
        f"{BOT_IC}{BRAIN} *ИИ-ассистент готов к работе!*\n\n"
        f"Я отвечу на любые вопросы о ценах, услугах и сроках.\n\n"
        f"Просто напиши свой вопрос прямо сейчас 💬",
        parse_mode="Markdown",
        reply_markup=_kbd_ai(),
    )
    return AI_CHAT

def cmd_help(update, context):
    text = (
        f"{SPARK} *Как пользоваться ботом:*\n\n"
        f"📎 Отправь файл (JPG, PNG, PDF, DOC, DOCX)\n"
        f"🖼 Выбери формат и количество копий\n"
        f"{CHECK} Подтверди заказ\n\n"
        f"{_sep()}\n\n"
        f"*Команды:*\n"
        f"/start — Главное меню\n"
        f"/prices — Цены на услуги\n"
        f"/ai — ИИ-ассистент\n"
        f"/help — Справка\n\n"
        f"{_sep()}\n\n"
        f"📞 *Телефон:* {CONTACT_PHONE}\n"
        f"🚚 *Доставка:* {DELIVERY_OPTIONS}"
    )
    update.message.reply_text(text, parse_mode="Markdown", reply_markup=_kbd_main())
    return WAITING_FOR_FILE

# ══════════════════════════════════════════
#  ИИ-ЧАТ HANDLER
# ══════════════════════════════════════════
def ai_chat_handler(update, context):
    """Обрабатывает текстовые сообщения в режиме AI_CHAT."""
    uid     = update.effective_user.id
    message = update.message.text

    # Показываем "печатает..."
    context.bot.send_chat_action(chat_id=uid, action=telegram.ChatAction.TYPING)

    reply = ask_ai(uid, message)

    update.message.reply_text(
        f"{BOT_IC} {reply}\n\n{_sep()}\n_Задай следующий вопрос или выбери действие:_",
        parse_mode="Markdown",
        reply_markup=_kbd_ai(),
    )
    return AI_CHAT

# ══════════════════════════════════════════
#  QUANTITY INPUT
# ══════════════════════════════════════════
def handle_quantity_input(update, context):
    uid      = update.effective_user.id
    quantity = extract_number(update.message.text)

    if not quantity or quantity < 1 or quantity > 1000:
        update.message.reply_text(
            f"😅 Введи число от *1 до 1000*\n\nИли выбери из кнопок 👇",
            parse_mode="Markdown",
            reply_markup=_kbd_qty(),
        )
        return ENTERING_QUANTITY

    s = user_sessions.get(uid)
    if not s:
        return cancel_order(uid, context=context)

    text = _do_calc(quantity, uid)
    if text is None:
        return cancel_order(uid, context=context)

    context.bot.send_message(chat_id=uid, text=text,
                              reply_markup=_kbd_confirm(), parse_mode="Markdown")
    return CONFIRMING_ORDER

# ══════════════════════════════════════════
#  BUTTON HANDLER
# ══════════════════════════════════════════
def button_handler(update, context):
    query = update.callback_query
    query.answer()
    uid  = query.from_user.id
    data = query.data
    logger.info(f"CB {data} uid={uid}")

    # ── главное меню ─────────────────────────
    if data == "main_menu":
        query.edit_message_text(
            f"🏠 *Главное меню*\n\nВыбери действие:",
            reply_markup=_kbd_main(),
            parse_mode="Markdown",
        )
        return WAITING_FOR_FILE

    # ── начать заказ ─────────────────────────
    if data == "start_order":
        query.edit_message_text(
            f"{CAMERA}{DOC} *Отправь файлы для печати*\n\n"
            f"Поддерживаемые форматы:\n"
            f"_JPG, PNG, PDF, DOC, DOCX_\n\n"
            f"📦 Можно кидать сразу несколько файлов!",
            parse_mode="Markdown",
        )
        return WAITING_FOR_FILE

    # ── показать цены ────────────────────────
    if data == "show_prices":
        kbd = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{CAMERA}  Сделать заказ", callback_data="start_order")],
            [InlineKeyboardButton(f"{BOT_IC}  Спросить ИИ",   callback_data="start_ai")],
            [InlineKeyboardButton("🏠  Главное меню",          callback_data="main_menu")],
        ])
        try:
            query.edit_message_text(PRICE_TEXT, reply_markup=kbd, parse_mode="Markdown")
        except Exception:
            context.bot.send_message(chat_id=uid, text=PRICE_TEXT,
                                     reply_markup=kbd, parse_mode="Markdown")
        return WAITING_FOR_FILE

    # ── контакты ─────────────────────────────
    if data == "show_contacts":
        query.edit_message_text(
            f"📞 *Контакты*\n\n"
            f"📱 Телефон: *{CONTACT_PHONE}*\n"
            f"🚚 Доставка: *{DELIVERY_OPTIONS}*\n\n"
            f"Как сделать заказ:\n"
            f"1️⃣ Отправьте файл в бот\n"
            f"2️⃣ Выберите формат и количество\n"
            f"3️⃣ Подтвердите заказ\n"
            f"4️⃣ Заберите готовую печать\n\n"
            f"Просто, быстро и аккуратно 🙌",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{CAMERA}  Сделать заказ", callback_data="start_order")],
                [InlineKeyboardButton("🏠  Главное меню",          callback_data="main_menu")],
            ]),
            parse_mode="Markdown",
        )
        return WAITING_FOR_FILE

    # ── ИИ-ассистент ─────────────────────────
    if data == "start_ai":
        try:
            query.edit_message_text(
                f"{BOT_IC}{BRAIN} *ИИ-ассистент*\n\n"
                f"Привет! Я отвечу на любые вопросы о ценах,\n"
                f"услугах, сроках и процессе заказа.\n\n"
                f"Просто напиши свой вопрос 💬",
                reply_markup=_kbd_ai(),
                parse_mode="Markdown",
            )
        except Exception:
            context.bot.send_message(
                chat_id=uid,
                text=(
                    f"{BOT_IC}{BRAIN} *ИИ-ассистент*\n\n"
                    f"Просто напиши свой вопрос 💬"
                ),
                reply_markup=_kbd_ai(),
                parse_mode="Markdown",
            )
        return AI_CHAT

    # ── сброс диалога с ИИ ───────────────────
    if data == "reset_ai":
        ai_histories.pop(uid, None)
        query.edit_message_text(
            f"{BOT_IC} *Диалог с ИИ сброшен* {CHECK}\n\n"
            f"Начнём заново! Задай свой вопрос 💬",
            reply_markup=_kbd_ai(),
            parse_mode="Markdown",
        )
        return AI_CHAT

    # ── отмена заказа ────────────────────────
    if data == "cancel":
        return cancel_order(uid, query, context)

    # ── добавить файлы ───────────────────────
    if data == "add_more":
        query.edit_message_text(
            f"{CAMERA}{DOC} *Отправь следующие файлы*\n\n"
            "_JPG, PNG, PDF, DOC, DOCX_",
            parse_mode="Markdown",
        )
        return WAITING_FOR_FILE

    # ── подсказка: ввести число ───────────────
    if data == "qty_hint":
        query.answer(
            "Просто напиши число в чат!\nНапример: 7 или 25",
            show_alert=True,
        )
        return ENTERING_QUANTITY

    # ── новый заказ ──────────────────────────
    if data == "new_order":
        _cleanup(uid)
        query.edit_message_text(
            f"{ROCKET}{SPARK} *Новый заказ!*\n\n"
            "Отправь файлы для печати\n"
            "_JPG, PNG, PDF, DOC, DOCX_",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{BOT_IC}  Спросить ИИ", callback_data="start_ai")],
            ]),
        )
        return WAITING_FOR_FILE

    # ── выбор формата фото ───────────────────
    if data.startswith("photo_"):
        if uid not in user_sessions:
            return cancel_order(uid, query, context)
        user_sessions[uid]["type"]   = "photo"
        user_sessions[uid]["format"] = data.split("_")[1]
        fmt_map = {"small": "10×15 / A6", "medium": "13×18 / 15×21", "large": "A4 / 21×30"}
        fmt     = user_sessions[uid]["format"]
        query.edit_message_text(
            f"{CAMERA} *Формат: {fmt_map.get(fmt, fmt)}*\n\n"
            f"{STAR} Отличный выбор!\n\n"
            f"{_sep()}\n\n"
            f"🔢 *Сколько копий напечатать?*\n\n"
            f"👆 Нажми кнопку или *напиши число* в чат:",
            parse_mode="Markdown",
            reply_markup=_kbd_qty(),
        )
        return ENTERING_QUANTITY

    # ── выбор типа документов ────────────────
    if data.startswith("doc_"):
        if uid not in user_sessions:
            return cancel_order(uid, query, context)
        user_sessions[uid]["type"]  = "doc"
        color = data.split("_")[1]
        user_sessions[uid]["color"] = color
        tot_ph = user_sessions[uid].get("total_photos", 0)
        tot_pg = user_sessions[uid].get("total_pages",  0)
        cn = {"bw": "⚫️ Чёрно-белая", "color": "🌈 Цветная"}
        query.edit_message_text(
            f"{PRINT} *Печать: {cn[color]}*\n\n"
            f"📊 В заказе: {CAMERA} {tot_ph} фото + {DOC} {tot_pg} стр.\n\n"
            f"{_sep()}\n\n"
            f"🔢 *Сколько копий напечатать?*\n\n"
            f"👆 Нажми кнопку или *напиши число* в чат:",
            parse_mode="Markdown",
            reply_markup=_kbd_qty(),
        )
        return ENTERING_QUANTITY

    # ── количество кнопкой ───────────────────
    if data.startswith("qty_"):
        quantity = int(data.split("_")[1])
        s = user_sessions.get(uid)
        if not s:
            return cancel_order(uid, query, context)
        text = _do_calc(quantity, uid)
        if text is None:
            return cancel_order(uid, query, context)
        try: query.message.delete()
        except Exception: pass
        context.bot.send_message(chat_id=uid, text=text,
                                  reply_markup=_kbd_confirm(), parse_mode="Markdown")
        return CONFIRMING_ORDER

    # ── подтверждение ────────────────────────
    if data == "confirm":
        s = user_sessions.get(uid)
        if not s:
            return cancel_order(uid, query, context)

        ok, oid, folder = save_order_to_folder(
            uid, s["user_info"]["username"], s, s["files"]
        )

        if ok:
            notify_admin(s, oid)
            ph = [f for f in s["files"] if f["type"] == "photo"]
            dc = [f for f in s["files"] if f["type"] == "doc"]
            tp = sum(f["items"] for f in ph)
            td = sum(f["items"] for f in dc)

            lines = [
                f"{PARTY}{FIRE}{PARTY}",
                "",
                f"{CHECK} *ЗАКАЗ ОФОРМЛЕН!*",
                "",
                f"🆔 `{oid}`",
                f"👤 *{s['user_info']['first_name']}*",
                f"📦 Файлов: *{len(s['files'])}*",
            ]
            if tp: lines.append(f"{CAMERA} Фото к печати: *{tp * s['quantity']} шт.*")
            if td: lines.append(f"📃 Страниц к печати: *{td * s['quantity']} шт.*")
            lines += [
                "",
                _sep(),
                "",
                f"{MONEY} *К оплате: {s['total']} руб.*",
                f"{CLOCK} Срок: *{s['delivery']}*",
                "",
                _sep(),
                "",
                f"📞 {CONTACT_PHONE}",
                f"🚚 {DELIVERY_OPTIONS}",
                "",
                f"📌 Статус: *{get_status_display('new')}*",
                f"{BELL} Уведомлю при каждом изменении статуса",
                "",
                f"{HEART} Спасибо за заказ!",
            ]
            context.bot.send_message(
                chat_id=uid, text="\n".join(lines), parse_mode="Markdown"
            )

            if ph:
                try:
                    group = []
                    for i, pf in enumerate(ph[:5]):
                        with open(pf["path"], "rb") as fp:
                            raw = fp.read()
                        cap = f"📸 Ваши фото ({len(ph)} шт.)" if i == 0 else None
                        group.append(InputMediaPhoto(raw, caption=cap))
                    if group:
                        context.bot.send_media_group(chat_id=uid, media=group)
                except Exception as e:
                    logger.error(f"preview: {e}")
        else:
            context.bot.send_message(
                chat_id=uid,
                text=(f"😔 *Что-то пошло не так*\n\n"
                      f"Попробуй ещё раз или напиши нам: {CONTACT_PHONE}"),
                parse_mode="Markdown",
            )

        _cleanup(uid)
        try: query.message.delete()
        except Exception: pass
        context.bot.send_message(
            chat_id=uid,
            text=f"Хочешь напечатать что-то ещё? {ROCKET}",
            reply_markup=_kbd_new(),
        )
        return WAITING_FOR_FILE

    return WAITING_FOR_FILE

# ══════════════════════════════════════════
#  ВЕБ-ИНТЕРФЕЙС
# ══════════════════════════════════════════
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

@app.route("/orders/")
def list_orders():
    try:
        orders = []
        if os.path.exists(ORDERS_PATH):
            hmap = {h.get("order_id"): h.get("status", "new") for h in load_history()}
            for item in sorted(os.listdir(ORDERS_PATH), reverse=True):
                ipath = os.path.join(ORDERS_PATH, item)
                if not os.path.isdir(ipath):
                    continue
                files  = []
                photos = []
                tsize  = 0
                for fn in os.listdir(ipath):
                    if fn == "информация_о_заказе.txt":
                        continue
                    fp    = os.path.join(ipath, fn)
                    fsz   = os.path.getsize(fp)
                    tsize += fsz
                    is_ph = fn.lower().endswith((".jpg", ".jpeg", ".png"))
                    fi = {"name": fn, "size_formatted": format_size(fsz),
                          "url": f"/orders/{item}/{fn}", "is_photo": is_ph}
                    files.append(fi)
                    if is_ph:
                        photos.append(fi)
                ctime  = datetime.fromtimestamp(os.path.getctime(ipath))
                status = hmap.get(item, "new")
                orders.append({
                    "id": item, "photos": photos[:5], "file_count": len(files),
                    "total_size": format_size(tsize),
                    "created": ctime.strftime("%d.%m.%Y %H:%M"),
                    "age_days": (datetime.now() - ctime).days,
                    "status": get_status_display(status),
                })
        orders.sort(key=lambda x: x["created"], reverse=True)

        html = """<!DOCTYPE html><html><head>
        <title>Заказы — Print Bot</title><meta charset="utf-8">
        """ + ANIMATED_STYLE + """
        <script>
        function upd(id,s){
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
                <button class="status-btn new"        onclick="upd('{{ o.id }}','new')">🆕</button>
                <button class="status-btn processing" onclick="upd('{{ o.id }}','processing')">🔄</button>
                <button class="status-btn printing"   onclick="upd('{{ o.id }}','printing')">🖨️</button>
                <button class="status-btn ready"      onclick="upd('{{ o.id }}','ready')">✅</button>
                <button class="status-btn shipped"    onclick="upd('{{ o.id }}','shipped')">📦</button>
                <button class="status-btn delivered"  onclick="upd('{{ o.id }}','delivered')">🏁</button>
                <button class="status-btn cancelled"  onclick="upd('{{ o.id }}','cancelled')">❌</button>
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
              <a href="/orders/{{ o.id }}/" class="action-btn btn-view">👁 Подробнее</a>
              <a href="/orders/{{ o.id }}/download" class="action-btn btn-dl">⬇️ ZIP</a>
            </div>
          </div>
          {% endfor %}
          </div>
        </div></body></html>"""
        return render_template_string(html, orders=orders)
    except Exception as e:
        logger.error(f"list_orders: {e}")
        return f"Ошибка: {e}", 500

@app.route("/orders/<path:order_id>/")
def view_order(order_id):
    try:
        opath = os.path.join(ORDERS_PATH, order_id)
        if not os.path.exists(opath) or not os.path.isdir(opath):
            abort(404)

        info_text = ""
        inf = os.path.join(opath, "информация_о_заказе.txt")
        if os.path.exists(inf):
            with open(inf, "r", encoding="utf-8") as f:
                info_text = f.read()

        status = "new"
        for h in load_history():
            if h.get("order_id") == order_id:
                status = h.get("status", "new")
                break

        files  = []
        photos = []
        tsize  = 0
        for fn in sorted(os.listdir(opath)):
            if fn == "информация_о_заказе.txt":
                continue
            fp   = os.path.join(opath, fn)
            fsz  = os.path.getsize(fp)
            tsize += fsz
            iph  = fn.lower().endswith((".jpg", ".jpeg", ".png"))
            fi   = {"name": fn, "size_formatted": format_size(fsz),
                    "url": f"/orders/{order_id}/{fn}", "is_photo": iph}
            files.append(fi)
            if iph:
                photos.append(fi)

        ctime = datetime.fromtimestamp(os.path.getctime(opath))

        ph_html = "".join(
            f'<div class="ph-item"><img src="{p["url"]}" class="ph-img" '
            f'onclick="window.open(\'{p["url"]}\')">'
            f'<div class="ph-lbl">{p["name"]}</div></div>'
            for p in photos
        )
        fl_html = "".join(
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
        function upd(s){{
            fetch('/orders/{order_id}/status',{{method:'POST',
                headers:{{'Content-Type':'application/json'}},
                body:JSON.stringify({{status:s}})
            }}).then(r=>r.json()).then(d=>{{if(d.success)location.reload();else alert('Ошибка');}});
        }}
        </script></head><body>
        <div class="container">
          <div class="header">
            <h1><span class="emoji-float">📁</span> {order_id}</h1>
            <p>Создан: {ctime.strftime('%d.%m.%Y %H:%M')} &bull; {len(files)} файлов &bull; {format_size(tsize)}</p>
          </div>
          <div class="nav-links">
            <a href="/orders/" class="nav-btn">← Назад</a>
            <a href="/" class="nav-btn"><span class="emoji-float">🏠</span> Главная</a>
          </div>
          <div class="content">
            <div class="sec">
              <h3><span class="emoji-pulse">📌</span> Статус: {get_status_display(status)}</h3>
              <div style="display:flex;flex-wrap:wrap;gap:7px;">
                <button class="status-btn new"        onclick="upd('new')">🆕 Новый</button>
                <button class="status-btn processing" onclick="upd('processing')">🔄 Обработка</button>
                <button class="status-btn printing"   onclick="upd('printing')">🖨️ Печать</button>
                <button class="status-btn ready"      onclick="upd('ready')">✅ Готов</button>
                <button class="status-btn shipped"    onclick="upd('shipped')">📦 Отправлен</button>
                <button class="status-btn delivered"  onclick="upd('delivered')">🏁 Доставлен</button>
                <button class="status-btn cancelled"  onclick="upd('cancelled')">❌ Отменён</button>
              </div>
            </div>
            <div class="sec"><h3>📋 Информация</h3><pre>{info_text}</pre></div>
            <div class="sec">
              <h3><span class="emoji-bounce">📸</span> Фото ({len(photos)})</h3>
              <div class="ph-grid">{ph_html}</div>
            </div>
            <div class="sec">
              <h3>📄 Файлы ({len(files)})</h3>
              <div class="files-grid">{fl_html}</div>
            </div>
            <div style="text-align:center;margin-top:20px;">
              <a href="/orders/{order_id}/download" class="dl-all">
                <span class="emoji-bounce">⬇️</span> Скачать всё (ZIP)
              </a>
            </div>
          </div>
        </div></body></html>"""
    except Exception as e:
        logger.error(f"view_order: {e}")
        return f"Ошибка: {e}", 500

@app.route("/orders/<path:order_id>/status", methods=["POST"])
def set_status(order_id):
    try:
        data = request.get_json()
        if not data or not data.get("status"):
            return jsonify({"success": False, "error": "no status"}), 400
        return jsonify({"success": update_order_status(order_id, data["status"])})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/orders/<path:order_id>/download")
def dl_zip(order_id):
    try:
        opath = os.path.join(ORDERS_PATH, order_id)
        if not os.path.exists(opath):
            return "Не найден", 404
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        with zipfile.ZipFile(tmp.name, "w") as zf:
            for root, dirs, files in os.walk(opath):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.relpath(fp, opath))
        return send_file(tmp.name, as_attachment=True, download_name=f"{order_id}.zip")
    except Exception as e:
        return f"Ошибка: {e}", 500

@app.route("/orders/<path:order_id>/<filename>")
def dl_file(order_id, filename):
    try:
        return send_from_directory(
            os.path.join(ORDERS_PATH, order_id), filename, as_attachment=True
        )
    except Exception as e:
        return f"Ошибка: {e}", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    global dispatcher
    try:
        if dispatcher is None:
            return jsonify({"error": "no dispatcher"}), 500
        data = request.get_json()
        if data:
            upd_obj = telegram.Update.de_json(data, bot)
            dispatcher.process_update(upd_obj)
        return "OK", 200
    except Exception as e:
        logger.error(f"webhook: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "bot_ready": dispatcher is not None})

@app.route("/stats")
def stats():
    cnt = len([d for d in os.listdir(ORDERS_PATH)
               if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    return jsonify({"status": "ok", "orders_count": cnt,
                    "active_sessions": len(user_sessions), "ai_sessions": len(ai_histories)})

@app.route("/")
def home():
    cnt = len([d for d in os.listdir(ORDERS_PATH)
               if os.path.isdir(os.path.join(ORDERS_PATH, d))]) if os.path.exists(ORDERS_PATH) else 0
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
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
          <div style="font-size:2em"><span class="emoji-bounce">📦</span></div>
          <div style="font-size:1.8em;font-weight:700">{cnt}</div>
          <div>заказов</div>
        </div>
        <div class="stat-card">
          <div style="font-size:2em"><span class="emoji-spin">⚙️</span></div>
          <div style="font-size:1.8em;font-weight:700">24/7</div>
          <div>работа</div>
        </div>
        <div class="stat-card">
          <div style="font-size:2em"><span class="emoji-float">🚀</span></div>
          <div style="font-size:1.8em;font-weight:700">1–3</div>
          <div>дня</div>
        </div>
      </div>
      <div class="nav-links2">
        <a href="/orders/" class="nav-btn2">📦 Все заказы</a>
        <a href="/stats"   class="nav-btn2">📊 Статистика</a>
      </div>
      <div class="info">
        <div><span class="emoji-pulse">📞</span> {CONTACT_PHONE}</div>
        <div><span class="emoji-float">🚚</span> {DELIVERY_OPTIONS}</div>
        <div><span class="emoji-spin">⏰</span> {now}</div>
      </div>
    </div></body></html>"""

# ══════════════════════════════════════════
#  ИНИЦИАЛИЗАЦИЯ
# ══════════════════════════════════════════
print("=" * 55)
print("🚀  ЗАПУСК БОТА v4.0")
print(f"📁  Заказы:  {ORDERS_PATH}")
print(f"👤  Admin:   {ADMIN_CHAT_ID}")
print(f"🤖  AI:      {'✅ OpenRouter включён' if OPENROUTER_API_KEY else '❌ OPENROUTER_API_KEY не задан'}")
print("=" * 55)

bot        = telegram.Bot(token=TOKEN)
updater    = Updater(token=TOKEN, use_context=True)
dispatcher = updater.dispatcher

conv = ConversationHandler(
    entry_points=[
        CommandHandler("start",  start),
        CommandHandler("prices", cmd_prices),
        CommandHandler("ai",     cmd_ai),
        CommandHandler("help",   cmd_help),
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
            MessageHandler(Filters.text & ~Filters.command, handle_quantity_input),
            CallbackQueryHandler(button_handler, pattern=r"^qty_\d+$"),
            CallbackQueryHandler(button_handler, pattern="^qty_hint$"),
            CallbackQueryHandler(button_handler, pattern="^cancel$"),
        ],
        CONFIRMING_ORDER: [
            CallbackQueryHandler(button_handler, pattern="^(confirm|cancel|new_order)$"),
        ],
        AI_CHAT: [
            MessageHandler(Filters.text & ~Filters.command, ai_chat_handler),
            CallbackQueryHandler(button_handler),
        ],
    },
    fallbacks=[
        CommandHandler("start",  start),
        CommandHandler("prices", cmd_prices),
        CommandHandler("ai",     cmd_ai),
        CommandHandler("help",   cmd_help),
    ],
    allow_reentry=True,
)

dispatcher.add_handler(conv)

wh = f"{RENDER_URL}/webhook"
updater.bot.set_webhook(url=wh)
print(f"✅  Webhook: {wh}")
print("✅  БОТ ГОТОВ!")
print("=" * 55)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
