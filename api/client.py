"""
File: api/client.py
Purpose:
    Клиенты для интеграции с платформой Higgsfield AI через REST API.
    Поддерживает несколько режимов генерации: NanoBanana и Seedream 4.5.

Responsibilities:
    - Загрузка изображений в Higgsfield (через официальный клиент или fallback)
    - Отправка запросов на генерацию изображений (text-to-image и image-to-image)
    - Получение статуса запросов и ожидание завершения генерации
    - Унификация работы с различными endpoints платформы

Key Design Decisions:
    - Используется официальный higgsfield-client для загрузки файлов
    - Fallback на публичные URL локального сервера, если клиент недоступен
    - Polling механизм для ожидания завершения асинхронных задач
    - Раздельные клиенты для разных режимов генерации (NanoBanana, Seedream)
    - Единый интерфейс через get_api_client() для получения нужного клиента

Notes:
    - API является асинхронным: запрос возвращает task_id, статус проверяется отдельно
    - Различные endpoints требуют разных заголовков авторизации
    - NanoBanana использует разные endpoints для text2image и image2image
    - Seedream использует единый endpoint для обоих типов запросов
"""
import os
import json
import time
import asyncio
import httpx
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from config.settings import settings
from config.constants import (
    HIGGSFIELD_BASE_URL,
    ENDPOINT_REQUEST_STATUS,
    DEFAULT_API_GENERATION_TIMEOUT,
    POLL_INTERVAL_SECONDS
)
from utils.logger import logger
from database.db_manager import db_manager
from storage.file_manager import file_manager

try:
    import higgsfield_client
    HIGGSFIELD_CLIENT_AVAILABLE = True
except ImportError:
    HIGGSFIELD_CLIENT_AVAILABLE = False
    logger.warning("higgsfield_client не доступен. Установите пакет higgsfield-client.")


class SeedreamAPIClient:
    """Клиент для работы с Seedream 4.5 API через REST."""
    
    def __init__(self):
        """Инициализация API клиента."""
        if not settings.HIGGSFIELD_API_KEY:
            raise ValueError("HIGGSFIELD_API_KEY не установлен в config.json")
        
        self.api_key = settings.HIGGSFIELD_API_KEY
        self.api_key_secret = settings.HIGGSFIELD_API_KEY_SECRET
        self.base_url = "https://platform.higgsfield.ai"
        # Endpoint для всех запросов (text2image и image2image)
        self.seedream_url = f"{self.base_url}/seedream/v4.5"
        
        # Устанавливаем переменные окружения для higgsfield_client
        if HIGGSFIELD_CLIENT_AVAILABLE:
            os.environ['HF_API_KEY'] = self.api_key
            if self.api_key_secret:
                os.environ['HF_API_SECRET'] = self.api_key_secret
        
        logger.debug("SeedreamAPIClient инициализирован")
    
    def _get_headers(self) -> Dict[str, str]:
        """
        Получить заголовки для запросов.
        Для Seedream используется Content-Type и авторизация.
        """
        headers = {
            "Content-Type": "application/json",
            "hf-api-key": self.api_key
        }
        if self.api_key_secret:
            headers["hf-secret"] = self.api_key_secret
        return headers
    
    def upload_file(self, file_path: str) -> str:
        """
        Загрузить файл в Higgsfield и получить URL.
        Использует кэш для переиспользования ссылок на уже загруженные файлы.
        
        Args:
            file_path: Путь к файлу на локальном диске
        
        Returns:
            URL загруженного файла в Higgsfield
        """
        try:
            # Проверяем существование файла
            if not os.path.exists(file_path):
                logger.error(f"Файл не найден: {file_path}")
                # Fallback: используем публичный URL локального сервера (даже если файл не существует)
                return self._get_public_url(file_path)
            
            # Вычисляем хеш файла для проверки кэша
            file_hash = file_manager.calculate_file_hash(file_path)
            
            # Проверяем кэш
            cached_url = db_manager.get_file_cache(file_hash)
            if cached_url:
                logger.debug(f"Используется кэшированная ссылка для файла: {file_path[:50]}...")
                return cached_url
            
            # Если кэша нет, загружаем файл
            if not HIGGSFIELD_CLIENT_AVAILABLE:
                # Fallback: используем публичный URL локального сервера
                # НЕ сохраняем в кэш, так как это не реальная ссылка на Higgsfield
                logger.warning("higgsfield_client недоступен, используем публичный URL (не кэшируется)")
                return self._get_public_url(file_path)
            
            # Загружаем файл через официальный клиент
            logger.debug(f"Загрузка файла в Higgsfield: {file_path}")
            url = higgsfield_client.upload_file(file_path)
            logger.debug(f"Файл успешно загружен в Higgsfield, URL: {url}")
            
            # Сохраняем в кэш ТОЛЬКО реальные URL от Higgsfield (по умолчанию ссылка действительна 7 дней)
            expires_at = datetime.utcnow() + timedelta(days=settings.FILE_CACHE_TTL_DAYS)
            db_manager.save_file_cache(file_hash, url, expires_at)
            logger.debug(f"Ссылка сохранена в кэш: file_hash={file_hash[:16]}..., expires_at={expires_at}")
            
            return url
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла в Higgsfield: {e}")
            # Fallback: используем публичный URL локального сервера
            # НЕ сохраняем в кэш, так как это не реальная ссылка на Higgsfield
            logger.warning("Используем публичный URL локального сервера как fallback (не кэшируется)")
            return self._get_public_url(file_path)
    
    def _get_public_url(self, file_path: str) -> str:
        """
        Получить публичный URL локального сервера для файла.
        
        Args:
            file_path: Путь к файлу на локальном диске
        
        Returns:
            Публичный URL файла
        """
        try:
            # Проверяем, что путь указывает на файл, а не на директорию
            from pathlib import Path
            path_obj = Path(file_path)
            if not path_obj.exists():
                logger.warning(f"Файл не существует: {file_path}")
                return file_path
            if path_obj.is_dir():
                logger.error(f"Передан путь к директории вместо файла: {file_path}")
                return file_path
            
            # Получаем user_id и filename из пути
            # Формат: storage/users/{user_id}/{filename} или storage/users/{user_id}/last_uploads/{filename}
            # или storage/users/{user_id}/sets/{set_id}/{filename}
            parts = file_path.replace('\\', '/').split('/')
            if 'users' in parts:
                user_idx = parts.index('users')
                if user_idx + 1 < len(parts):
                    user_id = parts[user_idx + 1]
                    
                    # Проверяем, находится ли файл в поддиректории
                    if user_idx + 4 < len(parts):
                        # Файл в поддиректории с двумя уровнями: storage/users/{user_id}/sets/{set_id}/{filename}
                        subdir1 = parts[user_idx + 2]  # sets
                        subdir2 = parts[user_idx + 3]  # set_id
                        filename = parts[user_idx + 4]
                        if not filename:
                            logger.error(f"Не удалось извлечь filename из пути: {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{subdir1}/{subdir2}/{filename}"
                    elif user_idx + 3 < len(parts):
                        # Файл в поддиректории: storage/users/{user_id}/last_uploads/{filename}
                        subdir = parts[user_idx + 2]  # last_uploads, results, или used
                        filename = parts[user_idx + 3]
                        # Проверяем, что filename не пустой
                        if not filename:
                            logger.error(f"Не удалось извлечь filename из пути: {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{subdir}/{filename}"
                    elif user_idx + 2 < len(parts):
                        # Файл в корне пользователя: storage/users/{user_id}/{filename}
                        filename = parts[user_idx + 2]
                        # Проверяем, что filename не пустой и не является директорией
                        if not filename or filename in ['last_uploads', 'results', 'used', 'sets']:
                            logger.error(f"Неправильный filename из пути (возможно директория): {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{filename}"
                    else:
                        # Не удалось извлечь - используем прямой путь
                        logger.warning(f"Не удалось извлечь filename из пути: {file_path}")
                        return file_path
                    
                    logger.debug(f"Публичный URL файла: {public_url}")
                    return public_url
            
            # Если не удалось извлечь, используем прямой путь
            logger.warning(f"Не удалось извлечь user_id из пути: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Ошибка при получении публичного URL файла: {e}")
            raise
    
    async def generate(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        image_paths: Optional[list] = None,
        resolution: str = "2k",
        aspect_ratio: str = "4:3",
        reference_influence: float = 1.0,
        style_strength: float = 0.3,
        creativity: float = 0.0
    ) -> Dict[str, Any]:
        """
        Генерация изображения через Seedream 4.5 API.
        
        Args:
            prompt: Текстовый промпт
            image_path: Путь к файлу изображения на локальном диске (опционально, для обратной совместимости)
            image_paths: Список путей к файлам изображений (опционально, приоритет над image_path)
            resolution: Разрешение (всегда "2k" для Seedream)
            aspect_ratio: Соотношение сторон (например, "4:3", "16:9")
            reference_influence: Влияние референса (не используется в Seedream)
            style_strength: Сила стиля (не используется в Seedream)
            creativity: Креативность (не используется в Seedream)
        
        Returns:
            Словарь с результатом генерации
        """
        try:
            # Определяем список изображений для отправки
            images_to_upload = []
            if image_paths:
                images_to_upload = image_paths
            elif image_path:
                images_to_upload = [image_path]
            
            # Проверяем лимит изображений (максимум 14 для Seedream)
            if len(images_to_upload) > 14:
                raise ValueError(f"Максимальное количество изображений для Seedream: 14, получено: {len(images_to_upload)}")
            
            # Загружаем изображения и получаем их URL
            image_urls = []
            for img_path in images_to_upload:
                image_url = self.upload_file(img_path)
                image_urls.append(image_url)
                logger.debug(f"Загружено изображение: {img_path} -> {image_url[:50]}...")
            
            # Формируем payload
            # Для Seedream: один endpoint для text-to-image и image-to-image
            # resolution всегда "2k"
            # num_images не отправляется
            payload = {
                "prompt": prompt,
                "image_urls": image_urls,  # Пустой список для text-to-image
                "resolution": "2k",  # Всегда 2k
                "aspect_ratio": aspect_ratio
            }
            
            logger.debug(f"Отправка запроса в Seedream 4.5 API: prompt={prompt[:50]}..., image_urls={len(image_urls)}, aspect_ratio={aspect_ratio}")
            
            # Логируем полный запрос для отладки
            logger.debug(f"URL запроса: {self.seedream_url}")
            
            # Логируем payload (без длинных URL изображений)
            safe_payload = payload.copy()
            safe_payload["image_urls"] = [url[:50] + "..." if len(url) > 50 else url for url in image_urls]
            logger.debug(f"Payload запроса: {json.dumps(safe_payload, ensure_ascii=False, indent=2)}")
            
            # Логируем полный payload с полными URL для детальной отладки
            logger.debug(f"Полный payload запроса: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            
            # Заголовки с авторизацией для Seedream
            headers = self._get_headers()
            # Логируем заголовки (без секретных данных)
            safe_headers = {k: '***' if k in ['hf-api-key', 'hf-secret'] else v for k, v in headers.items()}
            logger.debug(f"Заголовки запроса: {safe_headers}")
            
            # Отправляем POST запрос (асинхронный через httpx)
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    self.seedream_url,
                    json=payload,
                    headers=headers
                )
                
                # Проверяем статус ответа
                response.raise_for_status()
                
                # Парсим JSON ответ
                result = response.json()
            
            # Логируем полный ответ API
            logger.debug(f"Полный ответ API: {json.dumps(result, ensure_ascii=False, indent=2)}")
            logger.debug(f"Структура ответа: тип={type(result)}, является dict={isinstance(result, dict)}")
            if isinstance(result, dict):
                logger.debug(f"Ключи в ответе: {list(result.keys())}")
                if 'jobs' in result:
                    logger.debug(f"Jobs в ответе: {json.dumps(result.get('jobs'), ensure_ascii=False, indent=2)}")
                if 'id' in result:
                    logger.debug(f"ID задачи/результата: {result.get('id')}")
            
            return result
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка HTTP при запросе к Seedream API: {e}")
            logger.error(f"Статус ответа: {e.response.status_code}")
            logger.error(f"Тело ответа: {e.response.text}")
            raise Exception(f"Ошибка при запросе к Seedream API: {str(e)}")
        except httpx.RequestError as e:
            logger.error(f"Ошибка запроса к Seedream API: {e}")
            raise Exception(f"Ошибка при запросе к Seedream API: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка при генерации через Seedream API: {e}")
            raise
    
    async def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """
        Получить статус запроса по ID (асинхронная версия).
        
        Args:
            request_id: ID запроса
        
        Returns:
            Словарь со статусом запроса
        """
        try:
            status_url = f"{self.base_url}/requests/{request_id}/status"
            
            logger.debug(f"Проверка статуса запроса: {request_id}")
            logger.debug(f"URL для проверки статуса: {status_url}")
            
            headers = self._get_headers()
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    status_url,
                    headers=headers
                )
                
                response.raise_for_status()
                result = response.json()
            
            status = result.get('status', 'unknown')
            logger.info(f"По запросу: {request_id} статус: {status}")
            
            return result
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Ошибка HTTP при проверке статуса: {e}")
            logger.error(f"Статус ответа: {e.response.status_code}")
            logger.error(f"Тело ответа: {e.response.text}")
            raise Exception(f"Ошибка при проверке статуса: {str(e)}")
        except httpx.RequestError as e:
            logger.error(f"Ошибка запроса при проверке статуса: {e}")
            raise Exception(f"Ошибка при проверке статуса: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка при проверке статуса запроса: {e}")
            raise
    
    async def wait_for_completion(self, request_id: str, max_wait_time: int = None, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Ожидать завершения задачи с периодическим опросом статуса (асинхронная версия).
        
        Args:
            request_id: ID запроса
            max_wait_time: Максимальное время ожидания в секундах
            poll_interval: Интервал опроса в секундах
        
        Returns:
            Словарь с результатом генерации
        """
        import asyncio
        
        if max_wait_time is None:
            max_wait_time = settings.API_GENERATION_TIMEOUT
        
        start_time = time.time()
        
        logger.debug(f"Начало ожидания завершения задачи {request_id} (макс. {max_wait_time} сек, интервал {poll_interval} сек)")
        
        while True:
            try:
                # Асинхронный HTTP запрос через httpx
                status_result = await self.get_request_status(request_id)
                
                status = status_result.get('status', '').lower()
                if not status and 'jobs' in status_result:
                    jobs = status_result.get('jobs', [])
                    if jobs and isinstance(jobs[0], dict):
                        status = jobs[0].get('status', '').lower()
                
                logger.debug(f"Задача {request_id} - текущий статус: '{status}'")
                
                if status == 'completed':
                    logger.info(f"✅ Задача {request_id} завершена")
                    logger.debug(f"Полный результат: {json.dumps(status_result, ensure_ascii=False, indent=2)}")
                    
                    # Проверяем наличие images в корне (основной вариант)
                    if 'images' in status_result and len(status_result['images']) > 0:
                        logger.debug(f"✅ Найдены images напрямую в корне: {len(status_result['images'])} шт.")
                    # Проверяем наличие results в jobs[0] (альтернативный вариант)
                    elif 'jobs' in status_result and len(status_result['jobs']) > 0:
                        job = status_result['jobs'][0]
                        if 'results' in job and len(job['results']) > 0:
                            logger.debug(f"✅ Найдены results в jobs[0]: {len(job['results'])} шт.")
                    
                    return status_result
                
                elif status == 'nsfw':
                    logger.warning(f"⚠️ Задача {request_id} заблокирована цензурой (NSFW)")
                    raise ValueError("nsfw: Часть контента была заблокирована по соображениям цензуры")
                
                elif status == 'failed':
                    error_msg = status_result.get('error', 'Неизвестная ошибка')
                    logger.error(f"❌ Задача {request_id} завершилась с ошибкой: {error_msg}")
                    raise RuntimeError("failed: Ошибка сервера. Попробуйте повторить запрос позднее")
                
                elif status == 'canceled' or status == 'cancelled':
                    logger.info(f"ℹ️ Запрос {request_id} был отменен")
                    raise ValueError("canceled: Запрос был успешно отменён")
                
                # Проверяем таймаут
                elapsed_time = time.time() - start_time
                if elapsed_time >= max_wait_time:
                    logger.error(f"⏱️ Превышено время ожидания для задачи {request_id}: {elapsed_time:.2f} сек")
                    raise TimeoutError(f"Превышено время ожидания генерации: {max_wait_time} сек")
                
                # Используем asyncio.sleep вместо time.sleep для неблокирующего ожидания
                await asyncio.sleep(poll_interval)
                
            except (TimeoutError, RuntimeError, ValueError):
                # Пробрасываем критические ошибки (таймаут, failed, nsfw, canceled)
                raise
            except Exception as e:
                logger.error(f"Ошибка при опросе статуса задачи {request_id}: {e}")
                # Продолжаем опрос только для некритических ошибок (например, временные сетевые проблемы)
                await asyncio.sleep(poll_interval)


class NanoBananaAPIClient:
    """Клиент для работы с Nano Banana API через REST."""
    
    def __init__(self):
        """Инициализация API клиента."""
        if not settings.HIGGSFIELD_API_KEY:
            raise ValueError("HIGGSFIELD_API_KEY не установлен в config.json")
        
        self.api_key = settings.HIGGSFIELD_API_KEY
        self.api_key_secret = settings.HIGGSFIELD_API_KEY_SECRET
        self.base_url = "https://platform.higgsfield.ai"
        # Endpoint для запросов только с промптом (text2image)
        self.nanobanana_text2image_url = f"{self.base_url}/v1/text2image/nano-banana"
        # Endpoint для запросов с изображением (image2image)
        self.nanobanana_image2image_url = f"{self.base_url}/nano-banana"
        
        # Устанавливаем переменные окружения для higgsfield_client
        if HIGGSFIELD_CLIENT_AVAILABLE:
            os.environ['HF_API_KEY'] = self.api_key
            if self.api_key_secret:
                os.environ['HF_API_SECRET'] = self.api_key_secret
        
        logger.debug("NanoBananaAPIClient инициализирован")
    
    def _get_headers(self, with_auth: bool = True) -> Dict[str, str]:
        """
        Получить заголовки для запросов.
        
        Args:
            with_auth: Если True, добавляет заголовки авторизации (для text2image)
                      Если False, только Content-Type (для image2image)
        """
        headers = {
            "Content-Type": "application/json"
        }
        
        # Добавляем авторизацию только для text2image запросов
        if with_auth:
            headers["hf-api-key"] = self.api_key
            if self.api_key_secret:
                headers["hf-secret"] = self.api_key_secret
        
        return headers
    
    def upload_file(self, file_path: str) -> str:
        """
        Загрузить файл в Higgsfield и получить URL.
        Использует кэш для переиспользования ссылок на уже загруженные файлы.
        
        Args:
            file_path: Путь к файлу на локальном диске
        
        Returns:
            URL загруженного файла в Higgsfield
        """
        try:
            # Проверяем существование файла
            if not os.path.exists(file_path):
                logger.error(f"Файл не найден: {file_path}")
                # Fallback: используем публичный URL локального сервера (даже если файл не существует)
                return self._get_public_url(file_path)
            
            # Вычисляем хеш файла для проверки кэша
            file_hash = file_manager.calculate_file_hash(file_path)
            
            # Проверяем кэш
            cached_url = db_manager.get_file_cache(file_hash)
            if cached_url:
                logger.debug(f"Используется кэшированная ссылка для файла: {file_path[:50]}...")
                return cached_url
            
            # Если кэша нет, загружаем файл
            if not HIGGSFIELD_CLIENT_AVAILABLE:
                # Fallback: используем публичный URL локального сервера
                # НЕ сохраняем в кэш, так как это не реальная ссылка на Higgsfield
                logger.warning("higgsfield_client недоступен, используем публичный URL (не кэшируется)")
                return self._get_public_url(file_path)
            
            # Загружаем файл через официальный клиент
            logger.debug(f"Загрузка файла в Higgsfield: {file_path}")
            url = higgsfield_client.upload_file(file_path)
            logger.debug(f"Файл успешно загружен в Higgsfield, URL: {url}")
            
            # Сохраняем в кэш ТОЛЬКО реальные URL от Higgsfield (по умолчанию ссылка действительна 7 дней)
            expires_at = datetime.utcnow() + timedelta(days=settings.FILE_CACHE_TTL_DAYS)
            db_manager.save_file_cache(file_hash, url, expires_at)
            logger.debug(f"Ссылка сохранена в кэш: file_hash={file_hash[:16]}..., expires_at={expires_at}")
            
            return url
        except Exception as e:
            logger.error(f"Ошибка при загрузке файла в Higgsfield: {e}")
            # Fallback: используем публичный URL локального сервера
            # НЕ сохраняем в кэш, так как это не реальная ссылка на Higgsfield
            logger.warning("Используем публичный URL локального сервера как fallback (не кэшируется)")
            return self._get_public_url(file_path)
    
    def _get_public_url(self, file_path: str) -> str:
        """
        Получить публичный URL локального сервера для файла.
        
        Args:
            file_path: Путь к файлу на локальном диске
        
        Returns:
            Публичный URL файла
        """
        try:
            # Проверяем, что путь указывает на файл, а не на директорию
            from pathlib import Path
            path_obj = Path(file_path)
            if not path_obj.exists():
                logger.warning(f"Файл не существует: {file_path}")
                return file_path
            if path_obj.is_dir():
                logger.error(f"Передан путь к директории вместо файла: {file_path}")
                return file_path
            
            # Получаем user_id и filename из пути
            # Формат: storage/users/{user_id}/{filename} или storage/users/{user_id}/last_uploads/{filename}
            # или storage/users/{user_id}/sets/{set_id}/{filename}
            parts = file_path.replace('\\', '/').split('/')
            if 'users' in parts:
                user_idx = parts.index('users')
                if user_idx + 1 < len(parts):
                    user_id = parts[user_idx + 1]
                    
                    # Проверяем, находится ли файл в поддиректории
                    if user_idx + 4 < len(parts):
                        # Файл в поддиректории с двумя уровнями: storage/users/{user_id}/sets/{set_id}/{filename}
                        subdir1 = parts[user_idx + 2]  # sets
                        subdir2 = parts[user_idx + 3]  # set_id
                        filename = parts[user_idx + 4]
                        if not filename:
                            logger.error(f"Не удалось извлечь filename из пути: {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{subdir1}/{subdir2}/{filename}"
                    elif user_idx + 3 < len(parts):
                        # Файл в поддиректории: storage/users/{user_id}/last_uploads/{filename}
                        subdir = parts[user_idx + 2]  # last_uploads, results, или used
                        filename = parts[user_idx + 3]
                        # Проверяем, что filename не пустой
                        if not filename:
                            logger.error(f"Не удалось извлечь filename из пути: {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{subdir}/{filename}"
                    elif user_idx + 2 < len(parts):
                        # Файл в корне пользователя: storage/users/{user_id}/{filename}
                        filename = parts[user_idx + 2]
                        # Проверяем, что filename не пустой и не является директорией
                        if not filename or filename in ['last_uploads', 'results', 'used', 'sets']:
                            logger.error(f"Неправильный filename из пути (возможно директория): {file_path}")
                            return file_path
                        public_url = f"http://localhost:5000/files/{user_id}/{filename}"
                    else:
                        # Не удалось извлечь - используем прямой путь
                        logger.warning(f"Не удалось извлечь filename из пути: {file_path}")
                        return file_path
                    
                    logger.debug(f"Публичный URL файла: {public_url}")
                    return public_url
            
            # Если не удалось извлечь, используем прямой путь
            logger.warning(f"Не удалось извлечь user_id из пути: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"Ошибка при получении публичного URL файла: {e}")
            raise
    
    async def generate(
        self,
        prompt: str,
        image_path: Optional[str] = None,
        image_paths: Optional[list] = None,
        resolution: str = "2k",
        aspect_ratio: str = "4:3",
        reference_influence: float = 1.0,
        style_strength: float = 0.3,
        creativity: float = 0.0
    ) -> Dict[str, Any]:
        """
        Генерация изображения через Nano Banana API.
        
        Args:
            prompt: Текстовый промпт
            image_path: Путь к файлу изображения на локальном диске (опционально, для обратной совместимости)
            image_paths: Список путей к файлам изображений (опционально, приоритет над image_path)
            resolution: Разрешение (например, "2k", "4k")
            aspect_ratio: Соотношение сторон (например, "4:3", "16:9")
            reference_influence: Влияние референса (0.0-1.0)
            style_strength: Сила стиля (0.0-1.0)
            creativity: Креативность (0.0-1.0)
        
        Returns:
            Словарь с результатом генерации
        """
        try:
            # Определяем список изображений для отправки
            images_to_upload = []
            if image_paths:
                images_to_upload = image_paths
            elif image_path:
                images_to_upload = [image_path]
            
            # Определяем, какой endpoint и формат использовать
            if images_to_upload:
                # Запрос с изображением(ями) - используем image2image endpoint
                # Загружаем все изображения и получаем их URL
                input_images = []
                for img_path in images_to_upload:
                    image_url = self.upload_file(img_path)
                    input_images.append({
                        "type": "image_url",
                        "image_url": image_url
                    })
                    logger.debug(f"Загружено изображение: {img_path} -> {image_url[:50]}...")
                
                # Формируем payload напрямую (без обертки params)
                # Для запросов с фото не добавляем resolution, reference_influence, style_strength, creativity
                payload = {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "num_images": 1,
                    "input_images": input_images,
                    "output_format": "png"
                }
                
                logger.debug(f"Отправка запроса с {len(input_images)} изображением(ями)")
                
                # Используем image2image endpoint и заголовки с авторизацией
                url = self.nanobanana_image2image_url
                headers = self._get_headers(with_auth=True)
                
                logger.debug(f"Отправка запроса в Nano Banana API (image2image) с изображением: prompt={prompt[:50]}..., aspect_ratio={aspect_ratio}")
            else:
                # Запрос только с промптом - используем text2image endpoint
                # Формируем параметры запроса
                params = {
                    "prompt": prompt,
                    "aspect_ratio": aspect_ratio,
                    "num_images": 1,
                    "input_images": [],
                    "output_format": "png"
                }
                
                # Добавляем опциональные параметры
                if resolution:
                    params["resolution"] = resolution
                
                # Обертываем параметры в объект params
                payload = {
                    "params": params
                }
                
                # Используем text2image endpoint и заголовки с авторизацией
                url = self.nanobanana_text2image_url
                headers = self._get_headers(with_auth=True)
                
                logger.debug(f"Отправка запроса в Nano Banana API (text2image) только с промптом: prompt={prompt[:50]}..., resolution={resolution}, aspect_ratio={aspect_ratio}")
            
            # Логируем полный запрос для отладки
            logger.debug(f"URL запроса: {url}")
            
            # Логируем payload (без длинных URL изображений, если они есть)
            safe_payload = payload.copy()
            # Обрабатываем разные форматы payload
            if "params" in safe_payload and "input_images" in safe_payload["params"]:
                # Формат для text2image
                safe_payload["params"]["input_images"] = [
                    {
                        "type": img.get("type", "unknown"),
                        "image_url": f"{img.get('image_url', '')[:50]}..." if img.get("image_url") else None
                    }
                    for img in safe_payload["params"]["input_images"]
                ]
            elif "input_images" in safe_payload:
                # Формат для image2image
                safe_payload["input_images"] = [
                    {
                        "type": img.get("type", "unknown"),
                        "image_url": f"{img.get('image_url', '')[:50]}..." if img.get("image_url") else None
                    }
                    for img in safe_payload["input_images"]
                ]
            logger.debug(f"Payload запроса: {json.dumps(safe_payload, ensure_ascii=False, indent=2)}")
            
            # Логируем полный payload с полными URL для детальной отладки (если нужно)
            logger.debug(f"Полный payload запроса: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            
            # Логируем заголовки (без секретных данных)
            safe_headers = {k: '***' if k in ['hf-api-key', 'hf-secret'] else v for k, v in headers.items()}
            logger.debug(f"Заголовки запроса: {safe_headers}")
            
            # Отправляем POST запрос (асинхронный через httpx)
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers
                )
                
                # Проверяем статус ответа
                response.raise_for_status()
                
                # Парсим JSON ответ
                result = response.json()
            
            # Логируем полный ответ API
            logger.debug(f"Полный ответ API: {json.dumps(result, ensure_ascii=False, indent=2)}")
            logger.debug(f"Структура ответа: тип={type(result)}, является dict={isinstance(result, dict)}")
            if isinstance(result, dict):
                logger.debug(f"Ключи в ответе: {list(result.keys())}")
                if 'jobs' in result:
                    logger.debug(f"Jobs в ответе: {json.dumps(result.get('jobs'), ensure_ascii=False, indent=2)}")
                if 'id' in result:
                    logger.debug(f"ID задачи/результата: {result.get('id')}")
            
            return result
            
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP ошибка: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            raise Exception(f"Ошибка API: {error_msg}")
        except httpx.RequestError as e:
            error_msg = f"Ошибка запроса: {str(e)}"
            logger.error(error_msg)
            raise Exception(f"Ошибка запроса: {error_msg}")
        except Exception as e:
            logger.error(f"Ошибка при обработке запроса: {e}")
            raise
    
    async def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """
        Получить статус запроса по ID (асинхронная версия).
        
        Args:
            request_id: ID запроса/задачи
        
        Returns:
            Словарь со статусом запроса
        """
        try:
            # Используем правильный endpoint для получения статуса
            status_url = f"{self.base_url}/requests/{request_id}/status"
            
            logger.debug(f"Проверка статуса запроса: {request_id}")
            logger.debug(f"URL для проверки статуса: {status_url}")
            
            # Для проверки статуса используем заголовки с авторизацией
            headers = self._get_headers(with_auth=True)
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    status_url,
                    headers=headers
                )
                
                response.raise_for_status()
                result = response.json()
            
            # Определяем статус (может быть в корне или в jobs[0])
            status = result.get('status', 'unknown')
            if 'jobs' in result and len(result['jobs']) > 0:
                job_status = result['jobs'][0].get('status', 'unknown')
                if job_status:
                    status = job_status
            
            logger.debug(f"По запросу: {request_id} статус: {status}")
            
            return result
            
        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP ошибка при проверке статуса: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            raise Exception(f"Ошибка API: {error_msg}")
        except httpx.RequestError as e:
            error_msg = f"Ошибка запроса статуса: {str(e)}"
            logger.error(error_msg)
            raise Exception(f"Ошибка запроса: {error_msg}")
        except Exception as e:
            logger.error(f"Ошибка при проверке статуса: {e}")
            raise
    
    async def wait_for_completion(self, request_id: str, max_wait_time: int = None, poll_interval: int = 5) -> Dict[str, Any]:
        """
        Ожидать завершения задачи с периодическим опросом статуса (асинхронная версия).
        
        Args:
            request_id: ID запроса/задачи
            max_wait_time: Максимальное время ожидания в секундах (по умолчанию из конфига)
            poll_interval: Интервал опроса в секундах (по умолчанию 5 секунд)
        
        Returns:
            Финальный результат задачи
        """
        import time
        import asyncio
        
        # Используем значение из конфига, если не указано явно
        if max_wait_time is None:
            max_wait_time = settings.API_GENERATION_TIMEOUT
        
        start_time = time.time()
        
        logger.debug(f"Начало ожидания завершения задачи {request_id} (макс. {max_wait_time} сек, интервал {poll_interval} сек)")
        
        while True:
            try:
                # Асинхронный HTTP запрос через httpx
                status_result = await self.get_request_status(request_id)
                
                # Проверяем статус из ответа
                # Структура ответа: {"status": "queued", "request_id": "...", ...}
                # Или может быть с jobs: {"status": "...", "jobs": [...]}
                status = status_result.get('status', '').lower()
                
                # Если есть jobs, проверяем статус из jobs[0] (приоритет)
                if 'jobs' in status_result and len(status_result.get('jobs', [])) > 0:
                    job = status_result['jobs'][0]
                    job_status = job.get('status', '').lower()
                    if job_status:
                        status = job_status
                        logger.debug(f"Статус из jobs[0]: {status}")
                
                logger.debug(f"Задача {request_id} - текущий статус: '{status}'")
                
                if status == 'completed':
                    logger.info(f"✅ Задача {request_id} завершена")
                    logger.debug(f"Полный результат: {json.dumps(status_result, ensure_ascii=False, indent=2)}")
                    
                    # Проверяем наличие изображений в разных местах структуры ответа
                    # Вариант 1: images напрямую в корне (как в реальном ответе)
                    if 'images' in status_result and len(status_result['images']) > 0:
                        image_url = status_result['images'][0].get('url')
                        if image_url:
                            logger.debug(f"✅ URL изображения найден в images[0].url: {image_url}")
                        else:
                            logger.warning(f"В images[0] нет ключа 'url'. Ключи: {list(status_result['images'][0].keys())}")
                    
                    # Вариант 2: results в jobs[0] (альтернативная структура)
                    elif 'jobs' in status_result and len(status_result['jobs']) > 0:
                        job = status_result['jobs'][0]
                        if 'results' in job:
                            results = job['results']
                            logger.debug(f"Найдено результатов в jobs[0].results: {len(results)} шт.")
                            if len(results) > 0:
                                result_item = results[0]
                                if 'url' in result_item:
                                    logger.debug(f"✅ URL изображения найден в jobs[0].results[0].url: {result_item['url']}")
                                else:
                                    logger.warning(f"В results[0] нет ключа 'url'. Ключи: {list(result_item.keys())}")
                            else:
                                logger.warning(f"Массив results пуст")
                        else:
                            logger.warning(f"В job нет ключа 'results'. Ключи job: {list(job.keys())}")
                    else:
                        logger.warning(f"Не найдено изображений в результате. Ключи результата: {list(status_result.keys())}")
                    
                    return status_result
                    
                elif status == 'nsfw':
                    logger.warning(f"⚠️ Задача {request_id} заблокирована цензурой (NSFW)")
                    raise ValueError("nsfw: Часть контента была заблокирована по соображениям цензуры")
                
                elif status == 'failed':
                    error_msg = status_result.get('error', 'Неизвестная ошибка')
                    logger.error(f"❌ Задача {request_id} завершилась с ошибкой: {error_msg}")
                    raise RuntimeError("failed: Ошибка сервера. Попробуйте повторить запрос позднее")
                
                elif status == 'canceled' or status == 'cancelled':
                    logger.info(f"ℹ️ Запрос {request_id} был отменен")
                    raise ValueError("canceled: Запрос был успешно отменён")
                    
                elif status == 'error':
                    error_msg = status_result.get('error', 'Неизвестная ошибка')
                    logger.error(f"❌ Задача {request_id} завершилась с ошибкой: {error_msg}")
                    raise Exception(f"Ошибка генерации: {error_msg}")
                
                # Проверяем таймаут
                elapsed_time = time.time() - start_time
                if elapsed_time >= max_wait_time:
                    raise TimeoutError(f"Превышено время ожидания ({max_wait_time} секунд) для задачи {request_id}")
                
                # Используем asyncio.sleep вместо time.sleep для неблокирующего ожидания
                logger.debug(f"Задача {request_id} в статусе '{status}', ждем {poll_interval} секунд... (прошло {elapsed_time:.1f} сек)")
                await asyncio.sleep(poll_interval)
                
            except (TimeoutError, RuntimeError, ValueError):
                # Пробрасываем критические ошибки (таймаут, failed, nsfw, canceled)
                raise
            except Exception as e:
                # Если ошибка при проверке статуса, логируем и продолжаем
                logger.warning(f"Ошибка при опросе статуса задачи {request_id}: {e}. Повторная попытка через {poll_interval} сек...")
                
                # Проверяем таймаут даже при ошибке
                elapsed_time = time.time() - start_time
                if elapsed_time >= max_wait_time:
                    raise TimeoutError(f"Превышено время ожидания ({max_wait_time} секунд) для задачи {request_id}")
                
                await asyncio.sleep(poll_interval)  # Ждем перед повторной попыткой
                # Продолжаем цикл, чтобы повторить запрос


# Создаем глобальные экземпляры API клиентов
nanobanana_client = None
seedream_client = None

def get_nanobanana_client():
    """Получить экземпляр NanoBanana клиента."""
    global nanobanana_client
    if nanobanana_client is None:
        try:
            nanobanana_client = NanoBananaAPIClient()
        except ValueError as e:
            logger.error(f"Не удалось инициализировать NanoBananaAPIClient: {e}")
            raise
    return nanobanana_client

def get_seedream_client():
    """Получить экземпляр Seedream 4.5 клиента."""
    global seedream_client
    if seedream_client is None:
        try:
            seedream_client = SeedreamAPIClient()
        except ValueError as e:
            logger.error(f"Не удалось инициализировать SeedreamAPIClient: {e}")
            raise
    return seedream_client

def get_api_client(route: str):
    """Получить API клиент по маршруту."""
    if route == "nanobanana":
        try:
            return get_nanobanana_client()
        except Exception as e:
            logger.error(f"Ошибка при получении NanoBanana клиента: {e}")
            raise Exception(f"Сервис NanoBanana недоступен: {str(e)}")
    elif route == "seedream":
        try:
            return get_seedream_client()
        except Exception as e:
            logger.error(f"Ошибка при получении Seedream клиента: {e}")
            raise Exception(f"Сервис Seedream недоступен: {str(e)}")
    else:
        raise ValueError(f"Неизвестный маршрут: {route}. Доступные маршруты: 'nanobanana', 'seedream'")
