"""
File: bot/handlers.py
Purpose:
    Обработчики команд и сообщений Telegram бота. Содержит всю логику взаимодействия
    с пользователями через Telegram Bot API.

Responsibilities:
    - Обработка команд бота (/start, /help, /balance, /history, etc.)
    - Управление состоянием диалога пользователя
    - Обработка промптов, фото и выбора параметров генерации
    - Интеграция с API клиентами для генерации изображений
    - Управление кредитами и запросами на пополнение
    - Отправка результатов генерации пользователям

Key Design Decisions:
    - Используется ConversationHandler для многошаговых диалогов
    - Состояние хранится в context.user_data для каждого пользователя
    - Inline-кнопки используются для интерактивных действий
    - Обработка ошибок с логированием и уведомлением пользователей

Notes:
    - Файл большой (1000+ строк) - в будущем можно разбить на модули по функциональности
    - Все временные файлы перемещаются в last_uploads после успешной генерации
    - При загрузке новых фото папка last_uploads очищается
"""
import json
import io
import os
import requests
from pathlib import Path
from telegram import Update, ReplyKeyboardRemove, InputFile
from telegram.ext import ContextTypes, ConversationHandler
from database.db_manager import db_manager
from storage.file_manager import file_manager
from api.client import get_api_client
from utils.logger import logger
from bot.keyboards import (
    get_main_keyboard, 
    get_mode_selection_keyboard,
    get_admin_credit_request_keyboard,
    get_aspect_ratio_inline_keyboard,
    get_photo_skip_inline_keyboard,
    get_photos_ready_inline_keyboard,
    get_use_last_uploads_inline_keyboard
)
from config.settings import settings
from config.constants import (
    GENERATION_CREDIT_COST,
    CREDIT_REQUEST_AMOUNT,
    get_max_photos_for_mode,
    get_mode_display_name,
    MODE_NANOBANANA,
    MODE_SEEDREAM
)
from bot.states import UserState
from PIL import Image


# Состояния пользователя (хранятся в user_data)
STATE_IDLE = "idle"  # Ожидание промпта
STATE_WAITING_PHOTO = "waiting_photo"  # Ожидание фото
STATE_WAITING_ASPECT = "waiting_aspect"  # Ожидание выбора аспектов

# Состояние для смены режима (отдельный conversation)
WAITING_FOR_MODE = 100


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    telegram_id = user.id
    username = user.username or user.first_name
    
    # Очищаем предыдущие данные
    context.user_data.clear()
    
    # Создаем или получаем пользователя
    db_user = db_manager.get_or_create_user(telegram_id, username)
    
    # Получаем текущий режим (по умолчанию nanobanana)
    selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    mode_name = get_mode_display_name(selected_mode)
    
    welcome_message = (
        f"Привет, {user.first_name}! 👋\n"
        f"Твой баланс: {db_user.credits:.2f} кредитов\n\n"
        f"Я бот для работы с cloud.higgsfield.ai.\n"
        f"Текущий режим: {mode_name}\n\n"
        f"💡 Используй кнопку 'Сменить режим' в главном меню для выбора режима генерации.\n"
        f"💡 И уже можешь вводить промпты!\n\n"
        f"Но за лучшими промптами лучше обратись к LLM (ChatGPT/Grok/DeepSeek)"
    )
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=get_main_keyboard()
    )
    
    logger.info(f"Пользователь {telegram_id} использовал команду /start")
    return ConversationHandler.END


async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода промпта."""
    prompt = update.message.text.strip()
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        user = db_manager.get_or_create_user(telegram_id, update.effective_user.username or update.effective_user.first_name)
    
    if not prompt:
        await update.message.reply_text(
            "❌ Промпт не может быть пустым. Введи текстовое описание:"
        )
        return ConversationHandler.END
    
    # Проверяем баланс
    if user.credits < GENERATION_CREDIT_COST:
        await update.message.reply_text(
            f"❌ Недостаточно кредитов. Требуется: {GENERATION_CREDIT_COST}, у вас: {user.credits:.2f}"
        )
        return ConversationHandler.END
    
    # Сохраняем промпт и состояние
    context.user_data['prompt'] = prompt
    context.user_data['state'] = STATE_WAITING_PHOTO
    context.user_data['user_id'] = user.id
    context.user_data['credit_cost'] = GENERATION_CREDIT_COST
    
    # Устанавливаем режим по умолчанию, если не установлен
    if 'selected_mode' not in context.user_data:
        context.user_data['selected_mode'] = MODE_NANOBANANA
    
    # Очищаем предыдущие фото из контекста (но не удаляем файлы)
    context.user_data['image_paths'] = []
    context.user_data['media_group_photos'] = {}
    
    # Проверяем наличие последних загрузок
    last_uploads = file_manager.get_last_uploads(user.id)
    
    if last_uploads:
        # Есть последние загрузки - предлагаем использовать их или пропустить
        await update.message.reply_text(
            "📸 Фото которые применялись в последней генерации, можно применить повторно или загрузить новые:",
            reply_markup=get_use_last_uploads_inline_keyboard()
        )
    else:
        # Нет последних загрузок - обычный процесс
        selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
        max_photos = get_max_photos_for_mode(selected_mode)
        await update.message.reply_text(
            f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или используй кнопку ниже, чтобы пропустить:"
        )
        await update.message.reply_text(
            "Или нажми кнопку ниже, чтобы пропустить загрузку фото:",
            reply_markup=get_photo_skip_inline_keyboard()
        )
    
    logger.info(f"Пользователь {user.id} отправил промпт: {prompt[:50]}...")
    return ConversationHandler.END


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загрузки фото или пропуска."""
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Проверяем состояние - должны быть в состоянии ожидания фото
    state = context.user_data.get('state', STATE_IDLE)
    if state != STATE_WAITING_PHOTO:
        await update.message.reply_text(
            "❌ Сначала отправь промпт для генерации."
        )
        return ConversationHandler.END
    
    # При загрузке новых фото очищаем папку last_uploads
    file_manager.clear_last_uploads(user.id)
    
    # Если это не фото, просим отправить фото или пропустить
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Пожалуйста, отправь фото или используй кнопку ниже, чтобы пропустить:"
        )
        await update.message.reply_text(
            "Или нажми кнопку ниже, чтобы пропустить загрузку фото:",
            reply_markup=get_photo_skip_inline_keyboard()
        )
        return ConversationHandler.END
    
    # Проверяем баланс
    if user.credits < GENERATION_CREDIT_COST:
        await update.message.reply_text(
            f"❌ Недостаточно кредитов. Требуется: {GENERATION_CREDIT_COST}, у вас: {user.credits:.2f}"
        )
        context.user_data.clear()
        return ConversationHandler.END
    
    try:
        # Проверяем, является ли это частью медиа-группы
        media_group_id = update.message.media_group_id
        photos = update.message.photo
        
        if not photos:
            raise Exception("Не найдено фото в сообщении")
        
        # Берем самое большое фото из текущего сообщения (последнее в списке размеров)
        photo = photos[-1]
        logger.debug(f"Получено фото: {len(photos)} размеров, media_group_id={media_group_id}")
        logger.debug(f"Используем фото размером: {photo.width}x{photo.height}, file_size={photo.file_size if hasattr(photo, 'file_size') else 'unknown'}")
        
        # Максимальное количество фото (зависит от режима)
        selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
        MAX_PHOTOS = get_max_photos_for_mode(selected_mode)
        
        # Инициализируем структуру для хранения фото медиа-группы
        if 'media_group_photos' not in context.user_data:
            context.user_data['media_group_photos'] = {}
        
        # Если это медиа-группа, сохраняем фото во временное хранилище
        if media_group_id:
            if media_group_id not in context.user_data['media_group_photos']:
                context.user_data['media_group_photos'][media_group_id] = []
            
            # Проверяем лимит фото
            current_count = len(context.user_data['media_group_photos'][media_group_id])
            if current_count >= MAX_PHOTOS:
                await update.message.reply_text(
                    f"❌ Достигнут лимит: можно загрузить не более {MAX_PHOTOS} фото. Нажми кнопку ниже, когда все фото загружены:"
                )
                await update.message.reply_text(
                    "Нажми кнопку ниже, когда все фото загружены:",
                    reply_markup=get_photos_ready_inline_keyboard()
                )
                return ConversationHandler.END
            
            # Скачиваем и сохраняем фото
            file = await context.bot.get_file(photo.file_id)
            file_data = await file.download_as_bytearray()
            
            # Конвертируем в JPEG если нужно
            try:
                image = Image.open(io.BytesIO(file_data))
                if image.format != 'JPEG':
                    rgb_image = image.convert('RGB')
                    jpeg_buffer = io.BytesIO()
                    rgb_image.save(jpeg_buffer, format='JPEG', quality=95)
                    file_data = jpeg_buffer.getvalue()
            except Exception as e:
                logger.warning(f"Не удалось обработать изображение: {e}")
            
            # Сохраняем файл локально
            file_path, public_url = file_manager.save_file(
                user.id, 
                bytes(file_data), 
                f"photo_{media_group_id}_{len(context.user_data['media_group_photos'][media_group_id])}.jpg"
            )
            
            context.user_data['media_group_photos'][media_group_id].append(str(file_path))
            photo_count = len(context.user_data['media_group_photos'][media_group_id])
            logger.debug(f"Фото {photo_count} из медиа-группы сохранено: {file_path}")
            
            # После получения первого фото отправляем inline-кнопку
            if photo_count >= MAX_PHOTOS:
                await update.message.reply_text(
                    f"📸 Фото {photo_count}/{MAX_PHOTOS} получено (лимит достигнут). Нажми кнопку ниже, когда все фото загружены:"
                )
            else:
                await update.message.reply_text(
                    f"📸 Фото {photo_count}/{MAX_PHOTOS} получено. Отправь еще фото (максимум {MAX_PHOTOS}) или нажми кнопку ниже, когда все фото загружены:"
                )
            
            # Отправляем inline-кнопку для подтверждения загрузки всех фото
            await update.message.reply_text(
                "Нажми кнопку ниже, когда все фото загружены:",
                reply_markup=get_photos_ready_inline_keyboard()
            )
            
            return ConversationHandler.END
        
        # Если это не медиа-группа, обрабатываем как одно фото
        # Скачиваем фото
        file = await context.bot.get_file(photo.file_id)
        file_data = await file.download_as_bytearray()
        
        # Конвертируем в JPEG если нужно
        try:
            image = Image.open(io.BytesIO(file_data))
            if image.format != 'JPEG':
                # Конвертируем в JPEG
                rgb_image = image.convert('RGB')
                jpeg_buffer = io.BytesIO()
                rgb_image.save(jpeg_buffer, format='JPEG', quality=95)
                file_data = jpeg_buffer.getvalue()
        except Exception as e:
            logger.warning(f"Не удалось обработать изображение: {e}")
        
        # Сохраняем файл локально как JPEG
        file_path, public_url = file_manager.save_file(
            user.id, 
            bytes(file_data), 
            "photo.jpg"
        )
        
        # Сохраняем путь к файлу в контексте
        image_paths = [str(file_path)]
        context.user_data['image_path'] = str(file_path)
        context.user_data['image_paths'] = image_paths
        context.user_data['user_id'] = user.id
        context.user_data['credit_cost'] = GENERATION_CREDIT_COST
        
        logger.debug(f"Фото сохранено: user_id={user.id}, path={file_path}")
        
        # Меняем состояние на ожидание выбора аспектов
        context.user_data['state'] = STATE_WAITING_ASPECT
        
        # Просим выбрать соотношение сторон
        await update.message.reply_text(
            "✅ Фото сохранено. Теперь выбери соотношение сторон:"
        )
        
        # Отправляем inline-кнопки для выбора аспектов
        await update.message.reply_text(
            "Выбери соотношение сторон:",
            reply_markup=get_aspect_ratio_inline_keyboard()
        )
        
        return ConversationHandler.END
        
    except Exception as e:
        await update.message.reply_text(
            f"❌ Ошибка при обработке фото: {str(e)}"
        )
        logger.error(f"Ошибка при обработке фото для пользователя {user.id}: {e}")
        context.user_data.clear()
        return ConversationHandler.END


# Старая функция handle_aspect_ratio удалена - теперь используется handle_aspect_ratio_callback


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help."""
    help_text = (
        "📖 Доступные команды:\n\n"
        "/start - Начать работу\n"
        "/balance - Проверить баланс кредитов\n"
        "/history - Просмотреть историю запросов\n"
        "/help - Показать эту справку\n\n"
        "💡 Процесс:\n"
        "1. Выбери маршрут (NanoBanana или Другой)\n"
        "2. Введи промпт\n"
        "3. Отправь фото\n"
        "4. Выбери соотношение сторон\n\n"
        "💰 Одна генерация стоит 50 кредитов\n"
        "💳 Используй кнопку 'Запросить кредиты' для пополнения баланса"
    )
    
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {update.effective_user.id} использовал команду /help")
    return ConversationHandler.END


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /balance."""
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if user:
        message = f"💰 Ваш баланс: {user.credits:.2f} кредитов"
    else:
        message = "❌ Пользователь не найден. Используйте /start"
    
    await update.message.reply_text(message, reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {telegram_id} проверил баланс")
    return ConversationHandler.END


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /history."""
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Получаем последние 5 записей
    history = db_manager.get_user_history(user.id, limit=5)
    
    if not history:
        await update.message.reply_text("📜 История пуста", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    message = "📜 Последние запросы:\n\n"
    for action in history:
        timestamp = action.timestamp.strftime("%d.%m.%Y %H:%M")
        message += f"• {timestamp} - {action.action_type}\n"
        if action.credits_spent > 0:
            message += f"  Потрачено: {action.credits_spent:.2f} кредитов\n"
        message += "\n"
    
    await update.message.reply_text(message, reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {telegram_id} просмотрел историю")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик отмены."""
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Операция отменена.",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


async def request_credits_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды запроса кредитов."""
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Создаем запрос на кредиты
    credit_request = db_manager.create_credit_request(user.id, amount=CREDIT_REQUEST_AMOUNT)
    
    # Сохраняем ID запроса до использования (на случай проблем с сессией)
    request_id = credit_request.id
    request_amount = credit_request.amount
    
    # Отправляем сообщение админу
    admin_id = settings.TELEGRAM_BOT_ADMIN_ID
    if admin_id:
        try:
            admin_message = (
                f"💳 Новый запрос на кредиты\n\n"
                f"Пользователь: {user.username or f'ID: {user.telegram_id}'}\n"
                f"Telegram ID: {user.telegram_id}\n"
                f"Текущий баланс: {user.credits:.2f} кредитов\n"
                f"Запрошено: {request_amount:.2f} кредитов\n"
                f"ID запроса: {request_id}"
            )
            
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                reply_markup=get_admin_credit_request_keyboard(request_id)
            )
            
            await update.message.reply_text(
                "✅ Запрос на кредиты отправлен администратору. Ожидайте ответа.",
                reply_markup=get_main_keyboard()
            )
            logger.info(f"Запрос на кредиты отправлен админу: user_id={user.id}, request_id={credit_request.id}")
        except Exception as e:
            logger.error(f"Ошибка при отправке запроса админу: {e}")
            await update.message.reply_text(
                "❌ Ошибка при отправке запроса. Попробуйте позже.",
                reply_markup=get_main_keyboard()
            )
    else:
        await update.message.reply_text(
            "❌ Администратор не настроен. Обратитесь к администратору.",
            reply_markup=get_main_keyboard()
        )
    
    return ConversationHandler.END


async def handle_credit_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для админа при запросе кредитов."""
    query = update.callback_query
    await query.answer()
    
    # Проверяем, что это админ
    if query.from_user.id != settings.TELEGRAM_BOT_ADMIN_ID:
        await query.message.reply_text("❌ У вас нет прав для выполнения этого действия.")
        return
    
    callback_data = query.data
    
    if callback_data.startswith("credit_approve_"):
        request_id = int(callback_data.split("_")[-1])
        credit_request = db_manager.get_credit_request(request_id)
        
        if credit_request and credit_request.status == 'pending':
            if db_manager.approve_credit_request(request_id):
                user = db_manager.get_user_by_id(credit_request.user_id)
                
                # Уведомляем пользователя
                try:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"✅ Ваш запрос на кредиты одобрен!\n\nНачислено: {credit_request.amount:.2f} кредитов\nТекущий баланс: {user.credits:.2f} кредитов"
                    )
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления пользователю: {e}")
                
                await query.edit_message_text(
                    f"✅ Запрос одобрен\n\nПользователю начислено: {credit_request.amount:.2f} кредитов"
                )
            else:
                await query.edit_message_text("❌ Ошибка при одобрении запроса")
        else:
            await query.edit_message_text("❌ Запрос уже обработан или не найден")
    
    elif callback_data.startswith("credit_reject_"):
        request_id = int(callback_data.split("_")[-1])
        credit_request = db_manager.get_credit_request(request_id)
        
        if credit_request and credit_request.status == 'pending':
            if db_manager.reject_credit_request(request_id):
                user = db_manager.get_user_by_id(credit_request.user_id)
                
                # Уведомляем пользователя
                try:
                    await context.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"❌ Ваш запрос на кредиты отклонен администратором."
                    )
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления пользователю: {e}")
                
                await query.edit_message_text("❌ Запрос отклонен")
            else:
                await query.edit_message_text("❌ Ошибка при отклонении запроса")
        else:
            await query.edit_message_text("❌ Запрос уже обработан или не найден")


async def change_mode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик смены режима генерации."""
    telegram_id = update.effective_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Получаем текущий режим (по умолчанию nanobanana)
    current_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    current_mode_name = get_mode_display_name(current_mode)
    
    await update.message.reply_text(
        f"🔄 Выбери режим генерации:\n\n"
        f"Текущий режим: {current_mode_name}",
        reply_markup=get_mode_selection_keyboard()
    )
    
    # Сохраняем состояние для обработки выбора режима
    context.user_data['changing_mode'] = True
    
    logger.info(f"Пользователь {telegram_id} запросил смену режима")
    return WAITING_FOR_MODE


async def handle_mode_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик выбора режима генерации."""
    mode_text = update.message.text.strip()
    telegram_id = update.effective_user.id
    
    if mode_text == "Отмена":
        await update.message.reply_text(
            "❌ Смена режима отменена.",
            reply_markup=get_main_keyboard()
        )
        context.user_data.pop('changing_mode', None)
        return ConversationHandler.END
    
    # Определяем выбранный режим
    selected_mode = None
    mode_name = None
    
    if mode_text == "🍌 NANOBANANA":
        selected_mode = MODE_NANOBANANA
        mode_name = get_mode_display_name(MODE_NANOBANANA)
    elif mode_text == "🎨 Seedream 4.5":
        selected_mode = MODE_SEEDREAM
        mode_name = get_mode_display_name(MODE_SEEDREAM)
    else:
        await update.message.reply_text(
            "❌ Неверный выбор. Выбери режим из предложенных:",
            reply_markup=get_mode_selection_keyboard()
        )
        return WAITING_FOR_MODE
    
    # Сохраняем выбранный режим
    context.user_data['selected_mode'] = selected_mode
    context.user_data.pop('changing_mode', None)
    
    await update.message.reply_text(
        f"✅ Режим изменен на: {mode_name}\n\n"
        f"💡 Теперь можешь вводить новый промпт для генерации!",
        reply_markup=get_main_keyboard()
    )
    
    logger.info(f"Пользователь {telegram_id} выбрал режим: {selected_mode}")
    return ConversationHandler.END


async def handle_use_last_uploads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для использования последних загруженных фото."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = query.from_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Проверяем, что есть промпт
    if 'prompt' not in context.user_data:
        await query.message.reply_text(
            "❌ Сначала отправь промпт для генерации."
        )
        return
    
    # Получаем последние загрузки
    last_uploads = file_manager.get_last_uploads(user.id)
    
    if not last_uploads:
        await query.message.reply_text(
            "❌ Нет сохраненных фото. Загрузи новые фото."
        )
        return
    
    # Используем последние загрузки
    context.user_data['image_paths'] = last_uploads
    context.user_data['state'] = STATE_WAITING_ASPECT
    
    # Сразу переходим к выбору соотношения сторон
    await query.message.reply_text(
        f"✅ Используются последние {len(last_uploads)} загруженных фото. Теперь выбери соотношение сторон:"
    )
    
    # Отправляем inline-кнопки для выбора аспектов
    await query.message.reply_text(
        "Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} использует последние {len(last_uploads)} загруженных фото")


async def handle_upload_new_photos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для загрузки новых фото."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = query.from_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Очищаем папку last_uploads при загрузке новых фото
    file_manager.clear_last_uploads(user.id)
    
    # Просим отправить новые фото
    selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    max_photos = get_max_photos_for_mode(selected_mode)
    await query.message.reply_text(
        f"📸 Отправь новые фото для обработки (можно несколько, но не более {max_photos}) или используй кнопку ниже, чтобы пропустить:"
    )
    await query.message.reply_text(
        "Или нажми кнопку ниже, чтобы пропустить загрузку фото:",
        reply_markup=get_photo_skip_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} выбрал загрузку новых фото, папка last_uploads очищена")


async def handle_skip_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для пропуска загрузки фото."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = query.from_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Проверяем, что есть промпт
    if 'prompt' not in context.user_data:
        await query.message.reply_text(
            "❌ Сначала отправь промпт для генерации."
        )
        return
    
    # Пропускаем загрузку фото
    context.user_data['image_paths'] = []
    context.user_data['state'] = STATE_WAITING_ASPECT
    
    # Просим выбрать соотношение сторон
    await query.message.reply_text(
        "✅ Пропущено. Теперь выбери соотношение сторон:"
    )
    
    # Отправляем inline-кнопки для выбора аспектов
    await query.message.reply_text(
        "Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} пропустил загрузку фото")


async def handle_photos_ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для подтверждения загрузки всех фото."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = query.from_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Собираем все фото из медиа-группы
    if 'media_group_photos' in context.user_data and context.user_data['media_group_photos']:
        all_media_groups = context.user_data['media_group_photos']
        if all_media_groups:
            # Берем последнюю группу (самую большую по количеству фото)
            latest_group_id = max(all_media_groups.keys(), key=lambda k: len(all_media_groups[k]))
            image_paths = all_media_groups[latest_group_id]
            
            if image_paths:
                context.user_data['image_paths'] = image_paths
                context.user_data['state'] = STATE_WAITING_ASPECT
                logger.debug(f"Собрано {len(image_paths)} фото из медиа-группы {latest_group_id}")
                # Очищаем временное хранилище
                del context.user_data['media_group_photos']
                
                await query.message.reply_text(
                    f"✅ Загружено {len(image_paths)} фото. Теперь выбери соотношение сторон:"
                )
                
                # Отправляем inline-кнопки для выбора аспектов
                await query.message.reply_text(
                    "Выбери соотношение сторон:",
                    reply_markup=get_aspect_ratio_inline_keyboard()
                )
                return
    
    # Если фото нет, но кнопка нажата - переходим к выбору соотношения сторон
    context.user_data['state'] = STATE_WAITING_ASPECT
    await query.message.reply_text(
        "✅ Фото обработано. Теперь выбери соотношение сторон:"
    )
    
    # Отправляем inline-кнопки для выбора аспектов
    await query.message.reply_text(
        "Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )


async def handle_aspect_ratio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для выбора соотношения сторон."""
    query = update.callback_query
    await query.answer()
    
    aspect_ratio = query.data.replace("aspect_", "")
    
    telegram_id = query.from_user.id
    user = db_manager.get_user(telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Валидация соотношения сторон
    valid_ratios = ["16:9", "1:1", "9:16", "4:3", "3:4", "21:9"]
    if aspect_ratio not in valid_ratios:
        await query.answer("❌ Неверное соотношение сторон", show_alert=True)
        return
    
    # Сохраняем соотношение сторон
    context.user_data['aspect_ratio'] = aspect_ratio
    
    # Проверяем, есть ли необработанные фото из медиа-группы
    if 'media_group_photos' in context.user_data and context.user_data['media_group_photos']:
        all_media_groups = context.user_data['media_group_photos']
        if all_media_groups:
            latest_group_id = max(all_media_groups.keys(), key=lambda k: len(all_media_groups[k]))
            image_paths = all_media_groups[latest_group_id]
            
            if image_paths:
                context.user_data['image_paths'] = image_paths
                logger.debug(f"Собрано {len(image_paths)} фото из медиа-группы {latest_group_id}")
                del context.user_data['media_group_photos']
    
    # Отправляем сообщение о начале обработки
    processing_msg = await query.message.reply_text(
        "⏳ Обрабатываю ваш запрос..."
    )
    
    # Получаем данные из контекста
    selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    route = selected_mode
    
    image_paths = context.user_data.get('image_paths', [])
    prompt = context.user_data.get('prompt')
    user_id = context.user_data.get('user_id')
    credit_cost = context.user_data.get('credit_cost', GENERATION_CREDIT_COST)
    
    # Проверяем наличие всех данных
    if not all([prompt, user_id]):
        try:
            await processing_msg.edit_text(
                "❌ Ошибка: не все данные получены. Попробуй снова."
            )
        except Exception:
            await query.message.reply_text(
                "❌ Ошибка: не все данные получены. Попробуй снова."
            )
        context.user_data['state'] = STATE_IDLE
        return
    
    # Вызываем существующую функцию обработки генерации
    # (используем код из handle_aspect_ratio, но адаптируем для callback)
    try:
        api_client = get_api_client(route)
        
        if route == MODE_NANOBANANA:
            initial_result = api_client.generate(
                prompt=prompt,
                image_paths=image_paths if image_paths else None,
                resolution="2k",
                aspect_ratio=aspect_ratio
            )
        else:  # MODE_SEEDREAM
            initial_result = api_client.generate(
                prompt=prompt,
                image_paths=image_paths if image_paths else None,
                aspect_ratio=aspect_ratio
            )
        
        logger.debug(f"Получен начальный результат от API: {json.dumps(initial_result, ensure_ascii=False, indent=2)}")
        
        request_id = None
        if isinstance(initial_result, dict):
            if 'request_id' in initial_result:
                request_id = initial_result.get('request_id')
            elif 'id' in initial_result:
                request_id = initial_result.get('id')
            elif 'jobs' in initial_result and len(initial_result.get('jobs', [])) > 0:
                jobs = initial_result.get('jobs', [])
                if jobs and isinstance(jobs[0], dict) and 'id' in jobs[0]:
                    request_id = jobs[0].get('id')
        
        result = initial_result
        generation_failed = False  # По умолчанию считаем генерацию успешной
        
        # Ожидаем завершения для всех режимов, если есть request_id
        if request_id:
            try:
                try:
                    await processing_msg.edit_text("⏳ Генерация в процессе, ожидаю завершения...")
                except Exception:
                    pass
                
                import asyncio
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    api_client.wait_for_completion,
                    request_id,
                    settings.API_GENERATION_TIMEOUT,
                    5
                )
                
                logger.debug(f"Задача завершена, финальный результат: {json.dumps(result, ensure_ascii=False, indent=2)}")
                generation_failed = False  # Генерация успешна
            except TimeoutError as e:
                logger.error(f"Таймаут при ожидании завершения задачи: {e}")
                try:
                    await processing_msg.edit_text("⏱️ Превышено время ожидания генерации. Попробуйте позже.\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                except Exception:
                    try:
                        await query.message.reply_text("⏱️ Превышено время ожидания генерации. Попробуйте позже.\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                    except Exception:
                        pass
                result = initial_result
                generation_failed = True
            except ValueError as e:
                error_msg = str(e)
                logger.warning(f"Ошибка валидации при ожидании завершения задачи: {e}")
                if error_msg.startswith("nsfw:"):
                    user_message = "🚫 Часть контента была заблокирована по соображениям цензуры"
                elif error_msg.startswith("canceled:"):
                    user_message = "ℹ️ Запрос был успешно отменён"
                else:
                    user_message = f"⚠️ {error_msg}"
                try:
                    await processing_msg.edit_text(f"{user_message}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                except Exception:
                    try:
                        await query.message.reply_text(f"{user_message}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                    except Exception:
                        pass
                result = initial_result
                generation_failed = True
            except RuntimeError as e:
                error_msg = str(e)
                logger.error(f"Ошибка выполнения при ожидании завершения задачи: {e}")
                if error_msg.startswith("failed:"):
                    user_message = "❌ Ошибка сервера. Попробуйте повторить запрос позднее"
                else:
                    user_message = f"⚠️ {error_msg}"
                try:
                    await processing_msg.edit_text(f"{user_message}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                except Exception:
                    try:
                        await query.message.reply_text(f"{user_message}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                    except Exception:
                        pass
                result = initial_result
                generation_failed = True
            except Exception as e:
                logger.error(f"Ошибка при ожидании завершения задачи: {e}", exc_info=True)
                try:
                    await processing_msg.edit_text(f"⚠️ Ошибка при ожидании результата: {str(e)}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                except Exception:
                    try:
                        await query.message.reply_text(f"⚠️ Ошибка при ожидании результата: {str(e)}\n\n💡 Можешь ввести новый промпт для следующей генерации!")
                    except Exception:
                        pass
                result = initial_result
                generation_failed = True
        
        # Списываем кредиты только если генерация успешна
        if not generation_failed:
            db_manager.update_user_credits(user_id, -credit_cost)
        else:
            logger.info(f"Кредиты не списаны из-за ошибки генерации для пользователя {user_id}")
            credit_cost = 0.0
        
        # Получаем обновленный баланс пользователя
        updated_user = db_manager.get_user_by_id(user_id)
        current_balance = updated_user.credits if updated_user else 0.0
        
        # Записываем действие в историю (даже если генерация не удалась)
        db_manager.add_action(
            user_id=user_id,
            action_type=f'api_request_{route}' if not generation_failed else f'api_request_error_{route}',
            request_data=json.dumps({
                'route': route,
                'image_paths': image_paths,
                'prompt': prompt,
                'aspect_ratio': aspect_ratio
            }),
            response_data=json.dumps(result),
            credits_spent=credit_cost,
            model_name=route  # Сохраняем название модели для статистики
        )
        
        # Если генерация не удалась, не пытаемся обработать результат
        if generation_failed:
            logger.info(f"Генерация не удалась для пользователя {user_id}, обработка результата пропущена")
            
            # Очищаем временные данные из контекста
            context.user_data.pop('image_paths', None)
            context.user_data.pop('media_group_photos', None)
            context.user_data.pop('prompt', None)
            
            # Сбрасываем состояние для возможности ввода нового промпта
            context.user_data['state'] = STATE_IDLE
            
            return
        
        image_url = None
        if isinstance(result, dict):
            if 'images' in result and len(result['images']) > 0:
                image_url = result['images'][0].get('url') or result['images'][0].get('image_url')
            elif 'result' in result and isinstance(result['result'], dict):
                if 'images' in result['result'] and len(result['result']['images']) > 0:
                    image_url = result['result']['images'][0].get('url') or result['result']['images'][0].get('image_url')
                elif 'url' in result['result']:
                    image_url = result['result']['url']
            elif 'url' in result:
                image_url = result['url']
            elif 'jobs' in result and len(result.get('jobs', [])) > 0:
                for job in result['jobs']:
                    if isinstance(job, dict):
                        if 'results' in job and len(job['results']) > 0:
                            result_item = job['results'][0]
                            if isinstance(result_item, dict):
                                image_url = result_item.get('url') or result_item.get('image_url')
                                if image_url:
                                    break
        
        if image_url:
            try:
                logger.debug(f"Скачивание изображения: {image_url}")
                img_response = requests.get(image_url, timeout=60)
                img_response.raise_for_status()
                image_data = img_response.content
                
                result_path, result_url = file_manager.save_result_image(
                    user_id=user_id,
                    image_data=image_data,
                    filename=None
                )
                
                logger.debug(f"Изображение сохранено: {result_path}")
                
                success_message = (
                    f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)\n\n"
                    f"💰 Текущий баланс: {current_balance:.2f} кредитов\n\n"
                    f"💡 Можешь ввести новый промпт для следующей генерации!"
                )
                
                # Сначала отправляем как файл (без сжатия, высокое качество)
                with open(result_path, 'rb') as f:
                    await context.bot.send_document(
                        chat_id=query.from_user.id,
                        document=InputFile(f, filename=result_path.name),
                        caption=success_message
                    )
                
                # Затем отправляем как фото для предпросмотра
                await context.bot.send_photo(
                    chat_id=query.from_user.id,
                    photo=image_data
                )
                
                try:
                    await processing_msg.edit_text(success_message)
                except Exception:
                    pass
                
            except Exception as e:
                logger.error(f"Ошибка при скачивании или сохранении изображения: {e}", exc_info=True)
                response_text = (
                    f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)\n"
                    f"🖼️ Ссылка на результат: {image_url}\n\n"
                    f"💰 Текущий баланс: {current_balance:.2f} кредитов\n\n"
                    f"💡 Можешь ввести новый промпт для следующей генерации!"
                )
                try:
                    await processing_msg.edit_text(response_text)
                except Exception:
                    await query.message.reply_text(response_text)
        else:
            response_text = (
                f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)\n"
                f"⚠️ URL изображения не найден в ответе API.\n\n"
                f"💰 Текущий баланс: {current_balance:.2f} кредитов\n\n"
                f"💡 Можешь ввести новый промпт для следующей генерации!"
            )
            try:
                await processing_msg.edit_text(response_text)
            except Exception:
                await query.message.reply_text(response_text)
        
        logger.info(f"Запрос успешно обработан для пользователя {user_id}, маршрут: {route}")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при обработке запроса: {str(e)}"
        try:
            await processing_msg.edit_text(error_msg)
        except Exception:
            await query.message.reply_text(error_msg)
        
        db_manager.add_action(
            user_id=user_id,
            action_type=f'api_request_error_{route}',
            request_data=json.dumps({
                'route': route,
                'image_paths': image_paths,
                'prompt': prompt,
                'aspect_ratio': aspect_ratio
            }),
            response_data=json.dumps({'error': str(e)}),
            credits_spent=0.0
        )
        
        logger.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
    finally:
        # Перемещаем временные файлы в last_uploads вместо удаления
        if 'image_paths' in context.user_data and context.user_data['image_paths']:
            # Перемещаем файлы в last_uploads
            moved_paths = file_manager.move_to_last_uploads(user_id, context.user_data['image_paths'])
            logger.debug(f"Перемещено {len(moved_paths)} файлов в last_uploads для пользователя {user_id}")
        
        if 'media_group_photos' in context.user_data:
            # Собираем все пути из медиа-групп
            all_media_paths = []
            for media_id, paths in context.user_data['media_group_photos'].items():
                all_media_paths.extend(paths)
            
            if all_media_paths:
                moved_paths = file_manager.move_to_last_uploads(user_id, all_media_paths)
                logger.debug(f"Перемещено {len(moved_paths)} файлов из медиа-групп в last_uploads для пользователя {user_id}")
            
            del context.user_data['media_group_photos']
        
        # Сбрасываем состояние
        context.user_data['state'] = STATE_IDLE


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений."""
    text = update.message.text
    
    # Проверяем на пустое сообщение
    if not text or not text.strip():
        # Игнорируем пустые сообщения - не отправляем никаких ответов
        return ConversationHandler.END
    
    # Команды меню
    if text == "💰 Баланс":
        return await balance_command(update, context)
    elif text == "📜 История":
        return await history_command(update, context)
    elif text == "💳 Запросить кредиты":
        return await request_credits_command(update, context)
    elif text == "ℹ️ Помощь":
        return await help_command(update, context)
    elif text == "🔄 Сменить режим":
        return await change_mode_command(update, context)
    else:
        # Если это не команда меню, проверяем состояние
        state = context.user_data.get('state', STATE_IDLE)
        
        if state == STATE_IDLE:
            # Это промпт - начинаем процесс генерации
            return await handle_prompt(update, context)
        elif state == STATE_WAITING_PHOTO:
            # Пользователь отправил текст вместо фото - напоминаем
            selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
            max_photos = get_max_photos_for_mode(selected_mode)
            await update.message.reply_text(
                f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или используй кнопку ниже, чтобы пропустить:"
            )
            await update.message.reply_text(
                "Или нажми кнопку ниже, чтобы пропустить загрузку фото:",
                reply_markup=get_photo_skip_inline_keyboard()
            )
        else:
            # Неизвестное состояние - сбрасываем
            context.user_data['state'] = STATE_IDLE
            # Не отправляем сообщение, чтобы не показывать меню
            # Пользователь может просто ввести промпт
        
        return ConversationHandler.END
