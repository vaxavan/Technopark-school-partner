"""Адаптеры площадок. Если токена нет — работает демо-адаптер: «публикует» на
внутреннюю страницу системы, чтобы весь процесс можно было показать без доступов."""

import json
import logging
import time

import requests
from django.conf import settings

from core.ports import PlatformError, PostResult
from materials.models import Channel

log = logging.getLogger(__name__)


class VkPlatform:
    """Публикация на стену сообщества ВКонтакте от имени сообщества.

    Нужен ключ доступа ПОЛЬЗОВАТЕЛЯ — администратора или редактора группы — с правами
    wall и photos: ключ сообщества не может загружать фото на стену (ошибка 27)."""

    code = Channel.VK
    demo = False
    API = "https://api.vk.com/method/"

    def __init__(self, group_id, token, version="5.199"):
        self.group_id = str(group_id).lstrip("-")
        self.token = token
        self.version = version

    def _call(self, method, **params):
        params.update(access_token=self.token, v=self.version)
        try:
            data = requests.post(self.API + method, data=params, timeout=30).json()
        except (requests.RequestException, ValueError) as exc:
            raise PlatformError(f"ВКонтакте недоступен: {exc}") from exc
        if "error" in data:
            err = data["error"]
            raise PlatformError(f"ВКонтакте: {err.get('error_code')} {err.get('error_msg')}")
        return data["response"]

    def _upload_photo(self, path):
        server = self._call("photos.getWallUploadServer", group_id=self.group_id)
        with open(path, "rb") as fh:
            try:
                up = requests.post(server["upload_url"], files={"photo": fh}, timeout=60).json()
            except (requests.RequestException, ValueError) as exc:
                raise PlatformError(f"Загрузка фото во ВКонтакте: {exc}") from exc
        saved = self._call("photos.saveWallPhoto", group_id=self.group_id, photo=up.get("photo"),
                           server=up.get("server"), hash=up.get("hash"))
        return f"photo{saved[0]['owner_id']}_{saved[0]['id']}"

    def publish(self, text, image_paths):
        attachments = []
        for path in image_paths[:10]:
            try:
                attachments.append(self._upload_photo(path))
            except PlatformError as exc:
                # без прав photos пост уходит без фото — это лучше, чем не уйти вовсе
                log.warning("Фото не загружено во ВКонтакте: %s", exc)
                break
        resp = self._call("wall.post", owner_id=f"-{self.group_id}", from_group=1, message=text,
                          attachments=",".join(attachments))
        post_id = resp["post_id"]
        return PostResult(external_id=str(post_id), url=f"https://vk.com/wall-{self.group_id}_{post_id}", raw=resp)


class MaxPlatform:
    """Публикация в канал Max через Bot API. Бот должен быть администратором канала
    с правом публикации."""

    code = Channel.MAX
    demo = False

    def __init__(self, chat_id, token, base_url, ca_bundle=""):
        self.chat_id = chat_id
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.verify = ca_bundle or True

    def _request(self, method, path, **kw):
        try:
            resp = requests.request(method, self.base_url + path, headers={"Authorization": self.token},
                                    timeout=60, verify=self.verify, **kw)
        except requests.RequestException as exc:
            raise PlatformError(f"Max недоступен: {exc}") from exc
        if resp.status_code >= 400:
            raise PlatformError(f"Max: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            return {}

    def _upload_image(self, path):
        slot = self._request("POST", "/uploads", params={"type": "image"})
        with open(path, "rb") as fh:
            try:
                up = requests.post(slot["url"], files={"data": fh}, timeout=120, verify=self.verify)
                up.raise_for_status()
                uploaded = up.json() if up.content else {}
            except (requests.RequestException, ValueError, KeyError) as exc:
                raise PlatformError(f"Загрузка фото в Max: {exc}") from exc
        token = slot.get("token") or uploaded.get("token")
        if not token and isinstance(uploaded.get("photos"), dict):
            token = next(iter(uploaded["photos"].values()), {}).get("token")
        if not token:
            raise PlatformError("Max не вернул токен загруженного фото")
        return {"type": "image", "payload": {"token": token}}

    def publish(self, text, image_paths):
        body = {"text": text[:4000], "format": "markdown"}
        if image_paths:
            try:
                body["attachments"] = [self._upload_image(image_paths[0])]
            except PlatformError as exc:
                log.warning("Фото не загружено в Max: %s", exc)
        last_exc = None
        for attempt in range(4):  # вложение может быть ещё не готово (attachment.not.ready)
            try:
                data = self._request("POST", "/messages", params={"chat_id": self.chat_id}, json=body)
                break
            except PlatformError as exc:
                last_exc = exc
                if "not.ready" not in str(exc):
                    raise
                time.sleep(2 * (attempt + 1))
        else:
            raise last_exc
        msg = data.get("message", data)
        mid = str(msg.get("body", {}).get("mid") or msg.get("mid") or "")
        return PostResult(external_id=mid, url=msg.get("url") or "", raw=data)


class TelegramPlatform:
    """Публикация в канал или группу Telegram через Bot API. Бот должен быть администратором
    канала с правом «Публикация сообщений». chat_id — «@имя_канала» или число вида -100…"""

    code = Channel.TELEGRAM
    demo = False
    CAPTION_LIMIT = 1024
    TEXT_LIMIT = 4096

    def __init__(self, chat_id, token, base_url="https://api.telegram.org"):
        self.chat_id = str(chat_id).strip()
        self.token = token
        self.base = f"{base_url.rstrip('/')}/bot{token}/"

    def _call(self, method, data=None, files=None):
        for attempt in range(3):
            try:
                resp = requests.post(self.base + method, data=data, files=files, timeout=60)
                body = resp.json()
            except (requests.RequestException, ValueError) as exc:
                # в тексте ошибки requests есть адрес с токеном — его не показываем
                raise PlatformError(f"Telegram недоступен: {type(exc).__name__}") from None
            if body.get("ok"):
                return body["result"]
            retry = (body.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry and attempt < 2:
                time.sleep(min(int(retry), 30))
                for fh in (files or {}).values():
                    fh.seek(0)
                continue
            raise PlatformError(f"Telegram: {body.get('error_code')} {body.get('description')}")
        raise PlatformError("Telegram: слишком много запросов")

    def post_url(self, message):
        chat = message.get("chat", {})
        mid = message.get("message_id")
        if chat.get("username"):
            return f"https://t.me/{chat['username']}/{mid}"
        cid = str(chat.get("id", self.chat_id))
        if cid.startswith("-100"):
            return f"https://t.me/c/{cid[4:]}/{mid}"
        return ""

    def publish(self, text, image_paths):
        text = text[: self.TEXT_LIMIT]
        images = image_paths[:10]
        caption_fits = len(text) <= self.CAPTION_LIMIT
        try:
            return self._publish(text, images, caption_fits)
        except PlatformError as exc:
            fatal = ("401", "403", "chat not found", "недоступен", "слишком много")
            if not images or any(x in str(exc) for x in fatal):
                raise
            # Telegram не принял фото (больше 10 МБ, формат HEIC и т. п.) — публикуем хотя бы текст
            log.warning("Telegram не принял фото, публикуем текст без фото: %s", exc)
            msg = self._call("sendMessage", data={"chat_id": self.chat_id, "text": text})
            return PostResult(external_id=str(msg.get("message_id")), url=self.post_url(msg), raw=msg)

    def _publish(self, text, images, caption_fits):
        handles = [open(p, "rb") for p in images]
        try:
            if len(handles) == 1:
                data = {"chat_id": self.chat_id}
                if caption_fits:
                    data["caption"] = text
                msg = self._call("sendPhoto", data=data, files={"photo": handles[0]})
            elif handles:
                media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(handles))]
                if caption_fits:
                    media[0]["caption"] = text
                msgs = self._call("sendMediaGroup", data={"chat_id": self.chat_id, "media": json.dumps(media)},
                                  files={f"p{i}": h for i, h in enumerate(handles)})
                msg = msgs[0]
            if not handles or not caption_fits:
                # длинный текст отдельным сообщением после фото
                msg = self._call("sendMessage", data={"chat_id": self.chat_id, "text": text})
        finally:
            for h in handles:
                h.close()
        return PostResult(external_id=str(msg.get("message_id")), url=self.post_url(msg), raw=msg)


class DemoPlatform:
    """Без токена: публикация на внутреннюю страницу /demo/... (видно, что это демо)."""

    demo = True

    def __init__(self, code):
        self.code = code

    def publish(self, text, image_paths):
        return PostResult(external_id="demo", url="")


def platform_for(organization, channel):
    """Подбирает адаптер: сначала аккаунт из базы, затем значения из .env, иначе демо."""
    from .models import PlatformAccount

    acc = PlatformAccount.objects.filter(organization=organization, channel=channel, enabled=True).first()
    if channel == Channel.VK:
        group, token = (acc.target_id, acc.token) if acc and acc.token else (settings.VK_GROUP_ID, settings.VK_ACCESS_TOKEN)
        if group and token:
            return VkPlatform(group, token, settings.VK_API_VERSION)
    elif channel == Channel.TELEGRAM:
        chat, token = ((acc.target_id, acc.token) if acc and acc.token
                       else (settings.TELEGRAM_CHAT_ID, settings.TELEGRAM_BOT_TOKEN))
        if chat and token:
            return TelegramPlatform(chat, token, settings.TELEGRAM_API_URL)
    elif channel == Channel.MAX:
        chat, token = (acc.target_id, acc.token) if acc and acc.token else (settings.MAX_CHAT_ID, settings.MAX_BOT_TOKEN)
        if chat and token:
            return MaxPlatform(chat, token, settings.MAX_API_URL, settings.MAX_CA_BUNDLE)
    return DemoPlatform(channel)
