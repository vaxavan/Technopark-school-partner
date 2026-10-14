"""Адаптеры площадок. Если токена нет — работает демо-адаптер: «публикует» на
внутреннюю страницу системы, чтобы весь процесс можно было показать без доступов."""

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
    elif channel == Channel.MAX:
        chat, token = (acc.target_id, acc.token) if acc and acc.token else (settings.MAX_CHAT_ID, settings.MAX_BOT_TOKEN)
        if chat and token:
            return MaxPlatform(chat, token, settings.MAX_API_URL, settings.MAX_CA_BUNDLE)
    return DemoPlatform(channel)
