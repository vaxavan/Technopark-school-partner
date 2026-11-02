from celery import shared_task

from .events import deliver_pending


@shared_task
def deliver_events():
    return deliver_pending()
