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
import asyncio
import time
import httpx
from pathlib import Path
from telegram import Update, ReplyKeyboardRemove, InputFile, InputMediaPhoto
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import telegram
from telegram.ext import ContextTypes, ConversationHandler
from database.db_manager import db_manager
from storage.file_manager import file_manager
from api.client import get_api_client
from utils.logger import logger


# Вспомогательная функция для асинхронных операций с БД
async def run_db_operation(func, *args, **kwargs):
    """
    Выполнить синхронную операцию с БД в executor'е, чтобы не блокировать event loop.
    
    Args:
        func: Функция для выполнения
        *args, **kwargs: Аргументы для функции
    
    Returns:
        Результат выполнения функции
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


# Вспомогательная функция для асинхронных операций с файлами
async def run_file_operation(func, *args, **kwargs):
    """
    Выполнить синхронную операцию с файлами в executor'е, чтобы не блокировать event loop.
    
    Args:
        func: Функция для выполнения
        *args, **kwargs: Аргументы для функции
    
    Returns:
        Результат выполнения функции
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
from bot.keyboards import (
    get_main_keyboard, 
    get_mode_selection_keyboard,
    get_admin_credit_request_keyboard,
    get_aspect_ratio_inline_keyboard,
    get_photo_skip_inline_keyboard,
    get_photos_ready_inline_keyboard,
    get_use_last_uploads_inline_keyboard,
    get_photo_upload_control_keyboard,
    get_face_reference_sets_keyboard,
    get_face_reference_set_management_keyboard,
    get_face_reference_set_slideshow_keyboard
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
from api.deepseek_client import DeepSeekClient
from bot.states import UserState
from PIL import Image


# Состояния пользователя (хранятся в user_data)
STATE_IDLE = "idle"  # Ожидание промпта
STATE_WAITING_PHOTO = "waiting_photo"  # Ожидание фото
STATE_WAITING_ASPECT = "waiting_aspect"  # Ожидание выбора аспектов
STATE_WAITING_SET_NAME = "waiting_set_name"  # Ожидание имени набора
STATE_WAITING_SET_NAME_EDIT = "waiting_set_name_edit"  # Ожидание нового имени набора для редактирования
STATE_WAITING_SET_PHOTOS = "waiting_set_photos"  # Ожидание фото для набора
STATE_WAITING_PROMPT_DESCRIPTION = "waiting_prompt_description"  # Ожидание описания для промпт-мастера

# Состояние для смены режима (отдельный conversation)
WAITING_FOR_MODE = 100


def cancel_prompt_master_state(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Отменить состояние промт-мастера, если оно активно.
    
    Returns:
        True если состояние было отменено, False если не было активно
    """
    state = context.user_data.get('state')
    if state == STATE_WAITING_PROMPT_DESCRIPTION:
        context.user_data['state'] = STATE_IDLE
        context.user_data.pop('generated_prompt', None)
        context.user_data.pop('prompt_ready', None)
        logger.debug("Состояние промт-мастера отменено")
        return True
    return False


def get_user_mode(user, context: ContextTypes.DEFAULT_TYPE) -> str:
    """
    Получить режим пользователя из БД или контекста, или использовать Seedream по умолчанию.
    
    Args:
        user: Объект User из БД
        context: Контекст бота
    
    Returns:
        Режим генерации (MODE_NANOBANANA или MODE_SEEDREAM)
    """
    # Сначала проверяем контекст
    if 'selected_mode' in context.user_data:
        return context.user_data['selected_mode']
    
    # Затем проверяем БД
    if user and hasattr(user, 'selected_mode') and user.selected_mode:
        mode = user.selected_mode
        context.user_data['selected_mode'] = mode  # Сохраняем в контексте
        return mode
    
    # По умолчанию используем Seedream
    context.user_data['selected_mode'] = MODE_SEEDREAM
    return MODE_SEEDREAM


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start."""
    user = update.effective_user
    telegram_id = user.id
    username = user.username or user.first_name
    
    # Очищаем предыдущие данные
    context.user_data.clear()
    
    # Создаем или получаем пользователя (асинхронно)
    db_user = await run_db_operation(db_manager.get_or_create_user, telegram_id, username)
    
    # Получаем текущий режим из БД или используем Seedream по умолчанию
    user_mode = db_user.selected_mode if hasattr(db_user, 'selected_mode') and db_user.selected_mode else MODE_SEEDREAM
    selected_mode = context.user_data.get('selected_mode', user_mode)
    context.user_data['selected_mode'] = selected_mode  # Сохраняем в контексте
    mode_name = get_mode_display_name(selected_mode)
    
    welcome_message = (
        f"Привет, {user.first_name}! 👋\n"
        f"Твой баланс: {db_user.credits:.2f} кредитов\n\n"
        f"Я бот для генерации изображений на базе HiggsField.ai\n"
        f"Текущий режим: {mode_name}\n\n"
        f"💡 Используй кнопку 'Сменить режим' в главном меню для выбора режима генерации.\n"
        f"💡 И уже можешь вводить промпты!\n\n"
        f"Но за лучшими промптами лучше обратись к LLM (ChatGPT/Grok/DeepSeek)"
    )
    
    await update.message.reply_text(
        welcome_message,
        reply_markup=get_main_keyboard(telegram_id)
    )
    
    logger.info(f"Пользователь {telegram_id} использовал команду /start")
    return ConversationHandler.END


async def handle_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ввода промпта."""
    prompt = update.message.text.strip()
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        user = await run_db_operation(
            db_manager.get_or_create_user, 
            telegram_id, 
            update.effective_user.username or update.effective_user.first_name
        )
    
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
    context.user_data['prompt_message_id'] = update.message.message_id  # Сохраняем ID сообщения с промптом для удаления
    context.user_data['state'] = STATE_WAITING_PHOTO
    context.user_data['user_id'] = user.id
    context.user_data['credit_cost'] = GENERATION_CREDIT_COST
    
    # Устанавливаем режим по умолчанию, если не установлен
    if 'selected_mode' not in context.user_data:
        # Используем режим из БД или Seedream по умолчанию
        user_mode = user.selected_mode if hasattr(user, 'selected_mode') and user.selected_mode else MODE_SEEDREAM
        context.user_data['selected_mode'] = user_mode
    
    # Очищаем предыдущие фото из контекста (но не удаляем файлы)
    context.user_data['image_paths'] = []
    context.user_data['media_group_photos'] = {}
    
    # Проверяем наличие последних загрузок и наборов (асинхронно)
    last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
    sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
    
    # Проверяем, есть ли непустые наборы
    has_non_empty_sets = False
    if sets:
        for ref_set in sets:
            images = await run_db_operation(db_manager.get_face_reference_set_images, ref_set.id, user.id)
            if images:
                has_non_empty_sets = True
                break
    
    if last_uploads or has_non_empty_sets:
        # Проверяем, был ли последний раз использован набор (скрываем кнопку "последние фото")
        last_photo_source = context.user_data.get('photo_source')
        show_last_uploads = last_photo_source != 'set'  # Скрываем, если последний раз был набор
        
        # Есть последние загрузки или наборы - предлагаем использовать их или пропустить
        message = "📸 Выбери источник изображений для генерации:"
        if last_uploads and has_non_empty_sets:
            message = "📸 Фото которые применялись в последней генерации или наборы референсов, можно применить повторно или загрузить новые:"
        elif last_uploads:
            message = "📸 Фото которые применялись в последней генерации, можно применить повторно или загрузить новые:"
        elif has_non_empty_sets:
            message = "📸 Можно использовать набор референсов или загрузить новые фото:"
        
        await update.message.reply_text(
            message,
            reply_markup=get_use_last_uploads_inline_keyboard(show_use_set=has_non_empty_sets, show_last_uploads=show_last_uploads and bool(last_uploads))
        )
    else:
        # Нет последних загрузок и наборов - обычный процесс
        selected_mode = get_user_mode(user, context)
        max_photos = get_max_photos_for_mode(selected_mode)
        await update.message.reply_text(
            f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или пропусти загрузку:",
            reply_markup=get_photo_skip_inline_keyboard()
        )
    
    logger.info(f"Пользователь {user.id} отправил промпт: {prompt[:50]}...")
    return ConversationHandler.END


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загрузки фото или пропуска."""
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
        context.user_data.clear()
        return ConversationHandler.END
    
    # Проверяем состояние
    state = context.user_data.get('state', STATE_IDLE)
    
    # Обработка фото для добавления в набор
    if state == STATE_WAITING_SET_PHOTOS:
        ref_set_id = context.user_data.get('ref_set_id')
        if not ref_set_id:
            await update.message.reply_text("❌ Ошибка: не найден ID набора. Начни заново.")
            context.user_data.clear()
            return ConversationHandler.END
        
        if not update.message.photo:
            await update.message.reply_text("❌ Пожалуйста, отправь фото для добавления в набор.")
            return ConversationHandler.END
        
        try:
            photos = update.message.photo
            if not photos:
                raise Exception("Не найдено фото в сообщении")
            
            # Берем самое большое фото
            photo = photos[-1]
            
            # Скачиваем и сохраняем фото
            file = await context.bot.get_file(photo.file_id)
            file_data = await file.download_as_bytearray()
            
            # Конвертируем в JPEG если нужно
            try:
                def convert_to_jpeg(data):
                    image = Image.open(io.BytesIO(data))
                    if image.format != 'JPEG':
                        rgb_image = image.convert('RGB')
                        jpeg_buffer = io.BytesIO()
                        rgb_image.save(jpeg_buffer, format='JPEG', quality=95)
                        return jpeg_buffer.getvalue()
                    return data
                
                file_data = await run_file_operation(convert_to_jpeg, file_data)
            except Exception as e:
                logger.warning(f"Не удалось конвертировать фото в JPEG: {e}")
            
            # Сохраняем файл
            file_path, _ = await run_file_operation(file_manager.save_file, user.id, file_data)
            
            # Перемещаем файл в папку набора
            set_file_path = await run_file_operation(file_manager.move_file_to_set, user.id, ref_set_id, file_path)
            
            # Добавляем фото в набор (сохраняем путь к файлу в папке набора)
            file_hash = await run_file_operation(file_manager.calculate_file_hash, set_file_path)
            await run_db_operation(db_manager.add_image_to_face_reference_set, ref_set_id, set_file_path, file_hash)
            
            # Получаем информацию о наборе для ответа
            ref_set = await run_db_operation(db_manager.get_face_reference_set, ref_set_id, user.id)
            images = await run_db_operation(db_manager.get_face_reference_set_images, ref_set_id, user.id)
            
            await update.message.reply_text(
                f"✅ Фото добавлено в набор '{ref_set.name}'\n"
                f"📸 Всего фото в наборе: {len(images)}\n\n"
                "Можешь добавить еще фото или используй команду /face_reference_sets для управления набором."
            )
            
            logger.info(f"Пользователь {user.id} добавил фото в набор {ref_set_id}")
        except Exception as e:
            logger.error(f"Ошибка при добавлении фото в набор: {e}")
            await update.message.reply_text("❌ Произошла ошибка при добавлении фото. Попробуй еще раз.")
        
        return ConversationHandler.END
    
    # Обычная обработка фото для генерации
    if state != STATE_WAITING_PHOTO:
        await update.message.reply_text(
            "❌ Сначала отправь промпт для генерации."
        )
        return ConversationHandler.END
    
    # При загрузке новых фото очищаем папку last_uploads (асинхронно)
    await run_file_operation(file_manager.clear_last_uploads, user.id)
    
    # Если это не фото, просим отправить фото или пропустить
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Пожалуйста, отправь фото или пропусти загрузку:",
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
        selected_mode = get_user_mode(user, context)
        MAX_PHOTOS = get_max_photos_for_mode(selected_mode)
        
        # Инициализируем структуру для хранения фото медиа-группы
        if 'media_group_photos' not in context.user_data:
            context.user_data['media_group_photos'] = {}
        
        # Инициализируем image_paths если его нет (для ранее загруженных одиночных фото)
        if 'image_paths' not in context.user_data:
            context.user_data['image_paths'] = []
        
        # Если это медиа-группа, сохраняем фото во временное хранилище
        if media_group_id:
            if media_group_id not in context.user_data['media_group_photos']:
                context.user_data['media_group_photos'][media_group_id] = []
            
            # Подсчитываем общее количество фото: ранее загруженные + текущая медиа-группа
            previously_uploaded = len(context.user_data.get('image_paths', []))
            current_group_count = len(context.user_data['media_group_photos'][media_group_id])
            total_count = previously_uploaded + current_group_count
            
            # Проверяем лимит фото (учитывая ранее загруженные)
            if total_count >= MAX_PHOTOS:
                await update.message.reply_text(
                    f"❌ Достигнут лимит: можно загрузить не более {MAX_PHOTOS} фото (уже загружено {previously_uploaded}, в этой группе {current_group_count}). Нажми кнопку ниже, когда все фото загружены:"
                )
                await update.message.reply_text(
                    "Нажми кнопку ниже, когда все фото загружены:",
                    reply_markup=get_photo_upload_control_keyboard()
                )
                return ConversationHandler.END
            
            # Скачиваем и сохраняем фото
            file = await context.bot.get_file(photo.file_id)
            file_data = await file.download_as_bytearray()
            
            # Конвертируем в JPEG если нужно (асинхронно)
            try:
                def convert_to_jpeg(data):
                    image = Image.open(io.BytesIO(data))
                    if image.format != 'JPEG':
                        rgb_image = image.convert('RGB')
                        jpeg_buffer = io.BytesIO()
                        rgb_image.save(jpeg_buffer, format='JPEG', quality=95)
                        return jpeg_buffer.getvalue()
                    return data
                
                file_data = await run_file_operation(convert_to_jpeg, file_data)
            except Exception as e:
                logger.warning(f"Не удалось обработать изображение: {e}")
            
            # Сохраняем файл локально
            file_path, public_url = await run_file_operation(file_manager.save_file,
                user.id, 
                bytes(file_data), 
                f"photo_{media_group_id}_{len(context.user_data['media_group_photos'][media_group_id])}.jpg"
            )
            
            context.user_data['media_group_photos'][media_group_id].append(str(file_path))
            group_photo_count = len(context.user_data['media_group_photos'][media_group_id])
            previously_uploaded_count = len(context.user_data.get('image_paths', []))
            total_photo_count = previously_uploaded_count + group_photo_count
            # Отмечаем, что были загружены новые фото (не из last_uploads или набора)
            context.user_data['new_photos_uploaded'] = True
            
            logger.debug(f"Фото {group_photo_count} из медиа-группы сохранено: {file_path}. Всего фото: {total_photo_count}")
            
            # После получения фото отправляем кнопки управления
            if total_photo_count >= MAX_PHOTOS:
                await update.message.reply_text(
                    f"📸 Фото {total_photo_count}/{MAX_PHOTOS} получено (лимит достигнут). Что дальше?",
                    reply_markup=get_photo_upload_control_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"📸 Фото {total_photo_count}/{MAX_PHOTOS} получено (в группе: {group_photo_count}, ранее: {previously_uploaded_count}). Что дальше?",
                    reply_markup=get_photo_upload_control_keyboard()
            )
            
            return ConversationHandler.END
        
        # Если это не медиа-группа, обрабатываем как одно фото
        # Скачиваем фото
        file = await context.bot.get_file(photo.file_id)
        file_data = await file.download_as_bytearray()
        
        # Конвертируем в JPEG если нужно (асинхронно)
        try:
            def convert_to_jpeg(data):
                image = Image.open(io.BytesIO(data))
                if image.format != 'JPEG':
                    rgb_image = image.convert('RGB')
                    jpeg_buffer = io.BytesIO()
                    rgb_image.save(jpeg_buffer, format='JPEG', quality=95)
                    return jpeg_buffer.getvalue()
                return data
            
            file_data = await run_file_operation(convert_to_jpeg, file_data)
        except Exception as e:
            logger.warning(f"Не удалось обработать изображение: {e}")
        
        # Сохраняем файл локально как JPEG (асинхронно)
        file_path, public_url = await run_file_operation(
            file_manager.save_file,
            user.id, 
            bytes(file_data), 
            "photo.jpg"
        )
        
        # Сохраняем путь к файлу в контексте
        # Инициализируем image_paths если его нет
        if 'image_paths' not in context.user_data:
            context.user_data['image_paths'] = []
        
        context.user_data['image_paths'].append(str(file_path))
        context.user_data['image_path'] = str(file_path)
        context.user_data['user_id'] = user.id
        context.user_data['credit_cost'] = GENERATION_CREDIT_COST
        # Отмечаем, что были загружены новые фото (не из last_uploads или набора)
        context.user_data['new_photos_uploaded'] = True
        
        logger.debug(f"Фото сохранено: user_id={user.id}, path={file_path}")
        
        # Остаемся в состоянии ожидания фото для возможности загрузки еще
        context.user_data['state'] = STATE_WAITING_PHOTO
        
        # Показываем кнопки управления загрузкой
        photo_count = len(context.user_data['image_paths'])
        selected_mode = get_user_mode(user, context)
        MAX_PHOTOS = get_max_photos_for_mode(selected_mode)
        
        await update.message.reply_text(
            f"✅ Фото {photo_count}/{MAX_PHOTOS} сохранено. Что дальше?",
            reply_markup=get_photo_upload_control_keyboard()
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
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
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
    
    # Устанавливаем состояние в IDLE для возврата к ожиданию промпта
    context.user_data['state'] = STATE_IDLE
    
    await update.message.reply_text(help_text, reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {update.effective_user.id} использовал команду /help")
    return ConversationHandler.END


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /balance."""
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if user:
        message = f"💰 Ваш баланс: {user.credits:.2f} кредитов"
    else:
        message = "❌ Пользователь не найден. Используйте /start"
    
    # Устанавливаем состояние в IDLE для возврата к ожиданию промпта
    context.user_data['state'] = STATE_IDLE
    
    await update.message.reply_text(message, reply_markup=get_main_keyboard())
    logger.info(f"Пользователь {telegram_id} проверил баланс")
    return ConversationHandler.END


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /history."""
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Получаем последние 5 записей
    history = await run_db_operation(db_manager.get_user_history, user.id, limit=5)
    
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
    
    # Устанавливаем состояние в IDLE для возврата к ожиданию промпта
    context.user_data['state'] = STATE_IDLE
    
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
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Создаем запрос на кредиты
    credit_request = await run_db_operation(db_manager.create_credit_request, user.id, amount=CREDIT_REQUEST_AMOUNT)
    
    # Сохраняем ID запроса до использования (на случай проблем с сессией)
    request_id = credit_request.id
    request_amount = credit_request.amount
    
    # Отправляем сообщение всем админам
    admin_ids = settings.get_admin_ids()
    if admin_ids:
        try:
            admin_message = (
                f"💳 Новый запрос на кредиты\n\n"
                f"Пользователь: {user.username or f'ID: {user.telegram_id}'}\n"
                f"Telegram ID: {user.telegram_id}\n"
                f"Текущий баланс: {user.credits:.2f} кредитов\n"
                f"Запрошено: {request_amount:.2f} кредитов\n"
                f"ID запроса: {request_id}"
            )
            
            # Отправляем сообщение всем администраторам
            for admin_id in admin_ids:
                try:
                    await context.bot.send_message(
                        chat_id=admin_id,
                        text=admin_message,
                        reply_markup=get_admin_credit_request_keyboard(request_id)
                    )
                except Exception as e:
                    logger.error(f"Ошибка при отправке сообщения админу {admin_id}: {e}")
            
            # Устанавливаем состояние в IDLE для возврата к ожиданию промпта
            context.user_data['state'] = STATE_IDLE
            
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
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
        # Продолжаем выполнение, даже если callback истек
    
    # Проверяем, что это админ
    if not settings.is_admin(query.from_user.id):
        await query.message.reply_text("❌ У вас нет прав для выполнения этого действия.")
        return
    
    callback_data = query.data
    
    if callback_data.startswith("credit_approve_"):
        request_id = int(callback_data.split("_")[-1])
        credit_request = await run_db_operation(db_manager.get_credit_request, request_id)
        
        if credit_request and credit_request.status == 'pending':
            if await run_db_operation(db_manager.approve_credit_request, request_id):
                user = await run_db_operation(db_manager.get_user_by_id, credit_request.user_id)
                
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
        credit_request = await run_db_operation(db_manager.get_credit_request, request_id)
        
        if credit_request and credit_request.status == 'pending':
            if await run_db_operation(db_manager.reject_credit_request, request_id):
                user = await run_db_operation(db_manager.get_user_by_id, credit_request.user_id)
                
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
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Получаем текущий режим из БД или используем Seedream по умолчанию
    user_mode = user.selected_mode if hasattr(user, 'selected_mode') and user.selected_mode else MODE_SEEDREAM
    current_mode = context.user_data.get('selected_mode', user_mode)
    current_mode_name = get_mode_display_name(current_mode)
    
    # Устанавливаем состояние в IDLE для возврата к ожиданию промпта после выбора режима
    context.user_data['state'] = STATE_IDLE
    
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
    
    # Сохраняем выбранный режим в БД и контексте (асинхронно)
    await run_db_operation(db_manager.update_user_mode, telegram_id, selected_mode)
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
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
        # Продолжаем выполнение, даже если callback истек
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
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
    last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
    
    if not last_uploads:
        await query.message.reply_text(
            "❌ Нет сохраненных фото. Загрузи новые фото."
        )
        return
    
    # Используем последние загрузки
    context.user_data['image_paths'] = last_uploads
    context.user_data['state'] = STATE_WAITING_ASPECT
    # НЕ устанавливаем флаг new_photos_uploaded, так как используем старые фото
    context.user_data.pop('new_photos_uploaded', None)
    # Сохраняем информацию об источнике фото
    context.user_data['photo_source'] = 'last'
    context.user_data.pop('photo_source_name', None)
    
    # Редактируем сообщение вместо создания нового
    await query.message.edit_text(
        f"✅ Используются последние {len(last_uploads)} загруженных фото. Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} использует последние {len(last_uploads)} загруженных фото")


async def handle_use_reference_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для использования набора референсов."""
    query = update.callback_query
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Получаем все наборы пользователя
    sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
    
    # Фильтруем только непустые наборы
    non_empty_sets = []
    for ref_set in sets:
        images = await run_db_operation(db_manager.get_face_reference_set_images, ref_set.id, user.id)
        if images:
            non_empty_sets.append(ref_set)
    
    if not non_empty_sets:
        await query.message.edit_text(
            "❌ Нет доступных наборов с фото. Создай набор или загрузи новые фото."
        )
        return
    
    # Показываем список наборов для выбора
    await query.message.edit_text(
        f"📁 Выбери набор референсов ({len(non_empty_sets)} доступно):",
        reply_markup=get_face_reference_sets_keyboard(non_empty_sets, prefix="ref_set_use", show_create=False)
    )


async def handle_upload_new_photos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для загрузки новых фото."""
    query = update.callback_query
    await query.answer()
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Очищаем папку last_uploads при загрузке новых фото
    await run_file_operation(file_manager.clear_last_uploads, user.id)
    
    # Просим отправить новые фото
    selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    max_photos = get_max_photos_for_mode(selected_mode)
    await query.message.reply_text(
        f"📸 Отправь новые фото для обработки (можно несколько, но не более {max_photos}) или пропусти загрузку:",
        reply_markup=get_photo_skip_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} выбрал загрузку новых фото, папка last_uploads очищена")


async def handle_skip_photo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для пропуска загрузки фото."""
    query = update.callback_query
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
        # Продолжаем выполнение, даже если callback истек
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
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
    # Сохраняем информацию об источнике фото
    context.user_data['photo_source'] = 'none'
    context.user_data.pop('photo_source_name', None)
    
    # Редактируем сообщение вместо создания нового
    await query.message.edit_text(
        "✅ Фото не используются. Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )
    
    logger.debug(f"Пользователь {user.id} пропустил загрузку фото")


async def handle_photos_ready_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для подтверждения загрузки всех фото."""
    query = update.callback_query
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
        # Продолжаем выполнение, даже если callback истек
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Собираем все фото: ранее загруженные одиночные + медиа-группы
    all_image_paths = []
    
    # Добавляем ранее загруженные одиночные фото
    if 'image_paths' in context.user_data and context.user_data['image_paths']:
        all_image_paths.extend(context.user_data['image_paths'])
    
    # Добавляем фото из всех медиа-групп (сохраняем порядок по времени добавления)
    if 'media_group_photos' in context.user_data and context.user_data['media_group_photos']:
        all_media_groups = context.user_data['media_group_photos']
        if all_media_groups:
            # Объединяем все медиа-группы, сохраняя порядок
            # Сортируем по ключу (media_group_id) для стабильности, но лучше сохранить порядок вставки
            # В Python 3.7+ словари сохраняют порядок вставки, но для гарантии используем sorted по ключу
            # или сохраняем порядок через список
            for media_group_id in sorted(all_media_groups.keys()):
                group_paths = all_media_groups[media_group_id]
                all_image_paths.extend(group_paths)
                # Очищаем временное хранилище
                del context.user_data['media_group_photos']
                
    if all_image_paths:
        # Сохраняем объединенный список всех фото
        context.user_data['image_paths'] = all_image_paths
        context.user_data['state'] = STATE_WAITING_ASPECT
        # Сохраняем информацию об источнике фото
        context.user_data['photo_source'] = 'new'
        context.user_data.pop('photo_source_name', None)
        logger.debug(f"Собрано {len(all_image_paths)} фото (включая ранее загруженные)")
        
        # Редактируем сообщение вместо создания нового
        await query.message.edit_text(
            f"✅ Загружено {len(all_image_paths)} фото. Выбери соотношение сторон:",
            reply_markup=get_aspect_ratio_inline_keyboard()
        )
        return
    
    # Если фото нет, но кнопка нажата - переходим к выбору соотношения сторон
    context.user_data['state'] = STATE_WAITING_ASPECT
    # Сохраняем информацию об источнике фото
    context.user_data['photo_source'] = 'none'
    context.user_data.pop('photo_source_name', None)
    # Редактируем сообщение вместо создания нового
    await query.message.edit_text(
        "✅ Фото обработано. Выбери соотношение сторон:",
        reply_markup=get_aspect_ratio_inline_keyboard()
    )


async def handle_aspect_ratio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для выбора соотношения сторон."""
    query = update.callback_query
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
        # Продолжаем выполнение, даже если callback истек
    
    aspect_ratio = query.data.replace("aspect_", "")
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
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
    
    # Получаем данные из контекста для формирования сообщения
    image_paths = context.user_data.get('image_paths', [])
    photo_source = context.user_data.get('photo_source', 'none')
    photo_source_name = context.user_data.get('photo_source_name')
    
    # Формируем информацию о фото
    photo_info = ""
    if photo_source == 'set' and photo_source_name:
        photo_info = f"📁 Набор: {photo_source_name} ({len(image_paths)} фото)\n"
    elif photo_source == 'last':
        photo_info = f"📸 Последние загруженные фото ({len(image_paths)} фото)\n"
    elif photo_source == 'new' and image_paths:
        photo_info = f"📷 Загружено новых фото ({len(image_paths)} фото)\n"
    elif photo_source == 'none':
        photo_info = "📷 Фото не используются\n"
    
    # Удаляем сообщение пользователя с промптом, если оно сохранено
    prompt_message_id = context.user_data.get('prompt_message_id')
    if prompt_message_id:
        try:
            await context.bot.delete_message(
                chat_id=query.from_user.id,
                message_id=prompt_message_id
            )
            logger.debug(f"Удалено сообщение пользователя с промптом (message_id={prompt_message_id})")
        except Exception as delete_error:
            logger.debug(f"Не удалось удалить сообщение с промптом: {delete_error}")
        # Очищаем из контекста
        context.user_data.pop('prompt_message_id', None)
    
    # Редактируем сообщение с информацией о выбранном соотношении сторон и фото (порядок: фото/набор, соотношение сторон)
    processing_msg = await query.message.edit_text(
        f"{photo_info}✅ Соотношение сторон: {aspect_ratio}\n⏳ Генерация в процессе, ожидаю завершения...",
        reply_markup=None  # Убираем клавиатуру во время генерации
    )
    
    # Получаем данные из контекста
    selected_mode = context.user_data.get('selected_mode', MODE_NANOBANANA)
    route = selected_mode
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
            initial_result = await api_client.generate(
                prompt=prompt,
                image_paths=image_paths if image_paths else None,
                resolution="2k",
                aspect_ratio=aspect_ratio
            )
        else:  # MODE_SEEDREAM
            initial_result = await api_client.generate(
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
                # Сообщение уже обновлено выше, просто обновляем статус
                try:
                    await processing_msg.edit_text(f"{photo_info}✅ Соотношение сторон: {aspect_ratio}\n⏳ Генерация в процессе, ожидаю завершения...")
                except Exception:
                    pass
                
                # wait_for_completion теперь асинхронная, не нужно использовать run_in_executor
                result = await api_client.wait_for_completion(
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
            await run_db_operation(db_manager.update_user_credits, user_id, -credit_cost)
        else:
            logger.info(f"Кредиты не списаны из-за ошибки генерации для пользователя {user_id}")
            credit_cost = 0.0
        
        # Получаем обновленный баланс пользователя
        updated_user = await run_db_operation(db_manager.get_user_by_id, user_id)
        current_balance = updated_user.credits if updated_user else 0.0
        
        # Записываем действие в историю (даже если генерация не удалась)
        from functools import partial
        add_action_call = partial(
            db_manager.add_action,
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
            model_name=route
        )
        await run_db_operation(add_action_call)
        
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
                
                # Повторные попытки скачивания с обработкой SSL ошибок
                max_retries = 3
                retry_delay = 2  # секунды
                
                img_response = None
                last_error = None
                
                # Скачивание изображения асинхронно через httpx
                img_response = None
                last_error = None
                
                for attempt in range(max_retries):
                    try:
                        # Асинхронный запрос через httpx
                        async with httpx.AsyncClient(timeout=60.0, verify=True) as client:
                            img_response = await client.get(image_url)
                            img_response.raise_for_status()
                            break  # Успешно скачано
                    except (httpx.ConnectError, httpx.ConnectTimeout) as ssl_error:
                        last_error = ssl_error
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (attempt + 1)  # Экспоненциальная задержка
                            logger.warning(f"SSL/Connection ошибка при скачивании (попытка {attempt + 1}/{max_retries}): {ssl_error}. Повтор через {wait_time} сек...")
                            await asyncio.sleep(wait_time)
                        else:
                            # Последняя попытка - пробуем без проверки SSL (не рекомендуется, но как fallback)
                            logger.warning(f"Последняя попытка скачивания без проверки SSL...")
                            try:
                                async with httpx.AsyncClient(timeout=60.0, verify=False) as client:
                                    img_response = await client.get(image_url)
                                    img_response.raise_for_status()
                                    break
                            except Exception as final_error:
                                logger.error(f"Не удалось скачать изображение даже без проверки SSL: {final_error}")
                                raise
                    except httpx.RequestError as req_error:
                        last_error = req_error
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (attempt + 1)
                            logger.warning(f"Ошибка запроса при скачивании (попытка {attempt + 1}/{max_retries}): {req_error}. Повтор через {wait_time} сек...")
                            await asyncio.sleep(wait_time)
                        else:
                            raise
                
                if img_response is None:
                    raise Exception(f"Не удалось скачать изображение после {max_retries} попыток: {last_error}")
                
                image_data = img_response.content
                
                # Получаем промпт и модель из контекста для сохранения в EXIF
                prompt = context.user_data.get('prompt', '')
                selected_mode = get_user_mode(user, context)
                model_name = get_mode_display_name(selected_mode)
                
                result_path, result_url = await run_file_operation(
                    file_manager.save_result_image,
                    user_id=user_id,
                    image_data=image_data,
                    filename=None,
                    prompt=prompt,
                    model=model_name
                )
                
                logger.debug(f"Изображение сохранено: {result_path}")
                
                # Получаем информацию о фото и соотношении сторон для финального сообщения
                photo_source = context.user_data.get('photo_source', 'none')
                photo_source_name = context.user_data.get('photo_source_name')
                saved_aspect_ratio = context.user_data.get('aspect_ratio', aspect_ratio)
                
                # Формируем информацию о фото для финального сообщения
                photo_info_final = ""
                if photo_source == 'set' and photo_source_name:
                    photo_info_final = f"📁 Набор: {photo_source_name} ({len(image_paths) if image_paths else 0} фото)\n"
                elif photo_source == 'last':
                    photo_info_final = f"📸 Последние загруженные фото ({len(image_paths) if image_paths else 0} фото)\n"
                elif photo_source == 'new' and image_paths:
                    photo_info_final = f"📷 Загружено новых фото ({len(image_paths)} фото)\n"
                elif photo_source == 'none':
                    photo_info_final = "📷 Фото не используются\n"
                
                # Финальное сообщение с информацией о генерации (порядок: фото/набор, соотношение сторон, завершение)
                final_message = (
                    f"{photo_info_final}"
                    f"✅ Соотношение сторон: {saved_aspect_ratio}\n"
                    f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)"
                )
                
                # Редактируем сообщение о процессе генерации
                try:
                    await processing_msg.edit_text(final_message)
                except Exception:
                    pass
                
                # Сначала отправляем как файл (без сжатия, высокое качество)
                try:
                    with open(result_path, 'rb') as f:
                        await context.bot.send_document(
                            chat_id=query.from_user.id,
                            document=InputFile(f, filename=result_path.name),
                            caption=None  # Без подписи, промпт будет в фото
                        )
                except Exception as send_error:
                    error_name = type(send_error).__name__
                    if 'TimedOut' in error_name or 'PeerFlood' in error_name:
                        logger.warning(f"Ошибка отправки файла ({error_name}), отправляем только ссылку: {send_error}")
                        # Если не удалось отправить файл, отправляем только сообщение со ссылкой
                        try:
                            await query.message.reply_text(final_message)
                        except Exception:
                            pass
                    else:
                        raise
                
                # Затем отправляем как фото для предпросмотра с промптом в подписи (если промпт не слишком длинный)
                try:
                    await asyncio.sleep(0.5)  # Небольшая задержка между отправками
                    # Формируем подпись с промптом и моделью
                    if prompt:
                        # Форматируем промпт через обратные кавычки
                        quoted_prompt = f"```\n{prompt}\n```"
                        photo_caption = f"📝 **Промпт:**\n{quoted_prompt}\n\n🤖 **Модель:** {model_name}"
                        
                        # Если промпт длиннее 1000 символов, отправляем его отдельным сообщением
                        if len(prompt) > 1000:
                            # Отправляем фото только с моделью
                            await context.bot.send_photo(
                                chat_id=query.from_user.id,
                                photo=image_data,
                                caption=f"🤖 Сгенерированно моделью: {model_name}"
                            )
                            # Отправляем промпт отдельным сообщением
                            await asyncio.sleep(0.3)  # Небольшая задержка
                            prompt_message = f"📝 **Промпт:**\n{quoted_prompt}\n\n🤖 **Модель:** {model_name}"
                            await context.bot.send_message(
                                chat_id=query.from_user.id,
                                text=prompt_message,
                                parse_mode='Markdown'
                            )
                        else:
                            # Промпт помещается в подпись - отправляем с промптом
                            await context.bot.send_photo(
                                chat_id=query.from_user.id,
                                photo=image_data,
                                caption=photo_caption,
                                parse_mode='Markdown'
                            )
                    else:
                        # Нет промпта - отправляем только с моделью
                        await context.bot.send_photo(
                            chat_id=query.from_user.id,
                            photo=image_data,
                            caption=f"🤖 Сгенерированно моделью: {model_name}"
                        )
                except Exception as photo_error:
                    error_name = type(photo_error).__name__
                    if 'TimedOut' in error_name or 'PeerFlood' in error_name:
                        logger.warning(f"Ошибка отправки фото ({error_name}): {photo_error}")
                        # Игнорируем ошибку - файл уже отправлен
                    else:
                        logger.warning(f"Ошибка отправки фото: {photo_error}")
                
                # Отправляем финальное сообщение с балансом и моделью
                try:
                    await asyncio.sleep(0.5)  # Небольшая задержка
                    final_info_message = (
                        f"💰 Текущий баланс: {current_balance:.2f} кредитов\n"
                        f"🎨 Текущая модель: {model_name}\n"
                        f"💡 Можешь ввести новый промпт для следующей генерации!"
                    )
                    await context.bot.send_message(
                        chat_id=query.from_user.id,
                        text=final_info_message
                    )
                except Exception as final_msg_error:
                    logger.warning(f"Ошибка отправки финального сообщения: {final_msg_error}")
                
            except Exception as e:
                logger.error(f"Ошибка при скачивании или сохранении изображения: {e}", exc_info=True)
                
                # Получаем информацию о фото и соотношении сторон
                photo_source = context.user_data.get('photo_source', 'none')
                photo_source_name = context.user_data.get('photo_source_name')
                saved_aspect_ratio = context.user_data.get('aspect_ratio', aspect_ratio)
                
                # Формируем информацию о фото
                photo_info_final = ""
                if photo_source == 'set' and photo_source_name:
                    photo_info_final = f"📁 Набор: {photo_source_name} ({len(image_paths) if image_paths else 0} фото)\n"
                elif photo_source == 'last':
                    photo_info_final = f"📸 Последние загруженные фото ({len(image_paths) if image_paths else 0} фото)\n"
                elif photo_source == 'new' and image_paths:
                    photo_info_final = f"📷 Загружено новых фото ({len(image_paths)} фото)\n"
                elif photo_source == 'none':
                    photo_info_final = "📷 Фото не используются\n"
                
                response_text = (
                    f"✅ Соотношение сторон: {saved_aspect_ratio}\n"
                    f"{photo_info_final}"
                    f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)\n"
                    f"🖼️ Ссылка на результат: {image_url}\n\n"
                    f"💰 Текущий баланс: {current_balance:.2f} кредитов\n"
                    f"🎨 Текущая модель: {model_name}\n"
                    f"💡 Можешь ввести новый промпт для следующей генерации!"
                )
                try:
                    await processing_msg.edit_text(response_text)
                except Exception:
                    await query.message.reply_text(response_text)
        else:
            # Получаем информацию о фото и соотношении сторон
            photo_source = context.user_data.get('photo_source', 'none')
            photo_source_name = context.user_data.get('photo_source_name')
            saved_aspect_ratio = context.user_data.get('aspect_ratio', aspect_ratio)
            
            # Формируем информацию о фото
            photo_info_final = ""
            if photo_source == 'set' and photo_source_name:
                photo_info_final = f"📁 Набор: {photo_source_name} ({len(image_paths) if image_paths else 0} фото)\n"
            elif photo_source == 'last':
                photo_info_final = f"📸 Последние загруженные фото ({len(image_paths) if image_paths else 0} фото)\n"
            elif photo_source == 'new' and image_paths:
                photo_info_final = f"📷 Загружено новых фото ({len(image_paths)} фото)\n"
            elif photo_source == 'none':
                photo_info_final = "📷 Фото не используются\n"
            
            response_text = (
                f"✅ Соотношение сторон: {saved_aspect_ratio}\n"
                f"{photo_info_final}"
                f"✅ Генерация завершена! (-{credit_cost:.2f} кредитов)\n"
                f"⚠️ URL изображения не найден в ответе API.\n\n"
                f"💰 Текущий баланс: {current_balance:.2f} кредитов\n"
                f"🎨 Текущая модель: {model_name}\n"
                f"💡 Можешь ввести новый промпт для следующей генерации!"
            )
            try:
                await processing_msg.edit_text(response_text)
            except Exception:
                await query.message.reply_text(response_text)
        
        logger.info(f"Запрос успешно обработан для пользователя {user_id}, маршрут: {route}")
        
        # Сохраняем пути к фото для предложения создания набора (до перемещения в last_uploads)
        if context.user_data.get('new_photos_uploaded') and image_paths:
            context.user_data['saved_image_paths'] = image_paths.copy() if isinstance(image_paths, list) else list(image_paths)
        
        # Предлагаем сохранить загруженные фото как набор (если были новые фото)
        if context.user_data.get('new_photos_uploaded') and context.user_data.get('saved_image_paths'):
            try:
                await asyncio.sleep(1.5)  # Небольшая задержка после отправки результата
                saved_paths = context.user_data.get('saved_image_paths', [])
                keyboard = [
                    [
                        InlineKeyboardButton("💾 Сохранить как набор", callback_data="save_photos_as_set"),
                        InlineKeyboardButton("❌ Пропустить", callback_data="skip_save_set")
                    ]
                ]
                await query.message.reply_text(
                    f"📸 У тебя загружено {len(saved_paths)} фото для этой генерации.\n\n"
                    "💡 Хочешь сохранить их как набор референсов для будущего использования?",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                logger.warning(f"Ошибка при предложении сохранить набор: {e}")
        
    except Exception as e:
        error_msg = f"❌ Ошибка при обработке запроса: {str(e)}"
        try:
            await processing_msg.edit_text(error_msg)
        except Exception:
            await query.message.reply_text(error_msg)
        
        from functools import partial
        add_action_call = partial(
            db_manager.add_action,
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
        await run_db_operation(add_action_call)
        
        logger.error(f"Ошибка при обработке запроса для пользователя {user_id}: {e}")
        
        # Сохраняем пути к фото для предложения создания набора (до перемещения в last_uploads)
        if context.user_data.get('new_photos_uploaded') and context.user_data.get('image_paths'):
            context.user_data['saved_image_paths'] = context.user_data['image_paths'].copy() if isinstance(context.user_data['image_paths'], list) else list(context.user_data['image_paths'])
        
        # Предлагаем сохранить загруженные фото как набор даже при ошибке (если были новые фото)
        if context.user_data.get('new_photos_uploaded') and context.user_data.get('saved_image_paths'):
            try:
                saved_paths = context.user_data.get('saved_image_paths', [])
                keyboard = [
                    [
                        InlineKeyboardButton("💾 Сохранить как набор", callback_data="save_photos_as_set"),
                        InlineKeyboardButton("❌ Пропустить", callback_data="skip_save_set")
                    ]
                ]
                # Используем processing_msg если доступен, иначе query.message
                try:
                    await processing_msg.reply_text(
                        f"📸 У тебя загружено {len(saved_paths)} фото для этой генерации.\n\n"
                        "💡 Хочешь сохранить их как набор референсов для будущего использования?",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                except Exception:
                    try:
                        await query.message.reply_text(
                            f"📸 У тебя загружено {len(saved_paths)} фото для этой генерации.\n\n"
                            "💡 Хочешь сохранить их как набор референсов для будущего использования?",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"Ошибка при предложении сохранить набор после ошибки: {e}")
    
    finally:
        # Перемещаем временные файлы в last_uploads вместо удаления
        if 'image_paths' in context.user_data and context.user_data['image_paths']:
            # Сохраняем пути перед перемещением (для предложения создания набора)
            saved_image_paths = context.user_data['image_paths'].copy() if isinstance(context.user_data['image_paths'], list) else list(context.user_data['image_paths'])
            
            # Фильтруем файлы из наборов - они не должны перемещаться
            # Файлы из наборов остаются в sets/{set_id}/
            paths_to_move = [p for p in context.user_data['image_paths'] if 'sets' not in str(p)]
            if paths_to_move:
                # Перемещаем только файлы, которые не из наборов
                moved_paths = await run_file_operation(file_manager.move_to_last_uploads, user_id, paths_to_move)
                logger.debug(f"Перемещено {len(moved_paths)} файлов в last_uploads для пользователя {user_id}")
            else:
                logger.debug(f"Нет файлов для перемещения в last_uploads (все из наборов)")
            
            # Обновляем сохраненные пути после перемещения (теперь они в last_uploads)
            if context.user_data.get('new_photos_uploaded') and 'saved_image_paths' in context.user_data:
                # Обновляем пути на новые пути в last_uploads
                context.user_data['saved_image_paths'] = moved_paths
        
        if 'media_group_photos' in context.user_data:
            # Собираем все пути из медиа-групп
            all_media_paths = []
            for media_id, paths in context.user_data['media_group_photos'].items():
                all_media_paths.extend(paths)
            
            if all_media_paths:
                moved_paths = await run_file_operation(file_manager.move_to_last_uploads, user_id, all_media_paths)
                logger.debug(f"Перемещено {len(moved_paths)} файлов из медиа-групп в last_uploads для пользователя {user_id}")
            
            del context.user_data['media_group_photos']
        
        # Предлагаем сохранить загруженные фото как набор (если были новые фото и генерация завершена)
        if context.user_data.get('new_photos_uploaded'):
            saved_paths = context.user_data.get('saved_image_paths') or context.user_data.get('image_paths', [])
            if saved_paths:
                try:
                    await asyncio.sleep(1)  # Небольшая задержка
                    keyboard = [
                        [
                            InlineKeyboardButton("💾 Сохранить как набор", callback_data="save_photos_as_set"),
                            InlineKeyboardButton("❌ Пропустить", callback_data="skip_save_set")
                        ]
                    ]
                    # Используем query если доступен, иначе отправляем новое сообщение
                    try:
                        await query.message.reply_text(
                            f"📸 У тебя загружено {len(saved_paths)} фото для этой генерации.\n\n"
                            "💡 Хочешь сохранить их как набор референсов для будущего использования?",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    except Exception:
                        # Если query недоступен, пропускаем предложение
                        pass
                except Exception as e:
                    logger.warning(f"Ошибка при предложении сохранить набор в finally: {e}")
        
        # Сбрасываем состояние
        context.user_data['state'] = STATE_IDLE


async def handle_photo_upload_control_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для кнопок управления загрузкой фото."""
    query = update.callback_query
    if not query:
        return
    
    # Обрабатываем истекшие callback query
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query (возможно истек): {e}")
    
    callback_data = query.data
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    if callback_data == "photos_all_uploaded":
        # Все фото загружены - переходим к выбору соотношения сторон
        # Собираем все фото: ранее загруженные одиночные + медиа-группы
        all_image_paths = []
        
        # Добавляем ранее загруженные одиночные фото
        if 'image_paths' in context.user_data and context.user_data['image_paths']:
            all_image_paths.extend(context.user_data['image_paths'])
        
        # Добавляем фото из всех медиа-групп (сохраняем порядок)
        if 'media_group_photos' in context.user_data and context.user_data['media_group_photos']:
            all_media_groups = context.user_data['media_group_photos']
            if all_media_groups:
                # Объединяем все медиа-группы, сохраняя порядок
                # Сортируем по ключу для стабильности порядка
                for media_group_id in sorted(all_media_groups.keys()):
                    group_paths = all_media_groups[media_group_id]
                    all_image_paths.extend(group_paths)
                # Очищаем временное хранилище
                del context.user_data['media_group_photos']
        
        if not all_image_paths:
            await query.message.edit_text("❌ Нет загруженных фото. Отправь фото или отмени операцию.")
            return
        
        # Сохраняем объединенный список всех фото
        context.user_data['image_paths'] = all_image_paths
        
        # Меняем состояние на ожидание выбора аспектов
        context.user_data['state'] = STATE_WAITING_ASPECT
        # Сохраняем информацию об источнике фото
        context.user_data['photo_source'] = 'new'
        context.user_data.pop('photo_source_name', None)
        
        # Редактируем сообщение вместо создания нового
        await query.message.edit_text(
            f"✅ Загружено {len(all_image_paths)} фото. Выбери соотношение сторон:",
            reply_markup=get_aspect_ratio_inline_keyboard()
        )
        
    elif callback_data == "photos_upload_more":
        # Продолжить загрузку - остаемся в состоянии ожидания фото
        selected_mode = get_user_mode(user, context)
        max_photos = get_max_photos_for_mode(selected_mode)
        
        # Подсчитываем общее количество фото: ранее загруженные + медиа-группы
        photo_count = len(context.user_data.get('image_paths', []))
        if 'media_group_photos' in context.user_data and context.user_data['media_group_photos']:
            for group_paths in context.user_data['media_group_photos'].values():
                photo_count += len(group_paths)
        
        if photo_count >= max_photos:
            await query.answer("❌ Достигнут лимит фото", show_alert=True)
            return
        
        await query.message.edit_text(
            f"📸 Отправь еще фото (загружено {photo_count}/{max_photos}):"
        )
        # Состояние уже STATE_WAITING_PHOTO, ничего не меняем
        
    elif callback_data == "photos_upload_cancel":
        # Отмена - очищаем загруженные фото и возвращаемся к ожиданию промпта
        # Удаляем сообщение пользователя с промптом, если оно сохранено
        prompt_message_id = context.user_data.get('prompt_message_id')
        if prompt_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=query.from_user.id,
                    message_id=prompt_message_id
                )
                logger.debug(f"Удалено сообщение пользователя с промптом при отмене загрузки фото (message_id={prompt_message_id})")
            except Exception as delete_error:
                logger.debug(f"Не удалось удалить сообщение с промптом: {delete_error}")
        
        context.user_data['image_paths'] = []
        context.user_data['state'] = STATE_IDLE
        
        # Очищаем временные файлы
        if 'image_paths' in context.user_data:
            user_id = context.user_data.get('user_id')
            if user_id:
                await run_file_operation(file_manager.clear_last_uploads, user_id)
        
        await query.message.edit_text(
            "❌ Загрузка фото отменена. Введи новый промпт для генерации:"
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений."""
    # Проверяем наличие сообщения
    if not update.message:
        logger.warning("Получен update без message в handle_text")
        return ConversationHandler.END
    
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
    elif text == "✨ Промт-мастер":
        return await prompt_master_command(update, context)
    elif text == "💾 Занимаемый объём":
        return await storage_size_command(update, context)
    else:
        # Если это не команда меню, проверяем состояние
        state = context.user_data.get('state', STATE_IDLE)
        
        if state == STATE_IDLE:
            # Это промпт - начинаем процесс генерации
            return await handle_prompt(update, context)
        elif state == STATE_WAITING_PROMPT_DESCRIPTION:
            # Обработка описания для промпт-мастера
            return await handle_prompt_description(update, context)
        elif state == STATE_WAITING_SET_NAME:
            # Обработка имени нового набора
            set_name = text.strip()
            if not set_name or len(set_name) > 255:
                await update.message.reply_text(
                    "❌ Название набора не может быть пустым или длиннее 255 символов. Введи название еще раз:"
                )
                return ConversationHandler.END
            
            telegram_id = update.effective_user.id
            user = await run_db_operation(db_manager.get_user, telegram_id)
            if not user:
                await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
                context.user_data.clear()
                return ConversationHandler.END
            
            # Создаем набор
            try:
                ref_set = await run_db_operation(db_manager.create_face_reference_set, user.id, set_name)
                
                # Проверяем, есть ли сохраненные фото из генерации
                saved_paths = context.user_data.get('saved_image_paths')
                if saved_paths:
                    # Добавляем сохраненные фото в набор (перемещаем в папку набора)
                    added_count = 0
                    for file_path in saved_paths:
                        try:
                            # Перемещаем файл в папку набора
                            set_file_path = await run_file_operation(file_manager.move_file_to_set, user.id, ref_set.id, file_path)
                            file_hash = await run_file_operation(file_manager.calculate_file_hash, set_file_path)
                            await run_db_operation(db_manager.add_image_to_face_reference_set, ref_set.id, set_file_path, file_hash)
                            added_count += 1
                        except Exception as e:
                            logger.error(f"Ошибка при добавлении фото {file_path} в набор {ref_set.id}: {e}")
                    
                    # Очищаем сохраненные пути
                    context.user_data.pop('saved_image_paths', None)
                    context.user_data.pop('new_photos_uploaded', None)
                    
                    await update.message.reply_text(
                        f"✅ Набор '{set_name}' создан!\n"
                        f"📸 Добавлено {added_count} фото в набор.\n\n"
                        f"Используй команду /face_reference_sets для управления набором."
                    )
                    logger.info(f"Пользователь {user.id} создал набор '{set_name}' (ID: {ref_set.id}) с {added_count} фото")
                else:
                    # Нет сохраненных фото - обычный процесс создания набора
                    context.user_data['ref_set_id'] = ref_set.id
                    context.user_data['state'] = STATE_WAITING_SET_PHOTOS
                    
                    # Проверяем наличие последних загрузок
                    last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
                    
                    if last_uploads:
                        # Предлагаем использовать последние загрузки или загрузить новые
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ Использовать последние фото", callback_data=f"ref_set_add_from_last_{ref_set.id}"),
                            ],
                            [
                                InlineKeyboardButton("📷 Загрузить новые фото", callback_data=f"ref_set_add_new_{ref_set.id}"),
                            ]
                        ]
                        await update.message.reply_text(
                            f"✅ Набор '{set_name}' создан!\n\n"
                            f"У тебя есть {len(last_uploads)} фото из последней генерации. Использовать их или загрузить новые?",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    else:
                        await update.message.reply_text(
                            f"✅ Набор '{set_name}' создан!\n\n"
                            "Загрузи фото, которые хочешь добавить в набор:"
                        )
                    
                    logger.info(f"Пользователь {user.id} создал набор '{set_name}' (ID: {ref_set.id})")
            except Exception as e:
                logger.error(f"Ошибка при создании набора: {e}")
                await update.message.reply_text(
                    "❌ Произошла ошибка при создании набора. Попробуй еще раз."
                )
                context.user_data.clear()
            
            return ConversationHandler.END
        elif state == STATE_WAITING_SET_NAME_EDIT:
            # Обработка нового названия для редактирования набора
            set_name = text.strip()
            if not set_name or len(set_name) > 255:
                await update.message.reply_text(
                    "❌ Название набора не может быть пустым или длиннее 255 символов. Введи название еще раз:"
                )
                return ConversationHandler.END
            
            telegram_id = update.effective_user.id
            user = await run_db_operation(db_manager.get_user, telegram_id)
            if not user:
                await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
                context.user_data.clear()
                return ConversationHandler.END
            
            ref_set_id = context.user_data.get('ref_set_id')
            if not ref_set_id:
                await update.message.reply_text("❌ Ошибка: не найден ID набора. Начни заново.")
                context.user_data.clear()
                return ConversationHandler.END
            
            # Обновляем название набора
            updated = await run_db_operation(db_manager.update_face_reference_set_name, ref_set_id, user.id, set_name)
            
            if updated:
                # Получаем обновленный набор
                ref_set = await run_db_operation(db_manager.get_face_reference_set, ref_set_id, user.id)
                images = await run_db_operation(db_manager.get_face_reference_set_images, ref_set_id, user.id)
                
                await update.message.reply_text(
                    f"✅ Название набора изменено на '{set_name}'.\n\n"
                    f"📁 Набор: {ref_set.name}\n"
                    f"📸 Фото в наборе: {len(images)}\n"
                    f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                    reply_markup=get_face_reference_set_management_keyboard(ref_set_id)
                )
                logger.info(f"Пользователь {user.id} изменил название набора {ref_set_id} на '{set_name}'")
            else:
                await update.message.reply_text("❌ Не удалось изменить название набора.")
            
            # Очищаем состояние
            context.user_data.pop('ref_set_id', None)
            context.user_data['state'] = STATE_IDLE
            return ConversationHandler.END
        elif state == STATE_WAITING_PHOTO:
            # Пользователь отправил текст вместо фото - напоминаем
            telegram_id = update.effective_user.id
            user = await run_db_operation(db_manager.get_user, telegram_id)
            selected_mode = get_user_mode(user, context)
            max_photos = get_max_photos_for_mode(selected_mode)
            
            # Проверяем, есть ли уже загруженные фото
            photo_count = len(context.user_data.get('image_paths', []))
            
            if photo_count > 0:
                # Есть загруженные фото - предлагаем продолжить или завершить
                await update.message.reply_text(
                    f"📸 У тебя уже загружено {photo_count} фото. Отправь еще фото (максимум {max_photos}) или используй кнопки ниже:",
                    reply_markup=get_photo_upload_control_keyboard()
                )
            else:
                # Нет загруженных фото - напоминаем загрузить
                await update.message.reply_text(
                    f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или пропусти загрузку:",
                    reply_markup=get_photo_skip_inline_keyboard()
                )
        else:
            # Неизвестное состояние - сбрасываем
            context.user_data['state'] = STATE_IDLE
            # Не отправляем сообщение, чтобы не показывать меню
            # Пользователь может просто ввести промпт
        
        return ConversationHandler.END


async def prompt_master_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды Промт-мастер."""
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    
    # Устанавливаем состояние ожидания описания
    context.user_data['state'] = STATE_WAITING_PROMPT_DESCRIPTION
    
    # Создаем inline клавиатуру с кнопкой отмены
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = [[InlineKeyboardButton("❌ Отменить промт-мастер", callback_data="cancel_prompt_master")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "✨ Промт-мастер поможет создать идеальный промпт для генерации!\n\n"
        "💬 Скажи, что ты хотел бы увидеть на изображении?\n\n"
        "Например:\n"
        "• Аниме девушка в готическом стиле, черное платье, темный фон\n"
        "• Космический корабль в стиле киберпанк, неоновые огни\n"
        "• Пейзаж заката над океаном, романтическая атмосфера",
        reply_markup=reply_markup
    )
    
    logger.info(f"Пользователь {telegram_id} запустил Промт-мастер")
    return ConversationHandler.END


async def handle_prompt_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик описания для промпт-мастера."""
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start", reply_markup=get_main_keyboard())
        context.user_data['state'] = STATE_IDLE
        return ConversationHandler.END
    
    description = update.message.text.strip()
    
    if not description:
        await update.message.reply_text(
            "❌ Описание не может быть пустым. Опиши желаемый результат:",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END
    
    # Логируем запрос
    logger.info(f"Пользователь {telegram_id} отправил описание для промпт-мастера: {description[:100]}...")
    
    # Отправляем сообщение о начале генерации
    processing_msg = await update.message.reply_text("⏳ Генерирую промпт через DeepSeek...")
    
    try:
        # Создаем клиент DeepSeek
        logger.info(f"Отправка запроса в DeepSeek API для пользователя {telegram_id}")
        deepseek_client = DeepSeekClient()
        
        # Генерируем промпт (теперь асинхронная функция, не нужно run_in_executor)
        response_text = await deepseek_client.generate_prompts(description)
        
        logger.info(f"Получен ответ от DeepSeek API для пользователя {telegram_id}, длина ответа: {len(response_text)} символов")
        
        # Извлекаем промпт из ```prompt``` блоков
        import re
        prompt_pattern = r'```prompt\s*\n(.*?)\n```'
        matches = re.findall(prompt_pattern, response_text, re.DOTALL | re.IGNORECASE)
        
        if matches:
            # Берем первый найденный промпт
            generated_prompt = matches[0].strip()
        else:
            # Если не нашли в тегах, пробуем найти в ``` без указания prompt
            code_pattern = r'```[^\n]*\n(.*?)\n```'
            code_matches = re.findall(code_pattern, response_text, re.DOTALL)
            if code_matches:
                generated_prompt = code_matches[0].strip()
            else:
                # Если ничего не нашли, используем весь ответ
                generated_prompt = response_text.strip()
        
        # Обновляем сообщение с результатом
        # Форматируем промпт через обратные кавычки
        quoted_prompt = f"```\n{generated_prompt}\n```"
        result_message = (
            f"✨ Промпт сгенерирован!\n\n"
            f"📝 **Промпт:**\n{quoted_prompt}\n\n"
            f"💡 Хочешь использовать этот промпт для генерации в режиме Seedream 4.5?"
        )
        
        # Создаем inline-кнопки для использования промпта
        # Сохраняем промпт в контексте для использования
        # Не передаем промпт через callback_data (ограничение 64 байта), используем контекст
        context.user_data['generated_prompt'] = generated_prompt
        context.user_data['prompt_ready'] = True  # Флаг, что промпт готов к использованию
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Использовать для Seedream", callback_data="use_prompt_seedream"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel_prompt_master")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        try:
            await processing_msg.edit_text(result_message, reply_markup=reply_markup, parse_mode='Markdown')
        except Exception:
            await update.message.reply_text(result_message, reply_markup=reply_markup, parse_mode='Markdown')
        
        logger.info(f"Промпт сгенерирован для пользователя {telegram_id}")
        
    except Exception as e:
        logger.error(f"Ошибка при генерации промпта: {e}", exc_info=True)
        
        # Формируем понятное сообщение об ошибке
        error_str = str(e)
        if "Неверный API ключ" in error_str:
            user_message = "❌ Ошибка: Неверный API ключ DeepSeek.\n\n💡 Обратитесь к администратору."
        elif "Недостаточно средств" in error_str:
            user_message = "❌ Ошибка: Недостаточно средств на балансе DeepSeek API.\n\n💡 Попробуй позже или используй обычный промпт."
            
            # Отправляем уведомление всем администраторам, если ошибка произошла не у админа
            if not settings.is_admin(telegram_id):
                try:
                    admin_message = (
                        f"⚠️ Уведомление о проблеме с DeepSeek API\n\n"
                        f"Пользователь {telegram_id} ({user.username if user else 'без username'}) получил ошибку:\n"
                        f"Недостаточно средств на балансе DeepSeek API\n\n"
                        f"Описание запроса: {description[:100]}..."
                    )
                    # Отправляем всем администраторам
                    for admin_id in settings.get_admin_ids():
                        try:
                            await context.bot.send_message(
                                chat_id=admin_id,
                                text=admin_message
                            )
                        except Exception as e:
                            logger.error(f"Ошибка при отправке уведомления админу {admin_id}: {e}")
                    logger.info(f"Отправлено уведомление администраторам о недостатке средств DeepSeek API")
                except Exception as admin_error:
                    logger.error(f"Ошибка при отправке уведомления администраторам: {admin_error}")
        elif "лимит запросов" in error_str:
            user_message = "❌ Ошибка: Превышен лимит запросов.\n\n💡 Попробуй через несколько минут или используй обычный промпт."
        else:
            user_message = f"❌ Ошибка при генерации промпта.\n\n💡 Попробуй еще раз или используй обычный промпт."
        
        try:
            await processing_msg.edit_text(user_message, reply_markup=get_main_keyboard())
        except Exception:
            await update.message.reply_text(user_message, reply_markup=get_main_keyboard())
    
    # Сбрасываем состояние
    context.user_data['state'] = STATE_IDLE
    return ConversationHandler.END


async def handle_use_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для использования сгенерированного промпта."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    callback_data = query.data
    
    if callback_data == "cancel_prompt_master":
        canceled = cancel_prompt_master_state(context)
        if canceled:
            await query.message.edit_text(
                "❌ Промт-мастер отменен.\n\n"
                "💡 Введи новый промпт для генерации или используй команды из меню."
            )
        else:
            await query.message.edit_text("❌ Промт-мастер уже не активен.")
        return
    
    if callback_data == "use_prompt_seedream":
        # Получаем промпт из контекста (не из callback_data из-за ограничения длины)
        generated_prompt = context.user_data.get('generated_prompt')
        if not generated_prompt:
            await query.message.edit_text("❌ Промпт не найден. Попробуй сгенерировать заново.")
            context.user_data.pop('prompt_ready', None)
            return
        
        # Устанавливаем режим Seedream и промпт
        context.user_data['selected_mode'] = MODE_SEEDREAM
        context.user_data['prompt'] = generated_prompt
        context.user_data['state'] = STATE_WAITING_PHOTO
        context.user_data['user_id'] = user.id
        context.user_data['credit_cost'] = GENERATION_CREDIT_COST
        
        # Очищаем предыдущие фото из контекста
        context.user_data['image_paths'] = []
        context.user_data['media_group_photos'] = {}
        
        # Проверяем наличие последних загрузок (асинхронно)
        last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
        
        # Форматируем промпт через обратные кавычки
        quoted_prompt = f"```\n{generated_prompt}\n```"
        
        if last_uploads:
            # Есть последние загрузки - предлагаем использовать их или пропустить
            await query.message.edit_text(
                f"✅ Промпт установлен!\n\n"
                f"📝 **Промпт:**\n{quoted_prompt}\n\n"
                f"📸 Фото которые применялись в последней генерации, можно применить повторно или загрузить новые:",
                parse_mode='Markdown',
                reply_markup=get_use_last_uploads_inline_keyboard()
            )
        else:
            # Нет последних загрузок - предлагаем загрузить фото или пропустить
            max_photos = get_max_photos_for_mode(MODE_SEEDREAM)
            await query.message.edit_text(
                f"✅ Промпт установлен!\n\n"
                f"📝 **Промпт:**\n{quoted_prompt}\n\n"
                f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или пропусти загрузку:",
                parse_mode='Markdown',
                reply_markup=get_photo_skip_inline_keyboard()
            )
        
        logger.info(f"Пользователь {telegram_id} использует сгенерированный промпт для Seedream")


async def face_reference_sets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для управления наборами референсов."""
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await update.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Получаем все наборы пользователя
    sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
    
    # Показываем список наборов (с кнопкой создания нового)
    if not sets:
        await update.message.reply_text(
            "📁 У тебя пока нет наборов референсов.\n\n"
            "💡 Наборы позволяют сохранять и переиспользовать наборы фотографий для генерации.\n\n"
            "Создай свой первый набор:",
            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_use", show_create=True)
        )
    else:
        await update.message.reply_text(
            f"📁 Твои наборы референсов ({len(sets)}):",
            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
        )


async def handle_face_reference_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для работы с наборами референсов."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    callback_data = query.data
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Использование набора при генерации
    if callback_data.startswith("ref_set_use_"):
        set_id_str = callback_data.replace("ref_set_use_", "")
        if set_id_str == "cancel":
            await query.message.edit_text("❌ Выбор набора отменен.")
            return
        
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        # Получаем набор и его изображения
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
        if not images:
            await query.answer("❌ В наборе нет изображений", show_alert=True)
            return
        
        # Получаем пути к файлам из набора (файлы должны быть в sets/{set_id}/)
        image_paths = [img.file_path for img in images]
        
        # Проверяем существование файлов (для обратной совместимости со старыми наборами)
        valid_paths = []
        for file_path in image_paths:
            if os.path.exists(file_path):
                valid_paths.append(file_path)
            else:
                # Файл не найден - пытаемся найти по имени (для старых наборов)
                filename = os.path.basename(file_path)
                found_path = await run_file_operation(file_manager.find_file_by_name, user.id, filename)
                if found_path:
                    # Обновляем путь в БД для старых наборов
                    for img in images:
                        if img.file_path == file_path:
                            await run_db_operation(db_manager.update_face_reference_set_image_path, img.id, found_path)
                            logger.info(f"Обновлен путь файла в наборе: {file_path} -> {found_path}")
                            break
                    valid_paths.append(found_path)
                else:
                    logger.warning(f"Файл не найден для набора референсов: {file_path} (пропущен)")
        
        if not valid_paths:
            await query.answer("❌ Не удалось найти файлы набора. Возможно, они были удалены.", show_alert=True)
            return
        
        image_paths = valid_paths
        
        # Очищаем временные файлы пользователя (last_uploads и корневая папка) при использовании набора
        moved_count = await run_file_operation(file_manager.clear_user_temp_files, user.id)
        if moved_count > 0:
            logger.debug(f"Очищены временные файлы пользователя {user.id} при использовании набора: перемещено {moved_count} файлов")
        
        # Сохраняем пути к файлам в контекст
        context.user_data['image_paths'] = image_paths
        context.user_data['state'] = STATE_WAITING_ASPECT
        # НЕ устанавливаем флаг new_photos_uploaded, так как используем набор
        context.user_data.pop('new_photos_uploaded', None)
        # Сохраняем информацию об источнике фото
        context.user_data['photo_source'] = 'set'
        context.user_data['photo_source_name'] = ref_set.name
        
        # Редактируем сообщение вместо создания нового
        await query.message.edit_text(
            f"✅ Используется набор '{ref_set.name}' ({len(image_paths)} фото). Выбери соотношение сторон:",
            reply_markup=get_aspect_ratio_inline_keyboard()
        )
        return
    
    # Создание нового набора
    if callback_data == "ref_set_create":
        context.user_data['state'] = STATE_WAITING_SET_NAME
        keyboard = [
            [telegram.InlineKeyboardButton("❌ Отмена", callback_data="ref_set_cancel_create")]
        ]
        await query.message.edit_text(
            "📝 Создание нового набора референсов\n\n"
            "Введи название для нового набора:",
            reply_markup=telegram.InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Отмена создания набора
    if callback_data == "ref_set_cancel_create":
        context.user_data.clear()
        sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
        if not sets:
            await query.message.edit_text(
                "📁 У тебя пока нет наборов референсов.\n\n"
                "💡 Наборы позволяют сохранять и переиспользовать наборы фотографий для генерации.\n\n"
                "Создай свой первый набор:",
                reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_use", show_create=True)
            )
        else:
            await query.message.edit_text(
                f"📁 Твои наборы референсов ({len(sets)}):",
                reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
            )
        return
    
    # Просмотр/управление набором
    if callback_data.startswith("ref_set_manage_"):
        set_id_str = callback_data.replace("ref_set_manage_", "")
        if set_id_str == "cancel":
            await query.message.edit_text("❌ Отменено.")
            return
        
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        # Получаем набор и его изображения
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
        
        # Проверяем, является ли текущее сообщение фото или текстом
        try:
            if query.message.photo:
                # Если это фото (из слайд-шоу), удаляем его и отправляем новое текстовое сообщение
                try:
                    await query.message.delete()
                except Exception as delete_error:
                    logger.debug(f"Не удалось удалить фото-сообщение: {delete_error}")
                    # Продолжаем выполнение, даже если не удалось удалить
                
                # Отправляем новое текстовое сообщение (используем context.bot, так как сообщение может быть удалено)
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text=f"📁 Набор: {ref_set.name}\n"
                         f"📸 Фото в наборе: {len(images)}\n"
                         f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                    reply_markup=get_face_reference_set_management_keyboard(set_id)
                )
            else:
                # Если это текстовое сообщение, редактируем его
                await query.message.edit_text(
                    f"📁 Набор: {ref_set.name}\n"
                    f"📸 Фото в наборе: {len(images)}\n"
                    f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                    reply_markup=get_face_reference_set_management_keyboard(set_id)
                )
        except Exception as e:
            logger.error(f"Ошибка при возврате к управлению набором: {e}")
            await query.answer("❌ Ошибка при возврате к набору", show_alert=True)
        return
    
    # Изменение названия набора
    if callback_data.startswith("ref_set_rename_"):
        set_id_str = callback_data.replace("ref_set_rename_", "")
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        # Устанавливаем состояние ожидания нового названия
        context.user_data['state'] = STATE_WAITING_SET_NAME_EDIT
        context.user_data['ref_set_id'] = set_id
        
        keyboard = [
            [InlineKeyboardButton("❌ Отмена", callback_data=f"ref_set_manage_{set_id}")]
        ]
        await query.message.edit_text(
            f"📝 Изменение названия набора\n\n"
            f"Текущее название: {ref_set.name}\n\n"
            f"Введи новое название:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Просмотр слайд-шоу изображений набора
    if callback_data.startswith("ref_set_view_"):
        # Формат: ref_set_view_{set_id}_{image_index}
        parts = callback_data.replace("ref_set_view_", "").split("_")
        if len(parts) != 2:
            await query.answer("❌ Неверный формат запроса", show_alert=True)
            return
        
        try:
            set_id = int(parts[0])
            image_index = int(parts[1])
        except ValueError:
            await query.answer("❌ Неверный ID набора или индекс", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
        
        if not images:
            await query.answer("❌ В наборе нет изображений", show_alert=True)
            return
        
        # Проверяем индекс
        if image_index < 0 or image_index >= len(images):
            await query.answer("❌ Неверный индекс изображения", show_alert=True)
            return
        
        current_image = images[image_index]
        file_path = current_image.file_path
        
        # Проверяем существование файла
        if not os.path.exists(file_path):
            await query.answer("❌ Файл изображения не найден", show_alert=True)
            return
        
        # Отправляем фото во всю ширину
        try:
            # Читаем файл в байты для правильной работы с API
            with open(file_path, 'rb') as f:
                photo_data = f.read()
            
            # Используем InputFile для правильной отправки фото
            photo_input = InputFile(io.BytesIO(photo_data), filename=os.path.basename(file_path))
            
            # Пытаемся отредактировать сообщение, если это возможно
            try:
                # Если предыдущее сообщение было фото, пытаемся отредактировать
                if query.message.photo:
                    # Для редактирования используем InputMediaPhoto с байтами
                    await query.message.edit_media(
                        media=InputMediaPhoto(media=io.BytesIO(photo_data), caption=f"📁 Набор: {ref_set.name}\n🖼️ Фото {image_index + 1} из {len(images)}"),
                        reply_markup=get_face_reference_set_slideshow_keyboard(set_id, image_index, len(images))
                    )
                else:
                    # Если это текстовое сообщение, отправляем новое фото
                    await query.message.reply_photo(
                        photo=photo_input,
                        caption=f"📁 Набор: {ref_set.name}\n"
                               f"🖼️ Фото {image_index + 1} из {len(images)}",
                        reply_markup=get_face_reference_set_slideshow_keyboard(set_id, image_index, len(images))
                    )
            except Exception as edit_error:
                # Если не удалось отредактировать, отправляем новое сообщение
                logger.debug(f"Не удалось отредактировать сообщение, отправляем новое: {edit_error}")
                # Создаем новый InputFile для отправки
                photo_input_new = InputFile(io.BytesIO(photo_data), filename=os.path.basename(file_path))
                await query.message.reply_photo(
                    photo=photo_input_new,
                    caption=f"📁 Набор: {ref_set.name}\n"
                           f"🖼️ Фото {image_index + 1} из {len(images)}",
                    reply_markup=get_face_reference_set_slideshow_keyboard(set_id, image_index, len(images))
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке фото из набора: {e}", exc_info=True)
            await query.answer("❌ Ошибка при загрузке изображения", show_alert=True)
        return
    
    # Удаление изображения из набора (из слайд-шоу)
    if callback_data.startswith("ref_set_delete_image_"):
        # Формат: ref_set_delete_image_{set_id}_{image_index}
        parts = callback_data.replace("ref_set_delete_image_", "").split("_")
        if len(parts) != 2:
            await query.answer("❌ Неверный формат запроса", show_alert=True)
            return
        
        try:
            set_id = int(parts[0])
            image_index = int(parts[1])
        except ValueError:
            await query.answer("❌ Неверный ID набора или индекс", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
        
        if image_index < 0 or image_index >= len(images):
            await query.answer("❌ Неверный индекс изображения", show_alert=True)
            return
        
        image_to_delete = images[image_index]
        image_id = image_to_delete.id
        
        # Удаляем изображение
        deleted = await run_db_operation(db_manager.remove_image_from_face_reference_set, image_id, set_id, user.id)
        
        if deleted:
            # Получаем обновленный список изображений
            images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
            
            if not images:
                # Если изображений не осталось, возвращаемся к управлению набором
                try:
                    # Пытаемся отредактировать сообщение
                    await query.message.edit_caption(
                        caption=f"✅ Изображение удалено.\n\n"
                               f"📁 Набор: {ref_set.name}\n"
                               f"📸 Фото в наборе: 0\n"
                               f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                        reply_markup=get_face_reference_set_management_keyboard(set_id)
                    )
                except Exception:
                    # Если не удалось отредактировать, отправляем новое сообщение
                    await query.message.reply_text(
                        f"✅ Изображение удалено.\n\n"
                        f"📁 Набор: {ref_set.name}\n"
                        f"📸 Фото в наборе: 0\n"
                        f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                        reply_markup=get_face_reference_set_management_keyboard(set_id)
                    )
            else:
                # Определяем новый индекс (если удалили последнее, показываем предыдущее)
                new_index = min(image_index, len(images) - 1)
                current_image = images[new_index]
                file_path = current_image.file_path
                
                # Отправляем следующее изображение
                try:
                    if os.path.exists(file_path):
                        # Читаем файл в байты для правильной работы с API
                        with open(file_path, 'rb') as f:
                            photo_data = f.read()
                        
                        # Используем InputFile для правильной отправки фото
                        photo_input = InputFile(io.BytesIO(photo_data), filename=os.path.basename(file_path))
                        try:
                            # Пытаемся отредактировать сообщение
                            await query.message.edit_media(
                                media=InputMediaPhoto(media=io.BytesIO(photo_data), caption=f"📁 Набор: {ref_set.name}\n🖼️ Фото {new_index + 1} из {len(images)}"),
                                reply_markup=get_face_reference_set_slideshow_keyboard(set_id, new_index, len(images))
                            )
                        except Exception:
                            # Если не удалось отредактировать, отправляем новое сообщение
                            await query.message.reply_photo(
                                photo=photo_input,
                                caption=f"📁 Набор: {ref_set.name}\n"
                                       f"🖼️ Фото {new_index + 1} из {len(images)}",
                                reply_markup=get_face_reference_set_slideshow_keyboard(set_id, new_index, len(images))
                            )
                    else:
                        await query.answer("✅ Изображение удалено", show_alert=True)
                        # Возвращаемся к управлению набором
                        try:
                            await query.message.edit_caption(
                                caption=f"✅ Изображение удалено.\n\n"
                                       f"📁 Набор: {ref_set.name}\n"
                                       f"📸 Фото в наборе: {len(images)}\n"
                                       f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                                reply_markup=get_face_reference_set_management_keyboard(set_id)
                            )
                        except Exception:
                            await query.message.reply_text(
                                f"✅ Изображение удалено.\n\n"
                                f"📁 Набор: {ref_set.name}\n"
                                f"📸 Фото в наборе: {len(images)}\n"
                                f"📅 Создан: {ref_set.created_at.strftime('%d.%m.%Y %H:%M')}",
                                reply_markup=get_face_reference_set_management_keyboard(set_id)
                            )
                except Exception as e:
                    logger.error(f"Ошибка при отправке следующего фото после удаления: {e}")
                    await query.answer("✅ Изображение удалено", show_alert=True)
            logger.info(f"Пользователь {user.id} удалил изображение {image_id} из набора {set_id}")
        else:
            await query.answer("❌ Не удалось удалить изображение", show_alert=True)
        return
    
    # Добавление фото в набор
    if callback_data.startswith("ref_set_add_"):
        set_id_str = callback_data.replace("ref_set_add_", "")
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        # Проверяем, что набор принадлежит пользователю
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        # Проверяем наличие последних загрузок
        last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
        
        if not last_uploads:
            context.user_data['state'] = STATE_WAITING_SET_PHOTOS
            context.user_data['ref_set_id'] = set_id
            await query.message.edit_text(
                f"📸 Добавление фото в набор '{ref_set.name}'\n\n"
                "Загрузи фото, которые хочешь добавить в набор:"
            )
        else:
            # Предлагаем использовать последние загрузки или загрузить новые
            keyboard = [
                [
                    InlineKeyboardButton("✅ Использовать последние фото", callback_data=f"ref_set_add_from_last_{set_id}"),
                ],
                [
                    InlineKeyboardButton("📷 Загрузить новые фото", callback_data=f"ref_set_add_new_{set_id}"),
                ],
                [
                    InlineKeyboardButton("◀️ Назад", callback_data=f"ref_set_manage_{set_id}"),
                ]
            ]
            await query.message.edit_text(
                f"📸 Добавление фото в набор '{ref_set.name}'\n\n"
                f"У тебя есть {len(last_uploads)} фото из последней генерации. Использовать их или загрузить новые?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        return
    
    # Добавление фото из last_uploads
    if callback_data.startswith("ref_set_add_from_last_"):
        set_id_str = callback_data.replace("ref_set_add_from_last_", "")
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
        if not last_uploads:
            await query.answer("❌ Нет доступных фото", show_alert=True)
            return
        
        # Добавляем все фото из last_uploads в набор (перемещаем в папку набора)
        added_count = 0
        for file_path in last_uploads:
            try:
                # Перемещаем файл в папку набора
                set_file_path = await run_file_operation(file_manager.move_file_to_set, user.id, set_id, file_path)
                file_hash = await run_file_operation(file_manager.calculate_file_hash, set_file_path)
                await run_db_operation(db_manager.add_image_to_face_reference_set, set_id, set_file_path, file_hash)
                added_count += 1
            except Exception as e:
                logger.error(f"Ошибка при добавлении фото {file_path} в набор {set_id}: {e}")
        
        await query.message.edit_text(
            f"✅ Добавлено {added_count} фото в набор '{ref_set.name}'",
            reply_markup=get_face_reference_set_management_keyboard(set_id)
        )
        logger.info(f"Пользователь {user.id} добавил {added_count} фото в набор {set_id}")
        return
    
    # Загрузка новых фото для набора
    if callback_data.startswith("ref_set_add_new_"):
        set_id_str = callback_data.replace("ref_set_add_new_", "")
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        context.user_data['state'] = STATE_WAITING_SET_PHOTOS
        context.user_data['ref_set_id'] = set_id
        await query.message.edit_text(
            f"📸 Добавление фото в набор '{ref_set.name}'\n\n"
            "Загрузи фото, которые хочешь добавить в набор:"
        )
        return
    
    # Подтверждение удаления набора (проверяем ПЕРВЫМ, чтобы не перехватить ref_set_delete_)
    if callback_data.startswith("ref_set_delete_confirm_"):
        logger.info(f"Обработка подтверждения удаления набора: callback_data={callback_data}, user_id={user.id}")
        set_id_str = callback_data.replace("ref_set_delete_confirm_", "")
        try:
            set_id = int(set_id_str)
            logger.debug(f"Извлечен set_id={set_id} из callback_data")
        except ValueError:
            logger.error(f"Неверный ID набора в callback_data: {set_id_str}")
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        try:
            # Получаем все файлы набора перед удалением
            images = await run_db_operation(db_manager.get_face_reference_set_images, set_id, user.id)
            
            logger.info(f"Попытка удаления набора {set_id} для пользователя {user.id}")
            
            # Перемещаем файлы набора в used перед удалением из БД
            if images:
                moved_count = await run_file_operation(file_manager.move_set_files_to_used, user.id, set_id)
                logger.info(f"Перемещено {moved_count} файлов из набора {set_id} в used")
            
            # Удаляем набор из БД
            deleted = await run_db_operation(db_manager.delete_face_reference_set, set_id, user.id)
            logger.info(f"Результат удаления набора {set_id}: deleted={deleted}")
            if deleted:
                # Возвращаемся к списку наборов после удаления
                sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
                try:
                    if not sets:
                        await query.message.edit_text(
                            "✅ Набор успешно удален.\n\n"
                            "📁 У тебя больше нет наборов референсов.",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
                    else:
                        await query.message.edit_text(
                            f"✅ Набор успешно удален.\n\n"
                            f"📁 Твои наборы референсов ({len(sets)}):",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
                except Exception as edit_error:
                    # Если не удалось отредактировать сообщение, отправляем новое
                    logger.warning(f"Не удалось отредактировать сообщение после удаления набора: {edit_error}")
                    if not sets:
                        await query.message.reply_text(
                            "✅ Набор успешно удален.\n\n"
                            "📁 У тебя больше нет наборов референсов.",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
                    else:
                        await query.message.reply_text(
                            f"✅ Набор успешно удален.\n\n"
                            f"📁 Твои наборы референсов ({len(sets)}):",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
                logger.info(f"Пользователь {user.id} удалил набор {set_id}")
            else:
                await query.answer("❌ Не удалось удалить набор", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении набора {set_id} для пользователя {user.id}: {e}", exc_info=True)
            try:
                await query.answer("❌ Произошла ошибка при удалении набора", show_alert=True)
            except Exception:
                pass
        return
    
    # Удаление набора (проверяем ПОСЛЕ ref_set_delete_confirm_)
    if callback_data.startswith("ref_set_delete_"):
        set_id_str = callback_data.replace("ref_set_delete_", "")
        try:
            set_id = int(set_id_str)
        except ValueError:
            await query.answer("❌ Неверный ID набора", show_alert=True)
            return
        
        ref_set = await run_db_operation(db_manager.get_face_reference_set, set_id, user.id)
        if not ref_set:
            await query.answer("❌ Набор не найден", show_alert=True)
            return
        
        # Подтверждение удаления
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, удалить", callback_data=f"ref_set_delete_confirm_{set_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"ref_set_manage_{set_id}")
            ]
        ]
        await query.message.edit_text(
            f"⚠️ Удалить набор '{ref_set.name}'?\n\n"
            "Все фото из набора будут удалены. Это действие нельзя отменить.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Возврат к списку наборов
    if callback_data == "ref_set_list":
        sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
        
        # Проверяем тип сообщения перед редактированием
        try:
            if query.message.photo:
                # Если это фото, пытаемся отредактировать подпись
                try:
                    if not sets:
                        await query.message.edit_caption(
                            caption="📁 У тебя пока нет наборов референсов.\n\n"
                                   "💡 Наборы позволяют сохранять и переиспользовать наборы фотографий для генерации.\n\n"
                                   "Создай свой первый набор:",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_use", show_create=True)
                        )
                    else:
                        await query.message.edit_caption(
                            caption=f"📁 Твои наборы референсов ({len(sets)}):",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
                except Exception:
                    # Если не удалось отредактировать подпись, отправляем новое текстовое сообщение
                    if not sets:
                        await query.message.reply_text(
                            "📁 У тебя пока нет наборов референсов.\n\n"
                            "💡 Наборы позволяют сохранять и переиспользовать наборы фотографий для генерации.\n\n"
                            "Создай свой первый набор:",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_use", show_create=True)
                        )
                    else:
                        await query.message.reply_text(
                            f"📁 Твои наборы референсов ({len(sets)}):",
                            reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                        )
            else:
                # Если это текстовое сообщение, редактируем его
                if not sets:
                    await query.message.edit_text(
                        "📁 У тебя пока нет наборов референсов.\n\n"
                        "💡 Наборы позволяют сохранять и переиспользовать наборы фотографий для генерации.\n\n"
                        "Создай свой первый набор:",
                        reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_use", show_create=True)
                    )
                else:
                    await query.message.edit_text(
                        f"📁 Твои наборы референсов ({len(sets)}):",
                        reply_markup=get_face_reference_sets_keyboard(sets, prefix="ref_set_manage", show_create=True)
                    )
        except Exception as e:
            logger.error(f"Ошибка при возврате к списку наборов: {e}")
            await query.answer("❌ Ошибка при возврате к списку наборов", show_alert=True)
        return
    
    # Закрыть настройки набора
    if callback_data == "ref_set_close":
        try:
            if query.message.photo:
                # Если это фото, пытаемся отредактировать подпись
                try:
                    await query.message.edit_caption(caption="✅ Настройки закрыты.")
                except Exception:
                    await query.message.reply_text("✅ Настройки закрыты.")
            else:
                await query.message.edit_text("✅ Настройки закрыты.")
        except Exception as e:
            logger.error(f"Ошибка при закрытии настроек набора: {e}")
            await query.answer("❌ Ошибка при закрытии настроек", show_alert=True)
        return
    
    # Закрыть главное меню наборов
    if callback_data == "ref_set_close_menu":
        try:
            if query.message.photo:
                # Если это фото, пытаемся отредактировать подпись
                try:
                    await query.message.edit_caption(
                        caption="✅ Меню наборов закрыто.\n\n"
                               "💡 Введи новый промпт для генерации или используй команды из меню."
                    )
                except Exception:
                    await query.message.reply_text(
                        "✅ Меню наборов закрыто.\n\n"
                        "💡 Введи новый промпт для генерации или используй команды из меню."
                    )
            else:
                await query.message.edit_text(
                    "✅ Меню наборов закрыто.\n\n"
                    "💡 Введи новый промпт для генерации или используй команды из меню."
                )
        except Exception as e:
            logger.error(f"Ошибка при закрытии меню наборов: {e}")
            await query.answer("❌ Ошибка при закрытии меню", show_alert=True)
        return


async def handle_restart_generation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Начать заново' - сбрасывает процесс генерации."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Удаляем сообщение пользователя с промптом, если оно сохранено
    prompt_message_id = context.user_data.get('prompt_message_id')
    if prompt_message_id:
        try:
            await context.bot.delete_message(
                chat_id=telegram_id,
                message_id=prompt_message_id
            )
            logger.debug(f"Удалено сообщение пользователя с промптом при начале заново (message_id={prompt_message_id})")
        except Exception as delete_error:
            logger.debug(f"Не удалось удалить сообщение с промптом: {delete_error}")
    
    # Очищаем все данные процесса генерации
    context.user_data.clear()
    context.user_data['state'] = STATE_IDLE
    
    logger.info(f"Пользователь {user.id} сбросил процесс генерации")
    
    # Отправляем сообщение о сбросе
    await query.message.edit_text(
        "🔄 Процесс генерации сброшен.\n\n"
        "💡 Введи новый промпт для начала генерации:"
    )


async def handle_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Назад' - возвращает на предыдущий шаг."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    callback_data = query.data
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    if callback_data == "back_to_photo_source":
        # Возвращаемся к выбору источника изображений
        context.user_data['state'] = STATE_WAITING_PHOTO
        
        # Проверяем наличие последних загрузок и наборов
        last_uploads = await run_file_operation(file_manager.get_last_uploads, user.id)
        sets = await run_db_operation(db_manager.get_user_face_reference_sets, user.id)
        
        has_non_empty_sets = False
        if sets:
            for ref_set in sets:
                images = await run_db_operation(db_manager.get_face_reference_set_images, ref_set.id, user.id)
                if images:
                    has_non_empty_sets = True
                    break
        
        if last_uploads or has_non_empty_sets:
            # Проверяем, был ли последний раз использован набор (скрываем кнопку "последние фото")
            last_photo_source = context.user_data.get('photo_source')
            show_last_uploads = last_photo_source != 'set'  # Скрываем, если последний раз был набор
            
            message = "📸 Выбери источник изображений для генерации:"
            if last_uploads and has_non_empty_sets:
                message = "📸 Фото которые применялись в последней генерации или наборы референсов, можно применить повторно или загрузить новые:"
            elif last_uploads:
                message = "📸 Фото которые применялись в последней генерации, можно применить повторно или загрузить новые:"
            elif has_non_empty_sets:
                message = "📸 Можно использовать набор референсов или загрузить новые фото:"
            
            await query.message.edit_text(
                message,
                reply_markup=get_use_last_uploads_inline_keyboard(show_use_set=has_non_empty_sets, show_last_uploads=show_last_uploads and bool(last_uploads))
            )
        else:
            selected_mode = get_user_mode(user, context)
            max_photos = get_max_photos_for_mode(selected_mode)
            await query.message.edit_text(
                f"📸 Отправь фото для обработки (можно несколько, но не более {max_photos}) или пропусти загрузку:",
                reply_markup=get_photo_skip_inline_keyboard()
            )
    
    elif callback_data == "back_to_prompt":
        # Возвращаемся к вводу промпта (сброс)
        # Удаляем сообщение пользователя с промптом, если оно сохранено
        prompt_message_id = context.user_data.get('prompt_message_id')
        if prompt_message_id:
            try:
                await context.bot.delete_message(
                    chat_id=telegram_id,
                    message_id=prompt_message_id
                )
                logger.debug(f"Удалено сообщение пользователя с промптом при возврате (message_id={prompt_message_id})")
            except Exception as delete_error:
                logger.debug(f"Не удалось удалить сообщение с промптом: {delete_error}")
        
        context.user_data.clear()
        context.user_data['state'] = STATE_IDLE
        await query.message.edit_text(
            "💡 Введи новый промпт для генерации:"
        )


async def storage_size_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для отображения занимаемого объёма проекта (только для админов)."""
    # Отменяем промт-мастер, если он активен
    cancel_prompt_master_state(context)
    
    telegram_id = update.effective_user.id
    
    # Проверяем права администратора
    if not settings.is_admin(telegram_id):
        await update.message.reply_text(
            "❌ У вас нет прав для выполнения этого действия.",
            reply_markup=get_main_keyboard(telegram_id)
        )
        return ConversationHandler.END
    
    try:
        # Подсчёт размеров в executor, чтобы не блокировать event loop (os.walk может быть долгим)
        def _compute_storage_sizes():
            project_root = Path(__file__).parent.parent

            def get_directory_size(path: Path) -> int:
                total_size = 0
                try:
                    for dirpath, dirnames, filenames in os.walk(path):
                        dirname = os.path.basename(dirpath)
                        if dirname in ['.git', '__pycache__', '.pytest_cache', 'venv', 'env', '.venv']:
                            dirnames[:] = []
                            continue
                        for filename in filenames:
                            filepath = os.path.join(dirpath, filename)
                            try:
                                total_size += os.path.getsize(filepath)
                            except (OSError, FileNotFoundError):
                                pass
                except Exception as e:
                    logger.error(f"Ошибка при подсчете размера директории {path}: {e}")
                return total_size

            storage_path = Path(settings.STORAGE_PATH)
            database_path = Path(settings.DATABASE_PATH)
            log_path = Path(settings.LOG_FILE).parent if settings.LOG_FILE else None

            total_size = get_directory_size(project_root)
            storage_size = get_directory_size(storage_path) if storage_path.exists() else 0
            db_size = os.path.getsize(database_path) if database_path.exists() else 0
            log_size = get_directory_size(log_path) if log_path and log_path.exists() else 0
            other_size = total_size - storage_size - db_size - log_size
            return total_size, storage_size, db_size, log_size, other_size

        total_size, storage_size, db_size, log_size, other_size = await run_file_operation(_compute_storage_sizes)
        
        # Форматируем размеры
        def format_size(size_bytes: int) -> str:
            """Форматировать размер в читаемый вид."""
            for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
                if size_bytes < 1024.0:
                    return f"{size_bytes:.2f} {unit}"
                size_bytes /= 1024.0
            return f"{size_bytes:.2f} ПБ"
        
        # Формируем сообщение
        message = (
            "💾 **Занимаемый объём проекта:**\n\n"
            f"📦 **Общий размер:** {format_size(total_size)}\n\n"
            f"📁 **Хранилище файлов:** {format_size(storage_size)}\n"
            f"🗄️ **База данных:** {format_size(db_size)}\n"
            f"📝 **Логи:** {format_size(log_size)}\n"
            f"📄 **Прочее (код, конфиги):** {format_size(other_size)}\n\n"
            f"📊 **Детализация:**\n"
            f"  • Хранилище: {storage_size / total_size * 100:.1f}%\n"
            f"  • База данных: {db_size / total_size * 100:.1f}%\n"
            f"  • Логи: {log_size / total_size * 100:.1f}%\n"
            f"  • Прочее: {other_size / total_size * 100:.1f}%"
        )
        
        await update.message.reply_text(
            message,
            parse_mode='Markdown',
            reply_markup=get_main_keyboard(telegram_id)
        )
        
        logger.info(f"Администратор {telegram_id} запросил информацию о занимаемом объёме")
        
    except Exception as e:
        logger.error(f"Ошибка при подсчете размера проекта: {e}")
        await update.message.reply_text(
            f"❌ Ошибка при подсчете размера: {str(e)}",
            reply_markup=get_main_keyboard(telegram_id)
        )
    
    # Устанавливаем состояние в IDLE для возврата к ожиданию промпта
    context.user_data['state'] = STATE_IDLE
    
    return ConversationHandler.END


async def handle_save_photos_as_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для сохранения загруженных фото как набор."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    telegram_id = query.from_user.id
    user = await run_db_operation(db_manager.get_user, telegram_id)
    
    if not user:
        await query.message.reply_text("❌ Пользователь не найден. Используйте /start")
        return
    
    # Получаем сохраненные пути к фото
    saved_paths = context.user_data.get('saved_image_paths') or context.user_data.get('image_paths', [])
    if not saved_paths:
        await query.answer("❌ Нет доступных фото для сохранения", show_alert=True)
        return
    
    # Переходим к созданию набора
    context.user_data['state'] = STATE_WAITING_SET_NAME
    context.user_data['saved_image_paths'] = saved_paths  # Сохраняем пути для использования после создания набора
    keyboard = [
        [InlineKeyboardButton("❌ Отмена", callback_data="ref_set_cancel_create")]
    ]
    await query.message.edit_text(
        f"📝 Создание набора из {len(saved_paths)} фото\n\n"
        "Введи название для нового набора:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_skip_save_set_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для пропуска сохранения набора."""
    query = update.callback_query
    if not query:
        return
    
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"Не удалось ответить на callback query: {e}")
    
    # Очищаем флаги
    context.user_data.pop('new_photos_uploaded', None)
    context.user_data.pop('saved_image_paths', None)
    
    await query.message.edit_text("✅ Понятно. Фото остались в last_uploads и доступны для следующей генерации.")
