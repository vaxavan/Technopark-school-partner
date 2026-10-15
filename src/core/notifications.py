"""Уведомления по событиям журнала. Отправляются фоновой задачей (core.tasks.deliver_events)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string

from accounts.models import Role
from integrations.mail import send_one

from .events import handler


def _staff_emails(org, role=None):
    users = get_user_model().objects.filter(memberships__organization=org, is_active=True)
    if role:
        users = users.filter(memberships__role=role)
    return sorted({u.email for u in users if u.email})


def _send(event, to_list, subject, template, ctx):
    """Письма по списку. Кому уже отправлено, запоминается в событии: при повторе после сбоя
    (например, почта упала на третьем адресате) первые два письма второй раз не уходят."""
    body = render_to_string(template, {**ctx, "site_url": settings.SITE_URL})
    sent = event.data.setdefault("sent_to", [])
    for to in to_list:
        key = f"{template}:{to}"
        if key in sent:
            continue
        send_one(to, subject, body)
        sent.append(key)


@handler("submission.received")
def on_received(event):
    from submissions.models import Submission

    sub = Submission.objects.get(pk=event.data["submission_id"])
    ctx = {"sub": sub, "org": event.organization}
    # сначала редакции: неверный адрес автора не должен задержать письмо администратору
    _send(event, _staff_emails(event.organization, Role.ADMIN), f"Новая заявка №{sub.number}: {sub.title}",
          "emails/staff_new_submission.txt", ctx)
    _send(event, [sub.author_email], f"Заявка №{sub.number} получена", "emails/author_received.txt", ctx)


@handler("submission.updated")
def on_author_added(event):
    from submissions.models import Submission

    sub = Submission.objects.get(pk=event.data["submission_id"])
    _send(event, _staff_emails(event.organization, Role.ADMIN), f"Автор дополнил заявку №{sub.number}: {sub.title}",
          "emails/staff_submission_updated.txt", {"sub": sub, "org": event.organization})


@handler("submission.decided")
def on_decided(event):
    from submissions.models import Submission

    sub = Submission.objects.get(pk=event.data["submission_id"])
    decision = event.data.get("decision")
    subjects = {"accepted": "принята в работу", "rejected": "отклонена", "request_info": "нужны дополнения"}
    _send(event, [sub.author_email], f"Заявка №{sub.number} {subjects.get(decision, '')}",
          "emails/author_decision.txt",
          {"sub": sub, "org": event.organization, "decision": decision})


@handler("material.approved_by_admin")
def on_admin_approval(event):
    from materials.models import Material

    material = Material.objects.get(pk=event.data["material_id"])
    _send(event, _staff_emails(event.organization, Role.DIRECTOR),
          f"Администратор утвердил материал: {material.title}",
          "emails/director_notice.txt",
          {"material": material, "event": event, "approval": material.last_approval, "org": event.organization})


@handler("publication.failed")
def on_publication_failed(event):
    _send(event, _staff_emails(event.organization, Role.ADMIN), "Ошибка публикации", "emails/staff_alert.txt",
          {"event": event, "org": event.organization})


@handler("publication.needs_check")
def on_publication_check(event):
    on_publication_failed(event)
