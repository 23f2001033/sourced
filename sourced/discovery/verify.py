"""Match verification (doc 03 0.4, ADR-002, risk R2).

A retrieved document is a *candidate*, not a source, until it is verified to
describe this specific part.

MPN presence is a hard requirement and not a weighted signal. A sibling part's
datasheet scores well on every soft signal: same manufacturer, overlapping
description tokens, identical layout. Only the part number distinguishes them,
and extraction from a sibling produces values that are internally consistent,
correctly formatted, in range, and wrong.
"""
from __future__ import annotations

import re

from sourced.config import MATCH_SCORE_THRESHOLD
from sourced.discovery import manufacturer as mfr
from sourced.discovery.mpn import mpn_present, mpn_variants
from sourced.ingest.chunks import Chunk, Document
from sourced.models import Evidence, MatchResult, SkuInput

_WORD = re.compile(r"[A-Za-z0-9]+")

WEIGHTS = {
    "mpn_in_structured_slot": 0.45,
    "manufacturer_match": 0.20,
    "desc_token_overlap": 0.20,
    "source_authority": 0.15,
}

# Doc 03 lists source authority among the verification signals. It is kept, but
# floored rather than 1/rank: verification asks whether a document *describes*
# this part, which a distributor listing naming it in its MPN field plainly
# does. How much that source's value is then worth is Stage 3's question, and
# ADR-004 answers it there. A raw 1/rank here would make any non-datasheet
# structurally unverifiable, which conflates the two questions.
AUTHORITY_SIGNAL = {1: 1.0, 2: 0.9, 3: 0.8, 4: 0.75, 5: 0.6}


def token_overlap(fragment: str | None, text: str) -> float:
    if not fragment:
        return 0.0
    frag = {t.lower() for t in _WORD.findall(fragment) if len(t) > 1}
    if not frag:
        return 0.0
    hay = {t.lower() for t in _WORD.findall(text or "")}
    return len(frag & hay) / len(frag)


def locate_mpn_chunk(sku: SkuInput, doc: Document) -> Chunk | None:
    """Find the chunk that carries the part number. For a family datasheet
    this is the ordering-information row for *this* part, which is also the
    row every per-part attribute must be read from."""
    best: tuple[int, Chunk] | None = None
    for chunk in doc.chunks:
        if not mpn_present(sku.mpn, chunk.text):
            continue
        rank = {"table_cell": 0, "structured_field": 1, "prose": 2}.get(chunk.locator, 3)
        if best is None or rank < best[0]:
            best = (rank, chunk)
    return best[1] if best else None


def _mpn_evidence(sku: SkuInput, doc: Document, chunk: Chunk | None) -> Evidence:
    span = sku.mpn
    if chunk is not None:
        for v in mpn_variants(sku.mpn):
            if v and v in chunk.text:
                span = v
                break
    return Evidence(
        source_id=doc.source_id,
        source_type=doc.source_type,
        page=chunk.page if chunk else None,
        bbox=chunk.bbox if chunk else None,
        span=span,
        chunk_id=chunk.chunk_id if chunk else "",
        locator=chunk.locator if chunk else "prose",
    )


def verify_match(sku: SkuInput, doc: Document) -> MatchResult:
    signals: dict[str, float] = {}

    # HARD REQUIREMENT: the MPN must appear in the document
    haystack = doc.full_text or " ".join(c.text for c in doc.chunks)
    present = mpn_present(sku.mpn, haystack)
    signals["mpn_present"] = float(present)
    if not present:
        return MatchResult(matched=False, reason="mpn_not_found_in_document",
                           signals=signals)

    chunk = locate_mpn_chunk(sku, doc)
    signals["mpn_in_structured_slot"] = float(
        chunk is not None and chunk.locator in ("table_cell", "structured_field"))
    signals["manufacturer_match"] = float(
        mfr.matches(sku.manufacturer, doc.full_text) if sku.manufacturer else 0.0)
    signals["desc_token_overlap"] = token_overlap(sku.description_fragment, doc.full_text)
    signals["source_authority"] = AUTHORITY_SIGNAL.get(doc.authority_rank, 0.5)

    # a SKU that arrived without a manufacturer must not be penalised for it
    weights = dict(WEIGHTS)
    if not sku.manufacturer:
        w = weights.pop("manufacturer_match")
        total = sum(weights.values())
        weights = {k: v + v / total * w for k, v in weights.items()}

    score = sum(weights[k] * signals[k] for k in weights)
    return MatchResult(
        matched=score >= MATCH_SCORE_THRESHOLD,
        score=round(score, 4),
        reason=None if score >= MATCH_SCORE_THRESHOLD else "match_score_below_threshold",
        evidence=_mpn_evidence(sku, doc, chunk),
        signals=signals,
    )
