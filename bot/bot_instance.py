"""
Модуль для доступа к экземпляру бота из других модулей (например, админки).
"""
from telegram import Bot
from config.settings import settings
from utils.logger import logger

# Глобальный экземпляр бота для использования в админке
_bot_instance = None


def get_bot_instance() -> Bot:
    """
    Получить экземпляр Telegram бота для отправки сообщений.
    
    Returns:
        Экземпляр Bot для отправки сообщений
    """
    global _bot_instance
    if _bot_instance is None:
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN не установлен в config.json")
        _bot_instance = Bot(token=token)
        logger.debug("Создан экземпляр Bot для админки")
    return _bot_instance


def set_bot_instance(bot: Bot):
    """
    Установить экземпляр бота (используется при запуске бота).
    
    Args:
        bot: Экземпляр Bot
    """
    global _bot_instance
    _bot_instance = bot
    logger.debug("Установлен экземпляр Bot для админки")
