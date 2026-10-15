"""Адаптеры языковой модели: YandexGPT (Yandex AI Studio) и демо-модель без сети."""

import json
import logging
import re

import requests
from django.conf import settings

from core.ports import LLMError

log = logging.getLogger(__name__)

YANDEX_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"


class YandexGPT:
    name = "yandex"

    def __init__(self, api_key, folder_id, model, model_lite):
        self.api_key = api_key
        self.folder_id = folder_id
        self.model = model
        self.model_lite = model_lite

    def complete(self, system, user, *, json_mode=False, lite=False, timeout=90, temperature=0.3):
        model = self.model_lite if lite else self.model
        body = {
            "modelUri": f"gpt://{self.folder_id}/{model}",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": "2000"},
            "messages": [{"role": "system", "text": system}, {"role": "user", "text": user}],
        }
        if json_mode:
            body["jsonObject"] = True
        try:
            resp = requests.post(
                YANDEX_URL,
                headers={"Authorization": f"Api-Key {self.api_key}", "x-folder-id": self.folder_id},
                json=body,
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise LLMError(f"Нет связи с YandexGPT: {exc}") from exc
        if resp.status_code == 400 and json_mode:
            # если выбранная модель не поддерживает режим JSON — повторяем без него,
            # формат всё равно проверяется схемой pydantic
            return self.complete(system, user, json_mode=False, lite=lite, timeout=timeout,
                                 temperature=temperature)
        if resp.status_code != 200:
            # тело ответа логируем без заголовков запроса (там ключ)
            raise LLMError(f"YandexGPT вернул HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()["result"]["alternatives"][0]["message"]["text"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("Неожиданный формат ответа YandexGPT") from exc


class FakeLLM:
    """Демо-модель: работает без ключа, детерминированно собирает текст из заявки.
    Нужна для показа системы и для автоматических тестов."""

    name = "fake"

    def complete(self, system, user, *, json_mode=False, lite=False, timeout=90, temperature=0.3):
        if json_mode and "complete" in system:
            return self._completeness(user)
        if "КОМАНДА ПРАВКИ" in user:
            src = user.split("ТЕКСТ:", 1)[-1].strip()
            return src
        facts = user.split("СВЕДЕНИЯ:", 1)[-1].split("\n\nПредыдущий вариант:", 1)[0].strip()
        fields = dict(re.findall(r"^(Тема|Описание|Когда|Где|От кого): (.*)$", facts, re.M))
        title = fields.get("Тема") or facts.split(".")[0][:90]
        body = fields.get("Описание") or facts
        extra = []
        if fields.get("Когда"):
            extra.append(f"Дата: {fields['Когда']}.")
        if fields.get("Где"):
            extra.append(f"Место: {fields['Где']}.")
        answers = re.findall(r"^— (.*)$", facts, re.M)
        if answers:
            extra.append(" ".join(a.split("?", 1)[-1].strip().rstrip(".") + "." for a in answers))
        text = f"{title}\n\n{body}" + ("\n\n" + " ".join(extra) if extra else "")
        limit_match = re.search(r"не длиннее (\d+) знаков", system + user)
        if limit_match and len(text) > int(limit_match.group(1)):
            cut = text[: int(limit_match.group(1)) - 1]
            text = cut[: cut.rfind(".") + 1] or cut
        return text

    @staticmethod
    def _completeness(user):
        lowered = user.lower()
        questions = []
        if not re.search(r"\d{1,2}[.\s]\d{1,2}|\d{1,2}\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)|вчера|сегодня", lowered):
            questions.append({"id": "q1", "type": "text", "text": "Когда проходило мероприятие?"})
        if "призёр" not in lowered and "победител" not in lowered and "конкурс" in lowered:
            questions.append({"id": "q2", "type": "yes_no", "text": "Были ли призёры?"})
        if "ответы автора" in lowered:
            questions = []
        return json.dumps({"complete": not questions, "missing": [q["text"] for q in questions],
                           "questions": questions}, ensure_ascii=False)


def llm_credentials(organization=None):
    """(ключ, каталог, модель, модель_lite, источник): сначала настройки организации, затем .env."""
    acc = None
    if organization is not None:
        from core.models import LLMAccount

        acc = LLMAccount.objects.filter(organization=organization).first()
    if acc and acc.api_key and acc.folder_id:
        return (acc.api_key, acc.folder_id, acc.model or settings.YANDEX_MODEL,
                acc.model_lite or settings.YANDEX_MODEL_LITE, "settings")
    if settings.YANDEX_API_KEY and settings.YANDEX_FOLDER_ID:
        return (settings.YANDEX_API_KEY, settings.YANDEX_FOLDER_ID, settings.YANDEX_MODEL,
                settings.YANDEX_MODEL_LITE, "env")
    return None


def get_llm(organization=None):
    """Модель для организации. LLM_BACKEND=fake (тесты, демо) принудительно включает демо-модель."""
    if settings.LLM_BACKEND == "fake":
        return FakeLLM()
    creds = llm_credentials(organization)
    if creds is None:
        return FakeLLM()
    key, folder, model, lite, _ = creds
    return YandexGPT(key, folder, model, lite)


def llm_mode(organization=None):
    if settings.LLM_BACKEND == "fake":
        return "demo"
    creds = llm_credentials(organization)
    return "demo" if creds is None else creds[4]


def extract_json(text: str) -> str:
    """Модель иногда оборачивает JSON в ```; достаём объект."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
