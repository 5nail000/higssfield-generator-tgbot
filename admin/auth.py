"""
File: admin/auth.py
Purpose:
    Система авторизации для Flask админ-панели.
    Обеспечивает проверку пароля и управление сессиями администраторов.

Responsibilities:
    - Проверка пароля администратора
    - Создание и валидация сессий
    - Декоратор для защиты маршрутов (login_required)

Key Design Decisions:
    - Используется простое сравнение паролей (для продакшена рекомендуется хеширование)
    - Сессии хранятся в БД с временем истечения
    - Декоратор login_required проверяет наличие сессии и её валидность

Notes:
    - Пароль хранится в открытом виде в config.json (небезопасно для продакшена)
    - Сессии автоматически истекают через определенное время
    - При невалидной сессии происходит автоматический logout
"""
from functools import wraps
from flask import session, redirect, url_for, request
from database.db_manager import db_manager
from config.settings import settings


def check_password(password: str) -> bool:
    """
    Проверить пароль администратора.
    
    Args:
        password: Введенный пароль
    
    Returns:
        True если пароль верный
    """
    # Простое сравнение пароля (для продакшена лучше использовать хеширование)
    return password == settings.ADMIN_PASSWORD


def login_required(f):
    """Декоратор для проверки авторизации."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session:
            return redirect(url_for('admin.login'))
        
        # Проверяем валидность сессии
        session_token = session.get('session_token')
        if session_token and not db_manager.validate_admin_session(session_token):
            session.clear()
            return redirect(url_for('admin.login'))
        
        return f(*args, **kwargs)
    return decorated_function


def login_user(password: str) -> bool:
    """
    Авторизовать пользователя.
    
    Args:
        password: Пароль
    
    Returns:
        True если авторизация успешна
    """
    if check_password(password):
        # Создаем сессию
        session_token = db_manager.create_admin_session()
        session['admin_logged_in'] = True
        session['session_token'] = session_token
        return True
    return False


def logout_user():
    """Выйти из системы."""
    session.clear()
