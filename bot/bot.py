"""
Основной файл Telegram бота.
"""
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ConversationHandler
from config.settings import settings
from utils.logger import logger
from bot.handlers import (
    start_command,
    help_command,
    balance_command,
    history_command,
    request_credits_command,
    handle_prompt,
    handle_photo,
    handle_text,
    handle_credit_request_callback,
    change_mode_command,
    handle_mode_selection,
    handle_skip_photo_callback,
    handle_photos_ready_callback,
    handle_use_last_uploads_callback,
    handle_upload_new_photos_callback,
    handle_aspect_ratio_callback,
    cancel,
    WAITING_FOR_MODE
)


class TelegramBot:
    """Класс для управления Telegram ботом."""
    
    def __init__(self):
        """Инициализация бота."""
        self.token = settings.TELEGRAM_BOT_TOKEN
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен в config.json")
        
        self.application = Application.builder().token(self.token).build()
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Настройка обработчиков команд и сообщений."""
        # Команды
        self.application.add_handler(CommandHandler("start", start_command))
        self.application.add_handler(CommandHandler("help", help_command))
        self.application.add_handler(CommandHandler("balance", balance_command))
        self.application.add_handler(CommandHandler("history", history_command))
        self.application.add_handler(CommandHandler("cancel", cancel))
        
        # Conversation handler для смены режима
        mode_conv_handler = ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^🔄 Сменить режим$"), change_mode_command)],
            states={
                WAITING_FOR_MODE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_mode_selection),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", cancel),
                MessageHandler(filters.COMMAND, cancel),
            ],
        )
        self.application.add_handler(mode_conv_handler)
        
        # Обработка фото
        self.application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        # Обработка текста (для команд меню и промптов)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        
        # Обработка callback запросов
        self.application.add_handler(CallbackQueryHandler(handle_credit_request_callback, pattern="^credit_"))
        self.application.add_handler(CallbackQueryHandler(handle_skip_photo_callback, pattern="^skip_photo$"))
        self.application.add_handler(CallbackQueryHandler(handle_photos_ready_callback, pattern="^photos_ready$"))
        self.application.add_handler(CallbackQueryHandler(handle_use_last_uploads_callback, pattern="^use_last_uploads$"))
        self.application.add_handler(CallbackQueryHandler(handle_upload_new_photos_callback, pattern="^upload_new_photos$"))
        self.application.add_handler(CallbackQueryHandler(handle_aspect_ratio_callback, pattern="^aspect_"))
        
        logger.info("Обработчики бота настроены")
    
    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик ошибок."""
        logger.error(f"Ошибка в боте: {context.error}", exc_info=context.error)
    
    def run(self):
        """Запуск бота."""
        self.application.add_error_handler(self.error_handler)
        logger.info("Запуск Telegram бота...")
        self.application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )


def main():
    """Точка входа для запуска бота отдельно."""
    try:
        settings.validate()
        bot = TelegramBot()
        bot.run()
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise


if __name__ == "__main__":
    main()
