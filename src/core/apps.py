from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Общее"

    def ready(self):
        # регистрация обработчиков уведомлений
        from . import notifications  # noqa: F401
