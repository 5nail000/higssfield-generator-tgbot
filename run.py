"""
File: run.py
Purpose:
    Главная точка входа приложения. Запускает Telegram бота и Flask админ-панель
    в параллельных потоках.

Responsibilities:
    - Валидация конфигурации перед запуском
    - Инициализация базы данных
    - Запуск Flask сервера в отдельном потоке
    - Запуск Telegram бота в основном потоке
    - Обработка критических ошибок и graceful shutdown

Key Design Decisions:
    - Flask запускается в daemon-потоке (не блокирует основной поток)
    - Бот запускается в основном потоке (блокирует выполнение)
    - Небольшая задержка (2 сек) после запуска Flask для инициализации
    - Все ошибки логируются перед завершением

Notes:
    - При KeyboardInterrupt выполняется graceful shutdown
    - Оба компонента (бот и Flask) должны работать одновременно
    - Для продакшена рекомендуется использовать systemd или supervisor
"""
import threading
import time
from config.settings import settings
from utils.logger import logger
from bot.bot import TelegramBot
from admin.app import create_app
from database.db_manager import db_manager


def run_flask():
    """Запуск Flask сервера в отдельном потоке."""
    try:
        app = create_app()
        logger.info(f"Запуск Flask сервера на {settings.FLASK_HOST}:{settings.FLASK_PORT}")
        app.run(
            host=settings.FLASK_HOST,
            port=settings.FLASK_PORT,
            debug=False,
            use_reloader=False  # Отключаем релоадер при запуске в потоке
        )
    except Exception as e:
        logger.error(f"Ошибка при запуске Flask сервера: {e}")
        raise


def run_bot():
    """Запуск Telegram бота."""
    try:
        bot = TelegramBot()
        logger.info("Запуск Telegram бота...")
        bot.run()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise


def main():
    """Главная функция запуска."""
    try:
        # Валидация настроек
        settings.validate()
        settings.ensure_directories()
        
        # Инициализация БД
        db_manager.init_db()
        
        logger.info("Инициализация завершена")
        
        # Запуск Flask в отдельном потоке
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        
        # Небольшая задержка для запуска Flask
        time.sleep(2)
        
        # Запуск бота в основном потоке
        run_bot()
        
    except KeyboardInterrupt:
        logger.info("Остановка приложения...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        raise


if __name__ == "__main__":
    main()
