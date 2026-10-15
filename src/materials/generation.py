"""Подготовка текстов под площадки (рисунок 7 отчёта).

Воркер получает заявку и стилевой регламент, запрашивает модель, проверяет результат
на ограничения площадки и на сведения, которых не было в заявке; при замечаниях
повторяет запрос не более трёх раз и сохраняет вариант с замечаниями."""

import datetime
import difflib
import re

from core.ports import LLMError
from integrations.llm import get_llm

from .models import CHANNEL_RULES, Channel, TextVariant, VariantSource

MAX_ATTEMPTS = 3

BASE_RULES = """Ты — редактор новостей образовательной организации «{org}».
Пиши грамотным русским языком, официально, но понятно. Без канцелярита, без избыточно
восторженного тона, без калек с английского. Текст нейтральный и уважительный, без стереотипов.
Главное правило: используй ТОЛЬКО сведения из заявки. Не придумывай имена, даты, цифры, цитаты,
места, достижения и количество участников. Если чего-то нет — просто не упоминай.
Не добавляй пояснений о себе и о процессе — верни только готовый текст."""

STYLE_HINT = "\n\nСТИЛЕВОЙ РЕГЛАМЕНТ ОРГАНИЗАЦИИ:\n{guide}"
EXAMPLES_HINT = ("\n\nПРИМЕРЫ ОПУБЛИКОВАННЫХ ТЕКСТОВ (только как образец стиля, факты из них не переносить):\n"
                 "{examples}")

MONTHS = "января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря"


def system_prompt(org, channel, examples=()):
    lo, hi, hint = CHANNEL_RULES[channel]
    prompt = BASE_RULES.format(org=org.name)
    prompt += f"\n\nФОРМАТ: {hint}. Длина — не короче {lo} и не длиннее {hi} знаков."
    if org.style_guide.strip():
        prompt += STYLE_HINT.format(guide=org.style_guide.strip())
    if examples:
        prompt += EXAMPLES_HINT.format(examples="\n---\n".join(e[:1500] for e in examples))
    return prompt


def _facts(text):
    """Числа и даты, имена собственные — то, что модель могла выдумать."""
    low = text.lower().replace("ё", "е")
    numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?\b", low))
    dates = set(re.findall(rf"\b\d{{1,2}}\s+(?:{MONTHS})", low))
    names = set(re.findall(r"(?<![.!?]\s)(?<!^)\b[А-ЯЁ][а-яё]{2,}\s+[А-ЯЁ]\.\s?(?:[А-ЯЁ]\.)?", text))
    return numbers, dates, names


def check_variant(channel, text, source_text):
    """Возвращает список замечаний (пустой — всё в порядке)."""
    issues = []
    lo, hi, _ = CHANNEL_RULES[channel]
    n = len(text)
    if n > hi:
        issues.append(f"Длина {n} знаков больше допустимых {hi}")
    if n < lo:
        issues.append(f"Длина {n} знаков меньше рекомендуемых {lo}")
    src_numbers, src_dates, src_names = _facts(source_text)
    numbers, dates, names = _facts(text)
    src_low = source_text.lower().replace("ё", "е")
    allowed = {str(datetime.date.today().year)}
    new_numbers = sorted(x for x in numbers - src_numbers - allowed if x not in src_low)
    if new_numbers:
        issues.append("Числа, которых нет в заявке: " + ", ".join(new_numbers[:8]))
    new_dates = [d for d in dates - src_dates if d not in src_low]
    if new_dates:
        issues.append("Даты, которых нет в заявке: " + ", ".join(sorted(new_dates)[:5]))
    new_names = [x for x in names - src_names if x.split()[0].lower()[:5] not in src_low]
    if new_names:
        issues.append("Имена, которых нет в заявке: " + ", ".join(sorted(new_names)[:5]))
    if channel in (Channel.DZEN, Channel.PARTNERS, Channel.MAX) and "#" in text:
        issues.append("Хештеги на этой площадке не нужны")
    if re.search(r"(как языковая модель|я не могу|в качестве ии)", text.lower()):
        issues.append("В тексте служебные фразы модели")
    return issues


def clean_model_text(text):
    text = text.strip()
    text = re.sub(r"^```\w*\s*|\s*```$", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)  # площадки показывают звёздочки как есть
    text = re.sub(r"^#+\s+", "", text, flags=re.M)  # заголовки Markdown («# Текст»), но не хештеги «#тег»
    return text.strip()


def generate_for_channel(org, channel, source_text, examples=(), llm=None):
    """(текст, замечания, число попыток)."""
    llm = llm or get_llm(org)
    system = system_prompt(org, channel, examples)
    user = f"Подготовь текст по заявке.\n\nСВЕДЕНИЯ:\n{source_text}"
    text, issues = "", []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = clean_model_text(llm.complete(system, user, timeout=90))
        issues = check_variant(channel, text, source_text)
        if not issues:
            return text, [], attempt
        user = (f"Подготовь текст по заявке.\n\nСВЕДЕНИЯ:\n{source_text}\n\n"
                f"Предыдущий вариант:\n{text}\n\nИсправь замечания проверки:\n- " + "\n- ".join(issues))
    return text, issues, MAX_ATTEMPTS


EDIT_COMMANDS = {
    "shorter": "Сократи текст примерно на треть, сохранив все факты.",
    "formal": "Сделай текст более официальным и сдержанным.",
    "simpler": "Сделай текст проще и живее, без канцелярита.",
    "fix": "Исправь орфографию, пунктуацию и стилистику, больше ничего не меняй.",
    "title": "Придумай более точный и короткий заголовок (первая строка), остальное оставь.",
}


def edit_with_model(org, channel, current_text, instruction, source_text, llm=None):
    llm = llm or get_llm(org)
    system = system_prompt(org, channel)
    user = (f"КОМАНДА ПРАВКИ: {instruction}\nИспользуй только сведения из заявки.\n\n"
            f"ЗАЯВКА:\n{source_text}\n\nТЕКСТ:\n{current_text}")
    text = clean_model_text(llm.complete(system, user, timeout=60))
    return text, check_variant(channel, text, source_text)


def change_share(base, new):
    """Доля изменённого текста относительно базовой версии (0..1)."""
    if not base:
        return 0.0
    return round(1 - difflib.SequenceMatcher(None, base, new).ratio(), 3)


def add_version(material, channel, text, source, *, user=None, checks=(), attempts=1, instruction=""):
    first = material.variants.filter(channel=channel, source=VariantSource.MODEL).order_by("version").first()
    prev = material.variants.filter(channel=channel).order_by("-version").first()
    manual = prev.manual_share if prev else 0.0
    model_edit = prev.model_edit_share if prev else 0.0
    if first is not None and source == VariantSource.MANUAL:
        manual = round(manual + change_share(prev.text, text), 3)
    elif first is not None and source == VariantSource.MODEL_EDIT:
        model_edit = round(model_edit + change_share(prev.text, text), 3)
    elif source == VariantSource.MODEL:
        manual = model_edit = 0.0
    material.variants.filter(channel=channel, is_current=True).update(is_current=False)
    return TextVariant.objects.create(
        material=material, channel=channel, version=(prev.version + 1) if prev else 1, text=text,
        source=source, checks=list(checks), attempts=attempts, manual_share=min(manual, 1.0),
        model_edit_share=min(model_edit, 1.0), is_current=True, created_by=user, instruction=instruction[:500])


def style_examples(org, channel, limit=2):
    """Последние опубликованные тексты этой площадки — образец стиля."""
    qs = (TextVariant.objects.filter(material__organization=org, channel=channel, is_current=True,
                                     material__status="published")
          .order_by("-material__created_at").values_list("text", flat=True)[:limit])
    return list(qs)


__all__ = ["generate_for_channel", "edit_with_model", "add_version", "check_variant", "EDIT_COMMANDS",
           "LLMError", "style_examples"]
