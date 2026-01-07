"""
File: admin/routes.py
Purpose:
    Определение маршрутов (endpoints) для Flask админ-панели.
    Обеспечивает веб-интерфейс для управления пользователями, кредитами и просмотра истории.

Responsibilities:
    - Маршруты для авторизации (login, logout)
    - Маршруты для дашборда и статистики
    - Маршруты для управления пользователями (список, детали, начисление кредитов)
    - Маршруты для просмотра истории действий
    - Маршруты для обслуживания файлов пользователей

Key Design Decisions:
    - Все маршруты защищены декоратором @login_required (кроме /login)
    - Используется Blueprint для организации маршрутов
    - JSON ответы для AJAX запросов (начисление кредитов)
    - Статические файлы обслуживаются через send_from_directory

Notes:
    - Файлы пользователей доступны через /files/{user_id}/{filename}
    - Администратор может начислить неограниченное количество кредитов
    - История действий отображается с пагинацией
"""
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, send_from_directory
from admin.auth import login_required, login_user, logout_user
from database.db_manager import db_manager
from storage.file_manager import file_manager
from utils.logger import logger
from config.constants import MODE_NANOBANANA, MODE_SEEDREAM, get_mode_display_name
from datetime import datetime
import json

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа."""
    if request.method == 'POST':
        password = request.form.get('password', '')
        if login_user(password):
            return redirect(url_for('admin.dashboard'))
        else:
            return render_template('login.html', error='Неверный пароль')
    return render_template('login.html')


@admin_bp.route('/logout')
def logout():
    """Выход из системы."""
    logout_user()
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@login_required
def dashboard():
    """Главная страница (дашборд)."""
    # Получаем статистику
    all_users = db_manager.get_all_users()
    total_users = len(all_users)
    
    total_credits = sum(user.credits for user in all_users)
    
    recent_actions = db_manager.get_all_history(limit=10)
    
    stats = {
        'total_users': total_users,
        'total_credits': total_credits,
        'recent_actions': len(recent_actions)
    }
    
    return render_template('dashboard.html', stats=stats, recent_actions=recent_actions)


@admin_bp.route('/users')
@login_required
def users():
    """Страница управления пользователями."""
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    all_users = db_manager.get_all_users(limit=per_page, offset=offset)
    total_users = len(db_manager.get_all_users())
    total_pages = (total_users + per_page - 1) // per_page
    
    return render_template('users.html', users=all_users, page=page, total_pages=total_pages)


@admin_bp.route('/users/<int:user_id>/add_credits', methods=['POST'])
@login_required
def add_credits(user_id):
    """Начислить кредиты пользователю."""
    try:
        amount = float(request.form.get('amount', 0))
        if amount <= 0:
            return jsonify({'success': False, 'error': 'Сумма должна быть положительной'}), 400
        
        if db_manager.update_user_credits(user_id, amount):
            # Записываем в историю
            db_manager.add_action(
                user_id=user_id,
                action_type='credit_add',
                request_data=json.dumps({'amount': amount}),
                credits_spent=0.0
            )
            logger.info(f"Начислено кредитов пользователю {user_id}: {amount}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
    except ValueError:
        return jsonify({'success': False, 'error': 'Неверная сумма'}), 400


@admin_bp.route('/users/<int:user_id>/subtract_credits', methods=['POST'])
@login_required
def subtract_credits(user_id):
    """Списать кредиты у пользователя."""
    try:
        amount = float(request.form.get('amount', 0))
        if amount <= 0:
            return jsonify({'success': False, 'error': 'Сумма должна быть положительной'}), 400
        
        if db_manager.update_user_credits(user_id, -amount):
            # Записываем в историю
            db_manager.add_action(
                user_id=user_id,
                action_type='credit_subtract',
                request_data=json.dumps({'amount': amount}),
                credits_spent=0.0
            )
            logger.info(f"Списано кредитов у пользователя {user_id}: {amount}")
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
    except ValueError:
        return jsonify({'success': False, 'error': 'Неверная сумма'}), 400


@admin_bp.route('/history')
@login_required
def history():
    """Страница истории действий."""
    page = request.args.get('page', 1, type=int)
    user_id = request.args.get('user_id', type=int)
    per_page = 20
    offset = (page - 1) * per_page
    
    actions = db_manager.get_all_history(limit=per_page, offset=offset, user_id=user_id)
    all_actions = db_manager.get_all_history(user_id=user_id)
    total_actions = len(all_actions)
    total_pages = (total_actions + per_page - 1) // per_page
    
    # Получаем список пользователей для фильтра
    all_users = db_manager.get_all_users()
    
    return render_template(
        'history.html',
        actions=actions,
        page=page,
        total_pages=total_pages,
        selected_user_id=user_id,
        users=all_users
    )


@admin_bp.route('/history/<int:action_id>')
@login_required
def action_details(action_id):
    """Детали конкретного действия."""
    action = db_manager.get_action_by_id(action_id)
    if not action:
        return "Действие не найдено", 404
    
    # Парсим JSON данные
    request_data = {}
    response_data = {}
    
    if action.request_data:
        try:
            request_data = json.loads(action.request_data)
        except:
            request_data = {'raw': action.request_data}
    
    if action.response_data:
        try:
            response_data = json.loads(action.response_data)
        except:
            response_data = {'raw': action.response_data}
    
    user = db_manager.get_user_by_id(action.user_id)
    
    return render_template(
        'action_details.html',
        action=action,
        user=user,
        request_data=request_data,
        response_data=response_data
    )


@admin_bp.route('/files/<int:user_id>/<path:filename>')
def serve_file(user_id, filename):
    """
    Отдача файлов пользователей.
    Публичный доступ для API cloud.higgsfield.ai.
    Поддерживает файлы из основной папки и из папки results.
    """
    # Проверяем, не запрашивается ли файл из папки results
    if filename.startswith('results/'):
        # Убираем префикс results/
        actual_filename = filename.replace('results/', '', 1)
        results_dir = file_manager.get_results_directory(user_id)
        file_path = results_dir / actual_filename
        if file_path.exists() and file_path.is_file():
            return send_from_directory(results_dir, actual_filename)
    else:
        # Обычный файл из основной папки пользователя
        file_path = file_manager.get_file_path(user_id, filename)
        if file_path:
            return send_from_directory(file_path.parent, filename)
    
    return "Файл не найден", 404
