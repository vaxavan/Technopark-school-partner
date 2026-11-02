import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Channel(models.TextChoices):
    VK = "vk", "ВКонтакте"
    TELEGRAM = "telegram", "Telegram"
    MAX = "max", "Max"
    DZEN = "dzen", "Дзен"
    PARTNERS = "partners", "Рассылка школам"


# Ограничения площадок: (минимум, максимум знаков, пояснение для модели)
CHANNEL_RULES = {
    Channel.VK: (200, 2500, "пост для сообщества ВКонтакте: живой, но деловой; 1–3 абзаца; "
                            "в конце 2–4 хештега по теме; эмодзи не больше двух"),
    # 1000 знаков: пост с фото в Telegram — это подпись к фото, а она ограничена 1024 знаками
    Channel.TELEGRAM: (150, 1000, "пост для Telegram-канала: суть в первом предложении, 2–3 коротких абзаца, "
                                  "можно 1–2 хештега в конце, эмодзи не больше двух"),
    Channel.MAX: (150, 1500, "короткий пост для канала в мессенджере Max: суть в первом предложении, "
                             "2 абзаца, без хештегов"),
    Channel.DZEN: (700, 6000, "статья для Дзена: заголовок в первой строке, затем лид и 3–6 абзацев "
                              "с подробностями; без хештегов и эмодзи"),
    Channel.PARTNERS: (400, 3000, "новость для размещения на сайтах школ-партнёров: нейтральный "
                                  "официальный стиль, заголовок в первой строке, без хештегов и эмодзи"),
}


class MaterialStatus(models.TextChoices):
    PREPARING = "preparing", "Подготовка текстов"
    ON_APPROVAL = "on_approval", "На утверждении"
    SCHEDULED = "scheduled", "Запланирована"
    PUBLISHED = "published", "Опубликована"
    PUBLISH_ERROR = "publish_error", "Ошибка публикации"
    REJECTED = "rejected", "Отклонена"


class Material(models.Model):
    """Материал создаётся из принятой заявки и проходит конечный автомат (materials/domain.py)."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="materials")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    submission = models.OneToOneField("submissions.Submission", on_delete=models.PROTECT, related_name="material")
    title = models.CharField("заголовок", max_length=255)
    status = models.CharField(max_length=20, choices=MaterialStatus.choices, default=MaterialStatus.PREPARING,
                              db_index=True)
    channels = models.JSONField("площадки", default=list)
    scheduled_at = models.DateTimeField("дата выхода", null=True, blank=True)
    generation_error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "материал"
        verbose_name_plural = "материалы"
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def current_variants(self):
        return self.variants.filter(is_current=True).order_by("channel")

    def current_variant(self, channel):
        return self.variants.filter(is_current=True, channel=channel).first()

    @property
    def last_approval(self):
        return self.approvals.order_by("-created_at").first()


class VariantSource(models.TextChoices):
    MODEL = "model", "Модель"
    MODEL_EDIT = "model_edit", "Правка моделью"
    MANUAL = "manual", "Вручную"


class TextVariant(models.Model):
    """Версия текста под одну площадку. Старые версии не удаляются."""

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="variants")
    channel = models.CharField(max_length=20, choices=Channel.choices)
    version = models.PositiveIntegerField()
    text = models.TextField()
    source = models.CharField(max_length=20, choices=VariantSource.choices)
    instruction = models.CharField("команда правки", max_length=500, blank=True)
    checks = models.JSONField("замечания проверок", default=list, blank=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    manual_share = models.FloatField("доля ручных правок", default=0)
    model_edit_share = models.FloatField("доля правок моделью", default=0)
    is_current = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "вариант текста"
        verbose_name_plural = "варианты текста"
        ordering = ["channel", "-version"]
        constraints = [models.UniqueConstraint(fields=["material", "channel", "version"], name="uniq_variant_version")]

    @property
    def limits(self):
        lo, hi, _ = CHANNEL_RULES[self.channel]
        return lo, hi

    @property
    def length(self):
        return len(self.text)


class ApprovalBasis(models.TextChoices):
    DIRECTOR = "director", "Утвердил директор"
    ORAL = "oral", "Устно согласовано с директором"
    NONE = "none", "Без согласования"


class Approval(models.Model):
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="approvals")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    basis = models.CharField("основание", max_length=20, choices=ApprovalBasis.choices)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "утверждение"
        verbose_name_plural = "утверждения"
