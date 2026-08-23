# 06 — Build Plan

Submission closes **23 August 2026**. This plan assumes work starts **20 August**.

Ordered by dependency and by what protects the central claim. Every task has an acceptance criterion, because "done" needs to be checkable rather than felt.

---

## Hour zero — before any code

- [ ] **Register for the Digi-Key developer account and generate a key.** Self-service, immediate.
- [ ] **Apply for the Mouser API key.** 1–2 business day approval. This is the longest external lead time in the project and it starts now or not at all.
- [ ] Choose the vertical and one parametric category within it.

If both keys fail to arrive, the fallback is JSON-LD extraction plus a larger hand audit. Knowing that on day one is worth more than discovering it on day three.

---

## Day 1 — Data and ground truth

The order here is deliberate. The labelled set comes before the pipeline, so every later decision is measurable.

### 1.1 Corpus
- [ ] Pull 300–500 SKUs from the API for one category
- [ ] Download and deduplicate linked datasheet PDFs
- [ ] Construct the **sparse input** by discarding everything except MPN, manufacturer and truncated description
- **Acceptance:** `data/corpus.jsonl` holds ≥300 records, each with a sparse input, at least one linked PDF, and silver labels stored separately

### 1.2 Labels
- [ ] Load API parametric attributes into `eval_labels`, marked `label_source = distributor_api`
- [ ] Split dev / calibration / held-out **now**, before anything is fitted
- [ ] Hand-audit 30 random SKUs against their datasheets; record verdicts
- **Acceptance:** measured label-noise rate written into `docs/RESULTS.md`. Held-out split untouched by any code path except final reporting.

### 1.3 Ingestion
- [ ] `pdfplumber` extraction producing text blocks, table rows and bounding boxes
- [ ] Table rows emitted as self-describing chunks with headers prepended
- **Acceptance:** for 5 sample PDFs, every attribute present in the silver labels is visibly present somewhere in the extracted chunks. If a value is not in the chunks, extraction can never find it, and no amount of model quality fixes that.

### 1.4 Canonical model and schema
- [ ] Pydantic models from [02 — Data Model](02-DATA-MODEL.md)
- [ ] Postgres schema, migrations, unique constraint, GIN and family indexes
- [ ] One category YAML with attributes, types, ranges, **criticality tiers**, relational rules
- **Acceptance:** a record round-trips through the database with provenance intact

---

## Day 2 — The engine

### 2.1 Stage 0 — source discovery
- [ ] MPN normalisation and variant generation
- [ ] Manufacturer alias resolution with the 88 confidence floor
- [ ] BM25 + dense retrieval with RRF fusion
- [ ] **Match verification with the hard MPN-presence requirement**
- [ ] `no_source_located` terminal state with reason and resolution hint
- **Acceptance:** the wrong-part robustness test passes — given an MPN whose datasheet is absent but whose *sibling's* datasheet is present, the system returns `no_source_located` rather than extracting from the sibling. **This is the single most important test in the build.**

### 2.2 Stage 2 — candidates
- [ ] Rules: abbreviation lexicon, dimensional grammar, `pint` normalisation
- [ ] Tables: cell-level candidates carrying bounding boxes
- [ ] LLM: **one structured call per SKU** for all unresolved attributes, with prefix caching
- [ ] Verbatim span containment check, rejecting on failure
- **Acceptance:** on 20 dev SKUs, every LLM-produced candidate carries a span that literally appears in its cited chunk, or is rejected. Zero exceptions.

### 2.3 Stage 3 — adjudication
- [ ] Candidate grouping with `pint`-aware value equality
- [ ] Ranking by evidence locator, source authority, agreement
- [ ] Conflict between authoritative sources produces abstention, not a vote
- **Acceptance:** a constructed case where a distributor page contradicts a datasheet resolves to the datasheet value

### 2.4 Stage 4 — validation
- [ ] L1 per-attribute checks
- [ ] L2 relational rules from YAML
- [ ] L3 family coherence with median absolute deviation
- **Acceptance:** the injected-fabrication test catches a measured share, attributed by level

---

## Day 3 — Proof, output, packaging

Priority order matters here. If time runs out, it runs out from the bottom.

### 3.1 Calibration and results — **highest priority**
- [ ] Feature extraction from observable signals
- [ ] Logistic fit on the calibration split
- [ ] **Reliability diagram and Expected Calibration Error**
- [ ] Abstention curve per criticality tier
- [ ] Ablation table
- [ ] `docs/RESULTS.md` with measured numbers
- **Acceptance:** every X, Y, Z and W in the README claim is filled with a measured number from the held-out split

### 3.2 Stage 1 — classification
- [ ] Offline taxonomy leaf index
- [ ] Retrieve-then-constrain with hard code validation
- **Acceptance:** no returned code is absent from the taxonomy table, across the whole test set

### 3.3 Stage 6 — commerce-ready output
- [ ] Deterministic title template
- [ ] Constrained description with claim-to-attribute traceability
- [ ] Facets and completeness score
- **Acceptance:** every claim in a generated description traces to a published attribute; untraceable spans are stripped

### 3.4 API and UI
- [ ] FastAPI: record retrieval, provenance by attribute, batch trigger
- [ ] **Freeze the API contract before starting the UI**
- [ ] Product detail view with per-attribute confidence, tier badge and abstention reason
- [ ] Source panel rendering the PDF page with the bounding box highlighted
- [ ] Description hover revealing the licensing attribute
- **Acceptance:** clicking any published value shows the document, page and highlighted region it came from

### 3.5 Packaging
- [ ] `docker compose up` brings the whole stack up
- [ ] README with measured results and honest limitations
- [ ] Demo recording with a fallback in case the live run fails

---

## Cut list, in order

Cut from the top when behind.

1. Neo4j, MinIO, Meilisearch — never needed
2. Multi-agent critic loop — deterministic gates do this better and cheaper
3. Web-search enrichment — undermines the provenance claim
4. OCR path — only if the corpus is digital PDFs, which it should be
5. MPN structure decoding — valuable, not load-bearing
6. Hierarchical taxonomy traversal — flat retrieve-then-constrain suffices
7. Deduplication — out of scope for the core claim
8. Second category — one done properly beats two done partially

**Never cut, under any circumstance:**
- Source match verification and the `no_source_located` state
- The labelled set and the held-out split
- Verbatim span containment
- Calibration and the reliability diagram
- Provenance on every published value

Those five are the entire differentiator. Everything else is an implementation detail.

---

## Definition of done

The submission is complete when:

- [ ] `docker compose up` works from a clean clone
- [ ] Every claim in the README is a measured number from the held-out split
- [ ] The wrong-part robustness test passes
- [ ] Every published value has provenance down to page and region
- [ ] Abstentions carry a reason and a resolution hint
- [ ] The reliability diagram is in `docs/RESULTS.md`
- [ ] Known limitations are stated plainly

---

## Honest assessment of the timeline

Three days is tight for this scope with a small team. The plan is ordered so that a partial build still demonstrates the central claim: **source verification plus calibrated abstention on a single category**, with measured numbers, beats a broad pipeline with no evaluation.

If day 3 collapses, ship days 1 and 2 with the results document. A narrow, measured, honest system is a stronger submission than a wide one that cannot prove anything.
