"""
File: api/deepseek_client.py
Purpose:
    Клиент для работы с DeepSeek API для генерации промптов.
    
Responsibilities:
    - Отправка запросов к DeepSeek API
    - Генерация промптов для HiggsField Seedream 4.5
    - Обработка ответов от API
"""
from openai import OpenAI
from openai import APIConnectionError
import time
import asyncio
from config.settings import settings
from utils.logger import logger


class DeepSeekClient:
    """Клиент для работы с DeepSeek API."""
    
    def __init__(self):
        """Инициализация клиента."""
        self.api_key = settings.DEEPSEEK_API_KEY
        if not self.api_key:
            raise ValueError("DEEPSEEK_API_KEY не установлен в config.json")
        
        # Логируем начало ключа для отладки (без полного ключа)
        logger.debug(f"DeepSeek API ключ загружен, длина: {len(self.api_key)}, начинается с: {self.api_key[:7]}...")
        
        # Инициализируем OpenAI клиент с base_url для DeepSeek
        # Увеличиваем таймауты для более стабильного соединения
        import httpx
        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com",
            timeout=httpx.Timeout(60.0, connect=10.0)  # 60 сек на запрос, 10 сек на подключение
        )
        self.model = "deepseek-chat"
    
    async def generate_prompts(self, user_description: str) -> str:
        """
        Сгенерировать промпты для HiggsField Seedream 4.5 на основе описания пользователя.
        
        Args:
            user_description: Описание желаемого результата от пользователя
        
        Returns:
            Сгенерированный промпт или ответ от API
        """
        system_prompt = (
            "Ты помощник для генерации промптов для AI-модели генерации изображений HiggsField Seedream 4.5. "
            "Пользователь описывает желаемый результат, а ты должен составить несколько разных промптов "
            "(по сюжету или стилю), адаптированных для HiggsField Seedream 4.5. "
            "Промпты должны быть детальными и эффективными для генерации изображений. "
            "Верни промпт в формате: ```prompt\n[твой промпт здесь]\n```"
        )
        
        user_prompt = (
            f"Пользователь хочет: {user_description}\n\n"
            f"Составь промпт для HiggsField Seedream 4.5 на основе этого описания."
        )
        
        # Retry логика для ошибок соединения
        max_retries = 3
        retry_delay = 2  # секунды
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Отправка запроса в DeepSeek API для генерации промпта (попытка {attempt + 1}/{max_retries}). Описание: {user_description[:100]}...")
                logger.debug(f"Модель: {self.model}")
                
                # Синхронный API вызов выполняем в executor'е, чтобы не блокировать event loop
                loop = asyncio.get_event_loop()
                # Используем functools.partial для корректной передачи параметров
                from functools import partial
                api_call = partial(
                    self.client.chat.completions.create,
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    temperature=0.7,
                    max_tokens=1000,
                    stream=False
                )
                response = await loop.run_in_executor(None, api_call)
                
                logger.info(f"Получен ответ от DeepSeek API, статус: OK")
                logger.debug(f"Ответ содержит {len(response.choices)} вариантов")
                
                # Извлекаем текст ответа
                if response.choices and len(response.choices) > 0:
                    content = response.choices[0].message.content
                    return content
                else:
                    raise ValueError("Неожиданный формат ответа от DeepSeek API")
                    
            except APIConnectionError as e:
                # Ошибка соединения - пробуем повторить
                error_str = str(e)
                logger.warning(f"Ошибка соединения с DeepSeek API (попытка {attempt + 1}/{max_retries}): {error_str}")
                
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (attempt + 1)  # Экспоненциальная задержка
                    logger.info(f"Повторная попытка через {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    # Последняя попытка не удалась
                    logger.error(f"Все попытки соединения с DeepSeek API исчерпаны")
                    raise Exception("Ошибка соединения с DeepSeek API. Сервер недоступен или соединение прервано. Попробуйте позже.")
                    
            except Exception as e:
                logger.error(f"Ошибка при обращении к DeepSeek API: {e}", exc_info=True)
                
                # Обрабатываем специфичные ошибки API (не retry)
                error_str = str(e)
                if "401" in error_str or "Authentication" in error_str or "invalid" in error_str.lower():
                    raise Exception("Неверный API ключ DeepSeek. Проверьте настройки.")
                elif "402" in error_str or "Insufficient Balance" in error_str:
                    raise Exception("Недостаточно средств на балансе DeepSeek API. Пополните баланс на platform.deepseek.com")
                elif "429" in error_str or "rate limit" in error_str.lower():
                    raise Exception("Превышен лимит запросов к DeepSeek API. Попробуйте позже.")
                else:
                    # Для других ошибок тоже пробуем retry, если это не последняя попытка
                    if attempt < max_retries - 1 and ("Connection" in error_str or "timeout" in error_str.lower() or "RemoteProtocolError" in error_str):
                        wait_time = retry_delay * (attempt + 1)
                        logger.info(f"Повторная попытка через {wait_time} секунд из-за ошибки соединения...")
                        await asyncio.sleep(wait_time)
                        continue
                    else:
                        raise Exception(f"Ошибка при обращении к DeepSeek API: {str(e)}")
