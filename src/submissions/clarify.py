"""Уточняющие вопросы автору (рисунок 6 и приложение А отчёта).

Не более трёх раундов. Модель возвращает JSON по схеме; ответ проверяется pydantic.
Если модель не ответила за 15 секунд или дважды ответила не по схеме — вопросы
пропускаются, и заявка уходит администратору как есть."""

import logging
from typing import Literal

from django.utils import timezone
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.ports import LLMError
from integrations.llm import extract_json, get_llm

from .models import ClarificationRound, Submission

log = logging.getLogger(__name__)

MAX_ROUNDS = 3
TIMEOUT_SECONDS = 15

SYSTEM = """Ты помощник редакции технопарка. Тебе дают заявку на новость. Проверь, хватает ли сведений
для публикации. Обязательно должно быть: что произошло, когда, где, кто участвовал, чем закончилось.
Желательно: сколько было участников; если был конкурс — кто победил.
Если всего хватает — верни complete: true и пустой список вопросов.
Если чего-то нет — задай не больше трёх коротких дружелюбных вопросов на «вы», только о недостающем.
Не спрашивай о том, на что автор уже ответил.
Если на вопрос можно ответить «да» или «нет» — тип yes_no; если есть разумные варианты — тип choice
с вариантами (options). Иначе тип text. Ничего не придумывай.
Отвечай только JSON по схеме:
{"complete": bool, "missing": [строки], "questions": [{"id": "q1", "type": "text|yes_no|choice",
"text": "вопрос", "options": ["вариант", ...]}]}"""


class Question(BaseModel):
    id: str
    type: Literal["text", "yes_no", "choice"]
    text: str
    options: list[str] = []

    @field_validator("text")
    @classmethod
    def not_empty(cls, v):
        if not v.strip():
            raise ValueError("пустой вопрос")
        return v.strip()


class Completeness(BaseModel):
    complete: bool
    missing: list[str] = []
    questions: list[Question] = Field(default_factory=list, max_length=3)


def ask_model(submission: Submission, llm=None) -> Completeness | None:
    """Два запроса максимум; None — значит вопросы пропускаем."""
    llm = llm or get_llm(submission.organization)
    user = submission.full_text_for_model()
    for attempt in range(2):
        try:
            reply = llm.complete(SYSTEM, user, json_mode=True, lite=True, timeout=TIMEOUT_SECONDS, temperature=0.1)
        except LLMError as exc:
            log.warning("Уточняющие вопросы пропущены: %s", exc)
            return None
        try:
            result = Completeness.model_validate_json(extract_json(reply))
        except ValidationError:
            log.info("Ответ модели не по схеме (попытка %s)", attempt + 1)
            continue
        # переименуем id, чтобы они были уникальны в пределах заявки
        for i, q in enumerate(result.questions, start=1):
            q.id = f"q{i}"
            if q.type == "choice" and len(q.options) < 2:
                q.type = "text"
        return result
    return None


def next_round(submission: Submission, llm=None) -> ClarificationRound | None:
    """Создаёт следующий раунд вопросов или завершает уточнение (возвращает None)."""
    done_rounds = submission.rounds.count()
    if submission.questions_finished or done_rounds >= MAX_ROUNDS:
        _finish(submission)
        return None
    result = ask_model(submission, llm)
    if result is None or result.complete or not result.questions:
        if result is not None:
            submission.completeness = {"complete": result.complete, "missing": result.missing}
        else:
            submission.completeness = {"complete": None, "missing": [], "note": "проверка полноты не выполнена"}
        _finish(submission)
        return None
    submission.completeness = {"complete": False, "missing": result.missing}
    submission.save(update_fields=["completeness", "updated_at"])
    return ClarificationRound.objects.create(
        submission=submission, number=done_rounds + 1,
        questions=[q.model_dump() for q in result.questions])


def save_answers(rnd: ClarificationRound, answers: dict, skipped=False):
    clean = {}
    for q in rnd.questions:
        value = (answers.get(q["id"]) or "").strip()
        if q["type"] == "yes_no" and value not in ("да", "нет"):
            value = ""
        if q["type"] == "choice" and value and value not in q.get("options", []):
            value = value[:200]
        clean[q["id"]] = value[:1000]
    rnd.answers = clean
    rnd.skipped = skipped or not any(clean.values())
    rnd.answered_at = timezone.now()
    rnd.save()


def _finish(submission: Submission):
    if not submission.questions_finished:
        submission.questions_finished = True
        submission.save(update_fields=["questions_finished", "completeness", "updated_at"])
