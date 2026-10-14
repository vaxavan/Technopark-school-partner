# Развёртывание на Beget

Системе нужны Docker, PostgreSQL, Redis и фоновые процессы (Celery). На **обычном хостинге Beget**
этого нет, поэтому нужен **VPS** (облачный сервер Beget). Дальше всё запускается одной командой.

## 1. Заказать сервер

В панели Beget откройте раздел облачных серверов (VPS) → «Создать сервер».

- **ОС:** Ubuntu 24.04. Если в маркетплейсе есть готовый образ с Docker, можно выбрать его, тогда шаг 3 пропускается.
- **Конфигурация:** 2 ядра, **4 ГБ ОЗУ**, диск от 30 ГБ. На 2 ГБ система тоже запустится, но снимки страниц
  (браузер Chromium) на нескольких материалах сразу могут упереться в память.
- **Доступ:** добавьте SSH-ключ или запомните пароль root из письма.

Запишите **IP-адрес** сервера.

## 2. Домен

Нужен домен или поддомен, например `news.technopark-istok.ru`. Без домена не будет HTTPS, и Дзен не примет RSS.

- Домен на Beget: «DNS» → для домена/поддомена создайте **A-запись** на IP сервера.
- Домен в другом месте: та же A-запись у вашего регистратора.

Запись начинает работать через 5–30 минут. Проверить можно командой `ping news.technopark-istok.ru`:
в ответе должен быть IP сервера.

## 3. Подключиться к серверу и поставить Docker

На Windows откройте PowerShell:

```bash
ssh root@IP_СЕРВЕРА
```

На сервере:

```bash
apt update && apt install -y git
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version     # проверка
```

Откройте нужные порты (SSH, HTTP, HTTPS):

```bash
ufw allow 22 && ufw allow 80 && ufw allow 443 && ufw --force enable
```

> Если позже `docker compose` не сможет скачать образы (ошибка `pull ... forbidden` или таймаут к Docker Hub),
> подключите зеркало:
> ```bash
> echo '{"registry-mirrors": ["https://mirror.gcr.io"]}' > /etc/docker/daemon.json && systemctl restart docker
> ```

## 4. Забрать код

```bash
cd /opt
git clone https://github.com/vaxavan/Technopark-school-partner.git technopark
cd technopark
git checkout main        # после слияния Pull Request; до этого — git checkout system-v1
chmod +x prod.sh
```

Если репозиторий приватный, git спросит логин и пароль. Вместо пароля нужен токен GitHub:
github.com/settings/tokens → «Generate new token (classic)» → право `repo`.

## 5. Настроить `.env`

```bash
cp .env.example .env
nano .env
```

Обязательно для сервера:

```
DOMAIN=news.technopark-istok.ru
SITE_URL=https://news.technopark-istok.ru
DJANGO_ALLOWED_HOSTS=news.technopark-istok.ru
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=длинная_случайная_строка
FIELD_ENCRYPTION_KEY=ключ_шифрования
DIRECTOR_PASSWORD=...
ADMIN_PASSWORD=...
DIRECTOR_EMAIL=...
ADMIN_EMAIL=...
YANDEX_API_KEY=...
YANDEX_FOLDER_ID=...
```

Сгенерировать ключи прямо на сервере:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"                   # DJANGO_SECRET_KEY
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # FIELD_ENCRYPTION_KEY
```

> `FIELD_ENCRYPTION_KEY` нельзя менять после того, как в «Настройках» сохранены токены ВК и Max: старые
> токены перестанут расшифровываться, и их придётся ввести заново.

Почту, ВКонтакте и Max можно добавить позже (см. [ТОКЕНЫ.md](ТОКЕНЫ.md)). Сохранить в nano: `Ctrl+O`, `Enter`, `Ctrl+X`.

## 6. Запустить

```bash
./prod.sh up
```

Первая сборка занимает 5–10 минут. Caddy сам получит сертификат HTTPS: для этого домен уже должен указывать
на сервер, а порты 80 и 443 должны быть открыты.

Проверка:

```bash
./prod.sh logs web        # должно быть «Booting worker», без ошибок
./prod.sh check           # YandexGPT, почта, сайты школ
```

Откройте в браузере:
- `https://ваш-домен/login/` — вход (`admin` / `director`);
- `https://ваш-домен/f/istok/` — форма для авторов, эту ссылку можно раздавать;
- `https://ваш-домен/rss/istok.xml` — лента для Дзена.

## 7. Обновление

Когда в репозитории появляется новая версия:

```bash
cd /opt/technopark && ./prod.sh update
```

Миграции базы применяются автоматически при запуске.

## 8. Резервные копии

Контейнер `backup` раз в сутки кладёт дамп базы в `/opt/technopark/backups`, хранятся 14 последних.
Файлы (фото, снимки страниц) лежат в томе Docker `media`.

- Сделать копию вручную: `./prod.sh backup`
- Восстановить: `./prod.sh restore backups/db-2026-10-14.sql.gz`
- Раз в неделю копируйте `backups/` с сервера к себе:
  `scp -r root@IP_СЕРВЕРА:/opt/technopark/backups .` (в PowerShell на своём компьютере)

## Если что-то не работает

| Признак | Что проверить |
|---|---|
| Сайт не открывается | `./prod.sh logs caddy` и `ping домена` — A-запись могла ещё не обновиться |
| «Bad Request (400)» | домен в `DJANGO_ALLOWED_HOSTS` совпадает с адресом в браузере |
| «CSRF verification failed» | `SITE_URL` начинается с `https://` и совпадает с доменом |
| Тексты не готовятся | `./prod.sh logs worker`, затем `./prod.sh check` (ключ Yandex) |
| Не публикуется по расписанию | `./prod.sh logs beat` — планировщик должен раз в минуту отправлять `publish-due` |
| Мало памяти | `free -h`; в `docker-compose.yml` уменьшите `--workers 3` → `2` у web |

Пароль базы в `docker-compose.yml` (`technopark`) менять не обязательно: база не открыта наружу, к ней
ходят только контейнеры внутри сервера.
