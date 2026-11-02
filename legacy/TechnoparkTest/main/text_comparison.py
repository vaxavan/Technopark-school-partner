import re


def normalize_text(text):
    """Приводим текст к единому виду."""

    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def get_ngrams(text, n=4):
    """Создаём последовательности из четырёх слов."""

    words = normalize_text(text).split()

    if len(words) < n:
        return set()

    return {
        tuple(words[i:i + n])
        for i in range(len(words) - n + 1)
    }


def compare_texts(original_text, school_text):
    """Сравниваем исходную новость с публикацией школы."""

    original_ngrams = get_ngrams(original_text)
    school_ngrams = get_ngrams(school_text)

    if not original_ngrams or not school_ngrams:
        return {
            "similarity": 0.0,
            "status": "Не найдено"
        }

    common = original_ngrams & school_ngrams
    union = original_ngrams | school_ngrams

    # Коэффициент Жаккара
    jaccard = len(common) / len(union)

    # Доля исходных фрагментов, найденных у школы
    coverage = len(common) / len(original_ngrams)

    # Выбираем больший показатель
    similarity = max(jaccard, coverage)

    if similarity >= 0.60:
        status = "Размещение подтверждено"

    elif similarity >= 0.35:
        status = "Требуется проверка"

    else:
        status = "Совпадение не обнаружено"

    return {
        "similarity": round(similarity, 3),
        "status": status,
        "jaccard": round(jaccard, 3),
        "coverage": round(coverage, 3)
    }
if __name__ == "__main__":

    original = """
    Команда технопарка Исток примет участие
    в международной битве роботов.
    Наши ребята представят нового робота.
    """

    school_news = """
    Команда технопарка Исток примет участие
    в международной битве роботов.
    Наши ребята представят нового робота.
    """

    result = compare_texts(original, school_news)

    print("Коэффициент совпадения:", result["similarity"])
    print("Статус:", result["status"])