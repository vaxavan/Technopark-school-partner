#!/bin/sh
set -e

# Ждём, пока поднимется PostgreSQL
if [ -n "$POSTGRES_HOST" ]; then
  echo "Ожидание базы данных $POSTGRES_HOST..."
  python - <<'PY'
import os, time, psycopg
for i in range(60):
    try:
        psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ.get("POSTGRES_PORT", "5432"),
                        dbname=os.environ.get("POSTGRES_DB"), user=os.environ.get("POSTGRES_USER"),
                        password=os.environ.get("POSTGRES_PASSWORD")).close()
        break
    except Exception:
        time.sleep(1)
PY
fi

# Миграции и статика — только в контейнере web
if [ "$RUN_MIGRATIONS" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py collectstatic --noinput
  python manage.py init_system
fi

exec "$@"
