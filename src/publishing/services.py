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
PUBLIC_CHANNELS = (Channel.VK, Channel.TELEGRAM, Channel.MAX, Channel.DZEN)


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
        # блокируем только саму запись публикации (of=self): связанные таблицы через внешнее
        # соединение PostgreSQL блокировать не позволяет
        pub = (Publication.objects.select_for_update(skip_locked=True, of=("self",))
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
            # у демо-площадки адрес — наша страница; у настоящей площадки без ссылки (закрытый канал)
            # адрес оставляем пустым, чтобы «доказательством» не стала демо-страница
            url = result.url or (f"{settings.SITE_URL}/demo/{pub.channel}/{material.public_id}/" if demo else "")
    except Exception as exc:  # любая ошибка — «ошибка публикации», которую можно повторить, а не зависание
        if not isinstance(exc, PlatformError):
            log.exception("Сбой публикации %s", pub.pk)
        pub.status = PublicationStatus.FAILED
        pub.error = str(exc)[:2000] or exc.__class__.__name__
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
    if url:
        from .tasks import capture_publication_evidence

        transaction.on_commit(lambda: capture_publication_evidence.delay(pub.id))
    _sync_material_status(material)
    return pub


def _sync_material_status(material: Material):
    """Материал «опубликован», когда опубликованы все площадки; «ошибка», если есть сбой."""
    statuses = set(material.publications.values_list("status", flat=True))
    material.refresh_from_db(fields=["status"])
    if not statuses and material.status == MaterialStatus.PUBLISH_ERROR:
        new = MaterialStatus.PUBLISHED   # все проблемные площадки убраны — публиковать больше нечего
    elif statuses and statuses <= {PublicationStatus.PUBLISHED}:
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
    """Повтор после ошибки: только для площадок с ошибкой или «проверить вручную»; опубликованные
    не трогаем. Для «проверить вручную» администратор сначала смотрит площадку: если пост вышел,
    он нажимает «Вышло», иначе — «Повторить»."""
    material.publications.filter(status__in=[PublicationStatus.FAILED, PublicationStatus.NEEDS_CHECK]).update(
        status=PublicationStatus.SCHEDULED, scheduled_at=timezone.now(), error="")


def drop_publication(publication: Publication):
    """Площадку решили не использовать (например, Telegram не принимает фото): убираем её,
    чтобы материал не висел в ошибке и рассылка школам не ждала."""
    material = publication.material
    material.channels = [c for c in material.channels if c != publication.channel]
    material.save(update_fields=["channels", "updated_at"])
    publication.delete()
    _sync_material_status(material)


def release_partner_only_due(now=None):
    """Материалы только для рассылки школам: публикаций нет, поэтому в назначенное время
    материал считается вышедшим и уходит школам."""
    now = now or timezone.now()
    due = Material.objects.filter(status=MaterialStatus.SCHEDULED, scheduled_at__lte=now,
                                  publications__isnull=True)
    count = 0
    for material in due:
        with transaction.atomic():
            m = Material.objects.select_for_update(skip_locked=True).filter(
                pk=material.pk, status=MaterialStatus.SCHEDULED).first()
            if m is None or m.publications.exists():
                continue
            m.status = MaterialStatus.PUBLISHED
            m.save(update_fields=["status", "updated_at"])
            record(m.organization, "material.published", obj=m, message=f"Вышло (рассылка школам): {m.title}")
            if Channel.PARTNERS in m.channels:
                from partners.tasks import dispatch_material

                transaction.on_commit(lambda mid=m.pk: dispatch_material.delay(mid))
            count += 1
    return count


def mark_published_manually(publication: Publication, url):
    publication.status = PublicationStatus.PUBLISHED
    publication.url = url
    publication.published_at = publication.published_at or timezone.now()
    publication.error = ""
    publication.save()
    _sync_material_status(publication.material)


def capture_for_publication(publication_id):
    pub = Publication.objects.select_related("material__organization").get(pk=publication_id)
    if pub.evidence.exists() or not pub.url:
        return
    capture_evidence(pub.material.organization, pub.material, pub.url, publication=pub,
                     note="демо-публикация" if pub.demo else "")


def save_platform_account(organization, channel, *, target_id=None, token=None, enabled=None, clear=False, actor=None):
    """Настройки площадки организации. Токен шифруется в базе; пустой token — не менять."""
    from .models import PlatformAccount

    from .models import PLATFORM_CHANNELS

    if channel not in PLATFORM_CHANNELS:
        raise ValueError("Неизвестная площадка")
    acc, _ = PlatformAccount.objects.get_or_create(organization=organization, channel=channel)
    if target_id is not None:
        acc.target_id = str(target_id).strip()
    if enabled is not None:
        acc.enabled = bool(enabled)
    if clear:
        acc.token = ""
    elif token:
        acc.token = token.strip()
    acc.save()
    record(organization, "platform.updated", actor=actor, message=f"Изменены настройки {Channel(channel).label}")
    return acc


def integration_modes(organization):
    from integrations.llm import llm_mode
    from integrations.mail import mail_mode

    return {
        "llm": llm_mode(organization),
        "mail": mail_mode(),
        "vk": "demo" if platform_for(organization, Channel.VK).demo else "live",
        "max": "demo" if platform_for(organization, Channel.MAX).demo else "live",
        "telegram": "demo" if platform_for(organization, Channel.TELEGRAM).demo else "live",
        "vk_monitor": bool(settings.VK_SERVICE_TOKEN),
        "snapshot": settings.SNAPSHOT_BACKEND,
        "rss_url": f"{settings.SITE_URL}/rss/{organization.code}.xml",
        "public_form_url": f"{settings.SITE_URL}/f/{organization.code}/",
    }


def save_llm_account(organization, *, api_key=None, folder_id=None, model=None, model_lite=None, clear=False,
                     actor=None):
    """Доступ к YandexGPT в «Настройках». Пустой api_key — не менять."""
    from core.models import LLMAccount

    acc, _ = LLMAccount.objects.get_or_create(organization=organization)
    if clear:
        acc.api_key = ""
    elif api_key:
        acc.api_key = api_key.strip()
    for field, value in (("folder_id", folder_id), ("model", model), ("model_lite", model_lite)):
        if value is not None:
            setattr(acc, field, str(value).strip())
    acc.save()
    record(organization, "llm.updated", actor=actor, message="Изменены настройки языковой модели")
    return acc
