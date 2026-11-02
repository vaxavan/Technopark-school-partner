# Celery ищет задачи в <app>.tasks; сами задачи — в слое interfaces.
from partners.interfaces.tasks import (  # noqa: F401
    capture_school_evidence, crawl_all, dispatch_material, run_search, search_school, send_reminders)
