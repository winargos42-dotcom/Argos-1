import requests
import json
import time

# Настройки
BASE_URL = "http://localhost:5051"
TIMEOUT = 30

def debug_request(method, endpoint, data=None):
    """Отладочный запрос с полной информацией"""
    url = f"{BASE_URL}{endpoint}"
    print(f"🔍 Отладка запроса: {method} {url}")
    
    if data:
        print(f"📨 Данные: {json.dumps(data, ensure_ascii=False)}")
    
    headers = {"Content-Type": "application/json"}
    print(f"📋 Headers: {headers}")
    
    try:
        start_time = time.time()
        
        if method.upper() == "GET":
            response = requests.get(url, headers=headers, timeout=TIMEOUT)
        elif method.upper() == "POST":
            response = requests.post(url, headers=headers, json=data, timeout=TIMEOUT)
        else:
            raise ValueError(f"Неподдерживаемый метод: {method}")
            
        end_time = time.time()
        response_time = end_time - start_time
        
        print(f"⏱️  Время ответа: {response_time:.2f} секунд")
        print(f"📊 Status Code: {response.status_code}")
        print(f"📋 Headers ответа: {dict(response.headers)}")
        
        if response.status_code == 200:
            try:
                result = response.json()
                print(f"📦 Ответ JSON: {json.dumps(result, indent=2, ensure_ascii=False)}")
                return result
            except:
                print(f"📄 Текст ответа: {response.text}")
                return response.text
        else:
            print(f"❌ Ошибка: {response.status_code}")
            try:
                error_data = response.json()
                print(f"🚨 Ошибка JSON: {json.dumps(error_data, indent=2, ensure_ascii=False)}")
            except:
                print(f"🚨 Ошибка текст: {response.text}")
            return None
            
    except requests.exceptions.ConnectionError as e:
        print(f"🔌 Ошибка подключения: {e}")
        return None
    except requests.exceptions.Timeout as e:
        print(f"⏰ Таймаут: {e}")
        return None
    except Exception as e:
        print(f"💥 Неожиданная ошибка: {e}")
        return None

def test_endpoints():
    """Тест различных endpoints"""
    print("🚀 Тестирование API endpoints Argos")
    print("=" * 50)
    
    # Тест статуса
    print("\n1. Тест /api/status:")
    debug_request("GET", "/api/status")
    
    # Тест корневого пути
    print("\n2. Тест корневого пути /:")
    debug_request("GET", "/")
    
    # Тест чата
    print("\n3. Тест /api/chat:")
    debug_request("POST", "/api/chat", {"message": "test message"})
    
    # Тест различных путей
    test_paths = ["/status", "/chat", "/api", "/health"]
    for i, path in enumerate(test_paths, 4):
        print(f"\n{i}. Тест {path}:")
        if path in ["/chat"]:
            debug_request("POST", path, {"message": "test"})
        else:
            debug_request("GET", path)

def main():
    print("🔬 Детальная отладка Argos API")
    print("=" * 40)
    
    # Проверим базовую доступность
    try:
        print("🌐 Проверка доступности сервера...")
        response = requests.get(BASE_URL, timeout=5)
        print(f"✅ Сервер доступен: {response.status_code}")
        if response.status_code != 200:
            print(f"📄 Ответ сервера: {response.text[:200]}")
    except Exception as e:
        print(f"❌ Сервер недоступен: {e}")
        return
    
    # Тест всех endpoints
    test_endpoints()
    
    # Тест специфичных для Argos endpoints
    print("\n📊 Тест специфичных endpoints:")
    argos_endpoints = [
        "/api/skills",
        "/api/version", 
        "/api/config",
        "/api/nodes"
    ]
    
    for endpoint in argos_endpoints:
        print(f"\n🔍 Тест {endpoint}:")
        debug_request("GET", endpoint)

if __name__ == "__main__":
    main()
