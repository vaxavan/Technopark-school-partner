#!/bin/sh
# Управление системой на сервере.
#   ./prod.sh up       — первый запуск / перезапуск
#   ./prod.sh update   — забрать новую версию из git и перезапустить
#   ./prod.sh logs     — журналы (Ctrl+C — выход)
#   ./prod.sh check    — проверить подключения (YandexGPT, почта, ВК, Max, сайты школ)
#   ./prod.sh backup   — резервная копия базы и файлов (фото, снимки) прямо сейчас
#   ./prod.sh restore backups/db-2026-10-14.sql.gz — восстановить базу из копии
set -e
cd "$(dirname "$0")"
# Какие файлы compose использовать, задаёт COMPOSE_FILE в .env:
#   без домена (по IP и порту)  — ничего не указывать (только docker-compose.yml);
#   с доменом и HTTPS           — COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
DC="docker compose -p technopark"

case "$1" in
  up)      $DC up -d --build && $DC restart nginx && $DC ps ;;
  update)  git pull && $DC up -d --build && $DC restart nginx && $DC ps ;;
  logs)    $DC logs -f --tail=100 ${2:-} ;;
  check)   $DC exec web python manage.py check_integrations ;;
  backup)  mkdir -p backups && $DC exec -T db pg_dump -U technopark technopark | gzip > "backups/db-$(date +%F-%H%M).sql.gz" \
             && $DC exec -T web tar czf - -C /app media > "backups/media-$(date +%F-%H%M).tar.gz" && ls -lh backups | tail -4 ;;
  restore) [ -f "$2" ] || { echo "Укажите файл копии"; exit 1; }
           $DC stop web worker beat
           gunzip -c "$2" | $DC exec -T db psql -U technopark -d technopark -q -v ON_ERROR_STOP=1 \
             -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" -f -
           $DC start web worker beat ;;
  *)       sed -n '2,9p' "$0" ;;
esac
