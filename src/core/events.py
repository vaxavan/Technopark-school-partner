"""Запись событий и доставка уведомлений по ним."""

import logging

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


def deliver_pending(limit=100):
    """Обрабатывает необработанные события. Повторяет до 5 раз."""
    done = 0
    pending = Event.objects.filter(processed_at__isnull=True, attempts__lt=5).order_by("created_at")[:limit]
    for event in pending:
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
        event.attempts += 1
        event.save(update_fields=["processed_at", "last_error", "attempts"])
    return done
