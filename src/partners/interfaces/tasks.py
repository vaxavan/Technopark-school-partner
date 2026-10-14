"""Задачи планировщика модуля partners."""

import logging

from celery import shared_task

from accounts.models import Organization
from core.events import record
from partners import container

log = logging.getLogger(__name__)


@shared_task
def dispatch_material(material_id):
    from materials.models import Material

    material = Material.objects.select_related("organization").get(pk=material_id)
    report = container.dispatch_to_partners(material.organization.name)(material_id)
    record(material.organization, "partners.dispatched", obj=material,
           message=f"Материал разослан школам: {report.sent}; без e-mail: {len(report.without_email)}; "
                   f"ошибок: {len(report.failed)}",
           sent=report.sent, without_email=report.without_email, failed=report.failed)
    return report.sent


@shared_task
def crawl_all(organization_id=None, school_id=None):
    report = container.detect_placement()(organization_id, partner_id=school_id)
    orgs = Organization.objects.filter(pk=organization_id) if organization_id else Organization.objects.all()
    for org in orgs:
        record(org, "partners.crawled",
               message=f"Проверено размещений: {report.checked}; подтверждено: {report.confirmed}; "
                       f"требует решения: {report.disputed}; ошибок источников: {len(report.source_errors)}",
               checked=report.checked, confirmed=report.confirmed, disputed=report.disputed,
               source_errors=report.source_errors[:50])
    return report.checked


@shared_task
def send_reminders(organization_id=None):
    total = 0
    orgs = Organization.objects.filter(pk=organization_id) if organization_id else Organization.objects.all()
    for org in orgs:
        sent = container.remind_partner(org.name)(org.id)
        if sent:
            record(org, "partners.reminded", message=f"Отправлено напоминаний: {sent}", sent=sent)
        total += sent
    return total
