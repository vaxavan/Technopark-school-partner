#!/bin/sh
# Установка на сервер, где уже работают другие проекты (например, uavlog на Beget).
# Ничего чужого не трогает: своя папка /opt/technopark, свои контейнеры (проект «technopark»),
# свой порт. Повторный запуск безопасен — существующий .env не перезаписывается.
#
#   curl -fsSL https://raw.githubusercontent.com/vaxavan/Technopark-school-partner/system-v1/scripts/install_server.sh | sh -s -- 8080
#
# Аргумент — порт, на котором откроется система (по умолчанию 8080).
# Если вы входите не под root, а под пользователем с sudo (например, deploy): ... | sudo sh -s -- 8080
set -e
PORT="${1:-8080}"
DIR=/opt/technopark
BRANCH="${BRANCH:-system-v1}"
REPO=https://github.com/vaxavan/Technopark-school-partner.git

echo "== Проверка порта $PORT"
if ss -tln | grep -q ":$PORT "; then
  echo "Порт $PORT занят. Запустите скрипт с другим портом: ... | sh -s -- 8090"; exit 1
fi

echo "== Файл подкачки (если его нет и памяти меньше 4 ГБ)"
if [ -z "$(swapon --show)" ] && [ "$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)" -lt 3900 ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "Создан файл подкачки 2 ГБ"
else
  echo "Не нужен"
fi

echo "== Код в $DIR"
command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git)
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin "$BRANCH" && git -C "$DIR" checkout -q "$BRANCH" && git -C "$DIR" pull -q
else
  git clone -q -b "$BRANCH" "$REPO" "$DIR"
fi
cd "$DIR"
chmod +x prod.sh

if [ ! -f .env ]; then
  echo "== Создаю .env"
  IP=$(curl -fsS4 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
  SECRET=$(head -c 48 /dev/urandom | base64 | tr -d '\n/+=' | head -c 60)
  FERNET=$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')
  ADMIN_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '\n/+=' | head -c 14)
  DIRECTOR_PASS=$(head -c 12 /dev/urandom | base64 | tr -d '\n/+=' | head -c 14)
  cp .env.example .env
  sed -i \
    -e "s|^DJANGO_SECRET_KEY=.*|DJANGO_SECRET_KEY=$SECRET|" \
    -e "s|^DJANGO_DEBUG=.*|DJANGO_DEBUG=0|" \
    -e "s|^SITE_URL=.*|SITE_URL=http://$IP:$PORT|" \
    -e "s|^DJANGO_ALLOWED_HOSTS=.*|DJANGO_ALLOWED_HOSTS=$IP,localhost|" \
    -e "s|^HTTP_PORT=.*|HTTP_PORT=$PORT|" \
    -e "s|^WEB_WORKERS=.*|WEB_WORKERS=2|" \
    -e "s|^CELERY_CONCURRENCY=.*|CELERY_CONCURRENCY=1|" \
    -e "s|^FIELD_ENCRYPTION_KEY=.*|FIELD_ENCRYPTION_KEY=$FERNET|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=$ADMIN_PASS|" \
    -e "s|^DIRECTOR_PASSWORD=.*|DIRECTOR_PASSWORD=$DIRECTOR_PASS|" \
    .env
  chmod 600 .env
  NEW_ENV=1
fi

# если запущено через sudo — папка принадлежит пользователю (deploy), чтобы автодеплой мог делать git pull
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
  chown -R "$SUDO_USER":"$SUDO_USER" "$DIR"
  id -nG "$SUDO_USER" | grep -qw docker || usermod -aG docker "$SUDO_USER"
  echo "Папка $DIR передана пользователю $SUDO_USER"
fi

echo "== Сборка и запуск (первый раз 5–10 минут)"
docker compose -p technopark up -d --build

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow "$PORT"/tcp >/dev/null && echo "В ufw открыт порт $PORT"
fi

sleep 15
docker compose -p technopark ps
echo
echo "Готово: $(grep ^SITE_URL= .env | cut -d= -f2)/login/"
if [ -n "$NEW_ENV" ]; then
  echo "Вход:  admin@istok.local    / $ADMIN_PASS"
  echo "       director@istok.local / $DIRECTOR_PASS"
  echo "Пароли сохранены в $DIR/.env (ADMIN_PASSWORD, DIRECTOR_PASSWORD)."
fi
echo "Ключи YandexGPT и Telegram впишите в $DIR/.env (nano $DIR/.env), затем:"
echo "  cd $DIR && docker compose -p technopark up -d"
