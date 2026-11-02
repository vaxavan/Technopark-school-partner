import uuid

from django.db import models, transaction
from django.utils import timezone


class SubmissionStatus(models.TextChoices):
    NEW = "new", "Новая"
    WAITING_AUTHOR = "waiting_author", "Ожидает автора"
    ACCEPTED = "accepted", "Принята"
    REJECTED = "rejected", "Отклонена"


class Submission(models.Model):
    """Заявка: информационный повод от автора без регистрации."""

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="submissions")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    number = models.PositiveIntegerField("номер", editable=False)

    title = models.CharField("о чём новость", max_length=255)
    text = models.TextField("что произошло")
    event_date = models.CharField("когда", max_length=100, blank=True)
    place = models.CharField("где", max_length=255, blank=True)

    author_name = models.CharField("автор", max_length=255)
    author_email = models.EmailField("e-mail автора")
    author_phone = models.CharField("телефон", max_length=40, blank=True)
    author_org = models.CharField("школа или подразделение автора", max_length=255, blank=True)
    consent_at = models.DateTimeField("согласие на обработку ПДн получено")

    status = models.CharField("состояние", max_length=20, choices=SubmissionStatus.choices,
                              default=SubmissionStatus.NEW, db_index=True)
    decision_reason = models.TextField("причина решения / что запрошено", blank=True)
    completeness = models.JSONField("полнота сведений", default=dict, blank=True)
    questions_finished = models.BooleanField(default=False)
    # Уточняющие вопросы готовит модель; пока она думает, автор видит экран ожидания.
    # queued — нужно спросить модель, running — запрос идёт, "" — ничего не ждём.
    clarify_state = models.CharField(max_length=10, blank=True, default="")
    clarify_started_at = models.DateTimeField(null=True, blank=True)
    # Одноразовый номер формы: повторная отправка той же формы не создаёт вторую заявку
    form_token = models.UUIDField(null=True, blank=True, editable=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "заявка"
        verbose_name_plural = "заявки"
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["organization", "number"], name="uniq_submission_number"),
                       models.UniqueConstraint(fields=["organization", "form_token"], name="uniq_submission_form_token")]

    def __str__(self):
        return f"№{self.number} {self.title}"

    def save(self, *args, **kwargs):
        if not self.number:
            from accounts.models import Organization

            with transaction.atomic():
                # блокируем строку организации: две одновременные заявки получат разные номера
                Organization.objects.select_for_update().filter(pk=self.organization_id).first()
                last = Submission.objects.filter(organization_id=self.organization_id).aggregate(
                    m=models.Max("number"))["m"]
                self.number = (last or 0) + 1
                return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    def full_text_for_model(self):
        """Все сведения заявки одним текстом — то, что получает модель."""
        parts = [f"Тема: {self.title}", f"Описание: {self.text}"]
        if self.event_date:
            parts.append(f"Когда: {self.event_date}")
        if self.place:
            parts.append(f"Где: {self.place}")
        if self.author_org:
            parts.append(f"От кого: {self.author_org}")
        answers = []
        for rnd in self.rounds.all():
            for q in rnd.questions:
                ans = rnd.answers.get(q.get("id"))
                if ans not in (None, ""):
                    answers.append(f"— {q.get('text')} {ans}")
        if answers:
            parts.append("Ответы автора на уточняющие вопросы:\n" + "\n".join(answers))
        return "\n".join(parts)


def photo_path(instance, filename):
    return f"submissions/{instance.submission.public_id}/{filename}"


class SubmissionPhoto(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="photos")
    image = models.FileField("файл", upload_to=photo_path)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "фото заявки"
        verbose_name_plural = "фото заявок"


class ClarificationRound(models.Model):
    """Раунд уточняющих вопросов: что спросила модель и что ответил автор."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="rounds")
    number = models.PositiveSmallIntegerField()
    questions = models.JSONField(default=list)
    answers = models.JSONField(default=dict, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    skipped = models.BooleanField(default=False)

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["submission", "number"], name="uniq_round")]
