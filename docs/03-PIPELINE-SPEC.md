# 03 — Pipeline Specification

Implementation detail per stage. Algorithms, thresholds, and the reasoning behind each.

---

## Stage 0 — Source discovery and match verification

### 0.1 MPN normalisation

Part numbers are written inconsistently across systems: `3GAA132214-ADE`, `3GAA 132 214 ADE`, `3gaa132214ade`.

```python
def normalise_mpn(raw: str) -> str:
    s = raw.upper()
    s = re.sub(r"[\s\-_./]", "", s)      # strip separators
    s = re.sub(r"[^A-Z0-9]", "", s)      # strip everything else
    return s

def mpn_variants(raw: str) -> list[str]:
    """Search variants — retrieval needs the punctuated forms too."""
    n = normalise_mpn(raw)
    return list({raw.strip(), raw.upper(), n,
                 re.sub(r"[\s_./]", "-", raw.upper())})
```

Keep the raw form. Normalised is for matching and the uniqueness constraint; raw is what a human recognises.

### 0.2 Manufacturer alias resolution

```python
# aliases.yaml
ABB: [ABB, A.B.B., "ASEA BROWN BOVERI", ABB LTD, ABB INC]
```

Exact match on the normalised alias table first. Failing that, `rapidfuzz.process.extractOne` with `token_sort_ratio`. **Below 88, do not resolve** — record `manufacturer_resolved = None` and let it degrade retrieval rather than binding the SKU to the wrong manufacturer. A wrong manufacturer resolution poisons every downstream stage silently.

### 0.3 Candidate retrieval

Hybrid, but **weighted toward lexical** — this is the one place in the system where BM25 matters more than embeddings, because a part number is an exact-match token that dense retrieval handles poorly.

```python
def retrieve_candidates(sku, k=20):
    lexical = bm25.search(" ".join(mpn_variants(sku.mpn)), k=50)
    dense   = faiss.search(embed(sku.description_fragment or sku.mpn), k=50)
    return rrf_fuse(lexical, dense, k=k, weights=(0.7, 0.3))

def rrf_fuse(*rankings, k, weights, c=60):
    """Reciprocal Rank Fusion. Parameter-light, no score normalisation."""
    scores = defaultdict(float)
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += w / (c + rank)
    return sorted(scores, key=scores.get, reverse=True)[:k]
```

`c = 60` is the value from the original RRF paper and works without tuning.

### 0.4 Match verification — the gate

This is the stage that distinguishes this system. A retrieved document is a *candidate*, not a source, until it is verified to describe this specific part.

```python
def verify_match(sku, doc) -> MatchResult:
    signals = {}

    # HARD REQUIREMENT: the MPN must appear in the document
    signals["mpn_present"] = any(
        v in normalise_text(doc.full_text) for v in mpn_variants(sku.mpn)
    )
    if not signals["mpn_present"]:
        return MatchResult(matched=False, reason="mpn_not_found_in_document")

    signals["mpn_in_heading"] = mpn_in_heading_or_table_header(sku, doc)
    signals["manufacturer_match"] = manufacturer_matches(sku, doc)
    signals["desc_token_overlap"] = token_overlap(
        sku.description_fragment, doc.full_text
    )
    signals["source_authority"] = doc.authority_rank

    score = weighted_score(signals)
    return MatchResult(
        matched=score >= 0.70,
        score=score,
        evidence=locate_mpn_span(sku, doc),   # page + bbox for the UI
        signals=signals,
    )
```

**Why the MPN presence check is hard-required and not weighted:** a datasheet for a *different* part in the same family will score well on every soft signal. Manufacturer matches, description tokens overlap heavily, the layout is identical. Only the part number distinguishes them, and extracting from the sibling datasheet produces confidently wrong values that pass every downstream check.

### 0.5 Terminal state

```python
if not verified_sources:
    return ProductRecord(
        source_status="not_located",
        abstention=AbstentionReason(
            code="no_source_located",
            detail=f"No document containing MPN {sku.mpn} was found among "
                   f"{len(candidates)} candidates.",
            resolution_hint="Supply a manufacturer datasheet or catalog page "
                            "for this part number.",
        ),
    )
```

**Do not fall through to LLM extraction here.** Given an MPN and no document, a model will produce attributes from parametric memory. They will look right. This is the precise failure the system exists to prevent.

---

## Stage 1 — Category classification

### 1.1 Offline index

Embed every taxonomy leaf once. UNSPSC commodity level has roughly 40,000 entries; ETIM classes number in the thousands. Both fit comfortably in a FAISS index built in minutes.

```python
leaf_texts = [f"{leaf.code} {leaf.title}. {leaf.definition}" for leaf in taxonomy]
index = faiss.IndexFlatIP(384)
index.add(normalize(embed(leaf_texts)))
```

### 1.2 Retrieve then constrain

```python
def classify(product_text: str, k=20) -> CategoryAssignment:
    candidates = index.search(embed(product_text), k)      # k real codes
    choice = llm.choose(
        product_text=product_text,
        options=[(c.code, c.title, c.definition) for c in candidates],
    )
    # HARD VALIDATION — structurally prevents invented codes
    if choice.code not in {c.code for c in candidates}:
        return abstain("classifier returned a code outside the candidate set")
    return CategoryAssignment(
        code=choice.code,
        candidates_considered=[(c.code, c.score) for c in candidates],
        confidence=choice.confidence_features,
    )
```

Asking a model to pick from 40,000 codes fails twice: the list does not fit in context, and the model emits codes that do not exist. Retrieve-then-constrain removes both failure modes.

### 1.3 Hierarchical fallback

Where the tree is deep and flat retrieval is ambiguous, traverse: segment → family → class → commodity. Each step is a choice among a handful. Errors localise, and the system can **stop at the deepest level it is confident about** rather than forcing a leaf assignment.

---

## Stage 2 — Candidate generation

### 2.1 Rules

**Abbreviation lexicon.** Industrial descriptions are compressed: `1/2IN X 3/4IN BRS 90 ELL FIP 150#`. Each entry maps an abbreviation to both a value and the attribute it fills.

```yaml
BRS:   {value: brass,           key: material}
SS:    {value: stainless_steel, key: material}
FIP:   {value: female_iron_pipe, key: end_connection}
ELL:   {value: elbow,           key: form_factor}
SCH40: {value: schedule_40,     key: wall_schedule}
```

**Dimensional grammar.** Fractional inches are the recurring failure.

```python
FRAC = r"(?:\d+\s*-\s*)?\d+\s*/\s*\d+|\d+(?:\.\d+)?"
DIM   = rf"({FRAC})\s*(IN\b|\"|″|INCH|MM\b|CM\b)"

UNICODE_FRACTIONS = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅛": "1/8"}

def parse_dimension(text: str) -> pint.Quantity | None:
    for uni, ascii_ in UNICODE_FRACTIONS.items():
        text = text.replace(uni, f" {ascii_}")
    m = re.search(DIM, text, re.I)
    if not m:
        return None
    return ureg.Quantity(parse_mixed_fraction(m.group(1)), normalise_unit(m.group(2)))
```

Use `pint` for the algebra. Hand-rolled unit conversion accumulates errors and cannot express dimensionality checks.

**MPN structure decoding.** Manufacturers encode attributes in part numbers. This is learnable without supervision:

1. Group SKUs by resolved manufacturer
2. Find shared prefixes to identify families
3. For each varying segment position, correlate its values against attributes already extracted by lexicon and dimensional rules
4. Where a position correlates above 0.9 with a known attribute across a family, a decoder has been learned
5. Apply it to family members whose descriptions are useless

This produces attributes on records that have almost no text, which is exactly the "limited information" case. **It is also first on the cut list** — valuable but not load-bearing.

### 2.2 Tables

Datasheets are predominantly tabular. Naive prose chunking destroys row-to-column association and is the largest source of wrong values in this domain.

```python
def table_chunks(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            for table in page.find_tables():
                rows = table.extract()
                header = rows[0]
                for row in rows[1:]:
                    cells = [
                        {"header": h, "value": v, "bbox": cell_bbox(table, r, c)}
                        for c, (h, v) in enumerate(zip(header, row))
                    ]
                    yield TableChunk(
                        page=pageno,
                        text=" | ".join(f"{h}: {v}" for h, v in zip(header, row)),
                        cells=cells,
                        bbox=table.bbox,
                    )
```

Prepending the header to every row makes the chunk self-describing, so a retrieved row carries its own column semantics. Each cell keeps its bounding box, which is what powers the highlighted-source panel in the UI.

### 2.3 LLM — one call per SKU, not per attribute

**The cost bug to avoid:** looping tiers per attribute means up to 120 LLM calls per SKU. Collect everything unresolved, retrieve context once, extract in one structured call.

```python
def llm_candidates(sku, schema, unresolved: list[str], context: list[Chunk]):
    result = client.messages.create(
        model=MODEL,
        tools=[{"name": "return_attributes",
                "input_schema": build_schema(unresolved)}],
        tool_choice={"type": "tool", "name": "return_attributes"},
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],   # prefix caching
        messages=[{"role": "user", "content": render(sku, unresolved, context)}],
    )
```

**Prompt caching is the single largest cost lever.** The system prompt, attribute dictionary and schema block are byte-identical across every SKU in a category. Cache the prefix; only the product context varies.

**The extraction contract** requires, for every value, a verbatim span from the supplied context:

```
For each attribute, return:
  value, unit, raw_text, evidence_span, chunk_id

evidence_span MUST be an exact substring of the supplied chunk.
If the attribute is not present in the supplied context, omit it entirely.
Do not infer from general knowledge about this product or manufacturer.
If a value is configuration-dependent (e.g. 230V delta / 400V star),
return the primary value and list the others as alternatives with their condition.
```

Then verify deterministically:

```python
if candidate.evidence.span not in chunk_text(candidate.evidence.chunk_id):
    reject(candidate, reason="span_not_present")
```

Free, deterministic, and it removes a large fraction of fabrications before any model-based check runs.

### 2.4 Self-consistency on numerics

Sample n=3 at temperature 0.3. **Compare after normalisation**, so `0.5 in` and `12.7 mm` register as agreement rather than a false split.

| Outcome | Handling |
|---|---|
| Unanimous after normalisation | high agreement signal |
| Majority | keep, lower agreement signal |
| Three-way split | abstain, `self_consistency_split` |

---

## Stage 3 — Adjudication

```python
def adjudicate(candidates: list[Candidate]) -> Resolution:
    if not candidates:
        return abstain("not_in_source")

    groups = group_by_normalised_value(candidates)   # pint-aware equality

    if len(groups) == 1:
        return resolve(best_evidence(groups[0]), agreement=len(groups[0]))

    ranked = sorted(groups, key=group_score, reverse=True)
    top, second = ranked[0], ranked[1]

    # authoritative disagreement is a genuine conflict, not a vote
    if (min_authority(top) == min_authority(second) == AUTHORITY_DATASHEET
            and not values_equal(top, second)):
        return abstain("sources_conflict",
                       detail=f"{value_of(top)} vs {value_of(second)}, "
                              f"both from manufacturer datasheets")

    if group_score(top) - group_score(second) < MARGIN:
        return review(best_evidence(top), reason="close_call")

    return resolve(best_evidence(top), agreement=len(top))
```

**Ranking factors, in order of weight:**

| Factor | Ordering |
|---|---|
| Evidence locator | `table_cell` > `structured_field` > `prose` > `inferred` |
| Source authority | manufacturer datasheet > manufacturer page > distributor API > distributor page > marketplace |
| Independent agreement | count of distinct sources giving the same normalised value |
| Check results | failures veto regardless of other factors |

**Why authority beats agreement:** three distributor pages that copied one wrong datasheet value are not three independent confirmations. Naive voting inverts the correct answer in exactly the case where it matters.

---

## Stage 4 — Validation

### Level 1 — per attribute
Type conformance, unit parses under `pint`, enum membership, plausible range for the category. All deterministic, all free.

### Level 2 — cross attribute
Relational constraints declared per category in YAML:

```yaml
relational_rules:
  - id: phase_voltage_plausible
    when: "phase == 1"
    expr: "rated_voltage <= 240"
    severity: warn
    message: "Single-phase motors are typically ≤240V; {rated_voltage}V is unusual"

  - id: power_frame_consistent
    lookup: iec_frame_power_table
    tolerance_factor: 2.0
    severity: warn

  - id: temp_range_ordered
    expr: "operating_temp_min < operating_temp_max"
    severity: error
```

Catches a class of error that per-attribute checks structurally cannot: two individually plausible values that cannot both be true.

### Level 3 — cross SKU (family coherence)

Serves the **consistency** outcome. Cheap, no LLM, and finds errors nothing else does.

```python
def family_checks(product, family: list[ProductRecord]):
    for key, attr in product.attributes.items():
        siblings = [f.attributes[key].value for f in family
                    if key in f.attributes and f.attributes[key].value is not None]
        if len(siblings) < 5:
            continue

        if is_numeric(attr.value):
            # robust to outliers, unlike mean/std
            med = median(siblings)
            mad = median_abs_deviation(siblings)
            if mad > 0 and abs(attr.value - med) / mad > 5:
                flag(key, "family_coherent", False,
                     f"{attr.value} is a strong outlier against "
                     f"{len(siblings)} sibling SKUs (median {med})")

        # coverage gap: siblings all have it, this one does not
        if key not in product.attributes and len(siblings) / len(family) > 0.9:
            flag(key, "coverage_typical", False,
                 "Present on >90% of family members but absent here")
```

Median absolute deviation rather than standard deviation, because the population may itself contain the errors being hunted.

---

## Stage 5 — Confidence calibration

### The error being corrected

Asking a model to self-report confidence produces numbers that look meaningful and do not track correctness. An abstention policy built on them is built on noise.

### Features — all observable

```python
def features(attr) -> dict[str, float]:
    return {
        "tier_rule":            attr.tier == "rule",
        "tier_table":           attr.tier == "table",
        "tier_llm":             attr.tier == "llm",
        "locator_table_cell":   attr.evidence.locator == "table_cell",
        "span_present":         attr.checks["span_present"].passed,
        "unit_parsed":          attr.checks["unit_parsed"].passed,
        "range_plausible":      attr.checks["range_plausible"].passed,
        "relational_ok":        all_relational_passed(attr),
        "family_coherent":      attr.checks.get("family_coherent", PASS).passed,
        "agreement_count":      min(attr.agreement_count, 3) / 3,
        "source_authority":     1.0 / attr.evidence_authority_rank,
        "self_consistency":     attr.consistency_ratio,
        "match_confidence":     attr.product_match_confidence,
        "degraded_path":        attr.came_via_fallback,   # OCR / model fallback
    }
```

### Fit and report

Logistic regression against the labelled set, per criticality tier. Small feature count, interpretable coefficients, works on limited data.

**Report a reliability diagram:** predicted confidence bucketed against observed accuracy. If it tracks the diagonal, the abstention threshold is meaningful. Nearly every team will display confidence scores; almost none will demonstrate that theirs are calibrated.

### Publish thresholds by criticality

| Tier | Threshold | Additional requirement |
|---|---|---|
| safety | 0.99 | two independent sources agreeing |
| functional | 0.95 | — |
| cosmetic | 0.85 | — |

A wrong `housing_colour` is a cosmetic defect. A wrong `current_rating` is a fire. They must not share a threshold.

---

## Stage 6 — Commerce-ready generation

### Title — deterministic, not generated

```python
TEMPLATES = {
  "electrical_connector":
      "{manufacturer} {series} {contact_material} Connector, "
      "{current_rating}A, {pole_count}-Pole",
}
```

Only published attributes may fill slots. Missing slots are dropped, not invented. Deterministic titles are reproducible, cheap, and cannot hallucinate.

### Description — generated, constrained, traceable

The only generative surface in the system, therefore the only place hallucination can re-enter.

```python
def generate_description(product) -> tuple[str, list[DescriptionClaim]]:
    published = {k: v for k, v in product.attributes.items()
                 if v.resolution == "published"}

    text = llm.generate(
        system="Write a product description using ONLY the supplied attributes. "
               "Do not add specifications, applications, certifications or "
               "compatibility claims that are not in the supplied set.",
        attributes=published,
    )

    claims = align_claims_to_attributes(text, published)

    # any claim that cannot be traced back is stripped
    for claim in claims:
        if claim.source_attribute is None:
            text = remove_span(text, claim)

    return text, claims
```

In the UI, hovering a phrase in the description reveals the attribute that licensed it, and that attribute's own provenance chain down to the page and bounding box. **This converts the system's most vulnerable surface into its strongest demonstration of explainability.**

### Completeness

Scored against the category's merchandising checklist: which required fields are filled, which are blocking publication, and what would resolve each.

---

## Stage 7 — Catalog operations

### Idempotent upsert
Keyed on `(manufacturer_resolved, mpn_normalised)`. Reprocessing updates in place. No duplicates, no churn.

### Source change detection

```python
def on_source_updated(source_id, new_hash):
    if new_hash == stored_hash(source_id):
        return
    affected = products_linked_to(source_id)
    for product in affected:
        fresh = extract_candidates(product, source_id)
        changed = diff_against_stored(product, fresh)
        reverify(product, only=changed)      # not a full re-run
```

Re-verifying only what changed is the difference between a catalog engine and a batch script. It is also directly measurable: **re-verification cost on a source revision** is a reported metric.

### Telemetry

Throughput (SKUs/min), cost per 1,000 SKUs, share of attributes never reaching an LLM, share of SKUs with no located source, cache hit rate.
