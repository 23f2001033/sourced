"""Adjudication (doc 03 3, ADR-003, ADR-004).

No stage short-circuits on an arbitrary threshold. Every tier's candidates
compete, and the losers are retained so the record can explain *why* a value
won and detect conflict.

Authority beats agreement: three distributor pages that copied one wrong
datasheet value are not three independent confirmations, and naive voting
inverts the correct answer in exactly the case where source quality matters
most.
"""
from __future__ import annotations

from collections import defaultdict

from sourced.config import ADJUDICATION_MARGIN, AUTHORITY_DATASHEET
from sourced.ingest.normalize import normalised_value_key
from sourced.models import (AbstentionReason, Candidate, RejectedCandidate)
from sourced.registry import AttributeSpec

# Evidence locator ranking: a table cell states the value, prose mentions it,
# an inference guesses at it.
LOCATOR_SCORE = {"table_cell": 1.00, "structured_field": 0.85, "prose": 0.55,
                 "inferred": 0.25}
TIER_SCORE = {"table": 1.00, "llm": 0.75, "rule": 0.70}
AUTHORITY_SCORE = {1: 1.00, 2: 0.80, 3: 0.65, 4: 0.45, 5: 0.30, 6: 0.15}

W_LOCATOR, W_AUTHORITY, W_AGREEMENT, W_TIER = 0.40, 0.35, 0.15, 0.10


class Resolution:
    """Outcome of adjudicating one attribute's candidate set."""

    def __init__(self, winner: Candidate | None, rejected: list[RejectedCandidate],
                 abstention: AbstentionReason | None, agreement: int,
                 independent_sources: int, close_call: bool = False,
                 top_score: float = 0.0):
        self.winner = winner
        self.rejected = rejected
        self.abstention = abstention
        self.agreement = agreement
        self.independent_sources = independent_sources
        self.close_call = close_call
        self.top_score = top_score


def candidate_score(candidate: Candidate) -> float:
    return (W_LOCATOR * LOCATOR_SCORE.get(candidate.evidence.locator, 0.2)
            + W_AUTHORITY * AUTHORITY_SCORE.get(candidate.evidence.authority_rank, 0.1)
            + W_TIER * TIER_SCORE.get(candidate.tier, 0.5))


def group_score(group: list[Candidate]) -> float:
    """Evidence quality dominates, then authority, then independent agreement."""
    best = max(candidate_score(c) for c in group)
    sources = {c.evidence.source_id for c in group}
    agreement_bonus = W_AGREEMENT * min(len(sources), 3) / 3
    return best + agreement_bonus


def best_evidence(group: list[Candidate]) -> Candidate:
    return max(group, key=candidate_score)


def min_authority(group: list[Candidate]) -> int:
    """Strongest source backing this value (1 is strongest)."""
    return min(c.evidence.authority_rank for c in group)


def adjudicate(candidates: list[Candidate], spec: AttributeSpec | None = None) -> Resolution:
    if not candidates:
        return Resolution(None, [], AbstentionReason(
            code="not_in_source",
            detail="No tier proposed a value for this attribute.",
            resolution_hint="The attribute is absent from every verified source; "
                            "a fuller datasheet or a manufacturer parametric feed "
                            "would resolve it."), 0, 0)

    canonical_unit = spec.canonical_unit if spec else None
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        groups[normalised_value_key(c.value, c.unit, canonical_unit)].append(c)

    ranked = sorted(groups.values(), key=group_score, reverse=True)
    top = ranked[0]
    top_sources = {c.evidence.source_id for c in top}

    if len(ranked) == 1:
        return Resolution(best_evidence(top), [], None, len(top), len(top_sources),
                          top_score=group_score(top))

    second = ranked[1]
    rejected = [RejectedCandidate(candidate=best_evidence(g), reason="")
                for g in ranked[1:]]

    # authoritative disagreement is a genuine conflict, not a vote
    if min_authority(top) == AUTHORITY_DATASHEET and min_authority(second) == AUTHORITY_DATASHEET:
        for r in rejected:
            r.reason = "conflicting manufacturer datasheet"
        return Resolution(
            None, rejected,
            AbstentionReason(
                code="sources_conflict",
                detail=(f"{_render(best_evidence(top))} vs "
                        f"{_render(best_evidence(second))}, both from manufacturer "
                        f"datasheets"),
                resolution_hint="Two manufacturer documents disagree. Confirm which "
                                "datasheet revision supersedes the other, or ask the "
                                "manufacturer to reconcile them."),
            len(top), len(top_sources), top_score=group_score(top))

    delta = group_score(top) - group_score(second)
    for r in rejected:
        r.reason = ("lower evidence quality or source authority"
                    if delta >= ADJUDICATION_MARGIN else "narrowly outranked")

    return Resolution(best_evidence(top), rejected, None, len(top), len(top_sources),
                      close_call=delta < ADJUDICATION_MARGIN,
                      top_score=group_score(top))


def _render(candidate: Candidate) -> str:
    unit = f" {candidate.unit}" if candidate.unit else ""
    return f"{candidate.value}{unit} ({candidate.evidence.source_id})"
