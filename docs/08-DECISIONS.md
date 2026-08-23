# 08 — Architecture Decisions

Each decision records the alternative considered, the reason for the choice, and whether the technique is established practice rather than invented for this project.

---

## ADR-001 — Source discovery precedes extraction

**Decision.** Given a sparse SKU row, locate and verify a source document before extracting anything. If no document can be verified, return `no_source_located` rather than extracting.

**Alternative rejected.** Accept an uploaded datasheet as the starting point, as most enrichment demos do.

**Reasoning.** The brief specifies "limited product information" scattered across sources. If the datasheet were already attached, the stated problem would largely be solved. More importantly, an LLM given an MPN with no retrieved document produces attributes from parametric memory — plausible, unsourced, sometimes wrong — and a provenance layer will record `source: none` while publishing them anyway.

**Real-world grounding.** This is the standard retrieval-before-generation discipline used in production RAG systems, and the "abstain when retrieval fails" pattern is established practice in high-stakes question answering.

---

## ADR-002 — MPN presence is a hard gate, not a weighted signal

**Decision.** A candidate document must contain the part number verbatim before it can be treated as a source.

**Alternative rejected.** Score MPN presence alongside manufacturer match and description overlap, accept above a combined threshold.

**Reasoning.** A sibling part's datasheet scores well on every soft signal — same manufacturer, overlapping description tokens, identical layout. Only the part number distinguishes them. Extraction from a sibling produces values that are internally consistent, correctly formatted, in range, and wrong. Soft scoring cannot separate these cases.

**Real-world grounding.** Exact-identifier matching as a hard filter before fuzzy scoring is standard in record linkage and entity resolution.

---

## ADR-003 — Candidates from all tiers, then adjudication

**Decision.** Rules, tables and LLM each emit candidates. A separate adjudication stage ranks them.

**Alternative rejected.** Cascade through tiers, returning the first result that clears a threshold.

**Reasoning.** First-match-wins means a regex that happens to match beats a correct table cell purely on ordering, with hardcoded thresholds deciding the outcome. It also discards evidence already paid for, which is precisely the evidence needed to explain the decision and to detect conflict.

**Real-world grounding.** Candidate generation followed by ranking is the standard shape of information extraction and entity resolution systems.

---

## ADR-004 — Source authority outranks agreement count

**Decision.** Rank by source authority before counting agreement. Manufacturer datasheet outranks distributor pages regardless of how many agree.

**Alternative rejected.** Majority voting across sources.

**Reasoning.** Distributor listings frequently copy from one another. Three pages carrying the same wrong value are not three independent confirmations. Naive voting inverts the correct answer in exactly the case where source quality matters most.

**Real-world grounding.** Source-authority weighting is standard in data fusion and truth discovery, where the naive-voting failure mode is well documented.

---

## ADR-005 — Confidence is computed, never self-reported

**Decision.** Confidence is a fitted function of observable signals, calibrated against labelled data and reported with a reliability diagram.

**Alternative rejected.** Ask the model to rate its own confidence on a described scale.

**Reasoning.** Self-reported LLM confidence is not calibrated. The numbers look meaningful and do not track correctness, so an abstention policy built on them is built on noise. Observable signals — which tier produced the value, whether the span check passed, whether independent sources agreed — are things that can be measured against outcomes.

**Real-world grounding.** Calibration measurement, reliability diagrams and Expected Calibration Error are established practice in the machine learning literature on model confidence. Post-hoc calibration by logistic or isotonic regression is standard.

---

## ADR-006 — Verbatim span containment as the primary hallucination gate

**Decision.** Require every extracted value to cite a span, then check that the span literally appears in the cited chunk.

**Alternative rejected.** Have a second LLM review the extraction.

**Reasoning.** The containment check is free, deterministic, and removes a large share of fabrications outright. A second model reviewing the first is slower, costs more, and is weaker evidence — particularly when both are the same model, where failure modes are largely shared. The check also produces the explainability artefact at zero extra cost.

**Real-world grounding.** Attribution and grounding verification through source-span checking is established practice in RAG evaluation.

---

## ADR-007 — Hybrid retrieval with lexical weighted higher

**Decision.** BM25 and dense retrieval fused by Reciprocal Rank Fusion, weighted toward lexical for source discovery.

**Alternative rejected.** Dense retrieval alone.

**Reasoning.** Part numbers, series codes and standards references are exact-match tokens that embedding models handle poorly. Source discovery is a lookup by identifier, not a semantic search.

**Real-world grounding.** Hybrid sparse-plus-dense retrieval is standard in production search. Reciprocal Rank Fusion is a published method chosen here because it needs no score normalisation and has one parameter that works untuned.

---

## ADR-008 — Retrieve-then-constrain for taxonomy classification

**Decision.** Embed taxonomy leaves offline, retrieve top-k candidates, constrain the model to choose among them, hard-validate the returned code exists.

**Alternative rejected.** Give the model the taxonomy and ask it to return a code.

**Reasoning.** Tens of thousands of codes do not fit in context, and an unconstrained model invents codes that look valid. Constraining to retrieved candidates plus validating existence makes a fabricated code structurally impossible rather than merely unlikely.

**Real-world grounding.** Constrained generation over a retrieved candidate set is standard practice for classification over large label spaces.

---

## ADR-009 — Table rows as self-describing chunks

**Decision.** Extract tables with `pdfplumber`, emit each row as a chunk with the header prepended, retain cell bounding boxes.

**Alternative rejected.** Recursive text chunking over the whole document; or a transformer-based layout model.

**Reasoning.** Datasheets are predominantly tabular. Prose chunking destroys row-to-column association, which is the largest source of wrong values in this domain. A transformer layout model handles borderless tables better but carries install cost and, realistically, a GPU requirement, which is not justified when the corpus has ruled tables.

**Real-world grounding.** Layout-aware and table-aware chunking is established practice in document extraction.

---

## ADR-010 — Three validation levels, including cross-SKU

**Decision.** Validate per-attribute, cross-attribute, and across sibling SKUs in a product family.

**Alternative rejected.** Per-attribute range checks only.

**Reasoning.** The stated outcome names accuracy **and consistency** as separate goals. Per-attribute checks address accuracy. Cross-attribute checks catch pairs of individually plausible values that cannot both be true. Family coherence catches values that are outliers against sibling products, which is the only level that addresses consistency, and it needs no LLM.

**Real-world grounding.** Multi-level data quality validation, and outlier detection against a peer group, are standard data quality practice.

---

## ADR-011 — Median absolute deviation for outlier detection

**Decision.** Use median and median absolute deviation rather than mean and standard deviation for family coherence.

**Reasoning.** The population being examined may itself contain the errors being hunted. Mean and standard deviation are dragged by the outliers they are supposed to detect. MAD is robust to contamination.

**Real-world grounding.** MAD-based outlier detection is standard robust statistics.

---

## ADR-012 — Publish thresholds vary by attribute criticality

**Decision.** Safety-critical attributes require the highest confidence plus dual-source corroboration; cosmetic attributes publish at a lower bar.

**Alternative rejected.** One global confidence threshold.

**Reasoning.** A wrong housing colour is a cosmetic defect. A wrong current rating is a fire. A single threshold either over-suppresses cosmetic attributes or under-protects safety-critical ones. Tiering converts one knob into a policy, which is the actual judgment an industrial distributor is making.

**Real-world grounding.** Risk-tiered acceptance thresholds are standard practice in quality systems for regulated and safety-relevant domains.

---

## ADR-013 — Description generation constrained to verified attributes

**Decision.** Generate marketing copy only from published attributes, align each claim to its source attribute, strip untraceable spans.

**Alternative rejected.** Generate freely from the document; or omit descriptions entirely.

**Reasoning.** Commerce-ready output requires a description, so omitting it fails the brief. But generation is the one surface where fabrication can re-enter after every extraction gate. Constraining inputs and enforcing claim traceability keeps the guarantee intact, and turns the most vulnerable surface into the clearest demonstration of explainability.

---

## ADR-014 — One database

**Decision.** PostgreSQL with JSONB for attributes and relational tables for provenance. No graph database, no object store, no separate search engine.

**Alternative rejected.** Neo4j for a provenance graph, MinIO for files, Meilisearch for search.

**Reasoning.** Provenance is a per-attribute record with a foreign key, not a graph traversal problem. Each additional data store adds container complexity and operational surface for no capability the system needs at this scale. Decorative infrastructure is visible to a technical reviewer as decorative.

---

## ADR-015 — No multi-agent validation loop

**Decision.** Deterministic gates are mandatory; a single model-based critic is a last-resort tier only.

**Alternative rejected.** An extractor / validator / critic / enricher agent graph.

**Reasoning.** Each agent adds LLM calls, latency, cost, and another surface for fabrication. A model checking a model is weaker evidence than a deterministic check, especially when both share weights and therefore failure modes. The deterministic gates already implement what the validator and critic agents were intended to do, more cheaply and more auditably.

---

## ADR-016 — No web-search enrichment

**Decision.** Enrich only from verified, authority-ranked sources in the corpus.

**Alternative rejected.** An enricher agent that web-searches for missing attributes.

**Reasoning.** The system's entire claim is provenance and trust. Injecting an arbitrary web result introduces an unranked, unverified source and undermines that claim. If web enrichment were in scope, it would need a domain whitelist and an authority rank, which is a larger piece of work than the gap it fills.

---

## ADR-017 — Silver labels at volume, gold audit at the margin

**Decision.** Use distributor parametric data as labels, hand-audit roughly 30 SKUs, and report the measured label-noise rate.

**Alternative rejected.** Hand-label everything; or verify against pages that copied the same datasheet.

**Reasoning.** Verifying an extraction against a source derived from the same document measures nothing. Distributor parametric data is separately curated, which makes it usably independent. It is not perfect, so the noise rate is measured and reported rather than assumed away. Stating the error bar is more defensible than claiming perfect labels.

**Real-world grounding.** Silver-standard labelling with a gold-audited subset is established practice where exhaustive expert annotation is infeasible.
