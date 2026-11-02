"""Адаптер почты: письма уходят по одному на адресата (так требуют почтовые сервисы
для рассылок), с паузой и заголовком List-Unsubscribe."""

import logging
import mimetypes
import time
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMessage, get_connection

log = logging.getLogger(__name__)


def send_one(to, subject, body, *, attachments=(), reply_to=None, unsubscribe_url=None, connection=None):
    headers = {}
    if unsubscribe_url:
        headers["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    msg = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL,
                       to=[to], reply_to=[reply_to] if reply_to else None, headers=headers,
                       connection=connection)
    for path in attachments:
        p = Path(path)
        if p.exists():
            msg.attach(p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
    return msg.send(fail_silently=False)


def send_many(messages, pause=None):
    """messages: список словарей с ключами send_one. Возвращает (отправлено, ошибки)."""
    pause = settings.MAIL_PAUSE_SECONDS if pause is None else pause
    sent, errors = 0, []
    connection = get_connection()
    for i, m in enumerate(messages):
        try:
            send_one(connection=connection, **m)
            sent += 1
        except Exception as exc:
            log.warning("Письмо на %s не отправлено: %s", m.get("to"), exc)
            errors.append((m.get("to"), str(exc)))
        if pause and i < len(messages) - 1:
            time.sleep(pause)
    return sent, errors


def mail_mode():
    return "smtp" if settings.EMAIL_HOST else "demo"
