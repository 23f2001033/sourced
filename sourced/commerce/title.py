"""Title generation (doc 03 6).

Deterministic, not generated. Only published attributes may fill slots, missing
slots are dropped rather than invented. Reproducible, free, and incapable of
hallucinating.
"""
from __future__ import annotations

import re
from fractions import Fraction

from sourced.models import AttributeValue, ProductRecord
from sourced.registry import CategorySchema

_SLOT = re.compile(r"\{(\w+)\}")

DISPLAY_OVERRIDES = {
    "vertical": "Vertical", "right_angle": "Right-Angle",
    "through_hole": "Through-Hole", "surface_mount": "Surface-Mount",
    "panel_mount": "Panel-Mount", "cable_mount": "Cable-Mount",
    "gold": "Gold", "tin": "Tin", "tin_lead": "Tin-Lead", "silver": "Silver",
    "brass": "Brass", "phosphor_bronze": "Phosphor Bronze",
    "beryllium_copper": "Beryllium Copper", "stainless_steel": "Stainless Steel",
    "nylon_66": "Nylon 66", "pbt": "PBT", "lcp": "LCP", "pa6t": "PA6T",
    "ul94_v0": "UL94 V-0", "ul94_v1": "UL94 V-1", "ul94_hb": "UL94 HB",
    "female_iron_pipe": "FIP", "male_iron_pipe": "MIP", "socket_weld": "Socket-Weld",
    "butt_weld": "Butt-Weld", "push_fit": "Push-Fit",
    "stainless_steel_304": "304 Stainless Steel",
    "stainless_steel_316": "316 Stainless Steel",
    "malleable_iron": "Malleable Iron", "ductile_iron": "Ductile Iron",
    "carbon_steel": "Carbon Steel", "pvc": "PVC", "cpvc": "CPVC", "pex": "PEX",
    "npt": "NPT", "nptf": "NPTF", "bspt": "BSPT", "bspp": "BSPP",
    "schedule_10": "Schedule 10", "schedule_40": "Schedule 40",
    "schedule_80": "Schedule 80", "schedule_160": "Schedule 160",
    "class_125": "Class 125", "class_150": "Class 150", "class_200": "Class 200",
    "class_300": "Class 300", "class_600": "Class 600", "class_1000": "Class 1000",
    "class_2000": "Class 2000", "class_3000": "Class 3000",
}


# Pipe sizes are named in fractions, not decimals. A 3/8" bushing listed as
# "0.374016 in" -- the decimal that falls out of converting 9.5 mm -- is not a
# size a buyer recognises, and a title is merchandising copy before it is data.
FRACTIONAL_UNITS = {"inch", "in"}
MAX_DENOMINATOR = 16
FRACTION_TOLERANCE = 0.01


def as_fraction(value: float) -> str | None:
    """`0.374016` -> `3/8`, `1.5` -> `1-1/2`, `2.0` -> `2`. None if the value is
    not close enough to a catalogue fraction to name one honestly."""
    try:
        approx = Fraction(float(value)).limit_denominator(MAX_DENOMINATOR)
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    if value == 0 or abs(float(approx) - float(value)) / abs(float(value)) > FRACTION_TOLERANCE:
        return None
    whole, remainder = divmod(approx.numerator, approx.denominator)
    if remainder == 0:
        return str(whole)
    if whole == 0:
        return f"{remainder}/{approx.denominator}"
    return f"{whole}-{remainder}/{approx.denominator}"


def render_value(attr: AttributeValue) -> str:
    value = attr.value
    if isinstance(value, bool):
        return "RoHS Compliant" if value else "Non-RoHS"
    if isinstance(value, float):
        if (attr.unit or "").lower() in FRACTIONAL_UNITS:
            fraction = as_fraction(value)
            if fraction is not None:
                return f'{fraction}"'
        text = f"{value:g}"
    elif isinstance(value, str):
        text = DISPLAY_OVERRIDES.get(value, value.replace("_", " ").title())
    else:
        text = str(value)
    return text


def build_title(product: ProductRecord, schema: CategorySchema) -> tuple[str, str, list[str]]:
    """Returns (title, template, keys that filled slots)."""
    template = schema.title_template or "{manufacturer} {mpn}"
    published = {k: v for k, v in product.attributes.items() if v.resolution == "published"}

    used: list[str] = []
    values = {
        "manufacturer": product.manufacturer_resolved or product.manufacturer or "",
        "mpn": product.mpn,
        "series": _series(product),
    }
    for key, attr in published.items():
        values[key] = render_value(attr)

    def substitute(match: re.Match) -> str:
        slot = match.group(1)
        value = values.get(slot, "")
        if value and slot in published:
            used.append(slot)
        return str(value)

    title = _SLOT.sub(substitute, template)
    title = re.sub(r"\s*,\s*,", ",", title)
    title = re.sub(r"\s{2,}", " ", title).strip(" ,-")
    title = re.sub(r",\s*$", "", title)
    # a slot that produced nothing leaves a dangling separator; drop those
    title = re.sub(r"\s+,", ",", title)
    if not title.strip():
        title = f"{values['manufacturer']} {product.mpn}".strip()
    return title, template, sorted(set(used))


def _series(product: ProductRecord) -> str:
    """Best-effort series token from the part number: the leading alphanumeric
    run before the first separator. Purely mechanical, never invented."""
    m = re.match(r"[A-Za-z0-9]+", product.mpn.strip())
    return m.group(0) if m else ""
