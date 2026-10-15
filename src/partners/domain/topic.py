"""Поиск публикаций по теме («битва роботов») — не по нашему тексту, а по ключевой фразе.

Русские слова меняют окончания («битва роботов», «битве роботов», «битвы роботов»), поэтому
каждое слово сравнивается по основе — началу слова без последних букв. Слова фразы должны
стоять рядом (не дальше WINDOW слов друг от друга), иначе «битва» в одном абзаце и «роботы»
в другом дали бы ложное совпадение. Несколько фраз через запятую — ищем любую из них.
Чистый Python: работает и на сервере, и у агента обхода."""

import re
from dataclasses import dataclass

WINDOW = 6          # слова фразы — в пределах 6 слов
SNIPPET = 220


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"[\s_]+", " ", text).strip()


def stem(word: str) -> str:
    """Основа слова: короткие слова целиком, длинные — без двух последних букв (окончание)."""
    if len(word) <= 4:
        return word
    return word[: max(4, len(word) - 2)]


@dataclass(frozen=True)
class TopicMatch:
    score: float        # 1 — фраза целиком, меньше — слова рядом в другой форме
    phrase: str         # какая из фраз нашлась
    snippet: str        # фрагмент текста вокруг совпадения
    in_title: bool


class TopicQuery:
    def __init__(self, query: str):
        self.raw = query.strip()
        self.phrases = []
        for part in re.split(r"[,;\n]+", query):
            words = [w for w in normalize(part).split() if len(w) > 1]
            if words:
                self.phrases.append((" ".join(words), [stem(w) for w in words]))
        if not self.phrases:
            raise ValueError("Введите слово или фразу для поиска")

    def hint(self, text: str) -> bool:
        """Быстрая проверка текста ссылки в списке новостей — её страницу читаем первой."""
        return self._best(normalize(text)) is not None

    def match(self, title: str, text: str) -> TopicMatch | None:
        norm_title, norm_text = normalize(title), normalize(text)
        best = self._best(norm_title + " " + norm_text)
        if best is None:
            return None
        score, phrase, first_stem = best
        in_title = self._best(norm_title) is not None
        return TopicMatch(score=round(min(1.0, score + (0.1 if in_title else 0)), 2), phrase=phrase,
                          snippet=_snippet(text or title, first_stem), in_title=in_title)

    def _best(self, norm: str):
        tokens = norm.split()
        best = None
        for phrase, stems in self.phrases:
            score = _phrase_score(norm, tokens, phrase, stems)
            if score and (best is None or score > best[0]):
                best = (score, phrase, stems[0])
        return best


def _phrase_score(norm, tokens, phrase, stems):
    if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", norm):
        return 1.0
    # окончание — не длиннее 4 букв: «роботов» находит «роботы», но не «робототехника»
    positions = [[i for i, t in enumerate(tokens) if t.startswith(s) and len(t) <= len(s) + 4] for s in stems]
    if not all(positions):
        return 0
    if len(stems) == 1:
        return 0.8
    # ищем самое плотное окно, где встречаются все слова фразы
    best_span = None
    for start in positions[0]:
        span_end = start
        for other in positions[1:]:
            near = [p for p in other if abs(p - start) <= WINDOW]
            if not near:
                break
            span_end = max(span_end, min(near, key=lambda p: abs(p - start)))
        else:
            span = span_end - start
            best_span = span if best_span is None else min(best_span, span)
    if best_span is None:
        return 0
    return 0.8 if best_span <= len(stems) else 0.6


def _snippet(text, first_stem):
    low = (text or "").lower().replace("ё", "е")
    i = low.find(first_stem)
    if i < 0:
        return text[:SNIPPET]
    start = max(0, i - SNIPPET // 3)
    piece = text[start:start + SNIPPET].strip()
    return ("…" if start else "") + piece + ("…" if start + SNIPPET < len(text) else "")
