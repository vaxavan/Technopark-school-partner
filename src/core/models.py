"""Общие элементы: журнал событий (он же очередь уведомлений) и шифрованное поле."""

from django.conf import settings
from django.db import models
from django.utils import timezone

from .crypto import decrypt, encrypt


class EncryptedTextField(models.TextField):
    """Текст хранится в базе зашифрованным (Fernet), в коде виден открытым."""

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else value


class Event(models.Model):
    """Журнал событий. Каждое значимое действие пишется сюда в той же транзакции,
    что и изменение данных; уведомления рассылаются фоновой задачей по
    необработанным событиям — так письмо не теряется при сбое почты."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="events")
    kind = models.CharField("тип", max_length=60, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    message = models.CharField("описание", max_length=500, blank=True)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(null=True, blank=True)   # следующая попытка после сбоя (с нарастающей паузой)
    last_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "событие"
        verbose_name_plural = "журнал событий"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["processed_at", "created_at"])]

    def __str__(self):
        return f"{self.created_at:%d.%m %H:%M} {self.kind}"


class LLMAccount(models.Model):
    """Доступ к YandexGPT для организации. Ключ хранится зашифрованным; если его нет —
    берутся YANDEX_API_KEY / YANDEX_FOLDER_ID из .env."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.CASCADE, related_name="llm_account")
    api_key = EncryptedTextField("API-ключ", blank=True)
    folder_id = models.CharField("ID каталога", max_length=64, blank=True)
    model = models.CharField("модель для текстов", max_length=100, blank=True,
                             help_text="Пусто — из .env (по умолчанию yandexgpt/latest)")
    model_lite = models.CharField("модель для уточняющих вопросов", max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "доступ к языковой модели"
        verbose_name_plural = "доступ к языковой модели"
