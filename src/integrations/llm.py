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
        facts = user.split("СВЕДЕНИЯ:", 1)[-1].strip()
        facts = re.sub(r"\s+", " ", facts)
        limit_match = re.search(r"не длиннее (\d+) знаков", system + user)
        title = facts.split(".")[0][:90]
        text = f"{title}\n\n{facts}"
        if limit_match:
            text = text[: int(limit_match.group(1)) - 1]
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


def get_llm():
    if settings.LLM_BACKEND == "yandex":
        return YandexGPT(settings.YANDEX_API_KEY, settings.YANDEX_FOLDER_ID,
                         settings.YANDEX_MODEL, settings.YANDEX_MODEL_LITE)
    return FakeLLM()


def extract_json(text: str) -> str:
    """Модель иногда оборачивает JSON в ```; достаём объект."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text
