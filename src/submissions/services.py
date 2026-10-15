"""Сценарии публичной части: подача заявки и уточняющие вопросы.
Их вызывают и страницы на шаблонах, и REST API — логика не дублируется.

Состояния уточнения для автора:
  thinking  — модель проверяет заявку (автор видит экран ожидания);
  questions — есть раунд вопросов, ждём ответа автора;
  done      — уточнение закончено, заявка ушла в редакцию.
Запрос к модели выполняет тот, кто первым «захватит» заявку (queued → running), поэтому
два одновременных запроса — повторное нажатие, вторая вкладка — не создают два раунда."""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from core.events import record
from core.models import Event

from .clarify import MAX_ROUNDS, next_round, save_answers
from .models import Submission, SubmissionPhoto, SubmissionStatus

THINKING, QUESTIONS, DONE = "thinking", "questions", "done"
STALE_AFTER = timedelta(seconds=90)        # запрос к модели завис — идём дальше без вопросов
DUPLICATE_WINDOW = timedelta(minutes=30)   # та же заявка от того же автора — не дубль-новость
ABANDONED_AFTER = timedelta(minutes=30)    # автор закрыл страницу с вопросами


def create_submission(org, data, photos=(), form_token=None):
    """Возвращает (заявка, создана_ли). Повторная отправка той же формы (кнопка нажата дважды,
    «назад» и снова «отправить», обновление страницы) возвращает уже созданную заявку."""
    if form_token:
        existing = Submission.objects.filter(organization=org, form_token=form_token).first()
        if existing:
            return existing, False
    since = timezone.now() - DUPLICATE_WINDOW
    existing = (Submission.objects.filter(organization=org, created_at__gte=since,
                                          author_email__iexact=data["author_email"].strip(),
                                          title=data["title"].strip(), text=data["text"].strip())
                .order_by("-created_at").first())
    if existing:
        return existing, False
    try:
        with transaction.atomic():
            sub = Submission.objects.create(
                organization=org, title=data["title"].strip(), text=data["text"].strip(),
                event_date=data.get("event_date", ""), place=data.get("place", ""),
                author_name=data["author_name"], author_email=data["author_email"].strip(),
                author_phone=data.get("author_phone", ""), author_org=data.get("author_org", ""),
                consent_at=timezone.now(), form_token=form_token or None,
                clarify_state="queued", clarify_started_at=timezone.now())
            for f in photos:
                SubmissionPhoto.objects.create(submission=sub, image=f)
    except IntegrityError:
        # та же форма пришла дважды одновременно (двойной щелчок) — вторая получает первую заявку
        existing = Submission.objects.filter(organization=org, form_token=form_token).first() if form_token else None
        if existing is None:
            raise
        return existing, False
    return sub, True


def open_round(sub):
    return sub.rounds.filter(answered_at__isnull=True).first()


def clarify_state(sub):
    """Текущее состояние уточнения (без обращения к модели)."""
    sub.refresh_from_db()
    if sub.questions_finished or sub.status != SubmissionStatus.NEW:
        return DONE
    if open_round(sub):
        return QUESTIONS
    if sub.clarify_state == "running" and sub.clarify_started_at \
            and timezone.now() - sub.clarify_started_at > STALE_AFTER:
        finish_submission(sub, note="модель не ответила вовремя — вопросы пропущены")
        return DONE
    if sub.clarify_state in ("queued", "running"):
        return THINKING
    # нет открытого раунда и ничего не ждём — значит, автор ответил и нужно спросить модель снова
    _queue(sub)
    return THINKING


def prepare_round(sub, llm=None):
    """Спрашивает модель, если заявка ждёт этого. Возвращает новое состояние."""
    state = clarify_state(sub)
    if state != THINKING:
        return state
    claimed = Submission.objects.filter(pk=sub.pk, clarify_state="queued").update(
        clarify_state="running", clarify_started_at=timezone.now(), updated_at=timezone.now())
    if not claimed:
        return THINKING  # модель уже спрашивает другой запрос — ждём его
    try:
        sub.refresh_from_db()
        next_round(sub, llm)
    finally:
        Submission.objects.filter(pk=sub.pk).update(clarify_state="")
    state = clarify_state(sub)
    if state == DONE:
        finish_submission(sub)
    return state


def answer_round(sub, answers, skip=False, round_number=None):
    """round_number — номер раунда, на который отвечает автор. Старая страница (кнопка «Назад»,
    повторная отправка) не должна записать ответы первого раунда во второй."""
    rnd = open_round(sub)
    if rnd is None or (round_number and str(round_number) != str(rnd.number)):
        return clarify_state(sub)
    save_answers(rnd, {} if skip else answers, skipped=skip)
    sub.refresh_from_db()
    if sub.questions_finished:   # ответ пришёл позже срока — сохранили, заявка уже в редакции
        return DONE
    if skip or sub.rounds.count() >= MAX_ROUNDS:
        finish_submission(sub)
        return DONE
    _queue(sub)
    return THINKING


def _queue(sub):
    now = timezone.now()
    Submission.objects.filter(pk=sub.pk).update(clarify_state="queued", clarify_started_at=now, updated_at=now)
    sub.clarify_state = "queued"


def finish_submission(sub, note=""):
    """Завершает уточнение и один раз записывает событие «заявка получена» (письма автору и администратору)."""
    sub.refresh_from_db()
    fields = []
    if not sub.questions_finished:
        sub.questions_finished = True
        fields.append("questions_finished")
    if sub.clarify_state:
        sub.clarify_state = ""
        fields.append("clarify_state")
    if note and not sub.completeness.get("note"):
        sub.completeness = {**sub.completeness, "note": note}
        fields.append("completeness")
    if fields:
        sub.save(update_fields=fields + ["updated_at"])
    if not Event.objects.filter(kind="submission.received", object_type="Submission", object_id=str(sub.pk)).exists():
        record(sub.organization, "submission.received", obj=sub, message=f"Новая заявка №{sub.number}: {sub.title}",
               submission_id=sub.pk)


def finish_abandoned():
    """Автор закрыл страницу с вопросами — через полчаса заявка всё равно уходит в редакцию."""
    since = timezone.now() - ABANDONED_AFTER
    subs = Submission.objects.filter(questions_finished=False, status=SubmissionStatus.NEW, updated_at__lt=since)
    count = 0
    for sub in subs:
        finish_submission(sub, note="автор не ответил на вопросы")
        count += 1
    return count
