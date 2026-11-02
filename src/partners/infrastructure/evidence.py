"""Адаптер «Доказательства» для модуля partners.

Снимок страницы школы может занимать до минуты (особенно если сайт медленный), поэтому он
делается фоновой задачей — обход и ответ агенту обхода не ждут его."""

from django.db import transaction


class DjangoEvidenceStore:
    def capture(self, material_id, partner_id, url):
        from partners.interfaces.tasks import capture_school_evidence

        transaction.on_commit(lambda: capture_school_evidence.delay(material_id, partner_id, url))
