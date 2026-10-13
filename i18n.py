"""User interface translations.

Every language is a JSON file in lang/ (lang/en.json is the reference).
To add a language, copy lang/en.json to lang/<code>.json and translate the
values; missing keys fall back to English.
"""

import json
from pathlib import Path

LANG_DIR = Path(__file__).resolve().parent / "lang"
DEFAULT = "en"

_strings = {}
_current = DEFAULT


def _load(code):
    if code not in _strings:
        try:
            with open(LANG_DIR / f"{code}.json", encoding="utf-8") as f:
                _strings[code] = json.load(f)
        except (OSError, ValueError):
            _strings[code] = {}
    return _strings[code]


def available():
    """{code: native language name} of all UI translations."""
    return {p.stem: _load(p.stem).get("language.name", p.stem) for p in sorted(LANG_DIR.glob("*.json"))}


def set_language(code):
    global _current
    _current = code if code in available() else DEFAULT


def current():
    return _current


def tr(key, lang=None, /, **kwargs):
    """Translated text for key; lang overrides the UI language (e.g. 'en' for AI instructions).

    lang is positional-only, so "lang" can also be a placeholder in the text.
    """
    text = _load(lang or _current).get(key)
    if text is None:
        text = _load(DEFAULT).get(key, key)
    return text.format(**kwargs) if kwargs else text
