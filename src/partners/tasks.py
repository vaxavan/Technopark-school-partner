# Celery ищет задачи в <app>.tasks; сами задачи — в слое interfaces.
from partners.interfaces.tasks import crawl_all, dispatch_material, send_reminders  # noqa: F401
