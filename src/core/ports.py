"""Порты: интерфейсы, которые объявляет ядро. Адаптеры в integrations/ и
publishing/platforms.py их реализуют. Ядро не знает, какой адаптер подключён."""

from dataclasses import dataclass, field
from typing import Protocol


class LLMError(Exception):
    pass


class LanguageModel(Protocol):
    name: str

    def complete(self, system: str, user: str, *, json_mode: bool = False,
                 lite: bool = False, timeout: float = 90, temperature: float = 0.3) -> str: ...


@dataclass
class PostResult:
    external_id: str
    url: str
    raw: dict = field(default_factory=dict)


class PlatformError(Exception):
    pass


class Platform(Protocol):
    code: str
    demo: bool

    def publish(self, text: str, image_paths: list[str]) -> PostResult: ...


@dataclass
class Snapshot:
    png: bytes | None
    html: str


class PageSnapshotter(Protocol):
    def capture(self, url: str) -> Snapshot | None: ...
