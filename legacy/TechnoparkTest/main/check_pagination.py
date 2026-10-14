from school_parser import get_page
from urllib.parse import urljoin, urlparse


SCHOOL_URL = (
    "https://sch16-schel.edumsko.ru"
    "/activity/proforientation/miria"
)


def main():

    print("\nПРОВЕРКА АРХИВА ПУБЛИКАЦИЙ")
    print("=" * 60)

    soup = get_page(SCHOOL_URL)

    links = set()

    for link in soup.find_all("a", href=True):

        full_url = urljoin(SCHOOL_URL, link["href"])

        parsed = urlparse(full_url)

        if (
            parsed.netloc == urlparse(SCHOOL_URL).netloc
            and "/activity/proforientation/miria/post/" in parsed.path
        ):
            links.add(parsed.path)

    print("\nКоличество публикаций:", len(links))

    print("\nНАЙДЕННЫЕ ССЫЛКИ:")

    for link in sorted(links):
        print(link)

    print("\nВОЗМОЖНЫЕ ЭЛЕМЕНТЫ ЗАГРУЗКИ:")

    for element in soup.find_all(
        ["button", "a", "div"],
        limit=500
    ):

        classes = " ".join(element.get("class", []))

        element_id = element.get("id", "")

        attributes = (
            str(element.get("data-url", "")) + " " +
            str(element.get("data-page", "")) + " " +
            str(element.get("onclick", ""))
        )

        combined = (
            classes + " " +
            element_id + " " +
            attributes
        ).lower()

        keywords = [
            "pagination",
            "pager",
            "loadmore",
            "load-more",
            "showmore",
            "show-more",
            "next-page"
        ]

        if any(word in combined for word in keywords):

            print("\nТег:", element.name)
            print("Класс:", classes)
            print("ID:", element_id)
            print("Атрибуты:", attributes)

    print("\nПроверка завершена.")


if __name__ == "__main__":
    main()