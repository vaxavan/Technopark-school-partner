from school_parser import find_school_news, parse_news_page
from text_comparison import compare_texts


# Пока используем текст известной публикации.
# Позже заменим его исходной новостью технопарка.
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


def main():

    print("\nМОНИТОРИНГ ПУБЛИКАЦИЙ ШКОЛЫ № 16")
    print("=" * 60)

    try:
        urls = find_school_news()

    except Exception as error:
        print("Ошибка получения списка новостей:", error)
        return

    print(f"\nНайдено публикаций: {len(urls)}")

    best_result = None
    best_news = None

    for url in urls:

        try:
            news = parse_news_page(url)

            if not news["text"]:
                print(f"Не удалось извлечь текст: {url}")
                continue

            result = compare_texts(
                ORIGINAL_NEWS,
                news["text"]
            )

            print("\nПроверяем публикацию:")
            print(news["title"])

            print(
                "Совпадение:",
                f"{result['similarity'] * 100:.1f}%"
            )

            if (
                best_result is None
                or result["similarity"] > best_result["similarity"]
            ):
                best_result = result
                best_news = news

        except Exception as error:
            print(f"Ошибка проверки {url}: {error}")

    print("\n" + "=" * 60)
    print("ИТОГ МОНИТОРИНГА")
    print("=" * 60)

    if best_result is None:
        print("Не удалось проверить ни одной публикации.")
        return

    print("Школа: СОШ № 16 Щёлково")
    print("Результат:", best_result["status"])

    print(
        "Совпадение:",
        f"{best_result['similarity'] * 100:.1f}%"
    )

    print("Название:", best_news["title"])
    print("Ссылка:", best_news["url"])


if __name__ == "__main__":
    main()