"""Сценарии публичной части: подача заявки и уточняющие вопросы.
Их вызывают и страницы на шаблонах, и REST API — логика не дублируется."""

from django.db import transaction
from django.utils import timezone

from core.events import record
from core.models import Event

from .clarify import next_round, save_answers
from .models import Submission, SubmissionPhoto, SubmissionStatus


def create_submission(org, data, photos=()):
    with transaction.atomic():
        sub = Submission.objects.create(
            organization=org, title=data["title"], text=data["text"], event_date=data.get("event_date", ""),
            place=data.get("place", ""), author_name=data["author_name"], author_email=data["author_email"],
            author_phone=data.get("author_phone", ""), author_org=data.get("author_org", ""),
            consent_at=timezone.now())
        for f in photos:
            SubmissionPhoto.objects.create(submission=sub, image=f)
    return sub


def current_round(sub, llm=None):
    """Открытый раунд вопросов (создаёт следующий при необходимости) или None, если уточнение закончено."""
    if sub.questions_finished or sub.status != SubmissionStatus.NEW:
        return None
    rnd = sub.rounds.filter(answered_at__isnull=True).first()
    return rnd or next_round(sub, llm)


def answer_round(sub, answers, skip=False):
    rnd = sub.rounds.filter(answered_at__isnull=True).first()
    if rnd is None:
        return
    save_answers(rnd, {} if skip else answers, skipped=skip)
    if skip:
        sub.questions_finished = True
        sub.save(update_fields=["questions_finished", "updated_at"])


def finish_submission(sub):
    """Завершает уточнение и один раз записывает событие «заявка получена» (письма автору и администратору)."""
    if not sub.questions_finished:
        sub.questions_finished = True
        sub.save(update_fields=["questions_finished", "updated_at"])
    if not Event.objects.filter(kind="submission.received", object_type="Submission", object_id=str(sub.pk)).exists():
        record(sub.organization, "submission.received", obj=sub, message=f"Новая заявка №{sub.number}: {sub.title}",
               submission_id=sub.pk)
