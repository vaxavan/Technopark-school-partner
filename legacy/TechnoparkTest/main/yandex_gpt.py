import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from .prompts import build_messages


# Находим папку, в которой находится manage.py
BASE_DIR = Path(__file__).resolve().parent.parent

# Загружаем настройки из файла .env
load_dotenv(BASE_DIR / ".env")


def generate_news(user_text):

    # Получаем API-ключ и ID каталога
    api_key = os.getenv("YANDEX_API_KEY")
    folder_id = os.getenv("YANDEX_FOLDER_ID")

    # Проверяем наличие настроек
    if not api_key or not folder_id:
        raise ValueError(
            "Не найден API-ключ или ID каталога Yandex Cloud."
        )

    # Адрес API YandexGPT
    url = (
        "https://llm.api.cloud.yandex.net/"
        "foundationModels/v1/completion"
    )

    # Заголовки запроса
    headers = {
        "Authorization": f"Api-Key {api_key}",
        "Content-Type": "application/json"
    }

    # Формируем инструкции для нейросети
    messages = build_messages(user_text)

    # Данные, которые отправляем в YandexGPT
    data = {
        "modelUri": f"gpt://{folder_id}/yandexgpt/latest",

        "completionOptions": {
            "stream": False,
            "temperature": 0.3,
            "maxTokens": "2000"
        },

        "messages": messages
    }

    # Отправляем запрос
    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=90
    )

    # Проверяем, не вернул ли сервер ошибку
    # Проверяем ответ сервера
    if response.status_code != 200:

        print("Ошибка при обращении к YandexGPT")
        print("HTTP-код:", response.status_code)

        try:
            error_data = response.json()

            print("Ответ сервера Яндекса:")

            if isinstance(error_data, dict):
                print("Поля ответа:", list(error_data.keys()))

                for key, value in error_data.items():
                    if key.lower() not in ("request", "authorization", "token", "api_key"):
                        print(f"{key}: {str(value)[:500]}")

            else:
                print("Ответ:", str(error_data)[:500])

        except ValueError:
            print("Сервер вернул ответ не в формате JSON")

        response.raise_for_status()

    # Преобразуем ответ из JSON
    result = response.json()

    # Получаем текст, созданный нейросетью
    news_text = result["result"]["alternatives"][0]["message"]["text"]

    return news_text