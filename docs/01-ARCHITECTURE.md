# 01 — Architecture

## System shape

```
INPUT: sparse SKU row
{ mpn, manufacturer?, description_fragment }
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 0 — SOURCE DISCOVERY & MATCH VERIFICATION               │
│  normalise MPN → resolve manufacturer → retrieve candidates   │
│  → VERIFY the document describes THIS part                    │
│  no verified source ──────────────► terminal: no_source_located│
└───────────────────────────────────────────────────────────────┘
        │ verified source(s) + match evidence
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 1 — CATEGORY CLASSIFICATION                             │
│  retrieve-then-constrain over taxonomy leaves                 │
│  hard-validate code exists → selects the attribute schema     │
└───────────────────────────────────────────────────────────────┘
        │ category + schema
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 2 — CANDIDATE GENERATION  (all tiers run, no short-circuit)│
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ RULES       │  │ TABLES       │  │ LLM                │   │
│  │ lexicon,    │  │ pdfplumber   │  │ ONE structured call│   │
│  │ dimensional │  │ row chunks,  │  │ for all unresolved │   │
│  │ grammar,    │  │ cell + bbox  │  │ attributes         │   │
│  │ MPN decode  │  │              │  │                    │   │
│  └─────────────┘  └──────────────┘  └────────────────────┘   │
│         all emit Candidate{value, evidence, tier, source}      │
└───────────────────────────────────────────────────────────────┘
        │ candidate set per attribute
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 3 — ADJUDICATION                                        │
│  rank by evidence quality → source authority → agreement      │
│  authoritative sources in conflict ⇒ ABSTAIN, not vote        │
└───────────────────────────────────────────────────────────────┘
        │ winning candidate or abstention
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 4 — VALIDATION (three levels)                           │
│  L1 per-attribute   type · unit parses · enum · range         │
│  L2 cross-attribute relational/physics consistency            │
│  L3 cross-SKU       family coherence, outlier detection       │
└───────────────────────────────────────────────────────────────┘
        │ validated attributes + check results
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 5 — CONFIDENCE CALIBRATION                              │
│  features → fitted model → calibrated probability             │
│  threshold by ATTRIBUTE CRITICALITY TIER                      │
└───────────────────────────────────────────────────────────────┘
        │ publish / review / abstain decision per attribute
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 6 — COMMERCE-READY GENERATION                           │
│  title (deterministic template)                               │
│  description (generated, constrained to VERIFIED attrs only,  │
│               every claim traceable to its source attribute)  │
│  facet values · category · completeness score                 │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│ STAGE 7 — PERSISTENCE & CATALOG OPS                           │
│  idempotent upsert · provenance store · source-change         │
│  detection · selective re-verification · telemetry            │
└───────────────────────────────────────────────────────────────┘
        │
        ▼
OUTPUT: commerce-ready record + per-attribute provenance
        + explained abstentions + export projections
```

## Design principles

**1. Deterministic before probabilistic.** Anything a rule can decide, a rule decides. LLM calls are the expensive, least auditable tier and are used last and once.

**2. Candidates, then adjudication.** No stage short-circuits on an arbitrary threshold. Evidence already paid for is never discarded.

**3. Abstention is a first-class output.** Every terminal state carries a reason and a resolution hint.

**4. Provenance is not logging.** It is part of the record, and no value publishes without it.

**5. Confidence is measured, never self-reported.**

## Stage responsibilities

### Stage 0 — Source discovery and match verification
The stage that makes the input genuinely "limited." Turns `MPN + fragment` into verified source documents, or into an honest `no_source_located`.

Sub-steps: MPN normalisation and variant generation → manufacturer alias resolution → hybrid candidate retrieval (BM25 dominant, because part numbers are exact-match tokens) → **match verification** requiring the MPN to appear verbatim in the candidate → confidence scoring.

### Stage 1 — Category classification
Category selects the attribute schema, so it must run before extraction. Retrieve-then-constrain makes hallucinated taxonomy codes structurally impossible: embed taxonomy leaves offline, retrieve top-k candidates, constrain the model to choose among them, hard-validate the returned code exists.

### Stage 2 — Candidate generation
Three independent producers, no ordering dependency between them.

- **Rules:** abbreviation lexicon, dimensional grammar, unit normalisation, MPN structure decoding
- **Tables:** row-level chunks with headers prepended, each cell carrying its bounding box
- **LLM:** exactly one structured call per SKU covering all attributes the cheaper tiers left unresolved

### Stage 3 — Adjudication
Ranking function over the candidate set. Evidence quality dominates, then source authority, then cross-source agreement. Conflict between authoritative sources produces abstention.

### Stage 4 — Validation
Three levels, cheapest first, each catching a class the others cannot. L3 (cross-SKU family coherence) is the one that serves the *consistency* outcome and that nobody else will implement.

### Stage 5 — Confidence calibration
Features from stages 2–4 → fitted calibration → per-attribute publish decision against a threshold that varies by criticality tier.

### Stage 6 — Commerce-ready generation
The only stage that generates rather than extracts, and therefore the only place hallucination can re-enter. Constrained to verified attributes with claim-level traceability.

### Stage 7 — Persistence and catalog operations
Idempotency, source-change detection, selective re-verification, telemetry. This is what makes it a catalog engine rather than a batch script.

## Component map

```
sourced/
├── discovery/       # Stage 0
│   ├── mpn.py               normalisation, variant generation
│   ├── manufacturer.py      alias resolution
│   ├── retrieve.py          hybrid candidate retrieval
│   └── verify.py            match verification gate
├── taxonomy/        # Stage 1
│   ├── index.py             offline leaf embedding
│   └── classify.py          retrieve-then-constrain + hard validation
├── candidates/      # Stage 2
│   ├── rules.py             lexicon, dimensional grammar, MPN decode
│   ├── tables.py            pdfplumber row chunks + bbox
│   └── llm.py               single structured call
├── adjudicate/      # Stage 3
│   └── rank.py              evidence quality, authority, agreement
├── validate/        # Stage 4
│   ├── attribute.py         L1
│   ├── relational.py        L2
│   └── family.py            L3 coherence + outliers
├── confidence/      # Stage 5
│   ├── features.py          observable signal extraction
│   └── calibrate.py         fit, apply, reliability diagram
├── commerce/        # Stage 6
│   ├── title.py             deterministic template
│   ├── description.py       constrained generation + traceability
│   └── completeness.py      merchandising checklist
├── store/           # Stage 7
│   ├── models.py            SQLAlchemy
│   ├── upsert.py            idempotent
│   └── changes.py           source revision detection
├── ingest/          # shared
│   ├── pdf.py               pdfplumber, text layer + tables + bbox
│   └── normalize.py         pint units, controlled vocabularies
├── eval/
│   ├── labels.py            silver label ingestion, audit subset
│   ├── metrics.py
│   └── report.py            reliability diagram, ablations
└── api/
    └── routes.py            FastAPI
```

## Technology choices

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Ecosystem for every component below |
| PDF text + tables + bbox | `pdfplumber` | Gives text, tables and coordinates from one library. No heavy model dependency. |
| Lexical retrieval | `rank_bm25` | Part numbers are exact-match tokens |
| Dense retrieval | `sentence-transformers` + FAISS | Semantic matching for descriptions |
| Fusion | Reciprocal Rank Fusion | Standard, parameter-light, no score normalisation needed |
| Reranking | cross-encoder | Precision on the final shortlist |
| Units | `pint` | Correct unit algebra, do not hand-roll |
| Fuzzy matching | `rapidfuzz` | Manufacturer aliases, attribute key matching |
| Structured LLM output | Pydantic + tool/function calling | Type-safe, no JSON parsing failures |
| Calibration | scikit-learn logistic / isotonic | Standard, interpretable, small data friendly |
| Store | PostgreSQL + JSONB | One database. Attributes in JSONB, provenance relational. |
| Queue | Redis + `arq` | Async-native, light |
| API | FastAPI | Typed, async, free OpenAPI |
| UI | Next.js + shadcn/ui | Built last, against a frozen contract |

**Deliberately excluded:** Neo4j (provenance fits in Postgres), MinIO (local disk), Meilisearch (Postgres suffices at demo scale), Detectron2-based layout parsing (install cost exceeds value here), LangGraph agent loop (adds cost and latency without adding evidence quality).

See [08 — Decisions](08-DECISIONS.md) for the reasoning and real-world precedent behind each.
