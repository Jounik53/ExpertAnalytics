from __future__ import annotations

MODE_LABELS = {
    "general": "Общий",
    "scanning": "Сканирование",
    "processes": "Процессы",
    "startup": "Автозагрузка",
    "services": "Службы",
    "drivers": "Драйверы",
    "heuristics": "Эвристика",
}

MODE_ORDER = ["general", "scanning", "processes", "startup", "services", "drivers", "heuristics"]
MODE_LABEL_TO_ID = {v: k for k, v in MODE_LABELS.items()}


def normalize_mode(value: str | None) -> str:
    mode = str(value or "general").strip().lower()
    return mode if mode in MODE_LABELS else "general"


def mode_label(mode: str | None) -> str:
    return MODE_LABELS.get(normalize_mode(mode), MODE_LABELS["general"])


def mode_id_from_label(label: str | None) -> str:
    return MODE_LABEL_TO_ID.get(str(label or "").strip(), "general")
