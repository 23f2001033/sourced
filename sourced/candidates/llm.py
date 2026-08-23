"""LLM tier (doc 03 2.3, 2.4).

One structured call per SKU covering every attribute the cheaper tiers left
unresolved — not one call per attribute, which is the cost bug of risk R6.

The extraction contract requires a verbatim span for every value, and the span
is then checked deterministically against the cited chunk. That check is free,
removes a large share of fabrications before any model-based check runs, and
produces the explainability artefact at zero extra cost (ADR-006).

Without a configured provider this tier is disabled and returns nothing. It is
never simulated: a stubbed extractor would put values into the record that no
model produced, which is precisely the unsourced-value failure the system
exists to prevent.

The provider may be Anthropic (doc 03's choice) or any OpenAI-compatible
endpoint. Nothing downstream cares, because nothing downstream trusts the
model: a proposal becomes a candidate only if its cited span literally appears
in the cited chunk.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass

from sourced import config
from sourced.candidates.providers import get_provider
from sourced.ingest.chunks import Chunk
from sourced.ingest.normalize import normalised_value_key
from sourced.models import AlternativeValue, Candidate, Evidence, SkuInput
from sourced.registry import CategorySchema

SYSTEM_PROMPT = """You extract product attributes from supplied source chunks for an industrial \
product catalogue.

For each attribute you return: value, unit, raw_text, evidence_span, chunk_id.

evidence_span MUST be an exact substring of the supplied chunk it cites.
If the attribute is not present in the supplied context, omit it entirely.
Do not infer from general knowledge about this product or manufacturer.
If a value is configuration-dependent (e.g. 230V delta / 400V star), return the
primary value and list the others as alternatives with their condition.

Returning nothing for an attribute is correct and expected. A guessed value is
a defect."""

SELF_CONSISTENCY_N = int(os.getenv("SOURCED_SELF_CONSISTENCY_N", "3"))
SELF_CONSISTENCY_TEMPERATURE = 0.3


@dataclass
class ExtractionStats:
    """What the tier proposed and what the deterministic gates removed.

    ADR-006 claims span containment is the primary hallucination gate. That is
    only a claim until the rejection rate is counted, and it cannot be counted
    without running the tier.
    """

    skus: int = 0
    attributes_requested: int = 0
    proposals: int = 0
    accepted: int = 0
    rejected_span_missing: int = 0
    rejected_chunk_unknown: int = 0
    rejected_consistency_split: int = 0
    # the span was real but did not yield the claimed value
    rejected_span_unsupported: int = 0

    @property
    def span_rejection_rate(self) -> float | None:
        return (round(self.rejected_span_missing / self.proposals, 4)
                if self.proposals else None)

    def as_dict(self) -> dict:
        return {
            "skus_calling_the_model": self.skus,
            "attributes_requested": self.attributes_requested,
            "proposals_returned": self.proposals,
            "accepted": self.accepted,
            "rejected_span_not_in_chunk": self.rejected_span_missing,
            "rejected_chunk_id_unknown": self.rejected_chunk_unknown,
            "rejected_self_consistency_split": self.rejected_consistency_split,
            "rejected_span_did_not_support_value": self.rejected_span_unsupported,
            "span_rejection_rate": self.span_rejection_rate,
            "total_rejection_rate": (
                round((self.proposals - self.accepted + self.rejected_span_unsupported)
                      / self.proposals, 4) if self.proposals else None),
        }


class LLMDisabled(RuntimeError):
    pass


def build_tool_schema(schema: CategorySchema, unresolved: list[str]) -> dict:
    props: dict[str, dict] = {}
    for key in unresolved:
        spec = schema.spec(key)
        if spec is None:
            continue
        value_schema: dict
        if spec.type == "enum":
            value_schema = {"type": "string", "enum": list(spec.values or [])}
        elif spec.type == "bool":
            value_schema = {"type": "boolean"}
        elif spec.type == "quantity":
            value_schema = {"type": "number"}
        else:
            value_schema = {"type": "string"}
        props[key] = {
            "type": "object",
            "properties": {
                "value": value_schema,
                "unit": {"type": ["string", "null"],
                         "description": f"canonical unit: {spec.canonical_unit or 'none'}"},
                "raw_text": {"type": "string"},
                "evidence_span": {"type": "string",
                                  "description": "exact substring of the cited chunk"},
                "chunk_id": {"type": "string"},
                "alternatives": {
                    "type": "array",
                    "items": {"type": "object", "properties": {
                        "value": value_schema,
                        "unit": {"type": ["string", "null"]},
                        "condition": {"type": "string"},
                    }, "required": ["value", "condition"]},
                },
            },
            "required": ["value", "raw_text", "evidence_span", "chunk_id"],
        }
    return {"type": "object", "properties": props, "required": []}


def render_context(sku: SkuInput, unresolved: list[str], context: list[Chunk]) -> str:
    lines = [f"PART NUMBER: {sku.mpn}",
             f"MANUFACTURER: {sku.manufacturer or 'unknown'}",
             f"DESCRIPTION FRAGMENT: {sku.description_fragment or '(none)'}",
             "",
             "ATTRIBUTES STILL UNRESOLVED: " + ", ".join(unresolved),
             "",
             "SOURCE CHUNKS:"]
    for chunk in context:
        page = f" p{chunk.page}" if chunk.page else ""
        lines.append(f"[{chunk.chunk_id}]{page} ({chunk.locator}) {chunk.text}")
    return "\n".join(lines)


def select_context(sku: SkuInput, chunks: list[Chunk], limit: int = 60) -> list[Chunk]:
    """Prefer chunks that name the part, then table rows, then prose."""
    from sourced.discovery.mpn import mpn_present

    def rank(chunk: Chunk) -> tuple[int, int]:
        names_part = mpn_present(sku.mpn, chunk.text)
        locator_rank = {"table_cell": 0, "structured_field": 1, "prose": 2}.get(
            chunk.locator, 3)
        return (0 if names_part else 1, locator_rank)

    return sorted(chunks, key=rank)[:limit]


def _one_call(sku: SkuInput, schema: CategorySchema, unresolved: list[str],
              context: list[Chunk], temperature: float) -> dict:
    """One structured call. Returns {} when the provider failed or the response
    could not be parsed -- both are counted in provider usage rather than
    silently becoming an empty extraction."""
    provider = get_provider()
    if provider is None:
        raise LLMDisabled("no LLM provider configured; the tier is disabled")
    result = provider.structured_call(
        system=SYSTEM_PROMPT,
        user=render_context(sku, unresolved, context),
        tool_schema=build_tool_schema(schema, unresolved),
        tool_name="return_attributes",
        temperature=temperature)
    return result or {}


def llm_candidates(sku: SkuInput, schema: CategorySchema, unresolved: list[str],
                   context: list[Chunk], self_consistency: bool = True,
                   stats: ExtractionStats | None = None) -> list[Candidate]:
    """One structured call per SKU (plus self-consistency samples on numerics)."""
    if not config.LLM_ENABLED or not unresolved or not context:
        return []

    if stats is not None:
        stats.skus += 1
        stats.attributes_requested += len(unresolved)

    by_id = {c.chunk_id: c for c in context}
    samples = [_one_call(sku, schema, unresolved, context, 0.0)]
    if self_consistency and SELF_CONSISTENCY_N > 1:
        for _ in range(SELF_CONSISTENCY_N - 1):
            samples.append(_one_call(sku, schema, unresolved, context,
                                     SELF_CONSISTENCY_TEMPERATURE))

    out: list[Candidate] = []
    for key in unresolved:
        spec = schema.spec(key)
        if spec is None:
            continue
        proposals = [s[key] for s in samples if isinstance(s.get(key), dict)]
        if not proposals:
            continue
        if stats is not None:
            stats.proposals += 1

        # compare after normalisation, so `0.5 in` and `12.7 mm` register as
        # agreement rather than a false split (doc 03 2.4)
        buckets = Counter(normalised_value_key(p.get("value"), p.get("unit"),
                                               spec.canonical_unit) for p in proposals)
        winner_key, winner_n = buckets.most_common(1)[0]
        ratio = winner_n / len(samples)
        if len(buckets) >= 3 and winner_n == 1:
            if stats is not None:
                stats.rejected_consistency_split += 1
            continue                       # three-way split -> no candidate at all

        proposal = next(p for p in proposals
                        if normalised_value_key(p.get("value"), p.get("unit"),
                                                spec.canonical_unit) == winner_key)
        chunk = by_id.get(str(proposal.get("chunk_id", "")))
        if chunk is None:
            if stats is not None:
                stats.rejected_chunk_unknown += 1
            continue

        span = str(proposal.get("evidence_span", ""))
        # deterministic gate: the cited span must literally appear in the chunk
        if not span or span not in chunk.text:
            if stats is not None:
                stats.rejected_span_missing += 1
            continue
        if stats is not None:
            stats.accepted += 1

        alternatives = [
            AlternativeValue(value=a["value"], unit=a.get("unit"),
                             condition=str(a.get("condition", "")),
                             evidence=Evidence(source_id=chunk.source_id,
                                               source_type=chunk.source_type,
                                               page=chunk.page, bbox=chunk.bbox,
                                               span=span, chunk_id=chunk.chunk_id,
                                               locator=chunk.locator))
            for a in proposal.get("alternatives", []) or [] if isinstance(a, dict)
        ]

        out.append(Candidate(
            canonical_key=key, value=proposal.get("value"),
            unit=proposal.get("unit") or spec.canonical_unit,
            raw_text=str(proposal.get("raw_text", span)), tier="llm",
            evidence=Evidence(source_id=chunk.source_id, source_type=chunk.source_type,
                              page=chunk.page, bbox=chunk.bbox, span=span,
                              chunk_id=chunk.chunk_id, locator=chunk.locator),
            producer=f"llm:{config.LLM_MODEL}", consistency_ratio=ratio,
            alternatives=alternatives))
    return out


def rejected_for_span(sku: SkuInput, schema: CategorySchema, unresolved: list[str],
                      context: list[Chunk]) -> list[tuple[str, str]]:
    """Diagnostic: which proposals the span gate rejected, for the ablation that
    measures how much fabrication the cheapest gate catches."""
    if not config.LLM_ENABLED:
        return []
    by_id = {c.chunk_id: c for c in context}
    result = _one_call(sku, schema, unresolved, context, 0.0)
    rejected = []
    for key, proposal in result.items():
        if not isinstance(proposal, dict):
            continue
        chunk = by_id.get(str(proposal.get("chunk_id", "")))
        span = str(proposal.get("evidence_span", ""))
        if chunk is None or not span or span not in chunk.text:
            rejected.append((key, span))
    return rejected
