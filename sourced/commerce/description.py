"""Description generation (doc 03 6, ADR-013, risk R8).

The only generative surface in the system, and therefore the only place
hallucination can re-enter after every extraction gate. Three defences:

  1. only published attributes are supplied as input
  2. every claim is aligned to the attribute that licensed it
  3. any span that cannot be traced back is stripped programmatically

In the UI, hovering a phrase reveals the attribute that licensed it and that
attribute's own provenance chain down to the page and bounding box.

Without an API key the composed path is used: sentences are emitted from
individual attributes, so traceability is exact by construction. The record
states which generator produced the text — a composed description and a
generated one are not the same artefact and should not be reported as one.
"""
from __future__ import annotations

import re

from sourced import config
from sourced.commerce.title import render_value
from sourced.models import AttributeValue, DescriptionClaim, ProductRecord
from sourced.registry import CategorySchema

SYSTEM_PROMPT = """Write a product description using ONLY the supplied attributes.

Do not add specifications, applications, certifications or compatibility claims
that are not in the supplied set. Do not restate the part number. Two to four
sentences. Plain, factual trade copy for an industrial distributor."""

# Each attribute contributes one clause to exactly one sentence group, which is
# what makes the claim map exact rather than inferred.
PHRASES = {
    "pole_count":          ("form", "{value}-position"),
    "pitch":               ("form", "on a {value} mm pitch"),
    "orientation":         ("form", "in {article} {value} orientation"),
    "mounting_type":       ("form", "for {value} mounting"),
    "contact_plating":     ("construction", "{value}-plated contacts"),
    "contact_material":    ("construction", "{article} {value} contact base"),
    "housing_material":    ("construction", "{article} {value} housing"),
    "housing_colour":      ("construction", "{article} {value} housing colour"),
    "termination":         ("construction", "{value} termination"),
    "current_rating":      ("rating", "{value} A per contact"),
    "voltage_rating":      ("rating", "{value} V"),
    "contact_resistance":  ("rating", "{value} mOhm contact resistance"),
    "operating_temp_min":  ("range", "{value} degC"),
    "operating_temp_max":  ("range", "{value} degC"),
    "flammability_rating": ("compliance", "{article} {value} flammability rating"),
    "rohs_compliant":      ("compliance", "RoHS compliance"),

    # pipe fittings
    "nominal_size":           ("form", "{value}"),
    "nominal_size_secondary": ("form", "reducing to {value}"),
    "form_factor":            ("form", "{article} {value}"),
    "bend_angle":             ("form", "{value} degree bend"),
    "end_connection_1":       ("construction", "{article} {value} inlet"),
    "end_connection_2":       ("construction", "{article} {value} outlet"),
    "body_material":          ("construction", "{article} {value} body"),
    "thread_standard":        ("construction", "{value} threads"),
    "wall_schedule":          ("construction", "{value} wall"),
    "pressure_class":         ("rating", "{value}"),
    "max_working_pressure":   ("rating", "{value} psi cold working pressure"),
    "temperature_rating_max": ("rating", "a maximum of {value} degF"),
    "lead_free_compliant":    ("compliance", "lead-free certification"),
    "finish":                 ("compliance", "{article} {value} finish"),
}

LEADS = {
    "form": " It is ",
    "construction": " It uses ",
    "rating": " It is rated ",
    "compliance": " It carries ",
}

# letters whose name begins with a vowel sound, so "an LCP housing" reads right
_VOWEL_SOUNDING = set("AEFHILMNORSX")


def _article(text: str) -> str:
    head = text.strip()[:1].upper()
    if not head:
        return "a"
    if head in "AEIOU":
        return "an"
    if text.strip()[:2].isupper() and head in _VOWEL_SOUNDING:
        return "an"
    return "a"


def _published(product: ProductRecord) -> dict[str, AttributeValue]:
    return {k: v for k, v in product.attributes.items() if v.resolution == "published"}


def _phrase(key: str, attr: AttributeValue) -> tuple[str, str] | None:
    entry = PHRASES.get(key)
    if entry is None:
        return None
    group, template = entry
    if isinstance(attr.value, bool):
        return (group, template) if attr.value else None
    rendered = render_value(attr)
    return group, template.format(value=rendered, article=_article(rendered))


def compose(product: ProductRecord, schema: CategorySchema
            ) -> tuple[str, list[DescriptionClaim]]:
    """Deterministic composition: each clause is emitted by one attribute, so
    the claim map is exact rather than inferred."""
    published = _published(product)
    manufacturer = product.manufacturer_resolved or product.manufacturer or "This part"
    label = (schema.label or schema.category.replace("_", " ")).lower()

    text = f"{manufacturer} {product.mpn} is {_article(label)} {label}."
    claims: list[DescriptionClaim] = []

    groups: dict[str, list[tuple[str, str]]] = {}
    for key, attr in published.items():
        phrase = _phrase(key, attr)
        if phrase is None:
            continue
        groups.setdefault(phrase[0], []).append((key, phrase[1]))

    def emit(prefix: str, key: str, phrase: str) -> None:
        nonlocal text
        start = len(text) + len(prefix)
        text += prefix + phrase
        claims.append(DescriptionClaim(text_span=phrase, span_start=start,
                                       span_end=start + len(phrase),
                                       source_attribute=key))

    for group in ("form", "construction", "rating", "compliance"):
        items = groups.get(group, [])
        if not items:
            continue
        for i, (key, phrase) in enumerate(items):
            if i == 0:
                prefix = LEADS[group]
            elif i == len(items) - 1:
                prefix = " and "
            else:
                prefix = ", "
            emit(prefix, key, phrase)
        text += "."

    # the operating range reads as one clause built from two attributes
    range_items = dict(groups.get("range", []))
    lo = range_items.get("operating_temp_min")
    hi = range_items.get("operating_temp_max")
    if lo and hi:
        emit(" It operates from ", "operating_temp_min", lo)
        emit(" to ", "operating_temp_max", hi)
        text += "."
    elif lo:
        emit(" It operates from ", "operating_temp_min", lo)
        text += " upwards."
    elif hi:
        emit(" It operates up to ", "operating_temp_max", hi)
        text += "."

    return text, claims


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _canonical_number(token: str) -> str:
    try:
        return f"{float(token):g}"
    except ValueError:
        return token


def licence_violations(text: str, product: ProductRecord) -> list[str]:
    """Numbers in generated copy that nothing licenses.

    Claim alignment alone does not hold, and measuring it is what showed that.
    Alignment works a sentence at a time, so a fabricated number rides along
    inside a sentence that also cites a real attribute: "It is rated for Class
    150 service, with 9A current" is marked traceable on the strength of
    `Class 150` and carries an invented current rating with it.

    Two failure modes were observed on generated copy, and both introduce an
    unlicensed number:

      - an invented specification, e.g. a current rating the record abstained on
      - a **mangled part number** -- `WM413-N050` written as `WM413-1050`, or
        `B10B-VH12-A-GR` as `B10B-12`. In a catalogue that is the worse of the
        two: a buyer ordering against it gets the wrong part.

    So the gate is numeric and deterministic, in the same spirit as ADR-006:
    every number must be accounted for by a published attribute value or by a
    verbatim occurrence of the part's own identifiers.
    """
    allowed: set[str] = set()
    for attr in product.attributes.values():
        if attr.resolution != "published" or attr.value is None:
            continue
        for source in (str(attr.value), render_value(attr), str(attr.raw_text)):
            for token in _NUMBER.findall(source):
                allowed.add(_canonical_number(token))

    # digits inside an exact, verbatim identifier are licensed; a rewritten one
    # is not, which is precisely how the mangled part numbers are caught
    remainder = text or ""
    for identifier in filter(None, [product.mpn, product.manufacturer_resolved,
                                    product.manufacturer]):
        remainder = remainder.replace(identifier, " ")

    return [token for token in _NUMBER.findall(remainder)
            if _canonical_number(token) not in allowed]


def _align_claims(text: str, published: dict[str, AttributeValue]
                  ) -> list[DescriptionClaim]:
    """Attribute every sentence of generated copy to the attribute that permits
    it. A sentence mentioning no published value is untraceable."""
    claims: list[DescriptionClaim] = []
    for match in re.finditer(r"[^.!?]+[.!?]", text):
        sentence = match.group(0)
        # offsets must address exactly the text the claim carries: storing a
        # stripped sentence against unstripped bounds puts the UI's hover
        # highlight one character out, and makes the claim unverifiable
        start = match.start() + (len(sentence) - len(sentence.lstrip()))
        end = match.end() - (len(sentence) - len(sentence.rstrip()))
        sentence = sentence.strip()
        low = sentence.lower()
        owner = None
        for key, attr in published.items():
            rendered = render_value(attr).lower()
            token = key.replace("_", " ").lower()
            if (rendered and rendered in low) or token in low:
                owner = key
                break
        claims.append(DescriptionClaim(text_span=sentence, span_start=start,
                                       span_end=end, source_attribute=owner))
    return claims


def generate(product: ProductRecord, schema: CategorySchema,
             use_llm: bool = False) -> tuple[str, list[DescriptionClaim], str]:
    """Returns (description, claims, generator id)."""
    published = _published(product)
    if not published:
        return "", [], "none"

    if not (use_llm and config.LLM_ENABLED):
        text, claims = compose(product, schema)
        return text, claims, "composed"

    from sourced.candidates.providers import get_provider

    provider = get_provider()
    if provider is None:
        text, claims = compose(product, schema)
        return text, claims, "composed"

    attribute_lines = "\n".join(
        f"- {k}: {render_value(v)}{' ' + v.unit if v.unit else ''}"
        for k, v in published.items())
    text = provider.text_call(
        system=SYSTEM_PROMPT,
        user=(f"PRODUCT: {product.manufacturer_resolved or ''} {product.mpn}\n"
              f"CATEGORY: {schema.label or schema.category}\n"
              f"PUBLISHED ATTRIBUTES:\n{attribute_lines}")).strip()
    if not text:
        # a failed generation is not a reason to ship an empty description
        text, claims = compose(product, schema)
        return text, claims, "composed_after_generation_failed"

    claims = _align_claims(text, published)
    # any claim that cannot be traced back is stripped
    for claim in [c for c in claims if c.source_attribute is None]:
        text = text.replace(claim.text_span, "").strip()
    text = re.sub(r"\s{2,}", " ", text)

    # Hard gate. Stripping untraceable claims is not sufficient on its own: a
    # fabricated number survives inside a sentence that also cites a real
    # attribute. Copy carrying a number nothing licenses is not published --
    # the deterministic composition is, because it cannot invent one.
    violations = licence_violations(text, product)
    if violations:
        text, claims = compose(product, schema)
        return text, claims, "composed_after_failed_licence_check"

    claims = _align_claims(text, published)
    return text, claims, f"llm:{config.LLM_MODEL}"


def facets(product: ProductRecord, schema: CategorySchema) -> dict[str, str | float]:
    """Normalised, filter-ready values. Published only."""
    out: dict[str, str | float] = {}
    for key, attr in _published(product).items():
        spec = schema.spec(key)
        if spec is None:
            continue
        if spec.type == "quantity" and isinstance(attr.value, (int, float)):
            label = f"{key}_{spec.canonical_unit}" if spec.canonical_unit else key
            out[label] = float(attr.value)
        else:
            out[key] = str(attr.value)
    return out
