"""Manufacturer alias resolution (doc 03 0.2).

Exact match on the normalised alias table first, `rapidfuzz` as a fallback.
Below the floor nothing is resolved: a wrong manufacturer resolution poisons
every downstream stage silently, so degrading retrieval is the cheaper failure.
"""
from __future__ import annotations

import functools
import re
from pathlib import Path

import yaml
from rapidfuzz import fuzz, process

from sourced import config
from sourced.config import MANUFACTURER_FUZZ_FLOOR


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9 ]", "", (s or "").upper()).strip()


@functools.lru_cache(maxsize=1)
def _alias_table() -> tuple[dict[str, str], list[str]]:
    path = Path(config.SCHEMAS) / "manufacturer_aliases.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    lookup: dict[str, str] = {}
    for canonical, aliases in raw.items():
        lookup[_norm(canonical)] = canonical
        for a in aliases:
            lookup[_norm(a)] = canonical
    return lookup, list(lookup.keys())


def resolve(raw: str | None) -> tuple[str | None, float]:
    """Return (canonical name or None, confidence 0-100)."""
    if not raw:
        return None, 0.0
    lookup, keys = _alias_table()
    key = _norm(raw)
    if key in lookup:
        return lookup[key], 100.0
    hit = process.extractOne(key, keys, scorer=fuzz.token_sort_ratio)
    if hit and hit[1] >= MANUFACTURER_FUZZ_FLOOR:
        return lookup[hit[0]], float(hit[1])
    return None, float(hit[1]) if hit else 0.0


def matches(manufacturer: str | None, text: str) -> bool:
    """Does this document look like it comes from this manufacturer."""
    if not manufacturer:
        return False
    canonical, _ = resolve(manufacturer)
    lookup, _ = _alias_table()
    names = {a for a, c in lookup.items() if c == (canonical or manufacturer)}
    names.add(_norm(manufacturer))
    hay = _norm(text)
    return any(n and n in hay for n in names)
