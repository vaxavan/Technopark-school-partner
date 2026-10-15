"""Простой планировщик для запуска без Docker и Redis: раз в минуту публикует
наступившие материалы и рассылает уведомления, раз в сутки обходит сайты школ.

    python manage.py scheduler
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.events import deliver_pending
from partners.interfaces.tasks import crawl_all, send_reminders
from publishing.tasks import publish_due
from submissions.services import finish_abandoned


class Command(BaseCommand):
    help = "Планировщик без Celery beat (для локального запуска)"

    def handle(self, *args, **opts):
        self.stdout.write("Планировщик запущен. Остановить: Ctrl+C")
        last_daily = None
        while True:
            n = publish_due()
            finish_abandoned()
            d = deliver_pending()
            if n or d:
                self.stdout.write(f"{timezone.localtime():%H:%M} опубликовано: {n}, уведомлений: {d}")
            now = timezone.localtime()
            if now.hour == settings.CRAWL_HOUR and last_daily != now.date():
                last_daily = now.date()
                crawl_all()
                send_reminders()
            time.sleep(60)
