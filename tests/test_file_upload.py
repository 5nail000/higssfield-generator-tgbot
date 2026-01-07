"""
Тесты для загрузки файлов через higgsfield_client.
"""
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from api.client import NanoBananaAPIClient
from config.settings import settings


class TestFileUpload:
    """Тесты для загрузки файлов."""
    
    def test_upload_file_with_higgsfield_client(self):
        """Тест загрузки файла через higgsfield_client.upload_file()."""
        # Создаем временный файл для теста
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            tmp_file.write(b'fake image data')
            tmp_file_path = tmp_file.name
        
        try:
            # Мокаем higgsfield_client
            with patch('api.client.higgsfield_client') as mock_client:
                mock_client.upload_file.return_value = "https://example.com/uploaded_file.jpg"
                
                # Создаем клиент
                client = NanoBananaAPIClient()
                
                # Загружаем файл
                result_url = client.upload_file(tmp_file_path)
                
                # Проверяем, что метод был вызван
                mock_client.upload_file.assert_called_once_with(tmp_file_path)
                
                # Проверяем результат
                assert result_url == "https://example.com/uploaded_file.jpg"
                assert result_url.startswith("https://")
        
        finally:
            # Удаляем временный файл
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)
    
    def test_upload_file_fallback_to_public_url(self):
        """Тест fallback на публичный URL при недоступности higgsfield_client."""
        # Создаем временный файл
        test_file_path = "storage/users/123/test_image.jpg"
        
        # Мокаем недоступность higgsfield_client
        with patch('api.client.HIGGSFIELD_CLIENT_AVAILABLE', False):
            client = NanoBananaAPIClient()
            
            # Загружаем файл (должен использовать fallback)
            result_url = client.upload_file(test_file_path)
            
            # Проверяем, что используется публичный URL
            assert "http://localhost:5000" in result_url
            assert "/files/123/test_image.jpg" in result_url
    
    def test_upload_file_exception_fallback(self):
        """Тест fallback при исключении при загрузке."""
        # Создаем временный файл
        test_file_path = "storage/users/456/another_image.jpg"
        
        # Мокаем исключение при загрузке
        with patch('api.client.higgsfield_client') as mock_client:
            mock_client.upload_file.side_effect = Exception("Upload failed")
            
            client = NanoBananaAPIClient()
            
            # Загружаем файл (должен использовать fallback)
            result_url = client.upload_file(test_file_path)
            
            # Проверяем, что используется публичный URL как fallback
            assert "http://localhost:5000" in result_url
            assert "/files/456/another_image.jpg" in result_url
    
    def test_get_public_url_extraction(self):
        """Тест извлечения публичного URL из пути файла."""
        client = NanoBananaAPIClient()
        
        # Тест с правильным путем
        file_path = "storage/users/789/image.jpg"
        public_url = client._get_public_url(file_path)
        
        assert public_url == "http://localhost:5000/files/789/image.jpg"
        
        # Тест с Windows путем
        file_path = "storage\\users\\789\\image.jpg"
        public_url = client._get_public_url(file_path)
        
        assert public_url == "http://localhost:5000/files/789/image.jpg"
    
    def test_upload_file_real_integration(self):
        """
        Интеграционный тест с реальным файлом.
        Требует наличия higgsfield_client и валидного API ключа.
        """
        pytest.skip("Требует реального API ключа и подключения")
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp_file:
            tmp_file.write(b'fake image data for testing')
            tmp_file_path = tmp_file.name
        
        try:
            client = NanoBananaAPIClient()
            result_url = client.upload_file(tmp_file_path)
            
            # Проверяем, что получили URL
            assert result_url
            assert isinstance(result_url, str)
            assert len(result_url) > 0
        
        finally:
            if os.path.exists(tmp_file_path):
                os.unlink(tmp_file_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
