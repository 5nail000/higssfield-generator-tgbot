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
from config.constants import MODE_NANOBANANA, MODE_SEEDREAM, get_mode_display_name, higgsfield_credits_to_usd
from datetime import datetime
import json
import asyncio
import io
from pathlib import Path
from telegram import InputFile

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


@admin_bp.route('/metrics')
@login_required
def metrics():
    """Страница метрик по моделям генерации."""
    period = request.args.get('period', 'all')
    user_id = request.args.get('user_id', type=int)
    
    # Получаем статистику по моделям
    stats = db_manager.get_model_statistics(period=period, user_id=user_id)
    
    # Получаем статистику по пользователям (траты в кредитах)
    users_stats = db_manager.get_users_credits_statistics(period=period)
    
    # Получаем список всех пользователей для фильтра
    all_users = db_manager.get_all_users()
    
    # Словарь для отображения имен моделей
    model_display_names = {
        MODE_NANOBANANA: get_mode_display_name(MODE_NANOBANANA),
        MODE_SEEDREAM: get_mode_display_name(MODE_SEEDREAM)
    }
    
    return render_template(
        'metrics.html',
        stats=stats,
        users_stats=users_stats,
        period=period,
        user_id=user_id,
        all_users=all_users,
        model_display_names=model_display_names,
        credits_to_usd=higgsfield_credits_to_usd
    )


@admin_bp.route('/api/metrics')
@login_required
def api_metrics():
    """API endpoint для получения метрик в JSON формате."""
    period = request.args.get('period', 'all')
    user_id = request.args.get('user_id', type=int)
    
    stats = db_manager.get_model_statistics(period=period, user_id=user_id)
    
    return jsonify(stats)


@admin_bp.route('/files/<int:user_id>/<path:filename>')
def serve_file(user_id, filename):
    """
    Отдача файлов пользователей.
    Публичный доступ для API cloud.higgsfield.ai.
    Поддерживает файлы из основной папки, из папки results, last_uploads и sets/{set_id}/.
    """
    # Проверяем, не запрашивается ли директория (неправильный запрос)
    if filename in ['last_uploads', 'results', 'used', 'sets']:
        logger.warning(f"Попытка доступа к директории вместо файла: /files/{user_id}/{filename}")
        return "Директория недоступна", 404
    
    # Проверяем, не запрашивается ли файл из папки sets/{set_id}/
    if filename.startswith('sets/'):
        # Формат: sets/{set_id}/{filename}
        parts = filename.split('/', 2)
        if len(parts) == 3:
            set_id = parts[1]
            actual_filename = parts[2]
            set_dir = file_manager.get_set_directory(user_id, int(set_id))
            file_path = set_dir / actual_filename
            if file_path.exists() and file_path.is_file():
                return send_from_directory(set_dir, actual_filename)
    # Проверяем, не запрашивается ли файл из папки results
    elif filename.startswith('results/'):
        # Убираем префикс results/
        actual_filename = filename.replace('results/', '', 1)
        results_dir = file_manager.get_results_directory(user_id)
        file_path = results_dir / actual_filename
        if file_path.exists() and file_path.is_file():
            return send_from_directory(results_dir, actual_filename)
    # Проверяем, не запрашивается ли файл из папки last_uploads
    elif filename.startswith('last_uploads/'):
        # Убираем префикс last_uploads/
        actual_filename = filename.replace('last_uploads/', '', 1)
        last_uploads_dir = file_manager.get_last_uploads_directory(user_id)
        file_path = last_uploads_dir / actual_filename
        if file_path.exists() and file_path.is_file():
            return send_from_directory(last_uploads_dir, actual_filename)
    else:
        # Обычный файл из основной папки пользователя
        file_path = file_manager.get_file_path(user_id, filename)
        if file_path:
            return send_from_directory(file_path.parent, filename)
    
    return "Файл не найден", 404


@admin_bp.route('/send_message/<int:user_id>', methods=['GET', 'POST'])
@login_required
def send_message(user_id):
    """Страница отправки сообщения пользователю."""
    user = db_manager.get_user_by_id(user_id)
    if not user:
        return "Пользователь не найден", 404
    
    if request.method == 'POST':
        message_text = request.form.get('message', '').strip()
        file = request.files.get('file')
        
        if not message_text and not file:
            return render_template(
                'send_message.html',
                user=user,
                error='Необходимо указать текст сообщения или загрузить файл'
            )
        
        try:
            from bot.bot_instance import get_bot_instance
            bot = get_bot_instance()
            
            # Отправляем сообщение асинхронно
            async def send():
                try:
                    if file:
                        # Отправляем файл
                        file_data = file.read()
                        file_ext = Path(file.filename).suffix.lower() if file.filename else ''
                        
                        # Определяем тип файла
                        if file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                            # Отправляем как фото
                            if message_text:
                                await bot.send_photo(
                                    chat_id=user.telegram_id,
                                    photo=InputFile(io.BytesIO(file_data), filename=file.filename),
                                    caption=message_text
                                )
                            else:
                                await bot.send_photo(
                                    chat_id=user.telegram_id,
                                    photo=InputFile(io.BytesIO(file_data), filename=file.filename)
                                )
                        else:
                            # Отправляем как документ
                            if message_text:
                                await bot.send_document(
                                    chat_id=user.telegram_id,
                                    document=InputFile(io.BytesIO(file_data), filename=file.filename),
                                    caption=message_text
                                )
                            else:
                                await bot.send_document(
                                    chat_id=user.telegram_id,
                                    document=InputFile(io.BytesIO(file_data), filename=file.filename)
                                )
                    else:
                        # Отправляем только текст
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=message_text
                        )
                    return True, None
                except Exception as e:
                    return False, str(e)
            
            # Запускаем асинхронную функцию
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, error = loop.run_until_complete(send())
            loop.close()
            
            if success:
                logger.info(f"Админ отправил сообщение пользователю {user_id} (telegram_id: {user.telegram_id})")
                return redirect(url_for('admin.users'))
            else:
                return render_template(
                    'send_message.html',
                    user=user,
                    error=f'Ошибка отправки: {error}'
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}", exc_info=True)
            return render_template(
                'send_message.html',
                user=user,
                error=f'Ошибка: {str(e)}'
            )
    
    return render_template('send_message.html', user=user)
