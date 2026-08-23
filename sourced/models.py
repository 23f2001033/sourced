"""Canonical internal model (doc 02).

Industry standards are projections off this model, not separate pipelines.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

SourceType = Literal[
    "manufacturer_datasheet",
    "manufacturer_page",
    "distributor_api",
    "distributor_page",
    "marketplace",
    # Extension to doc 02's list. The sparse input row is itself a claim about
    # the part, made by the distributor's own system, and the rules tier reads
    # it. Calling it a `distributor_page` would launder the weakest evidence in
    # the system into a web source, so it gets its own type and the lowest
    # authority rank -- it can never outrank a document.
    "internal_record",
]
Locator = Literal["table_cell", "prose", "structured_field", "inferred"]
Tier = Literal["rule", "table", "llm"]
Criticality = Literal["safety", "functional", "cosmetic"]
Resolution = Literal["published", "review", "abstained"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- input


class SkuInput(BaseModel):
    """What a distributor actually has. Everything except mpn may be absent."""

    mpn: str
    manufacturer: str | None = None
    description_fragment: str | None = None
    internal_sku: str | None = None


# ------------------------------------------------------------------- evidence


class Evidence(BaseModel):
    source_id: str
    source_type: SourceType
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    span: str = ""
    chunk_id: str = ""
    locator: Locator = "prose"

    @property
    def authority_rank(self) -> int:
        from sourced.config import AUTHORITY_RANK

        return AUTHORITY_RANK.get(self.source_type, 9)


class Candidate(BaseModel):
    """A proposed value. Multiple candidates compete per attribute."""

    canonical_key: str
    value: bool | int | float | str | None
    unit: str | None = None
    raw_text: str = ""
    tier: Tier
    evidence: Evidence
    producer: str
    agreement_count: int = 1
    consistency_ratio: float = 1.0
    alternatives: list["AlternativeValue"] = Field(default_factory=list)


class AlternativeValue(BaseModel):
    """Configuration-dependent values. "230V / 400V" is not a conflict."""

    value: bool | int | float | str
    unit: str | None = None
    condition: str
    evidence: Evidence | None = None


class RejectedCandidate(BaseModel):
    candidate: Candidate
    reason: str


AbstentionCode = Literal[
    "no_source_located",
    "sources_conflict",
    "failed_validation",
    "self_consistency_split",
    "below_threshold",
    "not_in_source",
]


class AbstentionReason(BaseModel):
    code: AbstentionCode
    detail: str
    resolution_hint: str


class CheckResult(BaseModel):
    passed: bool
    level: Literal["attribute", "relational", "family"]
    detail: str | None = None


PASS = CheckResult(passed=True, level="attribute")


# ------------------------------------------------------------------ resolved


class AttributeValue(BaseModel):
    canonical_key: str
    raw_key: str = ""
    value: bool | int | float | str | None = None
    unit: str | None = None
    raw_text: str = ""

    alternatives: list[AlternativeValue] = Field(default_factory=list)

    resolution: Resolution
    abstention_reason: AbstentionReason | None = None
    winning_candidate: Candidate | None = None
    rejected_candidates: list[RejectedCandidate] = Field(default_factory=list)

    checks: dict[str, CheckResult] = Field(default_factory=dict)
    confidence: float = 0.0
    criticality: Criticality = "functional"

    # calibration bookkeeping
    features: dict[str, float] = Field(default_factory=dict)
    agreement_count: int = 1

    @property
    def tier(self) -> str | None:
        return self.winning_candidate.tier if self.winning_candidate else None

    @property
    def evidence(self) -> Evidence | None:
        return self.winning_candidate.evidence if self.winning_candidate else None


# ------------------------------------------------------------------- product


class SourceLink(BaseModel):
    source_id: str
    source_type: SourceType
    authority_rank: int
    match_confidence: float
    match_evidence: str = ""
    uri: str | None = None
    content_hash: str | None = None


class CategoryAssignment(BaseModel):
    taxonomy: Literal["unspsc", "etim", "internal"]
    code: str
    label: str
    candidates_considered: list[tuple[str, float]] = Field(default_factory=list)
    confidence: float = 0.0
    method: str = ""


class DescriptionClaim(BaseModel):
    """Every assertion in generated copy maps back to the attribute that
    licensed it. This is what makes generated text auditable."""

    text_span: str
    span_start: int
    span_end: int
    source_attribute: str | None


class CommerceOutput(BaseModel):
    title: str
    title_template: str
    title_inputs: list[str] = Field(default_factory=list)
    description: str = ""
    description_claims: list[DescriptionClaim] = Field(default_factory=list)
    description_generator: str = "template"
    facets: dict[str, str | float] = Field(default_factory=dict)


class CompletenessScore(BaseModel):
    required_total: int = 0
    required_filled: int = 0
    published_count: int = 0
    review_count: int = 0
    abstained_count: int = 0
    missing_required: list[str] = Field(default_factory=list)
    blocking_for_publish: list[str] = Field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.required_filled / self.required_total if self.required_total else 0.0


class ProductRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    mpn: str
    mpn_normalised: str
    manufacturer: str | None = None
    manufacturer_resolved: str | None = None

    source_status: Literal["verified", "unverified", "not_located"] = "not_located"
    sources: list[SourceLink] = Field(default_factory=list)

    category: CategoryAssignment | None = None
    # the attribute set this record was extracted against. The taxonomy code
    # says what the part is; this says which schema decided what to look for,
    # and they are not the same thing when a leaf has no populated schema.
    attribute_schema: str | None = None
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)

    commerce: CommerceOutput | None = None
    completeness: CompletenessScore = Field(default_factory=CompletenessScore)

    abstention: AbstentionReason | None = None   # record-level terminal state

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    source_content_hash: str | None = None

    telemetry: dict[str, float] = Field(default_factory=dict)


class MatchResult(BaseModel):
    matched: bool
    score: float = 0.0
    reason: str | None = None
    evidence: Evidence | None = None
    signals: dict[str, float] = Field(default_factory=dict)


Candidate.model_rebuild()
