"""
File: storage/file_manager.py
Purpose:
    Управление файловым хранилищем для фото пользователей.
    Обеспечивает сохранение, перемещение и получение публичных URL для файлов.

Responsibilities:
    - Сохранение загруженных пользователем фото
    - Сохранение сгенерированных изображений
    - Управление папками last_uploads, results, used
    - Генерация публичных URL для доступа к файлам через Flask

Key Design Decisions:
    - Используется singleton pattern (глобальный объект file_manager)
    - Файлы никогда не удаляются напрямую, только перемещаются в used/
    - Структура: storage/users/{user_id}/{last_uploads|results|used}/
    - Публичные URL генерируются с дефолтным значением (http://localhost:5000)

Notes:
    - Все файлы сохраняются с UUID именами для избежания конфликтов
    - При перемещении файлов в used/ добавляется timestamp, если файл уже существует
    - Файлы из last_uploads можно переиспользовать для следующей генерации
"""
import os
import time
import uuid
import hashlib
from pathlib import Path
from typing import Optional
from config.settings import settings
from utils.logger import logger


class FileManager:
    """
    Менеджер для работы с файловым хранилищем пользователей.
    
    Управляет сохранением, перемещением и получением URL для файлов пользователей.
    Все файлы организованы по структуре: storage/users/{user_id}/{category}/
    
    Attributes:
        storage_path: Базовый путь к хранилищу файлов
        external_url: Публичный URL сервера для доступа к файлам (дефолтное значение)
        max_file_size: Максимальный размер загружаемого файла
    """
    
    def __init__(self):
        """Инициализация менеджера файлов."""
        self.storage_path = Path(settings.STORAGE_PATH)
        self.external_url = "http://localhost:5000"  # Дефолтное значение
        self.max_file_size = settings.MAX_FILE_SIZE
        self.ensure_storage_exists()
    
    def ensure_storage_exists(self):
        """Создать директорию хранилища если её нет."""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Хранилище файлов: {self.storage_path}")
    
    def get_user_directory(self, user_id: int) -> Path:
        """
        Получить путь к директории пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Path к директории пользователя
        """
        user_dir = self.storage_path / str(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
    
    def get_results_directory(self, user_id: int) -> Path:
        """
        Получить путь к директории результатов пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Path к директории results пользователя
        """
        user_dir = self.get_user_directory(user_id)
        results_dir = user_dir / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        return results_dir
    
    def get_last_uploads_directory(self, user_id: int) -> Path:
        """
        Получить путь к директории последних загрузок пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Path к директории last_uploads пользователя
        """
        user_dir = self.get_user_directory(user_id)
        last_uploads_dir = user_dir / "last_uploads"
        last_uploads_dir.mkdir(parents=True, exist_ok=True)
        return last_uploads_dir
    
    def get_used_directory(self, user_id: int) -> Path:
        """
        Получить путь к директории использованных файлов пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Path к директории used пользователя
        """
        user_dir = self.get_user_directory(user_id)
        used_dir = user_dir / "used"
        used_dir.mkdir(parents=True, exist_ok=True)
        return used_dir
    
    def get_set_directory(self, user_id: int, set_id: int) -> Path:
        """
        Получить путь к директории набора референсов.
        
        Args:
            user_id: ID пользователя
            set_id: ID набора референсов
        
        Returns:
            Path к директории sets/{set_id} пользователя
        """
        user_dir = self.get_user_directory(user_id)
        sets_dir = user_dir / "sets" / str(set_id)
        sets_dir.mkdir(parents=True, exist_ok=True)
        return sets_dir
    
    def get_set_directory(self, user_id: int, set_id: int) -> Path:
        """
        Получить путь к директории набора референсов.
        
        Args:
            user_id: ID пользователя
            set_id: ID набора референсов
        
        Returns:
            Path к директории sets/{set_id} пользователя
        """
        user_dir = self.get_user_directory(user_id)
        sets_dir = user_dir / "sets" / str(set_id)
        sets_dir.mkdir(parents=True, exist_ok=True)
        return sets_dir
    
    def move_to_last_uploads(self, user_id: int, file_paths: list[str]) -> list[str]:
        """
        Переместить файлы в папку last_uploads.
        Если файл уже находится в last_uploads, не перемещает его повторно.
        
        Args:
            user_id: ID пользователя
            file_paths: Список путей к файлам для перемещения
        
        Returns:
            Список новых путей к файлам в last_uploads
        """
        last_uploads_dir = self.get_last_uploads_directory(user_id)
        new_paths = []
        
        for file_path in file_paths:
            if file_path and os.path.exists(file_path):
                try:
                    source_path = Path(file_path)
                    filename = source_path.name
                    
                    # Проверяем, не находится ли файл в папке набора (sets/{set_id}/)
                    # Файлы из наборов не должны перемещаться
                    if 'sets' in str(source_path):
                        logger.debug(f"Файл из набора, пропущен (не перемещается): {file_path}")
                        new_paths.append(str(source_path))  # Оставляем оригинальный путь
                        continue
                    
                    dest_path = last_uploads_dir / filename
                    
                    # Проверяем, не находится ли файл уже в last_uploads
                    if source_path.parent == last_uploads_dir:
                        # Файл уже в last_uploads - просто добавляем путь в список
                        new_paths.append(str(source_path))
                        logger.debug(f"Файл уже в last_uploads, пропущен: {file_path}")
                        continue
                    
                    # Если файл с таким именем уже существует в last_uploads, перемещаем его в used
                    if dest_path.exists():
                        self.move_to_used(user_id, [str(dest_path)])
                    
                    # Перемещаем файл
                    source_path.rename(dest_path)
                    new_paths.append(str(dest_path))
                    logger.debug(f"Файл перемещен в last_uploads: {file_path} -> {dest_path}")
                except Exception as e:
                    logger.error(f"Ошибка при перемещении файла {file_path} в last_uploads: {e}")
        
        return new_paths
    
    def move_to_used(self, user_id: int, file_paths: list[str]) -> list[str]:
        """
        Переместить файлы в папку used.
        
        Args:
            user_id: ID пользователя
            file_paths: Список путей к файлам для перемещения
        
        Returns:
            Список новых путей к файлам в used
        """
        used_dir = self.get_used_directory(user_id)
        new_paths = []
        
        for file_path in file_paths:
            if not file_path:
                continue
                
            # Проверяем существование файла
            if not os.path.exists(file_path):
                logger.warning(f"Файл не найден, пропускаем перемещение в used: {file_path}")
                continue
                
            try:
                source_path = Path(file_path)
                filename = source_path.name
                dest_path = used_dir / filename
                
                # Если файл с таким именем уже существует, добавляем timestamp
                if dest_path.exists():
                    timestamp = int(time.time())
                    name_parts = source_path.stem, timestamp, source_path.suffix
                    filename = f"{name_parts[0]}_{name_parts[1]}{name_parts[2]}"
                    dest_path = used_dir / filename
                
                # Дополнительная проверка перед перемещением (на случай, если файл был удален между проверками)
                if not source_path.exists():
                    logger.warning(f"Файл был удален перед перемещением, пропускаем: {file_path}")
                    continue
                
                # Перемещаем файл
                source_path.rename(dest_path)
                new_paths.append(str(dest_path))
                logger.debug(f"Файл перемещен в used: {file_path} -> {dest_path}")
            except FileNotFoundError:
                logger.warning(f"Файл не найден при перемещении в used (возможно, уже удален): {file_path}")
            except Exception as e:
                logger.error(f"Ошибка при перемещении файла {file_path} в used: {e}")
        
        return new_paths
    
    def get_last_uploads(self, user_id: int) -> list[str]:
        """
        Получить список путей к файлам из last_uploads.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список путей к файлам
        """
        last_uploads_dir = self.get_last_uploads_directory(user_id)
        file_paths = []
        
        if last_uploads_dir.exists():
            # Получаем файлы с информацией о времени модификации для сортировки по порядку создания
            files_with_time = []
            for file_path in last_uploads_dir.iterdir():
                if file_path.is_file():
                    files_with_time.append((file_path.stat().st_mtime, str(file_path)))
            
            # Сортируем по времени модификации (время создания файла) для сохранения порядка
            files_with_time.sort(key=lambda x: x[0])
            file_paths = [path for _, path in files_with_time]
        
        return file_paths
    
    def find_file_by_name(self, user_id: int, filename: str) -> Optional[str]:
        """
        Найти файл по имени в различных папках пользователя.
        Ищет в следующем порядке: sets (все наборы), last_uploads, корневая папка пользователя, results, used.
        
        Args:
            user_id: ID пользователя
            filename: Имя файла для поиска
        
        Returns:
            Путь к найденному файлу или None, если файл не найден
        """
        user_dir = self.get_user_directory(user_id)
        
        # Сначала ищем в папках наборов (sets/{set_id}/)
        sets_dir = user_dir / "sets"
        if sets_dir.exists():
            for set_dir in sets_dir.iterdir():
                if set_dir.is_dir():
                    file_path = set_dir / filename
                    if file_path.exists() and file_path.is_file():
                        logger.debug(f"Файл найден: {filename} в {set_dir}")
                        return str(file_path)
        
        # Затем ищем в других папках
        search_dirs = [
            self.get_last_uploads_directory(user_id),
            user_dir,  # Корневая папка пользователя
            self.get_results_directory(user_id),
            self.get_used_directory(user_id)
        ]
        
        for search_dir in search_dirs:
            if not search_dir.exists():
                continue
            
            file_path = search_dir / filename
            if file_path.exists() and file_path.is_file():
                logger.debug(f"Файл найден: {filename} в {search_dir}")
                return str(file_path)
        
        logger.warning(f"Файл не найден: {filename} для пользователя {user_id}")
        return None
    
    def move_file_to_set(self, user_id: int, set_id: int, file_path: str) -> str:
        """
        Переместить файл в папку набора референсов.
        Если файл уже находится в папке набора, возвращает его путь без изменений.
        
        Args:
            user_id: ID пользователя
            set_id: ID набора референсов
            file_path: Путь к файлу для перемещения
        
        Returns:
            Новый путь к файлу в папке набора
        """
        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        
        set_dir = self.get_set_directory(user_id, set_id)
        source_path = Path(file_path)
        filename = source_path.name
        dest_path = set_dir / filename
        
        # Проверяем, не находится ли файл уже в папке набора
        if source_path.parent == set_dir:
            logger.debug(f"Файл уже в папке набора, пропущен: {file_path}")
            return str(source_path)
        
        # Если файл с таким именем уже существует в папке набора, добавляем timestamp
        if dest_path.exists():
            timestamp = int(time.time())
            name_parts = filename.rsplit('.', 1)
            if len(name_parts) == 2:
                new_filename = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
            else:
                new_filename = f"{filename}_{timestamp}"
            dest_path = set_dir / new_filename
        
        # Перемещаем файл
        try:
            source_path.rename(dest_path)
            logger.debug(f"Файл перемещен в папку набора: {file_path} -> {dest_path}")
            return str(dest_path)
        except Exception as e:
            logger.error(f"Ошибка при перемещении файла {file_path} в папку набора: {e}")
            raise
    
    def move_set_files_to_used(self, user_id: int, set_id: int) -> int:
        """
        Переместить все файлы из папки набора в used.
        
        Args:
            user_id: ID пользователя
            set_id: ID набора референсов
        
        Returns:
            Количество перемещенных файлов
        """
        set_dir = self.get_set_directory(user_id, set_id)
        moved_count = 0
        
        if set_dir.exists():
            file_paths = [str(file_path) for file_path in set_dir.iterdir() if file_path.is_file()]
            if file_paths:
                moved_paths = self.move_to_used(user_id, file_paths)
                moved_count = len(moved_paths)
                logger.debug(f"Перемещено {moved_count} файлов из набора {set_id} в used для пользователя {user_id}")
        
        return moved_count
    
    def clear_user_temp_files(self, user_id: int) -> int:
        """
        Очистить временные файлы пользователя (last_uploads и корневая папка).
        Перемещает все файлы в used.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Количество перемещенных файлов
        """
        moved_count = 0
        
        # Очищаем last_uploads
        last_uploads_dir = self.get_last_uploads_directory(user_id)
        if last_uploads_dir.exists():
            file_paths = [str(file_path) for file_path in last_uploads_dir.iterdir() if file_path.is_file()]
            if file_paths:
                moved_paths = self.move_to_used(user_id, file_paths)
                moved_count += len(moved_paths)
        
        # Очищаем корневую папку пользователя (кроме папок sets, last_uploads, results, used)
        user_dir = self.get_user_directory(user_id)
        if user_dir.exists():
            file_paths = []
            for file_path in user_dir.iterdir():
                if file_path.is_file():
                    file_paths.append(str(file_path))
            
            if file_paths:
                moved_paths = self.move_to_used(user_id, file_paths)
                moved_count += len(moved_paths)
        
        if moved_count > 0:
            logger.debug(f"Очищены временные файлы пользователя {user_id}: перемещено {moved_count} файлов в used")
        
        return moved_count
    
    def clear_last_uploads(self, user_id: int) -> int:
        """
        Очистить папку last_uploads пользователя, переместив файлы в used.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Количество перемещенных файлов
        """
        last_uploads_dir = self.get_last_uploads_directory(user_id)
        moved_count = 0
        
        if last_uploads_dir.exists():
            file_paths = [str(file_path) for file_path in last_uploads_dir.iterdir() if file_path.is_file()]
            if file_paths:
                moved_paths = self.move_to_used(user_id, file_paths)
                moved_count = len(moved_paths)
        
        if moved_count > 0:
            logger.debug(f"Очищена папка last_uploads пользователя {user_id}: перемещено {moved_count} файлов в used")
        
        return moved_count
    
    def save_result_image(self, user_id: int, image_data: bytes, filename: str = None, prompt: str = None, model: str = None) -> tuple[Path, str]:
        """
        Сохранить результат генерации (изображение) в папку results.
        
        Args:
            user_id: ID пользователя
            image_data: Данные изображения (bytes)
            filename: Имя файла (опционально, будет сгенерировано если не указано)
            prompt: Промпт для записи в EXIF (опционально)
            model: Модель для записи в EXIF (опционально)
        
        Returns:
            Tuple (путь к файлу, публичный URL)
        """
        # Получаем директорию results
        results_dir = self.get_results_directory(user_id)
        
        # Генерируем имя файла если не указано
        if not filename:
            filename = f"{uuid.uuid4()}.png"
        
        file_path = results_dir / filename
        
        # Сохраняем файл
        try:
            # Если есть промпт или модель, добавляем EXIF метаданные
            if prompt or model:
                try:
                    from PIL import Image
                    from PIL.PngImagePlugin import PngInfo
                    import io
                    
                    # Открываем изображение из байтов
                    img = Image.open(io.BytesIO(image_data))
                    
                    # Создаем объект для метаданных PNG
                    metadata = PngInfo()
                    
                    # Добавляем промпт и модель в метаданные PNG
                    if prompt:
                        metadata.add_text("Prompt", prompt[:65535])
                    if model:
                        metadata.add_text("Model", model)
                    
                    # Сохраняем с метаданными
                    img.save(file_path, format='PNG', pnginfo=metadata)
                    logger.debug(f"Изображение сохранено с EXIF метаданными: prompt={bool(prompt)}, model={bool(model)}")
                except Exception as exif_error:
                    logger.warning(f"Ошибка при обработке EXIF: {exif_error}, сохраняем без метаданных")
                    # Сохраняем без EXIF если не удалось
                    with open(file_path, 'wb') as f:
                        f.write(image_data)
            else:
                # Сохраняем без EXIF
                with open(file_path, 'wb') as f:
                    f.write(image_data)
            
            # Генерируем публичный URL
            public_url = f"{self.external_url}/files/{user_id}/results/{filename}"
            
            logger.debug(f"Результат сохранен: user_id={user_id}, filename={filename}, size={len(image_data)}")
            return file_path, public_url
        except Exception as e:
            logger.error(f"Ошибка при сохранении результата: {e}")
            raise
    
    def save_file(self, user_id: int, file_data: bytes, original_filename: str = None) -> tuple[Path, str]:
        """
        Сохранить файл пользователя.
        
        Args:
            user_id: ID пользователя
            file_data: Данные файла (bytes)
            original_filename: Оригинальное имя файла (опционально)
        
        Returns:
            Tuple (путь к файлу, публичный URL)
        """
        # Проверка размера файла
        if len(file_data) > self.max_file_size:
            raise ValueError(f"Файл слишком большой. Максимальный размер: {self.max_file_size} байт")
        
        # Получаем директорию пользователя
        user_dir = self.get_user_directory(user_id)
        
        # Генерируем уникальное имя файла
        if original_filename:
            # Сохраняем расширение если есть
            ext = Path(original_filename).suffix
        else:
            ext = '.jpg'  # По умолчанию jpg для фото
        
        unique_filename = f"{uuid.uuid4()}{ext}"
        file_path = user_dir / unique_filename
        
        # Сохраняем файл
        try:
            with open(file_path, 'wb') as f:
                f.write(file_data)
            
            # Генерируем публичный URL
            public_url = f"{self.external_url}/files/{user_id}/{unique_filename}"
            
            logger.debug(f"Файл сохранен: user_id={user_id}, filename={unique_filename}, size={len(file_data)}")
            return file_path, public_url
        except Exception as e:
            logger.error(f"Ошибка при сохранении файла: {e}")
            raise
    
    def get_file_path(self, user_id: int, filename: str) -> Optional[Path]:
        """
        Получить путь к файлу пользователя.
        
        Args:
            user_id: ID пользователя
            filename: Имя файла
        
        Returns:
            Path к файлу или None если не найден
        """
        user_dir = self.get_user_directory(user_id)
        file_path = user_dir / filename
        
        if file_path.exists() and file_path.is_file():
            return file_path
        return None
    
    def get_public_url(self, user_id: int, filename: str) -> str:
        """
        Получить публичный URL файла.
        
        Args:
            user_id: ID пользователя
            filename: Имя файла
        
        Returns:
            Публичный URL
        """
        return f"{self.external_url}/files/{user_id}/{filename}"
    
    def delete_file(self, user_id: int, filename: str) -> bool:
        """
        Удалить файл пользователя.
        
        Args:
            user_id: ID пользователя
            filename: Имя файла
        
        Returns:
            True если файл удален
        """
        file_path = self.get_file_path(user_id, filename)
        if file_path:
            try:
                file_path.unlink()
                logger.debug(f"Файл удален: user_id={user_id}, filename={filename}")
                return True
            except Exception as e:
                logger.error(f"Ошибка при удалении файла: {e}")
                return False
        return False
    
    def get_user_files(self, user_id: int) -> list[dict]:
        """
        Получить список файлов пользователя.
        
        Args:
            user_id: ID пользователя
        
        Returns:
            Список словарей с информацией о файлах
        """
        user_dir = self.get_user_directory(user_id)
        files = []
        
        if user_dir.exists():
            for file_path in user_dir.iterdir():
                if file_path.is_file():
                    files.append({
                        'filename': file_path.name,
                        'size': file_path.stat().st_size,
                        'url': self.get_public_url(user_id, file_path.name)
                    })
        
        return files
    
    def calculate_file_hash(self, file_path: str) -> str:
        """
        Вычислить SHA256 хеш файла.
        
        Args:
            file_path: Путь к файлу
        
        Returns:
            SHA256 хеш файла в виде hex строки
        """
        try:
            sha256_hash = hashlib.sha256()
            with open(file_path, "rb") as f:
                # Читаем файл блоками для экономии памяти
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Ошибка при вычислении хеша файла {file_path}: {e}")
            raise
    
    def calculate_file_hash_from_data(self, file_data: bytes) -> str:
        """
        Вычислить SHA256 хеш из данных файла.
        
        Args:
            file_data: Данные файла в виде bytes
        
        Returns:
            SHA256 хеш файла в виде hex строки
        """
        try:
            sha256_hash = hashlib.sha256()
            sha256_hash.update(file_data)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Ошибка при вычислении хеша из данных: {e}")
            raise
    
    def cleanup_old_files(self, user_id: int, days: int = 30) -> int:
        """
        Очистить старые файлы пользователя.
        
        Args:
            user_id: ID пользователя
            days: Удалить файлы старше N дней
        
        Returns:
            Количество удаленных файлов
        """
        import time
        user_dir = self.get_user_directory(user_id)
        deleted_count = 0
        current_time = time.time()
        cutoff_time = current_time - (days * 24 * 60 * 60)
        
        if user_dir.exists():
            for file_path in user_dir.iterdir():
                if file_path.is_file():
                    file_time = file_path.stat().st_mtime
                    if file_time < cutoff_time:
                        try:
                            file_path.unlink()
                            deleted_count += 1
                        except Exception as e:
                            logger.error(f"Ошибка при удалении старого файла: {e}")
        
        if deleted_count > 0:
            logger.debug(f"Удалено старых файлов пользователя {user_id}: {deleted_count}")
        
        return deleted_count


# Создаем глобальный экземпляр менеджера файлов
file_manager = FileManager()
