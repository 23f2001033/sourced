"""Tables tier (doc 03 2.2, ADR-009).

Datasheets are predominantly tabular, and a value read from the wrong row is
internally consistent, correctly formatted, in range, and wrong (risk R4). Two
table shapes are handled:

  parameter/value   `Parameter: Pitch | Value: 1.25 mm`  — family-wide
  header-keyed      `Part Number: X | Positions: 4 | ...` — one row per part

For the second shape the row is only accepted when its part-number cell matches
the SKU being enriched. That is the wrong-row guard, and it is the table-level
counterpart of the Stage 0 match gate.
"""
from __future__ import annotations

import functools
import re

from rapidfuzz import fuzz

from sourced.discovery.mpn import mpn_present
from sourced.ingest.chunks import Cell, Chunk, Document
from sourced.ingest.normalize import (normalise_categorical, normalise_unit,
                                      parse_quantity)
from sourced.models import AlternativeValue, Candidate, Evidence, SkuInput
from sourced.registry import AttributeSpec, CategorySchema

PARAM_HEADERS = {"parameter", "attribute", "specification", "spec", "property",
                 "characteristic", "item"}
VALUE_HEADERS = {"value", "rating", "spec value", "typ", "nominal", "min/max"}
CONDITION_HEADERS = {"conditions", "condition", "notes", "remarks"}
PART_HEADERS = {"part number", "part no", "part no.", "partnumber", "mpn",
                "manufacturer part number", "ordering code", "order code",
                "catalog number", "model"}

BOOL_TRUE = {"yes", "true", "compliant", "rohs compliant", "y", "rohs"}
BOOL_FALSE = {"no", "false", "not compliant", "non compliant", "n"}


@functools.lru_cache(maxsize=4096)
def _alias_index(category: str) -> tuple[tuple[str, str], ...]:
    from sourced.registry import load_schema

    schema = load_schema(category)
    pairs: list[tuple[str, str]] = []
    for spec in schema.attributes:
        pairs.append((_norm_key(spec.key), spec.key))
        for alias in spec.aliases:
            pairs.append((_norm_key(alias), spec.key))
    return tuple(pairs)


def _norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).strip()


def match_key(raw_key: str, category: str, floor: int = 90) -> str | None:
    """Map a raw column header to a canonical attribute key."""
    norm = _norm_key(raw_key)
    if not norm:
        return None
    pairs = _alias_index(category)
    exact = [key for alias, key in pairs if alias == norm]
    if exact:
        return exact[0]
    best, best_score = None, 0
    for alias, key in pairs:
        score = fuzz.token_sort_ratio(norm, alias)
        if score > best_score:
            best, best_score = key, score
    return best if best_score >= floor else None


def coerce(raw_value: str, spec: AttributeSpec) -> tuple[object | None, str | None]:
    """Parse a raw cell into (value, canonical unit) for this attribute."""
    text = (raw_value or "").strip()
    if not text or text in {"-", "—", "n/a", "N/A", "TBD"}:
        return None, None

    if spec.type == "bool":
        low = text.lower()
        if any(t in low for t in BOOL_TRUE):
            return True, None
        if any(t in low for t in BOOL_FALSE):
            return False, None
        return None, None

    if spec.type == "enum":
        norm = normalise_categorical(text)
        for allowed in spec.values or []:
            if norm == allowed or norm.replace("_", "") == allowed.replace("_", ""):
                return allowed, None
        for allowed in spec.values or []:
            if fuzz.token_sort_ratio(norm, allowed) >= 88:
                return allowed, None
        return None, None

    if spec.type == "quantity":
        q = parse_quantity(text, spec.canonical_unit)
        if q is None:
            return None, None
        if spec.canonical_unit:
            # convert on the pint Quantity itself; round-tripping the unit
            # through str() loses offset units such as degF
            target = normalise_unit(spec.canonical_unit)
            try:
                conv = q.to(target)
            except Exception:
                return None, None
            return round(float(conv.magnitude), 6), spec.canonical_unit
        return int(round(float(q.magnitude))), None

    return text, None


def _row_is_for_part(cells: list[Cell], sku: SkuInput) -> bool | None:
    """None when the row carries no part-number cell (family-wide row)."""
    part_cells = [c for c in cells if _norm_key(c.header) in PART_HEADERS]
    if not part_cells:
        return None
    return any(mpn_present(sku.mpn, c.value) for c in part_cells)


def _evidence(chunk: Chunk, cell: Cell | None, span: str) -> Evidence:
    return Evidence(
        source_id=chunk.source_id,
        source_type=chunk.source_type,
        page=chunk.page,
        bbox=(cell.bbox if cell and cell.bbox else chunk.bbox),
        span=span,
        chunk_id=chunk.chunk_id,
        locator=chunk.locator,
    )


def _condition(cells: list[Cell]) -> str | None:
    for c in cells:
        if _norm_key(c.header) in CONDITION_HEADERS and c.value.strip():
            return c.value.strip()
    return None


def table_candidates(sku: SkuInput, doc: Document, schema: CategorySchema) -> list[Candidate]:
    out: list[Candidate] = []
    for chunk in doc.chunks:
        if chunk.locator not in ("table_cell", "structured_field") or not chunk.cells:
            continue

        belongs = _row_is_for_part(chunk.cells, sku)
        if belongs is False:
            continue          # a sibling part's row — the wrong-row guard

        headers = {_norm_key(c.header): c for c in chunk.cells}
        param_cell = next((c for h, c in headers.items() if h in PARAM_HEADERS), None)
        value_cell = next((c for h, c in headers.items() if h in VALUE_HEADERS), None)

        if param_cell is not None and value_cell is not None:
            # parameter/value shape: the key comes from the cell, not the header
            key = match_key(param_cell.value, schema.category)
            if key:
                out.extend(_emit(sku, chunk, value_cell, key, schema,
                                 producer="table:param_value"))
            continue

        for cell in chunk.cells:
            key = match_key(cell.header, schema.category)
            if not key:
                continue
            out.extend(_emit(sku, chunk, cell, key, schema, producer="table:header_keyed"))
    return out


def _emit(sku: SkuInput, chunk: Chunk, cell: Cell, key: str, schema: CategorySchema,
          producer: str) -> list[Candidate]:
    spec = schema.spec(key)
    if spec is None:
        return []
    raw = (cell.value or "").strip()

    # Configuration-dependent values: "230 V / 400 V" is two valid values under
    # different conditions, not a conflict (doc 02 AlternativeValue).
    #
    # The separator must be surrounded by whitespace. Splitting on any slash
    # before a digit tore fractions in half -- `1/4 in` became 1, `1-1/2 in`
    # became 1 -- which is exactly the fractional-inch failure doc 00 names as
    # the recurring one in this domain. A slash with no space around it is part
    # of a number, not a separator between two of them.
    parts = [p.strip() for p in re.split(r"\s+/\s+", raw) if p.strip()] or [raw]
    primary_value, unit = coerce(parts[0], spec)
    if primary_value is None:
        return []

    alternatives: list[AlternativeValue] = []
    condition = _condition(chunk.cells)
    for extra in parts[1:]:
        alt_value, _ = coerce(extra, spec)
        if alt_value is not None:
            alternatives.append(AlternativeValue(
                value=alt_value, unit=unit,
                condition=condition or "alternative configuration",
                evidence=_evidence(chunk, cell, extra)))

    span = raw if raw in chunk.text else chunk.text
    return [Candidate(
        canonical_key=key, value=primary_value, unit=unit, raw_text=raw, tier="table",
        evidence=_evidence(chunk, cell, span), producer=producer,
        alternatives=alternatives)]
