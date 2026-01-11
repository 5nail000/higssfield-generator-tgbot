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
from config.settings import settings


def get_main_keyboard(telegram_id: int = None):
    """
    Главная клавиатура.
    
    Args:
        telegram_id: Telegram ID пользователя (опционально, для проверки прав админа)
    """
    keyboard = [
        [KeyboardButton("💰 Баланс"), KeyboardButton("📜 История")],
        [KeyboardButton("💳 Запросить кредиты"), KeyboardButton("ℹ️ Помощь")],
        [KeyboardButton("🔄 Сменить режим"), KeyboardButton("✨ Промт-мастер")],
        [KeyboardButton("📁 Наборы референсов")]
    ]
    
    # Добавляем кнопки для администраторов
    if telegram_id and settings.is_admin(telegram_id):
        keyboard.append([KeyboardButton("💾 Занимаемый объём")])
    
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
        ],
        [
            InlineKeyboardButton("◀️ Назад", callback_data="back_to_photo_source"),
            InlineKeyboardButton("❌ Начать заново", callback_data="restart_generation")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_photo_skip_inline_keyboard():
    """Inline-клавиатура для пропуска загрузки фото."""
    keyboard = [
        [InlineKeyboardButton("⏭️ Пропустить фото", callback_data="skip_photo")],
        [InlineKeyboardButton("❌ Начать заново", callback_data="restart_generation")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_photos_ready_inline_keyboard():
    """Inline-клавиатура после загрузки хотя бы одного фото."""
    keyboard = [
        [InlineKeyboardButton("✅ Все фото загружены", callback_data="photos_ready")],
        [InlineKeyboardButton("❌ Начать заново", callback_data="restart_generation")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_use_last_uploads_inline_keyboard(show_use_set: bool = False, show_last_uploads: bool = True):
    """Inline-клавиатура для использования последних загруженных фото."""
    keyboard = []
    if show_use_set:
        keyboard.append([InlineKeyboardButton("📁 Использовать набор", callback_data="use_reference_set")])
    if show_last_uploads:
        keyboard.append([InlineKeyboardButton("📸 Использовать последние фото", callback_data="use_last_uploads")])
    keyboard.append([InlineKeyboardButton("⏭️ Не использовать изображения", callback_data="skip_photo")])
    keyboard.append([
        InlineKeyboardButton("◀️ Назад", callback_data="back_to_prompt"),
    ])
    return InlineKeyboardMarkup(keyboard)


def get_photo_upload_control_keyboard():
    """Клавиатура для управления загрузкой фото после загрузки каждого фото."""
    keyboard = [
        [
            InlineKeyboardButton("✅ Все фото загружены", callback_data="photos_all_uploaded"),
            InlineKeyboardButton("📷 Загрузить ещё фото", callback_data="photos_upload_more")
        ],
        [InlineKeyboardButton("❌ Отмена", callback_data="photos_upload_cancel")],
        [InlineKeyboardButton("❌ Начать заново", callback_data="restart_generation")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_face_reference_sets_keyboard(sets: list, prefix: str = "ref_set", show_create: bool = True) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру со списком наборов референсов.
    
    Args:
        sets: Список объектов FaceReferenceSet
        prefix: Префикс для callback_data (по умолчанию "ref_set")
        show_create: Показывать ли кнопку создания нового набора
    
    Returns:
        InlineKeyboardMarkup с кнопками наборов
    """
    keyboard = []
    for ref_set in sets:
        keyboard.append([
            InlineKeyboardButton(
                f"📁 {ref_set.name}",
                callback_data=f"{prefix}_{ref_set.id}"
            )
        ])
    
    if show_create:
        keyboard.append([InlineKeyboardButton("➕ Создать новый набор", callback_data="ref_set_create")])
    
    # Кнопка "Отмена" для использования набора при генерации
    if prefix == "ref_set_use":
        keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data=f"{prefix}_cancel")])
    # Кнопка "Закрыть" для главного меню наборов
    elif prefix == "ref_set_manage":
        keyboard.append([InlineKeyboardButton("❌ Закрыть", callback_data="ref_set_close_menu")])
    
    return InlineKeyboardMarkup(keyboard)


def get_face_reference_set_management_keyboard(set_id: int) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру для управления набором референсов.
    
    Args:
        set_id: ID набора
    
    Returns:
        InlineKeyboardMarkup с кнопками управления
    """
    keyboard = [
        [
            InlineKeyboardButton("📝 Изменить название", callback_data=f"ref_set_rename_{set_id}"),
            InlineKeyboardButton("🖼️ Просмотр фото", callback_data=f"ref_set_view_{set_id}_0")
        ],
        [
            InlineKeyboardButton("➕ Добавить фото", callback_data=f"ref_set_add_{set_id}"),
            InlineKeyboardButton("🗑️ Удалить набор", callback_data=f"ref_set_delete_{set_id}")
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="ref_set_list")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_face_reference_set_slideshow_keyboard(set_id: int, current_index: int, total_images: int) -> InlineKeyboardMarkup:
    """
    Создать клавиатуру для слайд-шоу изображений набора.
    
    Args:
        set_id: ID набора
        current_index: Текущий индекс изображения (0-based)
        total_images: Общее количество изображений
    
    Returns:
        InlineKeyboardMarkup с кнопками навигации
    """
    keyboard = []
    
    # Кнопки навигации
    nav_buttons = []
    if total_images > 1:
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("◀️ Предыдущее", callback_data=f"ref_set_view_{set_id}_{current_index - 1}"))
        if current_index < total_images - 1:
            nav_buttons.append(InlineKeyboardButton("▶️ Следующее", callback_data=f"ref_set_view_{set_id}_{current_index + 1}"))
        if nav_buttons:
            keyboard.append(nav_buttons)
    
    # Кнопки действий
    action_buttons = []
    if total_images > 0:
        action_buttons.append(InlineKeyboardButton("🗑️ Удалить это фото", callback_data=f"ref_set_delete_image_{set_id}_{current_index}"))
    action_buttons.append(InlineKeyboardButton("➕ Добавить фото", callback_data=f"ref_set_add_{set_id}"))
    keyboard.append(action_buttons)
    
    # Кнопка возврата
    keyboard.append([InlineKeyboardButton("◀️ Назад к набору", callback_data=f"ref_set_manage_{set_id}")])
    
    return InlineKeyboardMarkup(keyboard)