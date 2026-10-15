from django import template
from django.utils.html import format_html

register = template.Library()

TONE = {
    # заявки
    "new": "b-info", "waiting_author": "b-warn", "accepted": "b-ok", "rejected": "",
    # материалы
    "preparing": "b-info", "on_approval": "b-warn", "scheduled": "b-info", "published": "b-ok",
    "publish_error": "b-bad",
    # публикации
    "publishing": "b-info", "failed": "b-bad", "needs_check": "b-bad",
    # размещения
    "expected": "", "confirmed": "b-ok", "disputed": "b-warn", "not_found": "b-bad",
    # поиск по сайтам школ
    "found": "b-ok", "none": "b-bad", "unreachable": "b-warn", "no_site": "", "queued": "", "running": "b-info",
    "agent": "b-warn", "done": "b-ok", "cancelled": "",
}


@register.simple_tag
def badge(code, label):
    return format_html('<span class="badge {}">{}</span>', TONE.get(code, ""), label)


@register.filter
def pct(value):
    if value is None or value == "":
        return "—"
    return f"{float(value) * 100:.0f}%"


@register.filter
def get(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None
