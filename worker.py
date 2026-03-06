#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Worker для Telegram бота на Render
Запускает только бота (без Flask) в отдельном процессе
"""

import asyncio
import os
import logging
import sys
import traceback

# Импортируем функцию run_bot из основного файла
from bot import run_bot, TOKEN

# Настройка логирования для worker
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
    force=True  # Принудительно переопределяем настройки логирования
)

# Создаём логгер для worker
logger = logging.getLogger("worker")

async def check_webhook_before_start():
    """
    Проверяет и удаляет веб-хук перед запуском бота
    Это важно, чтобы бот работал в режиме polling
    """
    try:
        from telegram.ext import Application
        
        logger.info("🔄 Проверка веб-хука перед запуском...")
        
        # Создаём временное приложение только для проверки веб-хука
        app = Application.builder().token(TOKEN).build()
        
        # Получаем информацию о текущем веб-хуке
        webhook_info = await app.bot.get_webhook_info()
        
        if webhook_info.url:
            logger.warning(f"⚠️ Найден активный веб-хук: {webhook_info.url}")
            logger.info("🗑️ Удаляем веб-хук...")
            
            # Удаляем веб-хук и все ожидающие обновления
            await app.bot.delete_webhook(drop_pending_updates=True)
            
            logger.info("✅ Веб-хук успешно удалён!")
        else:
            logger.info("✅ Веб-хук не установлен, можно запускать polling")
            
        # Даём время на обработку
        await asyncio.sleep(1)
        
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке веб-хука: {e}")
        logger.error(traceback.format_exc())
        # Не выходим, пробуем запустить бота в любом случае

def main():
    """
    Главная функция worker'а
    Запускает бота с правильной обработкой asyncio
    """
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК WORKER ДЛЯ TELEGRAM БОТА")
    logger.info("=" * 60)
    
    # Проверяем наличие токена
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен бота не найден!")
        logger.error("Проверьте переменную окружения TOKEN на Render")
        sys.exit(1)
    
    logger.info(f"✅ Токен бота загружен")
    logger.info(f"📁 Папка для заказов: {os.path.abspath('заказы')}")
    
    # Создаём новый event loop для этого потока
    try:
        # Пытаемся получить существующий event loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # Если нет - создаём новый
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        # Сначала проверяем и удаляем веб-хук
        loop.run_until_complete(check_webhook_before_start())
        
        logger.info("🎯 Запуск основного цикла бота...")
        
        # Запускаем бота
        loop.run_until_complete(run_bot())
        
    except KeyboardInterrupt:
        logger.info("👋 Получен сигнал остановки, завершаем работу...")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в worker: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)
    finally:
        # Закрываем event loop
        loop.close()
        logger.info("👋 Worker завершил работу")

def check_environment():
    """
    Проверяет окружение и выводит отладочную информацию
    """
    logger.info("📋 Информация об окружении:")
    logger.info(f"  • Python версия: {sys.version}")
    logger.info(f"  • Рабочая директория: {os.getcwd()}")
    logger.info(f"  • Токен задан: {'✅' if TOKEN else '❌'}")
    logger.info(f"  • Папка заказов: {os.path.abspath('заказы')}")
    
    # Проверяем, что папка для заказов существует
    if not os.path.exists('заказы'):
        os.makedirs('заказы')
        logger.info("  • 📁 Папка 'заказы' создана")
    else:
        logger.info("  • 📁 Папка 'заказы' уже существует")

if __name__ == "__main__":
    # Запускаем проверку окружения
    check_environment()
    
    # Запускаем основную функцию
    main()
