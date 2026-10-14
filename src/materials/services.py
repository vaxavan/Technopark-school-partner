"""Сценарии работы с заявкой и материалом. Все переходы — через конечный автомат domain.py."""

import logging

from django.db import transaction
from django.utils import timezone

from accounts.access import role_in
from core.events import record
from core.ports import LLMError
from submissions.models import Submission, SubmissionStatus

from . import domain
from .generation import (EDIT_COMMANDS, add_version, edit_with_model, generate_for_channel,
                         style_examples)
from .models import Approval, Channel, Material, MaterialStatus, VariantSource

log = logging.getLogger(__name__)

DEFAULT_CHANNELS = [Channel.VK, Channel.TELEGRAM, Channel.MAX, Channel.DZEN, Channel.PARTNERS]


def _role(user, org):
    return role_in(user, org)


# ---------- Заявка ----------

def accept_submission(submission: Submission, user, channels=None) -> Material:
    org = submission.organization
    with transaction.atomic():
        submission = Submission.objects.select_for_update().get(pk=submission.pk)
        submission.status = domain.next_state(domain.SUBMISSION_TRANSITIONS, "accept", submission.status,
                                              _role(user, org))
        submission.save(update_fields=["status", "updated_at"])
        material = Material.objects.create(
            organization=org, submission=submission, title=submission.title,
            channels=[c for c in (channels or DEFAULT_CHANNELS) if c in Channel.values])
        record(org, "submission.decided", actor=user, obj=submission, message=f"Заявка №{submission.number} принята",
               decision="accepted", submission_id=submission.pk)
        from .tasks import generate_texts

        transaction.on_commit(lambda: generate_texts.delay(material.pk))
    return material


def reject_submission(submission: Submission, user, reason):
    if not reason.strip():
        raise domain.TransitionError("Укажите причину отказа — она уйдёт автору")
    org = submission.organization
    submission.status = domain.next_state(domain.SUBMISSION_TRANSITIONS, "reject", submission.status,
                                          _role(user, org))
    submission.decision_reason = reason.strip()
    submission.save(update_fields=["status", "decision_reason", "updated_at"])
    record(org, "submission.decided", actor=user, obj=submission, message=f"Заявка №{submission.number} отклонена",
           decision="rejected", submission_id=submission.pk)


def request_info(submission: Submission, user, what):
    if not what.strip():
        raise domain.TransitionError("Напишите, что нужно дополнить")
    org = submission.organization
    submission.status = domain.next_state(domain.SUBMISSION_TRANSITIONS, "request_info", submission.status,
                                          _role(user, org))
    submission.decision_reason = what.strip()
    submission.save(update_fields=["status", "decision_reason", "updated_at"])
    record(org, "submission.decided", actor=user, obj=submission,
           message=f"По заявке №{submission.number} запрошены материалы", decision="request_info",
           submission_id=submission.pk)


def author_added(submission: Submission, extra_text, files=()):
    from submissions.models import SubmissionPhoto

    submission.status = domain.next_state(domain.SUBMISSION_TRANSITIONS, "author_added", submission.status,
                                          system=True)
    if extra_text.strip():
        submission.text += f"\n\nДополнение автора ({timezone.localtime():%d.%m.%Y}):\n{extra_text.strip()}"
    submission.save(update_fields=["status", "text", "updated_at"])
    for f in files:
        SubmissionPhoto.objects.create(submission=submission, image=f)
    record(submission.organization, "submission.updated", obj=submission,
           message=f"Автор дополнил заявку №{submission.number}", submission_id=submission.pk)


# ---------- Подготовка текстов ----------

def run_generation(material: Material, llm=None):
    """Готовит варианты для всех площадок. При отказе модели материал всё равно уходит на
    утверждение — с черновиками из текста заявки, которые администратор правит вручную."""
    org = material.organization
    source = material.submission.full_text_for_model()
    errors = []
    for channel in material.channels:
        try:
            text, issues, attempts = generate_for_channel(org, channel, source, style_examples(org, channel), llm)
            add_version(material, channel, text, VariantSource.MODEL, checks=issues, attempts=attempts)
        except LLMError as exc:
            errors.append(str(exc))
            add_version(material, channel, f"{material.title}\n\n{material.submission.text}", VariantSource.MANUAL,
                        checks=["Языковая модель недоступна — подготовьте текст вручную"], attempts=0)
    material.generation_error = "; ".join(dict.fromkeys(errors))[:2000]
    material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "texts_ready", material.status, system=True)
    material.save(update_fields=["status", "generation_error", "updated_at"])
    record(org, "material.texts_ready", obj=material,
           message=f"Тексты готовы: {material.title}" + (" (модель недоступна)" if errors else ""))


def regenerate(material: Material, user):
    material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "regenerate", material.status,
                                        _role(user, material.organization))
    material.save(update_fields=["status", "updated_at"])
    record(material.organization, "material.regenerate", actor=user, obj=material, message="Тексты готовятся заново")
    from .tasks import generate_texts

    transaction.on_commit(lambda: generate_texts.delay(material.pk))


def _ensure_editable(material):
    if material.status not in (MaterialStatus.ON_APPROVAL,):
        raise domain.TransitionError("Править текст можно, пока материал на утверждении")


def edit_manually(material: Material, channel, text, user):
    from .generation import check_variant

    _ensure_editable(material)
    text = text.strip()
    if not text:
        raise domain.TransitionError("Текст не может быть пустым")
    current = material.current_variant(channel)
    if current and current.text == text:
        return current
    issues = check_variant(channel, text, material.submission.full_text_for_model())
    v = add_version(material, channel, text, VariantSource.MANUAL, user=user, checks=issues)
    record(material.organization, "variant.edited", actor=user, obj=material,
           message=f"Ручная правка ({v.get_channel_display()}, версия {v.version})")
    return v


def edit_by_command(material: Material, channel, command, user, llm=None):
    _ensure_editable(material)
    instruction = EDIT_COMMANDS.get(command) or command.strip()
    if not instruction:
        raise domain.TransitionError("Пустая команда правки")
    current = material.current_variant(channel)
    if current is None:
        raise domain.TransitionError("Нет текста для правки")
    text, issues = edit_with_model(material.organization, channel, current.text, instruction,
                                   material.submission.full_text_for_model(), llm)
    v = add_version(material, channel, text, VariantSource.MODEL_EDIT, user=user, checks=issues,
                    instruction=instruction)
    record(material.organization, "variant.edited", actor=user, obj=material,
           message=f"Правка моделью ({v.get_channel_display()}): {instruction[:80]}")
    return v


# ---------- Утверждение ----------

def approve(material: Material, user, basis, comment="", scheduled_at=None):
    from publishing.services import schedule_publications

    org = material.organization
    role = _role(user, org)
    domain.check_approval_basis(role, basis)
    if not material.current_variants().exists():
        raise domain.TransitionError("Нет текстов для публикации")
    with transaction.atomic():
        material = Material.objects.select_for_update().get(pk=material.pk)
        material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "approve", material.status, role)
        material.scheduled_at = scheduled_at or timezone.now()
        material.save(update_fields=["status", "scheduled_at", "updated_at"])
        approval = Approval.objects.create(material=material, user=user, basis=basis, comment=comment)
        schedule_publications(material)
        kind = "material.approved" if basis == "director" else "material.approved_by_admin"
        record(org, kind, actor=user, obj=material,
               message=f"Материал утверждён: {approval.get_basis_display()}. Выход {timezone.localtime(material.scheduled_at):%d.%m.%Y %H:%M}",
               basis=basis, material_id=material.pk, comment=comment)
        if not material.publications.exists():
            # только рассылка школам — публиковать на площадках нечего
            material.status = MaterialStatus.PUBLISHED
            material.save(update_fields=["status", "updated_at"])
            if Channel.PARTNERS in material.channels:
                from partners.tasks import dispatch_material

                transaction.on_commit(lambda: dispatch_material.delay(material.pk))
    return approval


def reject_material(material: Material, user, reason):
    if not reason.strip():
        raise domain.TransitionError("Укажите причину")
    org = material.organization
    material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "reject", material.status, _role(user, org))
    material.save(update_fields=["status", "updated_at"])
    sub = material.submission
    sub.status = SubmissionStatus.REJECTED
    sub.decision_reason = reason.strip()
    sub.save(update_fields=["status", "decision_reason", "updated_at"])
    record(org, "submission.decided", actor=user, obj=sub, message=f"Материал «{material.title}» отклонён",
           decision="rejected", submission_id=sub.pk)


def unschedule(material: Material, user):
    from publishing.services import unschedule as drop

    if material.publications.exclude(status="scheduled").exists():
        raise domain.TransitionError("Часть площадок уже опубликована — снять с публикации нельзя")
    material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "unschedule", material.status,
                                        _role(user, material.organization))
    material.save(update_fields=["status", "updated_at"])
    drop(material)
    record(material.organization, "material.unscheduled", actor=user, obj=material,
           message="Материал снят с публикации и возвращён на утверждение")


def retry_publication(material: Material, user):
    from publishing.services import retry

    material.status = domain.next_state(domain.MATERIAL_TRANSITIONS, "retry", material.status,
                                        _role(user, material.organization))
    material.save(update_fields=["status", "updated_at"])
    retry(material)
    record(material.organization, "material.retry", actor=user, obj=material, message="Повтор публикации")


def set_channels(material: Material, channels, user):
    _ensure_editable(material)
    channels = [c for c in channels if c in Channel.values]
    if not channels:
        raise domain.TransitionError("Выберите хотя бы одну площадку")
    material.channels = channels
    material.save(update_fields=["channels", "updated_at"])
    # для новых площадок готовим текст
    missing = [c for c in channels if not material.current_variant(c)]
    if missing:
        org = material.organization
        source = material.submission.full_text_for_model()
        for c in missing:
            try:
                text, issues, attempts = generate_for_channel(org, c, source)
                add_version(material, c, text, VariantSource.MODEL, checks=issues, attempts=attempts)
            except LLMError:
                add_version(material, c, f"{material.title}\n\n{material.submission.text}", VariantSource.MANUAL,
                            checks=["Языковая модель недоступна — подготовьте текст вручную"], attempts=0)


def restore_version(material: Material, variant_id, user):
    v = material.variants.filter(pk=variant_id).first()
    if v is None:
        raise domain.TransitionError("Версия не найдена")
    return edit_manually(material, v.channel, v.text, user)


def mark_published(material: Material, publication_id, url, user):
    from publishing.services import mark_published_manually

    pub = material.publications.filter(pk=publication_id).first()
    if pub is None or not str(url).startswith("http"):
        raise domain.TransitionError("Укажите ссылку на вышедший пост")
    mark_published_manually(pub, url)
    record(material.organization, "publication.manual", actor=user, obj=material,
           message=f"{pub.get_channel_display()}: отмечено вручную {url}")
    return pub


def dispatch_to_schools(material: Material, user):
    """Разослать школам, которым ещё не отправляли (например, добавили новые школы)."""
    if material.status != MaterialStatus.PUBLISHED:
        raise domain.TransitionError("Рассылка возможна после публикации")
    from partners.tasks import dispatch_material

    transaction.on_commit(lambda: dispatch_material.delay(material.pk))
