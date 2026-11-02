"""Агент обхода сайтов школ (см. partners/agent.py и docs/АГЕНТ_ОБХОДА.md).

    python manage.py crawl_agent --server http://159.194.245.202:8080 --token agt_...
    python manage.py crawl_agent --server ... --token ... --every 6     # повторять каждые 6 часов
"""

import os

from django.core.management.base import BaseCommand, CommandError

from partners.agent import AgentError, run_forever, run_once


class Command(BaseCommand):
    help = "Проверяет сайты школ с этого компьютера и отправляет найденное на сервер"

    def add_arguments(self, parser):
        parser.add_argument("--server", default=os.environ.get("AGENT_SERVER", ""), help="Адрес системы")
        parser.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""), help="Ключ агента из «Настроек»")
        parser.add_argument("--every", type=float, default=0, help="Повторять каждые N часов (0 — один раз)")

    def handle(self, *args, **o):
        if not o["server"] or not o["token"]:
            raise CommandError("Укажите --server и --token (или AGENT_SERVER и AGENT_TOKEN)")
        if o["every"]:
            run_forever(o["server"], o["token"], o["every"], log=self.stdout.write)
        try:
            run_once(o["server"], o["token"], log=self.stdout.write)
        except AgentError as exc:
            raise CommandError(str(exc)) from exc
