"""Проверка подключений: YandexGPT, почта, ВКонтакте, Max, сайты школ.

    docker compose exec web python manage.py check_integrations
    docker compose exec web python manage.py check_integrations --mail-to you@example.com
"""

from django.conf import settings
from django.core.mail import get_connection
from django.core.management.base import BaseCommand

from accounts.models import Organization
from materials.models import Channel


class Command(BaseCommand):
    help = "Проверяет внешние подключения и пишет, что настроено, а что нет"

    def add_arguments(self, parser):
        parser.add_argument("--mail-to", help="Отправить тестовое письмо на этот адрес")
        parser.add_argument("--schools", type=int, default=3, help="Сколько школ проверить (0 — не проверять)")

    def ok(self, name, msg):
        self.stdout.write(self.style.SUCCESS(f"[OK]   {name}: {msg}"))

    def bad(self, name, msg):
        self.stdout.write(self.style.ERROR(f"[ОШИБКА] {name}: {msg}"))

    def skip(self, name, msg):
        self.stdout.write(self.style.WARNING(f"[—]    {name}: {msg}"))

    def handle(self, *args, **o):
        self.check_llm()
        self.check_mail(o.get("mail_to"))
        org = Organization.objects.first()
        if org:
            self.check_vk(org)
            self.check_telegram(org)
            self.check_max(org)
            if o["schools"]:
                self.check_schools(org, o["schools"])

    def check_llm(self):
        from core.ports import LLMError
        from integrations.llm import get_llm
        from submissions.clarify import Completeness

        if settings.LLM_BACKEND != "yandex":
            return self.skip("YandexGPT", "ключ не задан — работает демо-модель (YANDEX_API_KEY, YANDEX_FOLDER_ID)")
        llm = get_llm()
        try:
            text = llm.complete("Ответь одним словом.", "Столица России?", timeout=30)
            self.ok("YandexGPT", f"модель {settings.YANDEX_MODEL} отвечает: {text.strip()[:40]}")
        except LLMError as exc:
            return self.bad("YandexGPT", str(exc))
        try:
            raw = llm.complete('Верни JSON {"complete": true, "missing": [], "questions": []}', "Проверка",
                               json_mode=True, lite=True, timeout=30)
            from integrations.llm import extract_json

            Completeness.model_validate_json(extract_json(raw))
            self.ok("YandexGPT (JSON)", f"модель {settings.YANDEX_MODEL_LITE} возвращает ответ по схеме")
        except Exception as exc:
            self.bad("YandexGPT (JSON)", f"{exc}")

    def check_mail(self, to):
        if not settings.EMAIL_HOST:
            return self.skip("Почта", "SMTP не задан — письма складываются в media/outbox")
        try:
            conn = get_connection()
            conn.open()
            conn.close()
            self.ok("Почта", f"вход на {settings.EMAIL_HOST} как {settings.EMAIL_HOST_USER} выполнен")
        except Exception as exc:
            return self.bad("Почта", f"{exc}. Проверьте пароль приложения и порт")
        if to:
            from integrations.mail import send_one

            send_one(to, "Проверка почты", "Если вы читаете это письмо — рассылка настроена.")
            self.ok("Почта", f"тестовое письмо отправлено на {to}")

    def check_vk(self, org):
        from publishing.platforms import VkPlatform, platform_for

        p = platform_for(org, Channel.VK)
        if not isinstance(p, VkPlatform):
            return self.skip("ВКонтакте", "токен не задан — публикация в демо-режиме")
        try:
            g = p._call("groups.getById", group_id=p.group_id, fields="can_post")
            group = g["groups"][0] if isinstance(g, dict) else g[0]
            self.ok("ВКонтакте", f"группа «{group.get('name')}» найдена")
            p._call("photos.getWallUploadServer", group_id=p.group_id)
            self.ok("ВКонтакте", "есть право загружать фото на стену")
        except Exception as exc:
            self.bad("ВКонтакте", f"{exc}. См. docs/ТОКЕНЫ.md, раздел ВКонтакте")

    def check_telegram(self, org):
        from publishing.platforms import TelegramPlatform, platform_for

        p = platform_for(org, Channel.TELEGRAM)
        if not isinstance(p, TelegramPlatform):
            return self.skip("Telegram", "токен не задан — публикация в демо-режиме")
        try:
            me = p._call("getMe")
            self.ok("Telegram", f"бот @{me.get('username')} авторизован")
            chat = p._call("getChat", data={"chat_id": p.chat_id})
            self.ok("Telegram", f"канал/группа «{chat.get('title')}» найден(а)")
            member = p._call("getChatMember", data={"chat_id": p.chat_id, "user_id": me["id"]})
            can_post = member.get("status") == "administrator" and (
                chat.get("type") != "channel" or member.get("can_post_messages"))
            if can_post or (chat.get("type") != "channel" and member.get("status") in ("member", "administrator")):
                self.ok("Telegram", "бот может публиковать")
            else:
                self.bad("Telegram", "бот не администратор с правом «Публикация сообщений»")
        except Exception as exc:
            self.bad("Telegram", f"{exc}. См. docs/ТОКЕНЫ.md, раздел Telegram")

    def check_max(self, org):
        from publishing.platforms import MaxPlatform, platform_for

        p = platform_for(org, Channel.MAX)
        if not isinstance(p, MaxPlatform):
            return self.skip("Max", "токен не задан — публикация в демо-режиме")
        try:
            me = p._request("GET", "/me")
            self.ok("Max", f"бот «{me.get('name') or me.get('username')}» авторизован")
        except Exception as exc:
            self.bad("Max", f"{exc}. Если ошибка сертификата — см. MAX_CA_BUNDLE в docs/ТОКЕНЫ.md")

    def check_schools(self, org, limit):
        from partners.infrastructure.crawlers import CompositeCrawler
        from partners.infrastructure.mappers import source_to_entity
        from partners.models import Source

        crawler = CompositeCrawler()
        for src in Source.objects.filter(school__organization=org, school__active=True)[:limit]:
            try:
                found = crawler.fetch(source_to_entity(src))
                self.ok("Сайт школы", f"{src.school.name[:50]}: найдено публикаций {len(found)}")
            except Exception as exc:
                self.bad("Сайт школы", f"{src.school.name[:50]}: {exc}")
