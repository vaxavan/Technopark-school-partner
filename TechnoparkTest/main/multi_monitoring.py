import time
import requests
import json

from urllib.parse import urljoin, urlparse

from school_parser import get_page, parse_news_page
from text_comparison import compare_texts
from schools_loader import load_schools

from pathlib import Path
from datetime import datetime


# Список школ для первоначальной проверки



# Пока используем фрагмент известной публикации.
# Позже заменим его оригиналом из рассылки технопарка.
ORIGINAL_NEWS = """
Уже в следующую субботу, 11 октября,
отважная команда РТУ МИРЭА Istok.Robotics
представит свою разработку:
единственную машину Международного чемпионата
по битве роботов на гусеничном ходу — RedHorn 3.

Робот создан при поддержке технологического
партнёра АО НПП «Исток» имени А.И. Шокина
и сразится во втором этапе «Битвы роботов».
"""


def find_news_on_school(school_url):
    """Находит ссылки на публикации конкретной школы."""

    # У каждой школы свой домен,
    # но предполагаем одинаковый путь к разделу.
    section_url = urljoin(
        school_url.rstrip("/") + "/",
        "activity/proforientation/miria"
    )

    soup = get_page(section_url)

    found_urls = []
    seen = set()

    school_domain = urlparse(school_url).netloc

    for link in soup.find_all("a", href=True):

        full_url = urljoin(section_url, link["href"])
        parsed = urlparse(full_url)

        # Проверяем, что публикация относится
        # к нужной школе и нужному разделу.
        if (
            parsed.netloc == school_domain
            and parsed.path.startswith(
                "/activity/proforientation/miria/post/"
            )
        ):

            clean_url = (
                f"{parsed.scheme}://"
                f"{parsed.netloc}"
                f"{parsed.path}"
            )

            if clean_url not in seen:
                seen.add(clean_url)
                found_urls.append(clean_url)

    return found_urls


def check_school(school):
    """Проверяет публикации одной школы."""

    print("\n" + "=" * 65)
    print("ПРОВЕРЯЕМ:", school["name"])
    print("=" * 65)

    try:
        urls = find_news_on_school(school["url"])

    except requests.exceptions.RequestException as error:

        print("Не удалось открыть раздел школы.")
        print("Ошибка:", error)

        return {
            "school": school["name"],
            "status": "Ошибка подключения",
            "similarity": None,
            "url": None
        }

    print("Найдено публикаций:", len(urls))

    if not urls:

        print("Ссылки на публикации не обнаружены.")

        return {
            "school": school["name"],
            "status": "Публикации не обнаружены",
            "similarity": None,
            "url": None
        }

    best_result = None
    best_news = None

    checked_count = 0
    error_count = 0
    empty_count = 0

    for url in urls:

        try:
            news = parse_news_page(url)

            if not news["text"]:
                print("Не удалось извлечь текст:", url)
                empty_count += 1
                continue

            result = compare_texts(
                ORIGINAL_NEWS,
                news["text"]
            )

            checked_count += 1

            print(
                f"Проверено: {news['title'][:65]}"
            )

            print(
                f"Совпадение: "
                f"{result['similarity'] * 100:.1f}%"
            )

            if (
                best_result is None
                or result["similarity"] > best_result["similarity"]
            ):
                best_result = result
                best_news = news

        except requests.exceptions.RequestException as error:

            print("Ошибка чтения новости:", url)
            print(error)
            error_count += 1

        # Небольшая пауза между запросами.
        time.sleep(0.5)

    if best_result is None:

        return {
            "school": school["name"],
            "status": "Не удалось проверить публикации",
            "similarity": None,
            "url": None
        }

    status = best_result["status"]

    # Если не все новости удалось проверить,
    # нельзя уверенно говорить об отсутствии материала.
    if (
        best_result["similarity"] < 0.35
        and (error_count > 0 or empty_count > 0)
    ):
        status = "Проверка неполная"

    print("\nЛУЧШЕЕ СОВПАДЕНИЕ:")
    print(best_news["title"])
    print(best_news["url"])

    print(
        "Коэффициент:",
        f"{best_result['similarity'] * 100:.1f}%"
    )

    print("Статус:", status)

    return {
        "school": school["name"],
        "status": status,
        "similarity": best_result["similarity"],
        "url": (
            best_news["url"]
            if best_result["similarity"] >= 0.35
            else None
        )
    }


def main():

    print("\nМОНИТОРИНГ ШКОЛ-ПАРТНЁРОВ")
    print("=" * 65)

    # Сначала загружаем школы из Excel
    SCHOOLS = load_schools()

    # Затем выводим их количество
    print("Количество школ:", len(SCHOOLS))

    results = []

    # Пока проверяем только первые 3 школы
    for school in SCHOOLS:

        result = check_school(school)
        results.append(result)

        time.sleep(1)

    # Путь к файлу с результатами мониторинга
    BASE_DIR = Path(__file__).resolve().parent.parent
    REPORT_FILE = BASE_DIR / "monitoring_results.json"

    report = {
        "checked_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "schools": results
    }

    # Сохраняем результаты проверки
    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=4)

    print("\nРезультаты сохранены в monitoring_results.json")

    print("\n" + "=" * 65)
    print("ИТОГОВЫЙ ОТЧЁТ")
    print("=" * 65)

    for result in results:

        print("\nШкола:", result["school"])
        print("Результат:", result["status"])

        if result["similarity"] is not None:
            print(
                "Совпадение:",
                f"{result['similarity'] * 100:.1f}%"
            )
        else:
            print("Совпадение: не рассчитано")

        if result["url"]:
            print("Ссылка:", result["url"])

    print("\nПроверка завершена.")


if __name__ == "__main__":
    main()