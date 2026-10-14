"""Модели Django модуля partners (справочник школ, источники, размещения)."""

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class School(models.Model):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="schools")
    number = models.PositiveIntegerField("№", null=True, blank=True)
    territory = models.CharField("территория", max_length=120, blank=True)
    name = models.CharField("название", max_length=500)
    email = models.EmailField("e-mail для рассылки", blank=True)
    contact_person = models.CharField("ответственный", max_length=255, blank=True)
    website = models.URLField("сайт", max_length=500, blank=True)
    active = models.BooleanField("активна", default=True)
    notes = models.TextField("примечание", blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "partners"
        verbose_name = "школа-партнёр"
        verbose_name_plural = "школы-партнёры"
        ordering = ["number", "name"]

    def __str__(self):
        return self.name


class Source(models.Model):
    KIND_CHOICES = [("sitemap", "Карта сайта"), ("rss", "Лента RSS"), ("vk", "Сообщество ВКонтакте"),
                    ("html", "Раздел сайта")]

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField("тип", max_length=10, choices=KIND_CHOICES)
    url = models.URLField("адрес", max_length=500)
    link_prefix = models.CharField("путь публикаций", max_length=255, blank=True,
                                   help_text="Для раздела сайта: учитывать только ссылки, начинающиеся с этого пути")
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        app_label = "partners"
        verbose_name = "источник школы"
        verbose_name_plural = "источники школ"


class FoundPublicationRecord(models.Model):
    """Публикации, найденные на ресурсах школ при обходе (кэш для сверки и отчёта)."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="found")
    url = models.URLField(max_length=700)
    title = models.CharField(max_length=500, blank=True)
    text = models.TextField()
    source_kind = models.CharField(max_length=10)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "partners"
        constraints = [models.UniqueConstraint(fields=["school", "url"], name="uniq_found_url")]


def new_token():
    return secrets.token_urlsafe(24)


class PlacementRecord(models.Model):
    STATUS_CHOICES = [
        ("expected", "Ожидается"),
        ("confirmed", "Подтверждено"),
        ("disputed", "Требует решения"),
        ("not_found", "Не найдено"),
        ("rejected", "Отклонено"),
    ]

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="placements")
    material = models.ForeignKey("materials.Material", on_delete=models.CASCADE, related_name="placements")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="placements")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="expected", db_index=True)
    similarity = models.FloatField(null=True, blank=True)
    found_url = models.URLField(max_length=700, blank=True)
    found_title = models.CharField(max_length=500, blank=True)
    rejected_urls = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    reminders_sent = models.PositiveSmallIntegerField(default=0)
    last_reminder_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    resolution_note = models.CharField(max_length=500, blank=True)
    report_token = models.CharField(max_length=64, default=new_token, unique=True)

    class Meta:
        app_label = "partners"
        verbose_name = "размещение"
        verbose_name_plural = "размещения"
        constraints = [models.UniqueConstraint(fields=["material", "school"], name="uniq_placement")]
