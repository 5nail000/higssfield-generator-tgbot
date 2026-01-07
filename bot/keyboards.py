"""
File: bot/keyboards.py
Purpose:
    Определение клавиатур для Telegram бота (Reply и Inline).
    Содержит все UI элементы для взаимодействия с пользователями.

Responsibilities:
    - Создание главной клавиатуры меню
    - Создание клавиатур для выбора режима генерации
    - Создание inline-клавиатур для интерактивных действий
    - Создание клавиатур для администраторов

Key Design Decisions:
    - ReplyKeyboardMarkup для постоянного меню (главное меню)
    - InlineKeyboardMarkup для временных действий (выбор аспекта, пропуск фото)
    - Все клавиатуры создаются через функции для переиспользования

Notes:
    - Главное меню всегда видимо (resize_keyboard=True)
    - Inline-кнопки не занимают место в чате после использования
"""
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton


def get_main_keyboard():
    """Главная клавиатура."""
    keyboard = [
        [KeyboardButton("💰 Баланс"), KeyboardButton("📜 История")],
        [KeyboardButton("💳 Запросить кредиты"), KeyboardButton("ℹ️ Помощь")],
        [KeyboardButton("🔄 Сменить режим")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_mode_selection_keyboard():
    """Клавиатура для выбора режима генерации."""
    keyboard = [
        [KeyboardButton("🍌 NANOBANANA"), KeyboardButton("🎨 Seedream 4.5")],
        [KeyboardButton("Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_admin_credit_request_keyboard(request_id: int):
    """Клавиатура для админа при запросе кредитов."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"credit_approve_{request_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"credit_reject_{request_id}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_aspect_ratio_inline_keyboard():
    """Inline-клавиатура для выбора соотношения сторон."""
    keyboard = [
        [
            InlineKeyboardButton("4:3", callback_data="aspect_4:3"),
            InlineKeyboardButton("16:9", callback_data="aspect_16:9"),
            InlineKeyboardButton("1:1", callback_data="aspect_1:1")
        ],
        [
            InlineKeyboardButton("9:16", callback_data="aspect_9:16"),
            InlineKeyboardButton("3:4", callback_data="aspect_3:4"),
            InlineKeyboardButton("21:9", callback_data="aspect_21:9")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_photo_skip_inline_keyboard():
    """Inline-клавиатура для пропуска загрузки фото."""
    keyboard = [
        [InlineKeyboardButton("⏭️ Пропустить фото", callback_data="skip_photo")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_photos_ready_inline_keyboard():
    """Inline-клавиатура после загрузки хотя бы одного фото."""
    keyboard = [
        [InlineKeyboardButton("✅ Все фото загружены", callback_data="photos_ready")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_use_last_uploads_inline_keyboard():
    """Inline-клавиатура для использования последних загруженных фото."""
    keyboard = [
        [InlineKeyboardButton("📸 Использовать последние фото", callback_data="use_last_uploads")],
        [InlineKeyboardButton("⏭️ Не использовать изображения", callback_data="skip_photo")]
    ]
    return InlineKeyboardMarkup(keyboard)
