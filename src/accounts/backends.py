from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Вход по e-mail без учёта регистра букв."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            username = username.strip().lower()
        return super().authenticate(request, username=username, password=password, **kwargs)
