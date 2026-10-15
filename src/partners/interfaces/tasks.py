"""Задачи планировщика модуля partners."""

import logging

from celery import shared_task

from accounts.models import Organization
from core.events import record
from partners import container

log = logging.getLogger(__name__)


# Имена заданы явно: задачи лежат в partners.interfaces.tasks, а расписание (settings.CELERY_BEAT_SCHEDULE)
# и Celery обращаются к ним как partners.tasks.* — без явного имени задача «не зарегистрирована».
@shared_task(name="partners.tasks.dispatch_material")
def dispatch_material(material_id):
    from materials.models import Material

    material = Material.objects.select_related("organization").get(pk=material_id)
    report = container.dispatch_to_partners(material.organization.name)(material_id)
    record(material.organization, "partners.dispatched", obj=material,
           message=f"Материал разослан школам: {report.sent}; без e-mail: {len(report.without_email)}; "
                   f"ошибок: {len(report.failed)}",
           sent=report.sent, without_email=report.without_email, failed=report.failed)
    return report.sent


@shared_task(name="partners.tasks.crawl_all")
def crawl_all(organization_id=None, school_id=None, force=False):
    """Обход по организациям. Если у организации работает агент обхода (отчитывался за последние
    36 часов), сервер её не обходит — сайты школ проверяет агент. force=True — обойти всё равно
    (кнопка «Проверить сейчас» в интерфейсе)."""
    from partners.models import CrawlAgent

    orgs = Organization.objects.filter(pk=organization_id) if organization_id else Organization.objects.all()
    checked = 0
    for org in orgs:
        agent = CrawlAgent.objects.filter(organization=org).first()
        if agent and agent.is_active and not force:
            continue
        report = container.detect_placement()(org.id, partner_id=school_id)
        checked += report.checked
        if not report.checked:
            continue
        record(org, "partners.crawled",
               message=f"Проверено размещений: {report.checked}; подтверждено: {report.confirmed}; "
                       f"требует решения: {report.disputed}; не проверено (сайт не открылся): {report.unchecked}; "
                       f"ошибок источников: {len(report.source_errors)}",
               checked=report.checked, confirmed=report.confirmed, disputed=report.disputed,
               unchecked=report.unchecked, source_errors=report.source_errors[:50])
    return checked


@shared_task(name="partners.tasks.capture_school_evidence", autoretry_for=(Exception,), retry_backoff=120,
             max_retries=2)
def capture_school_evidence(material_id, school_id, url):
    from materials.models import Material
    from publishing.evidence import capture_evidence

    material = Material.objects.select_related("organization").get(pk=material_id)
    capture_evidence(material.organization, material, url, school_id=school_id, note="размещение у школы")


@shared_task(name="partners.tasks.send_reminders")
def send_reminders(organization_id=None):
    total = 0
    orgs = Organization.objects.filter(pk=organization_id) if organization_id else Organization.objects.all()
    for org in orgs:
        sent = container.remind_partner(org.name)(org.id)
        if sent:
            record(org, "partners.reminded", message=f"Отправлено напоминаний: {sent}", sent=sent)
        total += sent
    return total
