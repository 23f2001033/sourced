# 02 — Data Model

## Core principle

Enrich once into a **canonical internal model**. Industry standards are *projections* off it, not separate pipelines. A distributor needing ETIM for one customer, UNSPSC for another and a bespoke ERP shape for a third gets three views of one record, not three enrichment runs.

---

## Input

```python
class SkuInput(BaseModel):
    """What a distributor actually has. Everything except mpn may be absent."""
    mpn: str
    manufacturer: str | None = None
    description_fragment: str | None = None
    internal_sku: str | None = None
```

---

## Evidence and candidates

```python
class Evidence(BaseModel):
    source_id: str              # document identifier
    source_type: Literal["manufacturer_datasheet", "manufacturer_page",
                         "distributor_api", "distributor_page", "marketplace"]
    page: int | None
    bbox: tuple[float, float, float, float] | None   # x0, y0, x1, y1
    span: str                   # MUST appear verbatim in the source chunk
    chunk_id: str
    locator: Literal["table_cell", "prose", "structured_field", "inferred"]


class Candidate(BaseModel):
    """A proposed value. Multiple candidates compete per attribute."""
    canonical_key: str
    value: float | str | bool | None
    unit: str | None            # canonical, pint-parseable
    raw_text: str               # exactly as it appeared
    tier: Literal["rule", "table", "llm"]
    evidence: Evidence
    producer: str               # rule id / table extractor / model id
    agreement_count: int = 1    # how many independent sources gave this value
```

**Why candidates are separate from resolved values:** adjudication needs the full set. Discarding losing candidates destroys the information needed to explain *why* a value won, and to detect conflict.

---

## Resolved attribute

```python
class AttributeValue(BaseModel):
    canonical_key: str          # "rated_voltage"
    raw_key: str                # "Nominal U"  — as seen in source
    value: float | str | bool | None
    unit: str | None
    raw_text: str

    alternatives: list[AlternativeValue] = []   # configuration-dependent values

    resolution: Literal["published", "review", "abstained"]
    abstention_reason: AbstentionReason | None
    winning_candidate: Candidate | None
    rejected_candidates: list[RejectedCandidate] = []

    checks: dict[str, CheckResult]
    confidence: float           # calibrated, not self-reported
    criticality: Literal["safety", "functional", "cosmetic"]


class AlternativeValue(BaseModel):
    """Industrial attributes are frequently configuration-dependent.
       "230V / 400V" is not a conflict — it is two valid values under
       different wiring configurations."""
    value: float | str
    unit: str | None
    condition: str              # "delta configuration", "60 Hz operation"
    evidence: Evidence


class RejectedCandidate(BaseModel):
    candidate: Candidate
    reason: str                 # "lower source authority", "failed range check"


class AbstentionReason(BaseModel):
    code: Literal["no_source_located", "sources_conflict", "failed_validation",
                  "self_consistency_split", "below_threshold", "not_in_source"]
    detail: str
    resolution_hint: str        # what would fix this
```

**`resolution_hint` is not decoration.** A data steward facing a blank field cannot act. "No authoritative source located for MPN 3GAA132214-ADE; a manufacturer datasheet would resolve this" is a work item.

---

## Check results

```python
class CheckResult(BaseModel):
    passed: bool
    level: Literal["attribute", "relational", "family"]
    detail: str | None


# Level 1 — per attribute
#   span_present      the claimed evidence appears verbatim in the chunk
#   unit_parsed       pint accepted the unit
#   enum_valid        value is in the controlled vocabulary
#   range_plausible   within category bounds
#
# Level 2 — cross attribute
#   phase_voltage_consistent
#   power_frame_consistent
#   pressure_material_consistent
#
# Level 3 — cross SKU
#   family_coherent   not an outlier against sibling SKUs
#   unit_uniform      same unit convention as the rest of the catalog
#   coverage_typical  attribute present on siblings is present here
```

---

## Product record

```python
class ProductRecord(BaseModel):
    id: UUID
    mpn: str
    mpn_normalised: str
    manufacturer: str | None
    manufacturer_resolved: str | None    # canonical form after alias resolution

    source_status: Literal["verified", "unverified", "not_located"]
    sources: list[SourceLink]

    category: CategoryAssignment | None
    attributes: dict[str, AttributeValue]

    commerce: CommerceOutput | None
    completeness: CompletenessScore

    created_at: datetime
    updated_at: datetime
    source_content_hash: str | None      # for change detection


class SourceLink(BaseModel):
    source_id: str
    source_type: str
    authority_rank: int         # 1 = manufacturer datasheet, higher = weaker
    match_confidence: float
    match_evidence: str         # where the MPN was found in this document


class CategoryAssignment(BaseModel):
    taxonomy: Literal["unspsc", "etim", "internal"]
    code: str
    label: str
    candidates_considered: list[tuple[str, float]]
    confidence: float
```

---

## Commerce-ready output

```python
class CommerceOutput(BaseModel):
    title: str
    title_template: str         # which deterministic template produced it
    title_inputs: list[str]     # canonical keys that fed it

    description: str
    description_claims: list[DescriptionClaim]

    facets: dict[str, str | float]      # normalised, filter-ready
    

class DescriptionClaim(BaseModel):
    """Every assertion in generated copy maps back to the attribute
       that licensed it. This is what makes generated text auditable."""
    text_span: str              # substring of the description
    span_start: int
    span_end: int
    source_attribute: str       # canonical_key that permits this claim


class CompletenessScore(BaseModel):
    required_total: int
    required_filled: int
    published_count: int
    review_count: int
    abstained_count: int
    missing_required: list[str]
    blocking_for_publish: list[str]
```

---

## Database schema

PostgreSQL. One database, two shapes: JSONB where the structure varies by category, relational where it must be queried and joined.

```sql
CREATE TABLE products (
    id                  UUID PRIMARY KEY,
    mpn                 TEXT NOT NULL,
    mpn_normalised      TEXT NOT NULL,
    manufacturer        TEXT,
    manufacturer_resolved TEXT,
    source_status       TEXT NOT NULL,
    category_taxonomy   TEXT,
    category_code       TEXT,
    attributes          JSONB NOT NULL DEFAULT '{}',
    commerce            JSONB,
    completeness        JSONB,
    source_content_hash TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- idempotency: reprocessing must update, never duplicate
    CONSTRAINT uq_product UNIQUE (manufacturer_resolved, mpn_normalised)
);

CREATE INDEX idx_products_attributes ON products USING GIN (attributes);
CREATE INDEX idx_products_family     ON products (manufacturer_resolved,
                                                  left(mpn_normalised, 8));
-- family index supports Level 3 coherence checks without a full scan


CREATE TABLE provenance (
    id                  UUID PRIMARY KEY,
    product_id          UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    canonical_key       TEXT NOT NULL,
    value_text          TEXT,
    unit                TEXT,
    raw_text            TEXT,
    tier                TEXT NOT NULL,
    resolution          TEXT NOT NULL,
    confidence          REAL,
    source_id           TEXT,
    source_type         TEXT,
    page                INT,
    bbox                REAL[],
    span                TEXT,
    locator             TEXT,
    checks              JSONB,
    abstention_code     TEXT,
    abstention_detail   TEXT,
    resolution_hint     TEXT,
    model_id            TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_prov_lookup ON provenance (product_id, canonical_key);


CREATE TABLE sources (
    id                  TEXT PRIMARY KEY,
    source_type         TEXT NOT NULL,
    authority_rank      INT  NOT NULL,
    uri                 TEXT,
    content_hash        TEXT NOT NULL,
    fetched_at          TIMESTAMPTZ NOT NULL,
    page_count          INT
);


CREATE TABLE product_sources (
    product_id          UUID REFERENCES products(id) ON DELETE CASCADE,
    source_id           TEXT REFERENCES sources(id),
    match_confidence    REAL NOT NULL,
    match_evidence      TEXT,
    PRIMARY KEY (product_id, source_id)
);


CREATE TABLE eval_labels (
    mpn_normalised      TEXT NOT NULL,
    manufacturer_resolved TEXT NOT NULL,
    canonical_key       TEXT NOT NULL,
    value_text          TEXT,
    unit                TEXT,
    label_source        TEXT NOT NULL,   -- 'distributor_api' | 'hand_audit'
    audited             BOOLEAN NOT NULL DEFAULT false,
    audit_verdict       TEXT,            -- 'correct' | 'incorrect' | 'ambiguous'
    PRIMARY KEY (manufacturer_resolved, mpn_normalised, canonical_key)
);
```

**Notes on the schema**

- The unique constraint on `(manufacturer_resolved, mpn_normalised)` is what makes reprocessing idempotent. Stage 7 depends on it.
- `idx_products_family` on a left-prefix of the normalised MPN is what makes family coherence checks cheap. Without it, Level 3 validation is a full table scan.
- `source_content_hash` on the product and `content_hash` on the source together drive change detection: when a datasheet is revised, only products linked to it are re-verified, and only the attributes that changed.
- `eval_labels.audited` separates silver labels from the hand-audited subset, so label noise can be measured rather than assumed.

---

## Canonical attribute registry

One schema per category. Attributes carry their criticality, which sets the publish threshold.

```yaml
# schemas/electrical_connector.yaml
category: electrical_connector
taxonomy_codes:
  unspsc: "39121400"
attributes:
  - key: contact_material
    type: enum
    values: [brass, phosphor_bronze, beryllium_copper, stainless_steel]
    criticality: functional
    required: true

  - key: current_rating
    type: quantity
    dimension: current           # pint dimension
    canonical_unit: A
    plausible_range: [0.01, 1000]
    criticality: safety          # publish threshold is highest
    required: true

  - key: operating_temp_min
    type: quantity
    dimension: temperature
    canonical_unit: degC
    plausible_range: [-273, 500]
    criticality: functional
    required: true

  - key: housing_colour
    type: enum
    values: [black, grey, white, natural]
    criticality: cosmetic        # lowest publish threshold
    required: false

relational_rules:
  - id: temp_range_ordered
    expr: "operating_temp_min < operating_temp_max"
    message: "Minimum operating temperature is not below maximum"
```

**Criticality tiering is the policy that makes the abstention curve usable.** A wrong `housing_colour` is a cosmetic defect. A wrong `current_rating` is a fire. They must not share a publish threshold. See [04 — Evaluation](04-EVALUATION.md).
