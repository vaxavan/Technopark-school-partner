from django.shortcuts import render
import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from .yandex_gpt import generate_news
from django.http import HttpResponse
from .prompts import build_messages
import json
from pathlib import Path
import subprocess
import sys



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

from django.shortcuts import render


def monitoring(request):
    """Отображает результаты мониторинга школ-партнёров."""

    # Папка проекта, в которой находится manage.py
    base_dir = Path(__file__).resolve().parent.parent

    # Файл с результатами нашего парсера
    report_file = base_dir / "monitoring_results.json"

    schools = []
    checked_at = None
    error = None

    if report_file.exists():

        try:
            with open(report_file, "r", encoding="utf-8") as file:
                report = json.load(file)

            schools = report.get("schools", [])
            checked_at = report.get("checked_at")

            # Переводим коэффициент совпадения в проценты
            for school in schools:
                similarity = school.get("similarity")

                if similarity is not None:
                    school["similarity"] = round(similarity * 100, 1)

        except (OSError, json.JSONDecodeError, TypeError) as exc:
            error = f"Не удалось прочитать отчёт: {exc}"

    else:
        error = "Отчёт мониторинга ещё не создан."

    return render(
        request,
        "main/monitoring.html",
        {
            "schools": schools,
            "checked_at": checked_at,
            "error": error,
        }
    )
@require_POST
def start_monitoring(request):
    """Запускает мониторинг школ в отдельном процессе."""

    base_dir = Path(__file__).resolve().parent.parent
    script_path = base_dir / "main" / "multi_monitoring.py"

    # Проверяем, существует ли скрипт
    if not script_path.exists():
        return JsonResponse(
            {"error": "Скрипт мониторинга не найден"},
            status=500
        )

    try:
        subprocess.Popen(
            [
                sys.executable,
                str(script_path)
            ],
            cwd=str(base_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        return JsonResponse({
            "status": "started",
            "message": "Мониторинг школ запущен"
        })

    except OSError as error:
        return JsonResponse(
            {"error": str(error)},
            status=500
        )