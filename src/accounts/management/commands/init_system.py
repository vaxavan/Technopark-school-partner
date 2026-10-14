"""Первичная настройка: организация из .env, директор, администратор, справочник школ.
Безопасно запускать повторно — существующее не перезаписывается."""

import os
import secrets
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from accounts.models import Membership, Organization, Role

DEFAULT_STYLE = """Пишем от имени технопарка во множественном числе («мы», «наши ребята») только в соцсетях;
на сайтах школ — от третьего лица.
Называем организацию полностью при первом упоминании.
Не используем слова «уникальный», «невероятный», «потрясающий».
Возраст и класс участников указываем, только если они есть в заявке."""


class Command(BaseCommand):
    help = "Создаёт организацию, пользователей и загружает справочник школ (если их ещё нет)"

    def handle(self, *args, **opts):
        code = os.getenv("ORG_CODE", "istok")
        name = os.getenv("ORG_NAME", "Технопарк «Исток — РТУ МИРЭА»")
        org, created = Organization.objects.get_or_create(code=code, defaults={"name": name,
                                                                                "style_guide": DEFAULT_STYLE})
        self.stdout.write(f"Организация: {org.name} ({'создана' if created else 'уже есть'})")

        User = get_user_model()
        for role, prefix in ((Role.DIRECTOR, "DIRECTOR"), (Role.ADMIN, "ADMIN")):
            username = os.getenv(f"{prefix}_USERNAME", role.value)
            if User.objects.filter(username=username).exists():
                user = User.objects.get(username=username)
            else:
                password = os.getenv(f"{prefix}_PASSWORD") or secrets.token_urlsafe(9)
                user = User.objects.create_user(username=username, password=password,
                                                email=os.getenv(f"{prefix}_EMAIL", ""),
                                                is_staff=True, is_superuser=(role == Role.ADMIN))
                shown = password if not os.getenv(f"{prefix}_PASSWORD") else "(из .env)"
                self.stdout.write(self.style.WARNING(f"Создан пользователь {username} / пароль: {shown}"))
            Membership.objects.get_or_create(user=user, organization=org, defaults={"role": role})

        if not org.schools.exists():
            path = Path(settings.REPO_DIR) / "data" / "schools.xlsx"
            if not path.exists():
                path = Path("/app/data/schools.xlsx")
            if path.exists():
                from partners.interfaces.importer import import_schools

                with open(path, "rb") as fh:
                    c, u = import_schools(org, fh)
                self.stdout.write(f"Справочник школ загружен: {c} школ")
