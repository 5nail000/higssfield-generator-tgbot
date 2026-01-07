"""
File: utils/logger.py
Purpose:
    Настройка системы логирования для приложения.
    Обеспечивает вывод логов в консоль и файл с временными метками.

Responsibilities:
    - Создание и настройка логгера
    - Форматирование сообщений с временем в начале
    - Настройка обработчиков для консоли и файла
    - Управление уровнями логирования

Key Design Decisions:
    - Используется кастомный форматтер TimeFormatter для добавления времени
    - Логи выводятся и в консоль, и в файл одновременно
    - Уровень логирования настраивается через config.json
    - Все логи имеют формат: [timestamp] level - name - message

Notes:
    - Время добавляется в начало сообщения, а не в стандартный формат
    - Файл логов создается автоматически, если директории не существует
    - Логгер является singleton (глобальный объект logger)
"""
import logging
import sys
from pathlib import Path
from datetime import datetime
from config.settings import settings


class TimeFormatter(logging.Formatter):
    """
    Кастомный форматтер для добавления времени в начало каждой записи лога.
    
    Переопределяет метод format() для добавления временной метки в формате
    [YYYY-MM-DD HH:MM:SS] в начало сообщения перед логированием.
    """
    
    def format(self, record):
        # Получаем текущее время
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Форматируем сообщение с временем в начале
        record.msg = f"[{timestamp}] {record.msg}"
        return super().format(record)


def setup_logger(name: str = "app", level: str = None) -> logging.Logger:
    """
    Настройка логгера.
    
    Args:
        name: Имя логгера
        level: Уровень логирования (если None, берется из настроек)
    
    Returns:
        Настроенный логгер
    """
    if level is None:
        level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Удаляем существующие обработчики
    logger.handlers.clear()
    
    # Форматтер с временем в начале
    formatter = TimeFormatter(
        fmt='%(levelname)s - %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Обработчик для консоли
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Обработчик для файла
    log_file_path = Path(settings.LOG_FILE)
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = logging.FileHandler(log_file_path, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger


# Создаем основной логгер
logger = setup_logger("app")
