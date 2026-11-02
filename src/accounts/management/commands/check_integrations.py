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

        from integrations.llm import llm_mode

        org = Organization.objects.first()
        mode = llm_mode(org)
        if mode == "demo":
            return self.skip("YandexGPT", "ключ не задан — работает демо-модель («Настройки» или .env)")
        llm = get_llm(org)
        try:
            text = llm.complete("Ответь одним словом.", "Столица России?", timeout=30)
            src = "из «Настроек»" if mode == "settings" else "из .env"
            self.ok("YandexGPT", f"ключ {src}, модель {llm.model} отвечает: {text.strip()[:40]}")
        except LLMError as exc:
            return self.bad("YandexGPT", str(exc))
        try:
            raw = llm.complete('Верни JSON {"complete": true, "missing": [], "questions": []}', "Проверка",
                               json_mode=True, lite=True, timeout=30)
            from integrations.llm import extract_json

            Completeness.model_validate_json(extract_json(raw))
            self.ok("YandexGPT (JSON)", f"модель {llm.model_lite} возвращает ответ по схеме")
        except Exception as exc:
            self.bad("YandexGPT (JSON)", f"{exc}")

    def check_mail(self, to):
        if not settings.EMAIL_HOST:
            return self.skip("Почта", "SMTP не задан — письма складываются файлами в папку outbox")
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
        """Для каждого источника: DNS, TCP-подключение, загрузка страницы и разбор. По итогам — подсказка."""
        import socket
        import time
        from urllib.parse import urlparse

        from partners.infrastructure.crawlers import CompositeCrawler, crawler_session, describe_error
        from partners.infrastructure.mappers import source_to_entity
        from partners.models import CrawlAgent, Source

        if settings.CRAWLER_PROXY:
            self.skip("Сайты школ", "запросы идут через прокси CRAWLER_PROXY (DNS и TCP ниже — без прокси)")
        agent = CrawlAgent.objects.filter(organization=org).first()
        if agent and agent.is_active:
            self.ok("Агент обхода", f"работает, последний отчёт {agent.last_seen_at:%d.%m %H:%M}: {agent.last_summary}")
        crawler, session = CompositeCrawler(), crawler_session(retries=0)
        blocked = ok_count = 0
        sources = Source.objects.filter(school__organization=org, school__active=True).exclude(kind="vk")
        for src in sources.select_related("school")[:limit]:
            name, u = src.school.name[:45], urlparse(src.url)
            port = u.port or (443 if u.scheme == "https" else 80)
            try:
                ips = sorted({i[4][0] for i in socket.getaddrinfo(u.hostname, port, proto=socket.IPPROTO_TCP)})
            except socket.gaierror as exc:
                self.bad("Сайт школы", f"{name}: адрес {u.hostname} не найден в DNS ({exc})")
                continue
            if not settings.CRAWLER_PROXY:
                started = time.monotonic()
                try:
                    socket.create_connection((u.hostname, port), timeout=10).close()
                except OSError as exc:
                    blocked += 1
                    self.bad("Сайт школы", f"{name}: {u.hostname} ({', '.join(ips[:2])}) не принимает подключение "
                                           f"с этого сервера за 10 с ({exc.__class__.__name__})")
                    continue
                tcp = time.monotonic() - started
            else:
                tcp = 0
            try:
                resp = session.get(src.url, timeout=(10, 25))
                resp.raise_for_status()
            except Exception as exc:
                self.bad("Сайт школы", f"{name}: {describe_error(exc)}")
                continue
            try:
                found = crawler.fetch(source_to_entity(src))
            except Exception as exc:
                self.bad("Сайт школы", f"{name}: страница открылась, но разобрать не удалось: {exc}")
                continue
            ok_count += 1
            self.ok("Сайт школы", f"{name}: подключение {tcp:.1f} с, найдено публикаций {len(found)}")
        if blocked and not ok_count:
            self.stdout.write(self.style.WARNING(
                "\nСайты школ не принимают подключения с этого сервера, хотя адреса находятся. Обычно так бывает, "
                "когда сайт пускает только российские адреса или не пускает адреса хостингов. Варианты:\n"
                "  1) агент обхода на компьютере, где сайты открываются («Настройки» → «Проверка сайтов школ», "
                "docs/АГЕНТ_ОБХОДА.md);\n"
                "  2) прокси с российским адресом: CRAWLER_PROXY=http://логин:пароль@адрес:порт в .env;\n"
                "  3) школы подтверждают размещение сами — по ссылке из письма (работает уже сейчас)."))
