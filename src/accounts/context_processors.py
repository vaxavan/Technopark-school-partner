def organization(request):
    return {
        "current_org": getattr(request, "org", None),
        "current_role": getattr(request, "role", None),
        "user_memberships": getattr(request, "memberships", []),
    }
