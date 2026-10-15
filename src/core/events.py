"""Запись событий и доставка уведомлений по ним."""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Event

log = logging.getLogger(__name__)

# kind -> функция(event), которая отправляет уведомление. Регистрируют модули.
_HANDLERS = {}


def handler(kind):
    def wrap(fn):
        _HANDLERS[kind] = fn
        return fn

    return wrap


def record(organization, kind, *, actor=None, obj=None, message="", **data):
    event = Event.objects.create(
        organization=organization,
        kind=kind,
        actor=actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(getattr(obj, "pk", "")) if obj is not None else "",
        message=message[:500],
        data=data,
        # события без обработчика сразу считаются обработанными
        processed_at=None if kind in _HANDLERS else timezone.now(),
    )
    if kind in _HANDLERS:
        from .tasks import deliver_events

        transaction.on_commit(lambda: deliver_events.delay())
    return event


MAX_ATTEMPTS = 8
BACKOFF_MINUTES = [1, 5, 15, 60, 180, 360, 720]   # почта недоступна — пробуем ещё почти сутки


def deliver_pending(limit=100):
    """Обрабатывает необработанные события. Каждое событие «захватывается» блокировкой строки,
    поэтому два одновременно работающих процесса не отправят одно письмо дважды."""
    from django.db.models import Q

    now = timezone.now()
    ids = list(Event.objects.filter(processed_at__isnull=True, attempts__lt=MAX_ATTEMPTS)
               .filter(Q(retry_at__isnull=True) | Q(retry_at__lte=now))
               .order_by("created_at").values_list("pk", flat=True)[:limit])
    done = 0
    for pk in ids:
        with transaction.atomic():
            event = (Event.objects.select_for_update(skip_locked=True)
                     .filter(pk=pk, processed_at__isnull=True).first())
            if event is None:
                continue  # уже обрабатывает другой процесс
            fn = _HANDLERS.get(event.kind)
            try:
                if fn:
                    fn(event)
                event.processed_at = timezone.now()
                event.last_error = ""
                done += 1
            except Exception as exc:  # уведомление не должно ронять систему
                log.exception("Не удалось обработать событие %s", event.pk)
                event.last_error = str(exc)[:2000]
                pause = BACKOFF_MINUTES[min(event.attempts, len(BACKOFF_MINUTES) - 1)]
                event.retry_at = timezone.now() + timedelta(minutes=pause)
            event.attempts += 1
            event.save(update_fields=["processed_at", "last_error", "attempts", "retry_at", "data"])
    return done
