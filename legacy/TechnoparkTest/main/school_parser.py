import requests

from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse


# Раздел базовой школы РТУ МИРЭА
SCHOOL_URL = (
    "https://sch16-schel.edumsko.ru"
    "/activity/proforientation/miria"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}


def get_page(url):
    """Загружает HTML-страницу школы."""

    response = requests.get(
        url,
        timeout=20,
        headers=HEADERS
    )

    response.raise_for_status()
    response.encoding = "utf-8"

    return BeautifulSoup(
        response.text,
        "html.parser"
    )


def find_school_news():
    """Находит ссылки на публикации в разделе школы."""

    soup = get_page(SCHOOL_URL)

    news = []
    seen = set()

    for link in soup.find_all("a", href=True):

        full_url = urljoin(
            SCHOOL_URL,
            link["href"]
        )

        parsed_url = urlparse(full_url)

        if (
            parsed_url.netloc == urlparse(SCHOOL_URL).netloc
            and parsed_url.path.startswith(
                "/activity/proforientation/miria/post/"
            )
        ):

            clean_url = (
                f"{parsed_url.scheme}://"
                f"{parsed_url.netloc}"
                f"{parsed_url.path}"
            )

            if clean_url not in seen:
                seen.add(clean_url)
                news.append(clean_url)

    return news


def parse_news_page(url):
    """Извлекает заголовок и основной текст новости."""

    soup = get_page(url)

    # Заголовок публикации
    heading = soup.find("h1")

    if heading:
        title = heading.get_text(" ", strip=True)
    else:
        title = "Без заголовка"

    # Основной текст новости
    article = soup.select_one("div.user_content")

    if article:
        text = article.get_text(" ", strip=True)
    else:
        # Если структура сайта изменилась,
        # не подменяем текст новости текстом всей страницы.
        text = ""

    return {
        "title": title,
        "text": text,
        "url": url
    }


def main():

    print("\nПАРСИНГ НОВОСТЕЙ ШКОЛЫ № 16")
    print("=" * 60)

    try:
        urls = find_school_news()

    except requests.exceptions.RequestException as error:
        print("Ошибка подключения к разделу школы:", error)
        return

    print(f"\nНайдено публикаций: {len(urls)}")

    for number, url in enumerate(urls, start=1):

        print("\n" + "=" * 60)
        print(f"ПУБЛИКАЦИЯ № {number}")
        print("=" * 60)

        try:
            news = parse_news_page(url)

            print("\nЗАГОЛОВОК:")
            print(news["title"])

            print("\nССЫЛКА:")
            print(news["url"])

            print("\nТЕКСТ НОВОСТИ:")
            if news["text"]:
                print(news["text"][:600])
            else:
                print("Не удалось найти контейнер текста новости.")

        except requests.exceptions.RequestException as error:
            print("Ошибка получения публикации:", error)


if __name__ == "__main__":
    main()