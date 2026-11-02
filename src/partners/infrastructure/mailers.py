"""Письма школам: материал для размещения и напоминание."""

import os

from django.conf import settings
from django.template.loader import render_to_string

from integrations.mail import send_one
from partners.application.ports import MaterialForPartners
from partners.domain.entities import Partner, Placement

from .models import PlacementRecord


def _links(placement: Placement):
    token = PlacementRecord.objects.only("report_token").get(pk=placement.id).report_token
    return {
        "report_url": f"{settings.SITE_URL}/p/{token}/",
        "unsubscribe_url": f"{settings.SITE_URL}/p/{token}/unsubscribe/",
    }


MAX_ATTACH_BYTES = 10 * 1024 * 1024   # после кодирования письма это ~14 МБ — проходит у почтовых сервисов


def _fit_attachments(paths):
    """Фото, которые помещаются в письмо; остальные — по запросу (почта не принимает большие письма)."""
    chosen, total, skipped = [], 0, 0
    for path in paths:
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if total + size > MAX_ATTACH_BYTES:
            skipped += 1
            continue
        chosen.append(path)
        total += size
    return chosen, skipped


class DjangoPartnerMailer:
    def __init__(self, org_name):
        self.org_name = org_name

    def send_material(self, partner: Partner, material: MaterialForPartners, placement: Placement):
        links = _links(placement)
        attachments, skipped = _fit_attachments(material.attachments)
        body = render_to_string("emails/partner_material.txt", {
            "school": partner, "material": material, "org_name": self.org_name, "attachments": attachments,
            "skipped_photos": skipped, **links})
        send_one(partner.email, f"Материал для размещения: {material.title}", body,
                 attachments=attachments, unsubscribe_url=links["unsubscribe_url"])

    def send_reminder(self, partner: Partner, material: MaterialForPartners, placement: Placement):
        links = _links(placement)
        body = render_to_string("emails/partner_reminder.txt", {
            "school": partner, "material": material, "org_name": self.org_name,
            "number": placement.reminders_sent + 1, **links})
        send_one(partner.email, f"Напоминание: {material.title}", body,
                 unsubscribe_url=links["unsubscribe_url"])
