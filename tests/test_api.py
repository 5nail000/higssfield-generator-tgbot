"""
Тест для проверки статуса запросов через API.
"""
import sys
import os
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import http.client
import json
from config.settings import settings

# Получаем API ключи из конфига
api_key = settings.HIGGSFIELD_API_KEY
api_secret = settings.HIGGSFIELD_API_KEY_SECRET

if not api_key:
    print("Ошибка: HIGGSFIELD_API_KEY не установлен в config.json")
    exit(1)

conn = http.client.HTTPSConnection("platform.higgsfield.ai")

# Заголовки с авторизацией
headers = {
    'hf-api-key': api_key,
    'Content-Type': 'application/json'
}

if api_secret:
    headers['hf-secret'] = api_secret

# Тест 1
print("=== Тест 1: Проверка статуса запроса ===")
request_id_1 = "ab097cf1-3c35-48ca-931e-8894243da7ee"
conn.request("GET", f"/requests/{request_id_1}/status", headers=headers)
res = conn.getresponse()
data = res.read()
response_text = data.decode("utf-8")
print(f"Статус код: {res.status}")
print(f"Ответ: {response_text}")

try:
    response_json = json.loads(response_text)
    print(f"JSON ответ: {json.dumps(response_json, ensure_ascii=False, indent=2)}")
except:
    print("Ответ не является валидным JSON")

print("\n" + "="*50 + "\n")

# Тест 2
print("=== Тест 2: Проверка статуса запроса ===")
request_id_2 = "5552ae36-4153-49f1-b90a-8a67f07e0fe5"
conn.request("GET", f"/requests/{request_id_2}/status", headers=headers)
res = conn.getresponse()
data = res.read()
response_text = data.decode("utf-8")
print(f"Статус код: {res.status}")
print(f"Ответ: {response_text}")

try:
    response_json = json.loads(response_text)
    print(f"JSON ответ: {json.dumps(response_json, ensure_ascii=False, indent=2)}")
except:
    print("Ответ не является валидным JSON")

conn.close()