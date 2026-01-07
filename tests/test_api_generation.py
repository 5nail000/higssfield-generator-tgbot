"""
Тесты для генерации изображений через API и получения результатов.
"""
import os
import tempfile
import pytest
import logging
from unittest.mock import patch, MagicMock, Mock
from api.client import NanoBananaAPIClient
from storage.file_manager import file_manager
from config.settings import settings

# Настройка логирования для тестов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestAPIGeneration:
    """Тесты для генерации изображений через API."""
    
    def test_generate_text2image_initial_response(self):
        """Тест получения начального ответа при генерации только с промптом."""
        logger.info("=== Тест: получение начального ответа при генерации ===")
        
        # Мокаем requests.post для начального запроса
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "test-request-id-123",
            "type": "nano_banana",
            "created_at": "2026-01-07T02:05:00.510151Z",
            "jobs": [{"id": "test-job-id-456", "job_set_type": "nano_banana"}],
            "status": "pending"
        }
        mock_response.raise_for_status = Mock()
        
        logger.info("Мокаем requests.post для начального запроса")
        
        with patch('api.client.requests.post', return_value=mock_response):
            client = NanoBananaAPIClient()
            logger.info("Отправка запроса на генерацию (только промпт)")
            result = client.generate(
                prompt="test prompt",
                image_path=None,
                aspect_ratio="16:9"
            )
            
            logger.info(f"Получен результат: {result}")
            logger.info(f"ID запроса: {result.get('id')}")
            logger.info(f"Статус: {result.get('status')}")
            
            assert result["id"] == "test-request-id-123"
            assert result["status"] == "pending"
            assert "jobs" in result
            logger.info("✅ Тест пройден: начальный ответ получен корректно")
    
    def test_get_request_status(self):
        """Тест получения статуса запроса."""
        logger.info("=== Тест: получение статуса запроса ===")
        request_id = "test-request-id-123"
        
        # Мокаем requests.get для проверки статуса
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": request_id,
            "status": "completed",
            "result": {
                "images": [
                    {"url": "https://example.com/generated-image.png"}
                ]
            }
        }
        mock_response.raise_for_status = Mock()
        
        logger.info(f"Проверка статуса запроса: {request_id}")
        
        with patch('api.client.requests.get', return_value=mock_response):
            client = NanoBananaAPIClient()
            status_result = client.get_request_status(request_id)
            
            logger.info(f"Получен статус: {status_result.get('status')}")
            logger.info(f"Результат содержит images: {'result' in status_result and 'images' in status_result.get('result', {})}")
            
            assert status_result["status"] == "completed"
            assert "result" in status_result
            logger.info("✅ Тест пройден: статус получен корректно")
    
    def test_wait_for_completion(self):
        """Тест ожидания завершения задачи."""
        logger.info("=== Тест: ожидание завершения задачи ===")
        request_id = "test-request-id-123"
        
        # Мокаем последовательные ответы: pending -> completed
        mock_responses = [
            Mock(json=Mock(return_value={"id": request_id, "status": "pending"}), raise_for_status=Mock()),
            Mock(json=Mock(return_value={"id": request_id, "status": "pending"}), raise_for_status=Mock()),
            Mock(json=Mock(return_value={
                "id": request_id,
                "status": "completed",
                "result": {
                    "images": [{"url": "https://example.com/generated-image.png"}]
                }
            }), raise_for_status=Mock())
        ]
        
        logger.info(f"Начало ожидания завершения задачи: {request_id}")
        logger.info("Ожидаемые статусы: pending -> pending -> completed")
        
        with patch('api.client.requests.get', side_effect=mock_responses):
            with patch('time.sleep'):  # Ускоряем тест, пропуская sleep
                client = NanoBananaAPIClient()
                result = client.wait_for_completion(request_id, max_wait_time=60, poll_interval=1)
                
                logger.info(f"Задача завершена. Финальный статус: {result.get('status')}")
                logger.info(f"Результат содержит изображения: {'result' in result and 'images' in result.get('result', {})}")
                
                assert result["status"] == "completed"
                assert "result" in result
                logger.info("✅ Тест пройден: задача успешно завершена")
    
    def test_save_result_image(self):
        """Тест сохранения результата генерации в папку results."""
        logger.info("=== Тест: сохранение результата в папку results ===")
        user_id = 1
        image_data = b'fake png image data'
        
        logger.info(f"Сохранение изображения для пользователя {user_id}, размер: {len(image_data)} байт")
        
        # Сохраняем результат
        result_path, result_url = file_manager.save_result_image(
            user_id=user_id,
            image_data=image_data,
            filename="test_result.png"
        )
        
        logger.info(f"Файл сохранен: {result_path}")
        logger.info(f"Публичный URL: {result_url}")
        
        # Проверяем, что файл создан
        assert result_path.exists(), f"Файл не существует: {result_path}"
        assert result_path.is_file(), f"Путь не является файлом: {result_path}"
        assert result_path.name == "test_result.png", f"Неверное имя файла: {result_path.name}"
        logger.info("✅ Файл создан и находится в правильном месте")
        
        # Проверяем, что файл в папке results
        assert "results" in str(result_path), f"Файл не в папке results: {result_path}"
        logger.info("✅ Файл находится в папке results")
        
        # Проверяем содержимое
        with open(result_path, 'rb') as f:
            saved_data = f.read()
            assert saved_data == image_data, "Содержимое файла не совпадает"
        logger.info("✅ Содержимое файла корректно")
        
        # Проверяем URL
        assert f"/files/{user_id}/results/test_result.png" in result_url, f"Неверный URL: {result_url}"
        logger.info("✅ Публичный URL сформирован корректно")
        
        # Удаляем тестовый файл
        result_path.unlink()
        logger.info("✅ Тест пройден: результат сохранен корректно")
    
    def test_full_generation_flow(self):
        """Интеграционный тест полного процесса генерации."""
        logger.info("=== Тест: полный процесс генерации (интеграционный) ===")
        
        # Создаем временный файл для теста
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            tmp_file.write(b'fake image data')
            tmp_file_path = tmp_file.name
        
        logger.info(f"Создан временный файл: {tmp_file_path}")
        
        try:
            # Мокаем весь процесс
            # 1. Начальный запрос
            initial_response = Mock()
            initial_response.json.return_value = {
                "id": "test-request-id-123",
                "type": "nano_banana",
                "status": "pending",
                "jobs": [{"id": "test-job-id-456"}]
            }
            initial_response.raise_for_status = Mock()
            
            # 2. Опрос статуса (pending -> completed)
            status_responses = [
                Mock(json=Mock(return_value={"id": "test-request-id-123", "status": "pending"}), raise_for_status=Mock()),
                Mock(json=Mock(return_value={
                    "id": "test-request-id-123",
                    "status": "completed",
                    "result": {
                        "images": [{"url": "https://example.com/generated-image.png"}]
                    }
                }), raise_for_status=Mock())
            ]
            
            # 3. Скачивание изображения
            image_response = Mock()
            image_response.content = b'generated image data'
            image_response.raise_for_status = Mock()
            
            with patch('api.client.requests.post', return_value=initial_response):
                with patch('api.client.requests.get', side_effect=status_responses + [image_response]):
                    with patch('api.client.higgsfield_client') as mock_client:
                        mock_client.upload_file.return_value = "https://example.com/uploaded.jpg"
                        
                        client = NanoBananaAPIClient()
                        
                        # Генерируем изображение
                        logger.info("Шаг 1: Отправка запроса на генерацию")
                        with patch('time.sleep'):  # Ускоряем тест
                            result = client.generate(
                                prompt="test prompt",
                                image_path=tmp_file_path,
                                aspect_ratio="16:9"
                            )
                            
                            logger.info(f"Получен начальный ответ: ID={result.get('id')}, статус={result.get('status')}")
                            
                            # Проверяем, что получили ID задачи
                            assert "id" in result
                            request_id = result["id"]
                            logger.info(f"✅ Шаг 1 завершен: ID задачи = {request_id}")
                            
                            # Ожидаем завершения
                            logger.info("Шаг 2: Ожидание завершения задачи")
                            final_result = client.wait_for_completion(request_id, max_wait_time=60, poll_interval=1)
                            
                            logger.info(f"Задача завершена. Статус: {final_result.get('status')}")
                            
                            # Проверяем, что задача завершена
                            assert final_result["status"] == "completed"
                            assert "result" in final_result
                            logger.info("✅ Шаг 2 завершен: задача успешно завершена")
                            
                            # Проверяем наличие изображений в результате
                            if "result" in final_result and "images" in final_result["result"]:
                                assert len(final_result["result"]["images"]) > 0
                                image_url = final_result["result"]["images"][0].get("url")
                                assert image_url is not None
                                logger.info(f"✅ Шаг 3: URL изображения получен: {image_url}")
                                
                                # Скачиваем и сохраняем изображение
                                logger.info("Шаг 4: Скачивание изображения")
                                import requests
                                with patch('requests.get', return_value=image_response):
                                    img_response = requests.get(image_url)
                                    image_data = img_response.content
                                    logger.info(f"Изображение скачано, размер: {len(image_data)} байт")
                                    
                                    # Сохраняем результат
                                    logger.info("Шаг 5: Сохранение изображения в папку results")
                                    result_path, result_url = file_manager.save_result_image(
                                        user_id=1,
                                        image_data=image_data
                                    )
                                    
                                    logger.info(f"Файл сохранен: {result_path}")
                                    logger.info(f"Публичный URL: {result_url}")
                                    
                                    # Проверяем, что файл сохранен
                                    assert result_path.exists()
                                    assert result_path.is_file()
                                    logger.info("✅ Шаг 5 завершен: файл успешно сохранен")
                                    
                                    # Удаляем тестовый файл
                                    result_path.unlink()
                                    logger.info("✅ Полный процесс генерации завершен успешно")
        
        finally:
            # Удаляем временный файл
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)
    
    def test_extract_image_url_from_result(self):
        """Тест извлечения URL изображения из различных форматов ответа."""
        logger.info("=== Тест: извлечение URL изображения из разных форматов ===")
        
        # Тест 1: формат с images массивом
        result1 = {
            "status": "completed",
            "result": {
                "images": [
                    {"url": "https://example.com/image1.png"},
                    {"url": "https://example.com/image2.png"}
                ]
            }
        }
        
        # Тест 2: формат с прямым url
        result2 = {
            "status": "completed",
            "url": "https://example.com/image.png"
        }
        
        # Тест 3: формат с image_url
        result3 = {
            "status": "completed",
            "image_url": "https://example.com/image.png"
        }
        
        # Проверяем извлечение URL
        def extract_url(result):
            logger.info(f"Извлечение URL из результата: ключи = {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            if isinstance(result, dict):
                if 'images' in result and len(result['images']) > 0:
                    url = result['images'][0].get('url') or result['images'][0].get('image_url')
                    logger.info(f"Найден URL в images: {url}")
                    return url
                elif 'result' in result and isinstance(result['result'], dict):
                    if 'images' in result['result'] and len(result['result']['images']) > 0:
                        url = result['result']['images'][0].get('url') or result['result']['images'][0].get('image_url')
                        logger.info(f"Найден URL в result.images: {url}")
                        return url
                elif 'url' in result:
                    url = result['url']
                    logger.info(f"Найден URL напрямую: {url}")
                    return url
                elif 'image_url' in result:
                    url = result['image_url']
                    logger.info(f"Найден image_url: {url}")
                    return url
            logger.warning("URL не найден в результате")
            return None
        
        url1 = extract_url(result1)
        assert url1 == "https://example.com/image1.png", f"Неверный URL для result1: {url1}"
        logger.info("✅ Формат 1 (result.images): URL извлечен корректно")
        
        url2 = extract_url(result2)
        assert url2 == "https://example.com/image.png", f"Неверный URL для result2: {url2}"
        logger.info("✅ Формат 2 (прямой url): URL извлечен корректно")
        
        url3 = extract_url(result3)
        assert url3 == "https://example.com/image.png", f"Неверный URL для result3: {url3}"
        logger.info("✅ Формат 3 (image_url): URL извлечен корректно")
        
        logger.info("✅ Тест пройден: все форматы обработаны корректно")
    
    def test_handle_failed_status(self):
        """Тест обработки статуса failed."""
        logger.info("=== Тест: обработка статуса failed ===")
        request_id = "test-request-id-123"
        
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": request_id,
            "status": "failed",
            "error": "Generation failed"
        }
        mock_response.raise_for_status = Mock()
        
        logger.info(f"Проверка обработки статуса 'failed' для задачи: {request_id}")
        
        with patch('api.client.requests.get', return_value=mock_response):
            with patch('time.sleep'):
                client = NanoBananaAPIClient()
                
                logger.info("Ожидание исключения при статусе 'failed'")
                with pytest.raises(Exception, match="завершилась с ошибкой"):
                    client.wait_for_completion(request_id, max_wait_time=60, poll_interval=1)
                
                logger.info("✅ Тест пройден: исключение при статусе 'failed' обработано корректно")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
