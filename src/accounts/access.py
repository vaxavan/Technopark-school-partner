"""Проверка прав. Все модули проверяют организацию и роль только через этот файл."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied

from .models import Membership, Role

SESSION_KEY = "current_org_id"


class AccessDenied(PermissionDenied):
    pass


def memberships_of(user):
    if not user.is_authenticated:
        return Membership.objects.none()
    return Membership.objects.filter(user=user).select_related("organization").order_by("organization__name")


def membership_for(user, organization):
    if not user.is_authenticated or organization is None:
        return None
    return Membership.objects.filter(user=user, organization=organization).first()


def role_in(user, organization):
    m = membership_for(user, organization)
    return m.role if m else None


def require_role(user, organization, *roles):
    role = role_in(user, organization)
    if role is None or (roles and role not in roles):
        raise AccessDenied("Недостаточно прав для этого действия")
    return role


def is_director(user, organization):
    return role_in(user, organization) == Role.DIRECTOR


class OrgRequiredMixin(LoginRequiredMixin):
    """Представление доступно только участнику текущей организации.
    request.org выставляет middleware; все запросы к данным фильтруются по нему."""

    allowed_roles = (Role.DIRECTOR, Role.ADMIN)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if getattr(request, "org", None) is None:
            raise AccessDenied("Вы не состоите ни в одной организации")
        require_role(request.user, request.org, *self.allowed_roles)
        return super().dispatch(request, *args, **kwargs)
