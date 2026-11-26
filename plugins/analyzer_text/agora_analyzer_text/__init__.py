"""Текстовый предпросмотр как производный артефакт (ADR-0018).

Анализатор берёт байты и отдаёт **обычные данные** — роль, байты,
`media_type`. Он не импортирует ни ядро, ни продукт: контракт структурный
(`IAnalyzer` в продукте), поэтому плагин остаётся независимым пакетом.

Польза ровно одна, зато настоящая: тяжёлый текстовый документ (лог, JSON,
страница) сводится к короткому предпросмотру, который дешево отдавать
клиенту и показывать в списках. Никаких внешних библиотек — только stdlib.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Версия контракта продукта, с которым совместим плагин (contract 5 ядра).
REQUIRES_CONTRACT = ">=5,<6"

#: Версия алгоритма: попадает в провенанс, смена = новый `derived_by`.
VERSION = "0.1.0"

#: Сколько символов оставлять в предпросмотре по умолчанию.
DEFAULT_LIMIT = 200

#: Роль, под которой результат прикрепляется к объекту.
DEFAULT_ROLE = "preview"

#: Типы содержимого, которые анализатор считает текстом.
DEFAULT_MEDIA_TYPES = ("text/", "application/json", "application/xml", "application/yaml")

#: Чем заменяется обрезанный хвост.
ELLIPSIS = "…"


@dataclass(slots=True)
class TextPreviewAnalyzer:
    """Первые N символов текста как артефакт `text/plain`."""

    limit: int = DEFAULT_LIMIT
    role: str = DEFAULT_ROLE
    media_types: tuple[str, ...] = DEFAULT_MEDIA_TYPES
    suffix: str = ELLIPSIS
    encoding: str = "utf-8"
    warnings: list[str] = field(default_factory=list)

    @property
    def version(self) -> str:
        """Версия алгоритма для `Provenance.version`."""
        return VERSION

    def describe(self) -> str:
        return (
            f"text-preview {VERSION}: первые {self.limit} символов "
            f"{', '.join(self.media_types)} → {self.role} (text/plain)"
        )

    def accepts(self, media_type: str) -> bool:
        """Текст — это `text/*` и перечисленные структурные типы."""
        normalized = media_type.split(";", 1)[0].strip().lower()
        return any(
            normalized.startswith(prefix) if prefix.endswith("/") else normalized == prefix
            for prefix in self.media_types
        )

    def analyze(self, data: bytes, media_type: str) -> tuple[str, bytes, str]:
        """Обрезать текст до `limit` символов (не байтов)."""
        text = data.decode(self.encoding, errors="replace")
        if len(text) > self.limit:
            text = text[: self.limit] + self.suffix
        return self.role, text.encode(self.encoding), "text/plain; charset=utf-8"


def _media_types(value: object) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_MEDIA_TYPES
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list | tuple):
        raise ValueError("text analyzer: 'media_types' must be a list of strings")
    return tuple(str(item) for item in value)


def create(options: Mapping[str, Any]) -> TextPreviewAnalyzer:
    """Фабрика из настроек плагина (конфиг продукта)."""
    limit = options.get("limit", DEFAULT_LIMIT)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("text analyzer: 'limit' must be a positive integer")

    role = str(options.get("role") or DEFAULT_ROLE)
    if not role.strip():
        raise ValueError("text analyzer: 'role' must be non-empty")

    return TextPreviewAnalyzer(
        limit=limit,
        role=role,
        media_types=_media_types(options.get("media_types")),
        encoding=str(options.get("encoding") or "utf-8"),
    )


def plugin() -> dict[str, Any]:
    """Манифест плагина обычными данными (ADR-0013 продукта)."""
    return {"requires_contract": REQUIRES_CONTRACT, "factory": create}


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_MEDIA_TYPES",
    "DEFAULT_ROLE",
    "ELLIPSIS",
    "REQUIRES_CONTRACT",
    "VERSION",
    "TextPreviewAnalyzer",
    "create",
    "plugin",
]
