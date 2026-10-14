"""Модуль «Организации и доступ»: организации, членство пользователей и роли."""

import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class Organization(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    code = models.SlugField(
        "код", max_length=40, unique=True,
        validators=[RegexValidator(r"^[a-z0-9-]+$", "Только латиница в нижнем регистре, цифры и дефис")],
        help_text="Используется в адресе публичной формы: /f/<код>/",
    )
    name = models.CharField("название", max_length=255)
    kind = models.CharField("тип", max_length=100, default="технопарк")
    style_guide = models.TextField(
        "стилевой регламент", blank=True,
        help_text="Правила, которые получает языковая модель при подготовке текстов",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "организация"
        verbose_name_plural = "организации"

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    DIRECTOR = "director", "Директор"
    ADMIN = "admin", "Администратор"


class Membership(models.Model):
    """Роль хранится в членстве, а не в учётной записи: один пользователь —
    несколько организаций с разными ролями."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField("роль", max_length=20, choices=Role.choices)

    class Meta:
        verbose_name = "членство"
        verbose_name_plural = "членства"
        constraints = [models.UniqueConstraint(fields=["user", "organization"], name="uniq_membership")]

    def __str__(self):
        return f"{self.user} — {self.organization} ({self.get_role_display()})"
