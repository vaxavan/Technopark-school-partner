"""Запросы для экранов (только чтение). Общие для шаблонов и REST API."""

from django.conf import settings
from django.utils import timezone

from core.models import Event
from materials.models import Material, MaterialStatus
from partners.interfaces.report import summary
from partners.models import PlacementRecord
from publishing.models import Publication, PublicationStatus
from submissions.models import Submission, SubmissionStatus


def dashboard(org, limit=10):
    subs = Submission.objects.filter(organization=org)
    mats = Material.objects.filter(organization=org)
    now = timezone.now()
    ready_subs = subs.filter(status=SubmissionStatus.NEW, questions_finished=True)
    return {
        "new_subs": ready_subs[:limit],
        "waiting_subs": subs.filter(status=SubmissionStatus.WAITING_AUTHOR)[:limit],
        "on_approval": mats.filter(status=MaterialStatus.ON_APPROVAL)[:limit],
        "preparing": mats.filter(status=MaterialStatus.PREPARING)[:limit],
        "errors": mats.filter(status=MaterialStatus.PUBLISH_ERROR)[:limit],
        "upcoming": Publication.objects.filter(material__organization=org, status=PublicationStatus.SCHEDULED)
                    .select_related("material").order_by("scheduled_at")[:limit],
        "disputed": PlacementRecord.objects.filter(organization=org, status="disputed")
                    .select_related("school", "material")[:limit],
        "counts": {
            "new": ready_subs.count(),
            "approval": mats.filter(status=MaterialStatus.ON_APPROVAL).count(),
            "scheduled": mats.filter(status=MaterialStatus.SCHEDULED).count(),
            "published_month": mats.filter(status=MaterialStatus.PUBLISHED,
                                           updated_at__gte=now - timezone.timedelta(days=30)).count(),
        },
        "partners": summary(org),
        "events": Event.objects.filter(organization=org)[:8],
        "public_url": f"{settings.SITE_URL}/f/{org.code}/",
    }
