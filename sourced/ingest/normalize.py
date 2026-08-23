"""Unit and vocabulary normalisation (doc 03 §2.1, doc 04 values_match).

`pint` does the unit algebra. Hand-rolled conversion accumulates errors and
cannot express dimensionality checks.
"""
from __future__ import annotations

import math
import re

import pint

ureg = pint.UnitRegistry()
Q = ureg.Quantity

UNICODE_FRACTIONS = {
    "½": "1/2", "¼": "1/4", "¾": "3/4", "⅛": "1/8",
    "⅜": "3/8", "⅝": "5/8", "⅞": "7/8", "⅓": "1/3", "⅔": "2/3",
}

# raw unit token -> pint-parseable canonical unit
UNIT_ALIASES = {
    '"': "inch", "”": "inch", "″": "inch", "in": "inch", "in.": "inch",
    "inch": "inch", "inches": "inch", "mm": "millimeter", "cm": "centimeter",
    "m": "meter", "a": "ampere", "amp": "ampere", "amps": "ampere",
    "ma": "milliampere", "v": "volt", "vac": "volt", "vdc": "volt",
    "kv": "kilovolt", "mv": "millivolt", "w": "watt", "kw": "kilowatt",
    "hp": "horsepower", "ohm": "ohm", "ω": "ohm", "mohm": "milliohm",
    "mω": "milliohm", "hz": "hertz", "khz": "kilohertz", "mhz": "megahertz",
    "degc": "degC", "°c": "degC", "c": "degC", "degf": "degF", "°f": "degF",
    "n": "newton", "kg": "kilogram", "g": "gram", "psi": "psi", "bar": "bar",
    "nm": "newton * meter", "kn": "kilonewton", "mm2": "millimeter ** 2",
    "awg": None, "pos": None, "pole": None, "poles": None, "": None,
}

FRAC = r"(?:\d+\s*-\s*)?\d+\s*/\s*\d+|\d+(?:\.\d+)?"
DIM = rf"({FRAC})\s*(IN\b|\"|″|INCH|MM\b|CM\b)"

_NUM = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def normalise_unit(raw: str | None) -> str | None:
    """Map a raw unit token to a canonical pint-parseable unit name."""
    if raw is None:
        return None
    text = raw.strip()
    key = text.lower()
    if key in UNIT_ALIASES:
        return UNIT_ALIASES[key]
    # pint unit names are case sensitive -- `degree_Fahrenheit` does not parse
    # lowercased, and silently lowercasing it turned temperatures into bare
    # numbers, which then compared as Celsius
    for candidate in (text, key):
        try:
            ureg.parse_units(candidate)
            return candidate
        except Exception:
            continue
    return None


def parse_mixed_fraction(text: str) -> float:
    """`1-1/2`, `1 1/2`, `3/4`, `0.5` -> float."""
    t = text.strip()
    for uni, ascii_ in UNICODE_FRACTIONS.items():
        t = t.replace(uni, f" {ascii_}")
    t = t.replace("-", " ").strip()
    parts = t.split()
    total = 0.0
    for p in parts:
        if "/" in p:
            num, _, den = p.partition("/")
            total += float(num.strip()) / float(den.strip())
        else:
            total += float(p)
    return total


def parse_dimension(text: str):
    """Extract a dimensional quantity from abbreviation soup."""
    t = text
    for uni, ascii_ in UNICODE_FRACTIONS.items():
        t = t.replace(uni, f" {ascii_}")
    m = re.search(DIM, t, re.I)
    if not m:
        return None
    unit = normalise_unit(m.group(2))
    if unit is None:
        return None
    return Q(parse_mixed_fraction(m.group(1)), unit)


def parse_quantity(text: str, unit_hint: str | None = None):
    """Best-effort `number + unit` out of free text. Returns a pint Quantity."""
    if text is None:
        return None
    t = str(text)
    for uni, ascii_ in UNICODE_FRACTIONS.items():
        t = t.replace(uni, f" {ascii_}")
    dim = parse_dimension(t)
    if dim is not None:
        return dim
    m = _NUM.search(t.replace(",", ""))
    if not m:
        return None
    value = float(m.group(0))
    tail = t[m.end():].strip()
    tok = re.match(r"[°µΩ\w\.\"”″²/]+", tail)
    unit = normalise_unit(tok.group(0)) if tok else None
    if unit is None:
        unit = normalise_unit(unit_hint) if unit_hint else None
    if unit is None:
        return Q(value, "dimensionless")
    return Q(value, unit)


def to_quantity(value, unit: str | None):
    """Build a Quantity from an already-split (value, unit) pair."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        q = parse_quantity(value, unit)
        return q
    u = normalise_unit(unit)
    if u is not None:
        return Q(float(value), u)
    if unit:
        # a stated unit that does not parse is a failure, not a bare number:
        # returning dimensionless here would launder an unreadable unit into a
        # value expressed in the canonical one
        return None
    return Q(float(value), "dimensionless")


def to_canonical(value, unit: str | None, canonical_unit: str | None = None):
    """Convert to the category's canonical unit where dimensions permit."""
    q = to_quantity(value, unit)
    if q is None:
        return None
    cu = normalise_unit(canonical_unit) if canonical_unit else None
    if cu is None:
        return q
    try:
        return q.to(cu)
    except Exception:
        return q


def normalise_categorical(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    s = re.sub(r"[\s\-/]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s.strip("_")


def is_quantity_like(value, unit: str | None) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    return bool(unit) and _NUM.search(str(value)) is not None


def values_match(pred_value, pred_unit, label_value, label_unit, rel_tol: float = 0.01) -> bool:
    """Tolerance-aware comparison (doc 04). `0.5 in` equals `12.7 mm`."""
    p_is_q = is_quantity_like(pred_value, pred_unit)
    l_is_q = is_quantity_like(label_value, label_unit)
    if p_is_q and l_is_q:
        p = to_quantity(pred_value, pred_unit)
        l = to_quantity(label_value, label_unit)
        if p is None or l is None:
            return False
        if p.dimensionality != l.dimensionality:
            # a bare number matches a dimensioned label only if magnitudes agree
            if p.dimensionless or l.dimensionless:
                return math.isclose(float(p.magnitude), float(l.magnitude), rel_tol=rel_tol)
            return False
        try:
            l_conv = l.to(p.units)
        except Exception:
            return False
        return math.isclose(float(p.magnitude), float(l_conv.magnitude), rel_tol=rel_tol, abs_tol=1e-9)
    return normalise_categorical(pred_value) == normalise_categorical(label_value)


def normalised_value_key(value, unit: str | None, canonical_unit: str | None = None) -> str:
    """Grouping key for adjudication: pint-aware value equality."""
    if is_quantity_like(value, unit):
        q = to_canonical(value, unit, canonical_unit)
        if q is not None:
            mag = float(q.magnitude)
            return f"q:{round(mag, 6)}:{q.dimensionality}"
    return f"c:{normalise_categorical(value)}"


def normalise_text(text: str) -> str:
    """Aggressive normalisation for MPN-presence search inside documents."""
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())
