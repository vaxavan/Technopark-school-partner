from django.shortcuts import render
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .yandex_gpt import generate_news
from django.http import HttpResponse
from .prompts import build_messages


def index(request):
    return render(request, 'main/index.html')

def about(request):
    return render(request, 'main/about.html')

def generate(request):
    return render(request, 'main/generate.html')

@require_POST
def generate_news_api(request):
    try:
        # Получаем данные, отправленные браузером
        data = json.loads(request.body)

        # Извлекаем текст пользователя
        user_text = data.get("text", "").strip()

        # Проверяем, что пользователь ввёл текст
        if not user_text:
            return JsonResponse(
                {"error": "Введите информацию о мероприятии."},
                status=400
            )

        # Ограничиваем размер текста
        if len(user_text) > 10000:
            return JsonResponse(
                {"error": "Текст слишком длинный. Максимум 10 000 символов."},
                status=400
            )

        # Отправляем текст в YandexGPT
        news = generate_news(user_text)

        # Возвращаем готовую новость браузеру
        return JsonResponse({"news": news})

    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {"error": "Некорректный формат запроса."},
            status=400
        )

    except Exception:
        return JsonResponse(
            {"error": "Не удалось сгенерировать новость. Попробуйте позже."},
            status=502
        )