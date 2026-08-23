"""Level 1 — per-attribute validation (doc 03 4).

Type conformance, span containment, unit parses under pint, enum membership,
plausible range for the category. All deterministic, all free.
"""
from __future__ import annotations

from sourced.ingest.chunks import Chunk
from sourced.ingest.normalize import normalise_unit, to_canonical
from sourced.models import Candidate, CheckResult
from sourced.registry import AttributeSpec


def check_span_present(candidate: Candidate, chunks: dict[str, Chunk]) -> CheckResult:
    """The claimed evidence must appear verbatim in the cited chunk (ADR-006).

    Applied to every tier, not only the LLM: a rule that cannot point at the
    text it fired on is no more auditable than a fabrication."""
    chunk = chunks.get(candidate.evidence.chunk_id)
    if chunk is None:
        return CheckResult(passed=False, level="attribute",
                           detail=f"cited chunk {candidate.evidence.chunk_id} not found")
    ok = bool(candidate.evidence.span) and candidate.evidence.span in chunk.text
    return CheckResult(passed=ok, level="attribute",
                       detail=None if ok else "cited span is not present in the chunk")


def check_type(candidate: Candidate, spec: AttributeSpec) -> CheckResult:
    v = candidate.value
    if spec.type == "bool":
        ok = isinstance(v, bool)
    elif spec.type == "enum":
        ok = isinstance(v, str)
    elif spec.type == "quantity":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    else:
        ok = v is not None
    return CheckResult(passed=ok, level="attribute",
                       detail=None if ok else f"{type(v).__name__} is not a {spec.type}")


def check_unit_parsed(candidate: Candidate, spec: AttributeSpec) -> CheckResult:
    if spec.type != "quantity" or spec.canonical_unit is None:
        return CheckResult(passed=True, level="attribute", detail="no unit required")
    parsed = normalise_unit(candidate.unit)
    ok = parsed is not None and to_canonical(
        candidate.value, candidate.unit, spec.canonical_unit) is not None
    return CheckResult(passed=ok, level="attribute",
                       detail=None if ok else f"unit {candidate.unit!r} did not parse "
                                              f"as {spec.canonical_unit}")


def check_enum_valid(candidate: Candidate, spec: AttributeSpec) -> CheckResult:
    if spec.type != "enum":
        return CheckResult(passed=True, level="attribute", detail="not an enum")
    ok = candidate.value in (spec.values or [])
    return CheckResult(passed=ok, level="attribute",
                       detail=None if ok else f"{candidate.value!r} is outside the "
                                              f"controlled vocabulary")


def check_range_plausible(candidate: Candidate, spec: AttributeSpec) -> CheckResult:
    if spec.type != "quantity" or spec.plausible_range is None:
        return CheckResult(passed=True, level="attribute", detail="no range declared")
    if not isinstance(candidate.value, (int, float)) or isinstance(candidate.value, bool):
        return CheckResult(passed=False, level="attribute", detail="value is not numeric")
    lo, hi = spec.plausible_range
    ok = lo <= float(candidate.value) <= hi
    return CheckResult(passed=ok, level="attribute",
                       detail=None if ok else f"{candidate.value} is outside the "
                                              f"plausible range [{lo}, {hi}] for "
                                              f"{spec.key}")


def check_value_supported_by_span(candidate: Candidate,
                                  spec: AttributeSpec) -> CheckResult:
    """The cited text must actually yield the claimed value.

    Span containment alone is weaker than ADR-006 assumes. A model can cite a
    span that genuinely appears in the chunk and pair it with a value the span
    does not support: `nominal_size_secondary = 1 in` citing `"3/4 in"` passed
    containment, published at 0.99 confidence, and was a fabrication about a
    fitting that has no secondary size at all. The span was real; the pairing
    was invented.

    This is applied to the LLM tier only, and deliberately so. Rules and tables
    *derive* the value from the text by deterministic parsing, so re-deriving it
    is a tautology -- and their raw text is an abbreviation like `VERT` that no
    general parser is expected to resolve. The model is the only producer that
    asserts a value/evidence pairing rather than computing one, so it is the
    only one that has to prove it.
    """
    if candidate.tier != "llm":
        return CheckResult(passed=True, level="attribute",
                           detail="value is derived from the span, not asserted")

    from sourced.candidates.tables import coerce
    from sourced.ingest.normalize import values_match

    for text in (candidate.raw_text, candidate.evidence.span):
        if not text:
            continue
        parsed, unit = coerce(str(text), spec)
        if parsed is None:
            continue
        if values_match(candidate.value, candidate.unit, parsed, unit):
            return CheckResult(passed=True, level="attribute")

    return CheckResult(
        passed=False, level="attribute",
        detail=(f"the cited text {candidate.evidence.span!r} does not yield "
                f"{candidate.value!r}"))


def validate_attribute(candidate: Candidate, spec: AttributeSpec,
                       chunks: dict[str, Chunk]) -> dict[str, CheckResult]:
    return {
        "span_present": check_span_present(candidate, chunks),
        "value_supported_by_span": check_value_supported_by_span(candidate, spec),
        "type_conformant": check_type(candidate, spec),
        "unit_parsed": check_unit_parsed(candidate, spec),
        "enum_valid": check_enum_valid(candidate, spec),
        "range_plausible": check_range_plausible(candidate, spec),
    }
