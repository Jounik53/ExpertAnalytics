from __future__ import annotations

from dataclasses import dataclass

from app.ui.language import Language
from app.ui.locales import LABELS_EN, LABELS_RU


@dataclass(frozen=True)
class LocalePack:
    code: str
    labels: dict[str, str]


PACKS = {
    Language.RU.value: LocalePack(code=Language.RU.value, labels=LABELS_RU),
    Language.EN.value: LocalePack(code=Language.EN.value, labels=LABELS_EN),
}


class I18n:
    def __init__(self, locale_code: str = Language.RU.value) -> None:
        self.set_locale(locale_code)

    def set_locale(self, locale_code: str) -> None:
        self._current = PACKS.get(locale_code, PACKS[Language.RU.value])

    @property
    def locale(self) -> str:
        return self._current.code

    def t(self, key: str) -> str:
        return self._current.labels.get(key, key)
