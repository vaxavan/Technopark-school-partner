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


class CrawlAgent(models.Model):
    """Агент обхода: программа на компьютере, с которого сайты школ открываются (например, в офисе),
    если сервер они не пускают. Агент берёт список источников, сам скачивает страницы и
    присылает найденное; сверку и решения делает сервер. В базе хранится только отпечаток ключа."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.CASCADE, related_name="crawl_agent")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_summary = models.CharField(max_length=500, blank=True)

    class Meta:
        app_label = "partners"
        verbose_name = "агент обхода"
        verbose_name_plural = "агенты обхода"

    @staticmethod
    def hash(token):
        import hashlib

        return hashlib.sha256(token.encode()).hexdigest()

    @classmethod
    def issue(cls, organization):
        """Новый ключ (старый перестаёт работать). Возвращает ключ — он показывается один раз."""
        token = "agt_" + secrets.token_urlsafe(32)
        cls.objects.update_or_create(organization=organization,
                                     defaults={"token_hash": cls.hash(token), "created_at": timezone.now(),
                                               "last_seen_at": None, "last_summary": ""})
        return token

    @classmethod
    def by_token(cls, token):
        if not token:
            return None
        return cls.objects.select_related("organization").filter(token_hash=cls.hash(token)).first()

    @property
    def is_active(self):
        """Агент отчитывался недавно — значит, обход этой организации выполняет он."""
        from datetime import timedelta

        return bool(self.last_seen_at and timezone.now() - self.last_seen_at < timedelta(hours=36))


def search_file_path(instance, filename):
    r = instance.result
    return f"searches/{r.search.organization.code}/{r.search_id}/{r.school_id}/{filename}"


class SchoolSearch(models.Model):
    """Поиск по сайтам школ: есть ли у школ публикации на тему (например, «битва роботов»)."""

    STATUS_CHOICES = [("queued", "В очереди"), ("running", "Идёт"), ("agent", "Ждёт агента обхода"),
                      ("done", "Готово"), ("cancelled", "Остановлен")]

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="searches")
    query = models.CharField("что ищем", max_length=300)
    since = models.DateField("публикации не раньше", null=True, blank=True)
    screenshots = models.BooleanField("сохранять снимки страниц", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="queued")

    class Meta:
        app_label = "partners"
        verbose_name = "поиск по сайтам школ"
        verbose_name_plural = "поиски по сайтам школ"
        ordering = ["-created_at"]

    def __str__(self):
        return f"«{self.query}» от {timezone.localtime(self.created_at):%d.%m.%Y %H:%M}"


class SchoolSearchResult(models.Model):
    """Итог поиска по одной школе."""

    STATUS_CHOICES = [("queued", "Ждёт"), ("running", "Проверяется"), ("found", "Есть"), ("none", "Нет"),
                      ("unreachable", "Сайт не открылся"), ("no_site", "Нет адреса сайта")]

    search = models.ForeignKey(SchoolSearch, on_delete=models.CASCADE, related_name="results")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="search_results")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="queued")
    pages_read = models.PositiveIntegerField(default=0)
    hits_count = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    checked_by_agent = models.BooleanField(default=False)

    class Meta:
        app_label = "partners"
        ordering = ["school__number", "school__name"]
        constraints = [models.UniqueConstraint(fields=["search", "school"], name="uniq_search_school")]

    @property
    def is_finished(self):
        return self.status not in ("queued", "running")


class SchoolSearchHit(models.Model):
    """Найденная публикация: ссылка, заголовок, дата, фрагмент и доказательство (снимок, копия страницы)."""

    result = models.ForeignKey(SchoolSearchResult, on_delete=models.CASCADE, related_name="hits")
    url = models.URLField(max_length=700)
    title = models.CharField(max_length=500, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    snippet = models.TextField(blank=True)
    score = models.FloatField(default=0)
    screenshot = models.FileField(upload_to=search_file_path, blank=True)
    html_copy = models.FileField(upload_to=search_file_path, blank=True)
    checksum = models.CharField("SHA-256 копии", max_length=64, blank=True)
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "partners"
        ordering = ["-score", "-published_at"]
        constraints = [models.UniqueConstraint(fields=["result", "url"], name="uniq_hit_url")]
