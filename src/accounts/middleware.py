from .access import SESSION_KEY, memberships_of


class CurrentOrganizationMiddleware:
    """Определяет текущую организацию пользователя (request.org и request.role)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.org = None
        request.role = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            memberships = list(memberships_of(user))
            if memberships:
                wanted = request.session.get(SESSION_KEY)
                current = next((m for m in memberships if m.organization_id == wanted), memberships[0])
                request.org = current.organization
                request.role = current.role
                request.memberships = memberships
        return self.get_response(request)
