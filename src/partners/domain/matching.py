"""Сравнение текстов: цепочки по 4 слова, коэффициент Жаккара и доля вхождения.

Доля вхождения нужна потому, что школа часто публикует материал внутри более
длинной заметки — тогда Жаккар маленький, а почти все цепочки исходника найдены."""

import re
from dataclasses import dataclass

from .entities import PlacementStatus

CONFIRM_THRESHOLD = 0.60
DISPUTE_THRESHOLD = 0.35
SHINGLE = 4


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def shingles(text: str, n: int = SHINGLE) -> set:
    words = normalize(text).split()
    if len(words) < n:
        return set()
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


@dataclass(frozen=True)
class MatchResult:
    similarity: float
    jaccard: float
    coverage: float
    decision: PlacementStatus


class TextMatcher:
    def __init__(self, confirm=CONFIRM_THRESHOLD, dispute=DISPUTE_THRESHOLD):
        self.confirm = confirm
        self.dispute = dispute

    def compare(self, original: str, found: str) -> MatchResult:
        a, b = shingles(original), shingles(found)
        if not a or not b:
            return MatchResult(0.0, 0.0, 0.0, PlacementStatus.EXPECTED)
        common = len(a & b)
        jaccard = common / len(a | b)
        coverage = common / len(a)
        similarity = max(jaccard, coverage)
        if similarity >= self.confirm:
            decision = PlacementStatus.CONFIRMED
        elif similarity >= self.dispute:
            decision = PlacementStatus.DISPUTED
        else:
            decision = PlacementStatus.EXPECTED  # пока не найдено
        return MatchResult(round(similarity, 3), round(jaccard, 3), round(coverage, 3), decision)

    def best(self, original: str, candidates):
        """Лучшее совпадение среди найденных публикаций: (публикация, результат) или (None, None)."""
        best_pub, best_res = None, None
        for pub in candidates:
            res = self.compare(original, pub.text)
            if best_res is None or res.similarity > best_res.similarity:
                best_pub, best_res = pub, res
        return best_pub, best_res
