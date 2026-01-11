"""
File: config/settings.py
Purpose:
    Загрузка и валидация конфигурации приложения из config.json.
    Предоставляет singleton объект settings для доступа к настройкам во всем приложении.

Responsibilities:
    - Загрузка конфигурации из JSON файла
    - Валидация обязательных параметров
    - Создание необходимых директорий
    - Предоставление типизированного доступа к настройкам

Key Design Decisions:
    - Используется singleton pattern (глобальный объект settings)
    - Все настройки загружаются при инициализации класса
    - Валидация выполняется явно через метод validate()
    - Пути к директориям создаются автоматически через ensure_directories()

Notes:
    - Файл config.json должен находиться в корне проекта
    - При отсутствии обязательных параметров выбрасывается ValueError
    - Все числовые значения конвертируются из строк
"""
import os
import json
from pathlib import Path

# Базовый путь проекта
BASE_DIR = Path(__file__).parent.parent
CONFIG_FILE = BASE_DIR / "config.json"


class Settings:
    """
    Класс для хранения и управления настройками приложения.
    
    Загружает конфигурацию из config.json и предоставляет типизированный доступ
    к настройкам через атрибуты класса.
    
    Attributes:
        TELEGRAM_BOT_TOKEN: Токен Telegram бота
        TELEGRAM_BOT_ADMIN_IDS: Список ID администраторов в Telegram
        HIGGSFIELD_API_KEY: API ключ для доступа к Higgsfield
        HIGGSFIELD_API_KEY_SECRET: Секретный ключ API
        HIGGSFIELD_API_URL: Базовый URL API (устаревший, не используется)
        HIGGSFIELD_MODEL_ID: ID модели (устаревший, не используется)
        ADMIN_PASSWORD: Пароль для админ-панели
        STORAGE_PATH: Путь к директории хранения файлов пользователей
        DATABASE_PATH: Путь к файлу базы данных SQLite
        FLASK_HOST: Хост для Flask сервера
        FLASK_PORT: Порт для Flask сервера
        LOG_LEVEL: Уровень логирования (DEBUG, INFO, WARNING, ERROR)
        LOG_FILE: Путь к файлу логов
        MAX_FILE_SIZE: Максимальный размер загружаемого файла в байтах
        API_GENERATION_TIMEOUT: Таймаут ожидания генерации в секундах
    """
    
    def __init__(self):
        """Загрузка настроек из config.json."""
        self._config = self._load_config()
        self._apply_config()
    
    def _load_config(self) -> dict:
        """Загрузить конфигурацию из config.json."""
        if not CONFIG_FILE.exists():
            raise FileNotFoundError(
                f"Файл конфигурации не найден: {CONFIG_FILE}\n"
                f"Создайте config.json в корне проекта."
            )
        
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Ошибка парсинга config.json: {e}")
        except Exception as e:
            raise ValueError(f"Ошибка при загрузке config.json: {e}")
    
    def _apply_config(self):
        """Применить настройки из конфига."""
        # Telegram Bot
        self.TELEGRAM_BOT_TOKEN: str = self._config.get("telegram_bot_token", "")
        
        # Поддержка старого формата (один ID) и нового (список)
        admin_ids = self._config.get("telegram_bot_admin_ids")
        if admin_ids is None:
            # Обратная совместимость: проверяем старое поле
            old_admin_id = self._config.get("telegram_bot_admin_id")
            if old_admin_id:
                self.TELEGRAM_BOT_ADMIN_IDS: list = [int(old_admin_id)]
            else:
                self.TELEGRAM_BOT_ADMIN_IDS: list = []
        else:
            # Новый формат: список ID
            if isinstance(admin_ids, list):
                self.TELEGRAM_BOT_ADMIN_IDS: list = [int(admin_id) for admin_id in admin_ids]
            else:
                # Если передан один ID как число, преобразуем в список
                self.TELEGRAM_BOT_ADMIN_IDS: list = [int(admin_ids)]
        
        # API Configuration
        self.HIGGSFIELD_API_KEY: str = self._config.get("higgsfield_api_key", "")
        self.HIGGSFIELD_API_KEY_SECRET: str = self._config.get("higgsfield_api_key_secret", "")
        self.HIGGSFIELD_API_URL: str = self._config.get("higgsfield_api_url", "https://cloud.higgsfield.ai/api")
        self.HIGGSFIELD_MODEL_ID: str = self._config.get("higgsfield_model_id", "")
        
        # DeepSeek API Configuration
        self.DEEPSEEK_API_KEY: str = self._config.get("deepseek_api_key", "")
        
        # Flask Admin
        self.ADMIN_PASSWORD: str = self._config.get("admin_password", "")
        
        # Server Configuration
        storage_path = self._config.get("storage_path")
        if storage_path:
            self.STORAGE_PATH: str = storage_path
        else:
            self.STORAGE_PATH: str = str(BASE_DIR / "storage" / "users")
        
        database_path = self._config.get("database_path")
        if database_path:
            self.DATABASE_PATH: str = database_path
        else:
            self.DATABASE_PATH: str = str(BASE_DIR / "database" / "app.db")
        
        # Flask Server
        self.FLASK_HOST: str = self._config.get("flask_host", "0.0.0.0")
        self.FLASK_PORT: int = int(self._config.get("flask_port", "5000"))
        
        # Logging
        self.LOG_LEVEL: str = self._config.get("log_level", "INFO")
        log_file = self._config.get("log_file")
        if log_file:
            self.LOG_FILE: str = log_file
        else:
            self.LOG_FILE: str = str(BASE_DIR / "logs" / "app.log")
        
        # File Upload Limits
        self.MAX_FILE_SIZE: int = int(self._config.get("max_file_size", "10485760"))  # 10MB по умолчанию
        
        # API Generation Timeout
        self.API_GENERATION_TIMEOUT: int = int(self._config.get("api_generation_timeout", "300"))  # 5 минут по умолчанию
        
        # File Cache TTL
        self.FILE_CACHE_TTL_DAYS: int = int(self._config.get("file_cache_ttl_days", "7"))  # 7 дней по умолчанию
    
    def validate(self) -> bool:
        """
        Валидация обязательных параметров.
        Возвращает True если все обязательные параметры заданы.
        """
        required = [
            ("telegram_bot_token", self.TELEGRAM_BOT_TOKEN),
            ("higgsfield_api_key", self.HIGGSFIELD_API_KEY),
            ("admin_password", self.ADMIN_PASSWORD),
        ]
        
        missing = [name for name, value in required if not value]
        
        if missing:
            raise ValueError(
                f"Отсутствуют обязательные параметры в config.json: {', '.join(missing)}"
            )
        
        return True
    
    def ensure_directories(self):
        """Создает необходимые директории если их нет."""
        os.makedirs(self.STORAGE_PATH, exist_ok=True)
        os.makedirs(os.path.dirname(self.DATABASE_PATH), exist_ok=True)
        os.makedirs(os.path.dirname(self.LOG_FILE), exist_ok=True)
    
    def is_admin(self, telegram_id: int) -> bool:
        """
        Проверить, является ли пользователь администратором.
        
        Args:
            telegram_id: Telegram ID пользователя
        
        Returns:
            True если пользователь является администратором
        """
        return telegram_id in self.TELEGRAM_BOT_ADMIN_IDS
    
    def get_admin_ids(self) -> list:
        """
        Получить список ID администраторов.
        
        Returns:
            Список Telegram ID администраторов
        """
        return self.TELEGRAM_BOT_ADMIN_IDS.copy()


# Создаем экземпляр настроек
settings = Settings()
