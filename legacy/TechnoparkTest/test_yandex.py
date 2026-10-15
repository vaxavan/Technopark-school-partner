from main.yandex_gpt import generate_news


# Тестовая информация о мероприятии
user_text = """
15 октября в технопарке «Исток — РТУ МИРЭА»
прошёл мастер-класс по робототехнике.

В мероприятии приняли участие 25 школьников.

Во время занятия ребята собирали роботов
и программировали их движение.
"""


print("Отправляем запрос в YandexGPT...")
print("Пожалуйста, подождите.\n")


try:
    news = generate_news(user_text)

    print("ГОТОВАЯ НОВОСТЬ:")
    print("-----------------------------")
    print(news)

except Exception as error:

    print("Произошла ошибка при генерации новости.")
    print("Тип ошибки:", type(error).__name__)
    print("Код HTTP:", getattr(
        getattr(error, "response", None),
        "status_code",
        "не получен"
    ))