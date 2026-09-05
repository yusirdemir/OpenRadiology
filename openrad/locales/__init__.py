"""Report locales.

Each ``<lang>.json`` file holds every user-facing string of the generated
documents (section titles, banners, status words, region names, date format).
The engine itself contains no natural-language report text; adding a language
means adding one JSON file with the same keys as ``en.json``.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

LOCALE_DIR = Path(__file__).resolve().parent
DEFAULT_LANGUAGE = "en"


def available_languages() -> Tuple[str, ...]:
    langs = sorted(p.stem for p in LOCALE_DIR.glob("*.json"))
    if DEFAULT_LANGUAGE in langs:
        langs.remove(DEFAULT_LANGUAGE)
        langs.insert(0, DEFAULT_LANGUAGE)
    return tuple(langs)


@lru_cache(maxsize=None)
def load_locale(lang: str) -> Dict[str, Any]:
    path = LOCALE_DIR / f"{lang}.json"
    if not path.is_file():
        raise KeyError(f"no locale '{lang}'; available: {', '.join(available_languages())}")
    data = json.loads(path.read_text(encoding="utf-8"))
    reference = json.loads((LOCALE_DIR / f"{DEFAULT_LANGUAGE}.json").read_text(encoding="utf-8")) if lang != DEFAULT_LANGUAGE else data
    missing = set(reference["text"]) - set(data["text"])
    if missing:
        raise KeyError(f"locale '{lang}' lacks keys: {sorted(missing)}")
    return data
