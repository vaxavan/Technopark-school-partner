"""Агент обхода сайтов школ — отдельный запуск без базы данных и Docker.

Нужен Python 3.10+ и библиотеки из scripts/agent-requirements.txt:
    pip install -r scripts/agent-requirements.txt
    python scripts/crawl_agent.py --server http://159.194.245.202:8080 --token agt_...
    python scripts/crawl_agent.py --server ... --token ... --every 6

Подробнее — docs/АГЕНТ_ОБХОДА.md."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from django.conf import settings  # noqa: E402

settings.configure(
    CRAWLER_USER_AGENT=os.environ.get("CRAWLER_USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 "
                                      "TechnoparkMonitor/1.0"),
    CRAWLER_PROXY=os.environ.get("CRAWLER_PROXY", ""),
    VK_SERVICE_TOKEN="", VK_API_VERSION="5.199",
)

from partners.agent import AgentError, run_forever, run_once  # noqa: E402


def main():
    p = argparse.ArgumentParser(description="Агент обхода сайтов школ")
    p.add_argument("--server", default=os.environ.get("AGENT_SERVER", ""), help="Адрес системы, например http://1.2.3.4:8080")
    p.add_argument("--token", default=os.environ.get("AGENT_TOKEN", ""), help="Ключ агента из «Настроек»")
    p.add_argument("--every", type=float, default=0, help="Повторять каждые N часов (0 — один раз)")
    a = p.parse_args()
    if not a.server or not a.token:
        p.error("укажите --server и --token")
    if a.every:
        run_forever(a.server, a.token, a.every)
    try:
        run_once(a.server, a.token)
    except AgentError as exc:
        print(f"Ошибка: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
