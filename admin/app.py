"""
File: admin/app.py
Purpose:
    Создание и настройка Flask приложения для административной панели.
    Предоставляет веб-интерфейс для управления пользователями и кредитами.

Responsibilities:
    - Инициализация Flask приложения
    - Регистрация blueprints (маршрутов)
    - Настройка конфигурации (сессии)
    - Очистка истекших сессий при старте

Key Design Decisions:
    - Используется factory pattern (create_app()) для создания приложения
    - Сессии хранятся в файловой системе (SESSION_TYPE = 'filesystem')
    - Blueprint зарегистрирован без префикса (url_prefix='')

Notes:
    - Приложение может запускаться отдельно через main()
    - При запуске через run.py используется threading для параллельной работы с ботом
"""
from flask import Flask, session
from config.settings import settings
from admin.routes import admin_bp
from database.db_manager import db_manager
from utils.logger import logger
import os


def create_app():
    """Создание и настройка Flask приложения."""
    app = Flask(__name__, template_folder='templates', static_folder='static')
    
    # Конфигурация
    app.secret_key = "dev-secret-key-change-in-production"  # Дефолтный ключ для сессий
    app.config['SESSION_TYPE'] = 'filesystem'
    
    # Регистрируем blueprint
    app.register_blueprint(admin_bp, url_prefix='')
    
    # Очистка истекших сессий при старте
    db_manager.cleanup_expired_sessions()
    
    logger.info("Flask приложение создано")
    
    return app


def main():
    """Точка входа для запуска Flask сервера отдельно."""
    try:
        settings.validate()
        app = create_app()
        
        # Создаем директории для шаблонов и статики если их нет
        os.makedirs('admin/templates', exist_ok=True)
        os.makedirs('admin/static', exist_ok=True)
        
        logger.info(f"Запуск Flask сервера на {settings.FLASK_HOST}:{settings.FLASK_PORT}")
        app.run(
            host=settings.FLASK_HOST,
            port=settings.FLASK_PORT,
            debug=False
        )
    except Exception as e:
        logger.error(f"Ошибка при запуске Flask сервера: {e}")
        raise


if __name__ == "__main__":
    main()
