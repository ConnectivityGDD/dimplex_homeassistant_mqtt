import json
from pathlib import Path
from typing import Any

TRANSLATION_DIR = (
    Path(__file__).parent.parent / "translations"
)

_TRANSLATIONS: dict[str, dict[str, Any]] = {}


def load_translations() -> None:
    global _TRANSLATIONS

    if _TRANSLATIONS:
        return

    for translation_file in TRANSLATION_DIR.glob("*.json"):
        try:
            with open(translation_file, encoding="utf-8") as file:
                _TRANSLATIONS[translation_file.stem] = json.load(file)
        except (OSError, json.JSONDecodeError):
            _TRANSLATIONS[translation_file.stem] = {}


def get_translations(language: str) -> dict[str, Any]:
    """Return cached translations loaded during integration setup."""
    return _TRANSLATIONS.get(
        language,
        _TRANSLATIONS.get("en", {}),
    )