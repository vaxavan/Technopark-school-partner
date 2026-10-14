"""Сценарии публикации: постановка в расписание и выполнение без повторов."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.events import record
from core.ports import PlatformError
from materials.models import Channel, Material, MaterialStatus

from .evidence import capture_evidence
from .models import Publication, PublicationStatus
from .platforms import platform_for

log = logging.getLogger(__name__)

STUCK_AFTER = timedelta(minutes=10)
PUBLIC_CHANNELS = (Channel.VK, Channel.MAX, Channel.DZEN)


def schedule_publications(material: Material):
    """Создаёт записи публикаций по выбранным площадкам (вызывается при утверждении)."""
    when = material.scheduled_at or timezone.now()
    for channel in material.channels:
        if channel not in PUBLIC_CHANNELS:
            continue
        variant = material.current_variant(channel)
        pub, created = Publication.objects.get_or_create(
            material=material, channel=channel,
            defaults={"scheduled_at": when, "variant": variant},
        )
        if not created and pub.status in (PublicationStatus.SCHEDULED, PublicationStatus.FAILED):
            pub.scheduled_at, pub.variant, pub.status = when, variant, PublicationStatus.SCHEDULED
            pub.save(update_fields=["scheduled_at", "variant", "status"])


def unschedule(material: Material):
    material.publications.filter(status=PublicationStatus.SCHEDULED).delete()


def publish_one(publication_id):
    """Публикует одну запись. Защита от повтора: запись «захватывается» переводом в
    состояние «публикуется» под блокировкой; повторно взять её нельзя. Если процесс упал
    посередине, запись через 10 минут уходит в «проверить вручную», а не публикуется снова."""
    with transaction.atomic():
        pub = (Publication.objects.select_for_update(skip_locked=True)
               .select_related("material__organization", "material__submission", "variant")
               .filter(pk=publication_id, status=PublicationStatus.SCHEDULED).first())
        if pub is None:
            return None
        pub.status = PublicationStatus.PUBLISHING
        pub.started_at = timezone.now()
        pub.attempts += 1
        pub.save(update_fields=["status", "started_at", "attempts"])

    material = pub.material
    org = material.organization
    variant = pub.variant or material.current_variant(pub.channel)
    text = variant.text if variant else material.title
    images = [p.image.path for p in material.submission.photos.all() if p.image]

    try:
        if pub.channel == Channel.DZEN:
            # Дзен сам забирает материал из RSS-ленты; публикация = включение в ленту
            url = f"{settings.SITE_URL}/n/{org.code}/{material.public_id}/"
            result_id, demo = str(material.public_id), False
        else:
            platform = platform_for(org, pub.channel)
            result = platform.publish(text, images)
            demo = platform.demo
            result_id = result.external_id
            url = result.url or f"{settings.SITE_URL}/demo/{pub.channel}/{material.public_id}/"
    except PlatformError as exc:
        pub.status = PublicationStatus.FAILED
        pub.error = str(exc)[:2000]
        pub.save(update_fields=["status", "error"])
        record(org, "publication.failed", obj=material, message=f"{pub.get_channel_display()}: {exc}",
               channel=pub.channel)
        _sync_material_status(material)
        return pub

    pub.status = PublicationStatus.PUBLISHED
    pub.published_at = timezone.now()
    pub.external_id, pub.url, pub.demo, pub.error = result_id, url, demo, ""
    pub.save(update_fields=["status", "published_at", "external_id", "url", "demo", "error"])
    record(org, "publication.published", obj=material,
           message=f"{pub.get_channel_display()}{' (демо)' if demo else ''}: {url}", channel=pub.channel, url=url)
    from .tasks import capture_publication_evidence

    transaction.on_commit(lambda: capture_publication_evidence.delay(pub.id))
    _sync_material_status(material)
    return pub


def _sync_material_status(material: Material):
    """Материал «опубликован», когда опубликованы все площадки; «ошибка», если есть сбой."""
    statuses = set(material.publications.values_list("status", flat=True))
    material.refresh_from_db(fields=["status"])
    if statuses and statuses <= {PublicationStatus.PUBLISHED}:
        new = MaterialStatus.PUBLISHED
    elif PublicationStatus.FAILED in statuses or PublicationStatus.NEEDS_CHECK in statuses:
        new = MaterialStatus.PUBLISH_ERROR
    else:
        return
    if material.status != new:
        material.status = new
        material.save(update_fields=["status", "updated_at"])
        if new == MaterialStatus.PUBLISHED and Channel.PARTNERS in material.channels:
            from partners.tasks import dispatch_material

            transaction.on_commit(lambda: dispatch_material.delay(material.id))


def due_publications(now=None):
    now = now or timezone.now()
    return list(Publication.objects.filter(status=PublicationStatus.SCHEDULED, scheduled_at__lte=now,
                                           material__status__in=[MaterialStatus.SCHEDULED,
                                                                 MaterialStatus.PUBLISH_ERROR])
                .values_list("id", flat=True))


def release_stuck(now=None):
    now = now or timezone.now()
    stuck = Publication.objects.filter(status=PublicationStatus.PUBLISHING, started_at__lt=now - STUCK_AFTER)
    for pub in stuck.select_related("material__organization"):
        pub.status = PublicationStatus.NEEDS_CHECK
        pub.error = "Публикация прервалась. Проверьте площадку вручную: пост мог выйти."
        pub.save(update_fields=["status", "error"])
        record(pub.material.organization, "publication.needs_check", obj=pub.material, message=pub.error)
        _sync_material_status(pub.material)


def retry(material: Material):
    """Повтор после ошибки: только для площадок с ошибкой, опубликованные не трогаем."""
    material.publications.filter(status__in=[PublicationStatus.FAILED]).update(
        status=PublicationStatus.SCHEDULED, scheduled_at=timezone.now(), error="")


def mark_published_manually(publication: Publication, url):
    publication.status = PublicationStatus.PUBLISHED
    publication.url = url
    publication.published_at = publication.published_at or timezone.now()
    publication.error = ""
    publication.save()
    _sync_material_status(publication.material)


def capture_for_publication(publication_id):
    pub = Publication.objects.select_related("material__organization").get(pk=publication_id)
    if pub.evidence.exists():
        return
    capture_evidence(pub.material.organization, pub.material, pub.url, publication=pub,
                     note="демо-публикация" if pub.demo else "")
