"""Фоновые задачи модуля заявок."""

from celery import shared_task

from .services import finish_abandoned


@shared_task
def finish_abandoned_submissions():
    """Заявки, автор которых бросил страницу с вопросами, всё равно уходят в редакцию."""
    return finish_abandoned()
