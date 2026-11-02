"""Конечный автомат заявки и материала (рисунок 5 отчёта).

Переход выполняется только по разрешённому действию и только пользователем с нужной ролью.
Чистый Python: без обращений к базе, поэтому легко проверяется тестами."""

from dataclasses import dataclass

DIRECTOR = "director"
ADMIN = "admin"
ANY_STAFF = (DIRECTOR, ADMIN)


class TransitionError(Exception):
    pass


@dataclass(frozen=True)
class Transition:
    action: str
    source: str
    target: str
    roles: tuple


# Заявка
SUBMISSION_TRANSITIONS = [
    Transition("accept", "new", "accepted", ANY_STAFF),
    Transition("reject", "new", "rejected", ANY_STAFF),
    Transition("request_info", "new", "waiting_author", ANY_STAFF),
    Transition("author_added", "waiting_author", "new", ()),  # делает сам автор
    Transition("reject", "waiting_author", "rejected", ANY_STAFF),
]

# Материал
MATERIAL_TRANSITIONS = [
    Transition("texts_ready", "preparing", "on_approval", ()),  # делает фоновая задача
    Transition("regenerate", "on_approval", "preparing", ANY_STAFF),
    Transition("approve", "on_approval", "scheduled", ANY_STAFF),
    Transition("reject", "on_approval", "rejected", ANY_STAFF),
    Transition("unschedule", "scheduled", "on_approval", ANY_STAFF),
    Transition("published", "scheduled", "published", ()),
    Transition("publish_failed", "scheduled", "publish_error", ()),
    Transition("retry", "publish_error", "scheduled", ANY_STAFF),
    Transition("published", "publish_error", "published", ()),
]


def _find(table, action, source):
    for t in table:
        if t.action == action and t.source == source:
            return t
    return None


def next_state(table, action, source, role=None, system=False):
    """Возвращает новое состояние или бросает TransitionError."""
    t = _find(table, action, source)
    if t is None:
        raise TransitionError(f"Действие «{action}» недоступно в состоянии «{source}»")
    if t.roles and not system and role not in t.roles:
        raise TransitionError("Недостаточно прав для этого действия")
    if not t.roles and not system:
        raise TransitionError("Это действие выполняет система")
    return t.target


def allowed_actions(table, source, role):
    return [t.action for t in table if t.source == source and role in t.roles]


def check_approval_basis(role, basis):
    """«Утвердил директор» ставит только директор; администратор — с отметкой
    «устно согласовано» или «без согласования»."""
    if basis == "director" and role != DIRECTOR:
        raise TransitionError("Отметку «утвердил директор» может поставить только директор")
    if role == DIRECTOR and basis != "director":
        raise TransitionError("Директор утверждает от своего имени")
    if basis not in ("director", "oral", "none"):
        raise TransitionError("Неизвестное основание утверждения")
