from django.db import models
from django.utils import timezone

from core.models import EncryptedTextField
from materials.models import Channel


# площадки, для которых нужен токен (Дзен работает через RSS без токена)
PLATFORM_CHANNELS = (Channel.VK, Channel.TELEGRAM, Channel.MAX)


class PlatformAccount(models.Model):
    """Аккаунт площадки организации. Токен хранится в зашифрованном виде."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE,
                                     related_name="platform_accounts")
    channel = models.CharField(max_length=20, choices=[(c.value, c.label) for c in PLATFORM_CHANNELS])
    target_id = models.CharField("ID группы / канала / чата", max_length=64, blank=True)
    token = EncryptedTextField("токен", blank=True)
    enabled = models.BooleanField("включено", default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "аккаунт площадки"
        verbose_name_plural = "аккаунты площадок"
        constraints = [models.UniqueConstraint(fields=["organization", "channel"], name="uniq_platform_account")]


class PublicationStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Запланирована"
    PUBLISHING = "publishing", "Публикуется"
    PUBLISHED = "published", "Опубликована"
    FAILED = "failed", "Ошибка"
    NEEDS_CHECK = "needs_check", "Проверить вручную"


class Publication(models.Model):
    """Размещение материала на площадке технопарка. Пара (материал, площадка) уникальна —
    это и есть защита от повторной публикации при перезапуске задачи."""

    material = models.ForeignKey("materials.Material", on_delete=models.CASCADE, related_name="publications")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    variant = models.ForeignKey("materials.TextVariant", null=True, on_delete=models.SET_NULL)
    status = models.CharField(max_length=20, choices=PublicationStatus.choices, default=PublicationStatus.SCHEDULED)
    scheduled_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(max_length=128, blank=True)
    url = models.URLField(max_length=500, blank=True)
    demo = models.BooleanField("демо-режим", default=False)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        verbose_name = "публикация"
        verbose_name_plural = "публикации"
        ordering = ["scheduled_at"]
        constraints = [models.UniqueConstraint(fields=["material", "channel"], name="uniq_publication")]


def evidence_path(instance, filename):
    return f"evidence/{instance.organization.code}/{timezone.now():%Y/%m}/{filename}"


class Evidence(models.Model):
    """Доказательство публикации: адрес, время, снимок, копия страницы и контрольная сумма.
    Для размещения у школы заполнена ссылка на школу."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="evidence")
    material = models.ForeignKey("materials.Material", on_delete=models.CASCADE, related_name="evidence")
    school = models.ForeignKey("partners.School", null=True, blank=True, on_delete=models.CASCADE)
    publication = models.ForeignKey(Publication, null=True, blank=True, on_delete=models.CASCADE,
                                    related_name="evidence")
    url = models.URLField(max_length=500)
    captured_at = models.DateTimeField(default=timezone.now)
    screenshot = models.FileField(upload_to=evidence_path, blank=True)
    html_copy = models.FileField(upload_to=evidence_path, blank=True)
    checksum = models.CharField("SHA-256 копии", max_length=64, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "доказательство"
        verbose_name_plural = "доказательства"
        ordering = ["-captured_at"]
