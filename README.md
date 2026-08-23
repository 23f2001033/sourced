# Sourced — Product Intelligence for Industrial Commerce

**UniHack 2026 · Unilog · AI-Powered Product Intelligence for Industrial Commerce**

A catalog enrichment engine that turns a sparse SKU row into commerce-ready product data, **and refuses to guess when it cannot find a source.**

---

## The problem, stated precisely

A distributor's real starting point is not a datasheet. It is a row like this:

```
MPN: 3GAA132214-ADE   MFR: ABB   DESC: MOT 3GAA132 5.5KW 4P B3
```

No attached document. A complete commerce record needs 80–120 attributes, a category, a search-usable title, a description, and facet-ready values. Manual enrichment costs dollars per SKU and the backlog never clears.

The naive fix — hand the fragment to an LLM and ask for attributes — produces plausible, unsourced, occasionally wrong specifications. In industrial supply a wrong pressure rating or thread pitch means the wrong part arrives on a job site. **Confidently wrong is worse than absent.**

## What this system does differently

1. **Finds the source first.** Given only an MPN and a fragment, it locates a candidate document and *verifies the document actually describes that part* before extracting anything. No verified source means no extraction, and the record is returned as `no_source_located` with the reason.
2. **Generates candidates from every tier, then adjudicates.** Rules, table cells and LLM extraction all propose values with evidence. Adjudication ranks them by evidence quality, source authority and cross-source agreement, rather than letting whichever tier ran first win.
3. **Calibrates its own confidence.** Confidence is computed from observable signals and fitted against a labelled set, then reported as a reliability diagram. It is not the model's opinion of itself.
4. **Checks the catalog, not just the SKU.** Family coherence catches values that are outliers against their sibling products — errors that per-SKU validation structurally cannot see.
5. **Explains abstentions, not only values.** A blank field is not actionable. "No authoritative source located for this MPN" is.

## Measured results

Two verticals, one pipeline, one taxonomy, one calibration model. From a sparse SKU row with no attached document, on the held-out test split:

| | |
|---|---|
| Locates **and verifies** an authoritative source | **100%** of records that have one |
| Precision of published values | **99.6%** over 1,897 values — safety 100%, functional 99.9%, cosmetic 97.0% |
| Auto-publishable with no human review | **76.9%** at the per-tier precision thresholds |
| **Routes each SKU to the right attribute schema** | **100%** of 207, zero invented taxonomy codes |
| Expected Calibration Error | **0.006** |
| Wrong-part traps refused | **24 of 24** — no extraction from a sibling's document |
| Injected fabrications intercepted | **100%** of 181 |
| Attributes resolved with **no LLM call** | **100%** in this run — the tier is measured separately |
| Cost of a source revision vs a full re-run | **7.3%** |

Per category:

| category | SKUs | coverage | precision | recall | auto-publish | ECE |
|---|---|---|---|---|---|---|
| `electrical_connector` | 373 | 74.1% | 99.4% | 78.0% | 72.3% | 0.010 |
| `pipe_fitting` | 283 | 90.0% | 99.9% | 98.6% | 84.7% | 0.001 |

Every published value carries the document, page and region it came from. Where the system cannot answer, it says why and what would resolve it.

**Read [docs/RESULTS.md](docs/RESULTS.md) before quoting any of these.** Two caveats materially change what they mean:

- **The corpus is generated, not pulled from a distributor API.** Digi-Key and Mouser keys require interactive registration and none was available, so `sourced/corpus/build.py` synthesises datasheets, catalogue pages, listings with realistic error rates, sibling parts and conflicting revisions. Labels therefore have zero noise by construction, and the documents are cleaner than production ones. The real Digi-Key path is implemented in `sourced/corpus/digikey.py` and needs only a key.
- **The headline run has the LLM tier off.** It is now measured, but separately: a paired A/B on 40 held-out SKUs against an open-weight model, reported with its own sample size in [docs/RESULTS.md](docs/RESULTS.md). The 100% LLM-avoidance figure describes the deterministic run.

The system is not simulated where it cannot run. A stubbed extractor would place values in the record that no model produced, which is precisely the unsourced-value failure this design exists to prevent.

**What running the model tier proved.** On 40 held-out SKUs an open-weight 12B model returned 44 proposals; the deterministic gates rejected **88.6%** of them, and the survivors were 100% correct against labels. With the gates switched off on the same SKUs, precision of the model's values fell to **33.3%** and it invented values for eight attributes the parts do not have. That was ADR-006's central bet, and it was an assertion until the tier was exercised.

**What running the description tier proved.** Risk R8 materialised. On 20 held-out SKUs, **35% of generated descriptions carried a number nothing licensed** — and the pipeline's own defence reported every claim as traceable, because alignment works a sentence at a time and a fabricated number rides along inside a sentence that also cites a real attribute. Six of the seven failures were the model **rewriting the part number** (`WM413-N050` → `WM413-1050`), which in catalogue copy is an ordering error rather than a wrong spec. A numeric licence gate now rejects such copy outright and ships the deterministic composition instead: 0% unlicensed, at the cost of 30% of descriptions being composed rather than generated.

It also exposed a hole in the extraction bet. Span containment alone accepted a *real* span paired with a *fabricated* value — `nominal_size_secondary = 1 in` citing `"3/4 in"`, published at 0.99 confidence, on a fitting with no secondary size. A second check now requires the cited text to actually **yield** the claimed value; it caught 15 more in the same sample. Full numbers, sample sizes and the ungated comparison are in [docs/RESULTS.md](docs/RESULTS.md).

RESULTS.md also reports where the design's own expectations were **not** borne out — doc 05 predicted the deterministic rules tier would do markedly more work in the abbreviation-soup vertical, and on this corpus it does not.

## Running it

```bash
pip install -e ".[dev]"

python -m sourced.corpus.build             # build the corpus (both categories)
python -m sourced.eval.report --persist --fresh   # evaluate, write RESULTS.md, load the store
uvicorn sourced.api.routes:app --reload    # http://127.0.0.1:8000
pytest                                     # the gates
```

Or the whole stack on Postgres:

```bash
docker compose up          # db -> corpus + evaluation -> API on :8000
```

Verified from a clean volume, after the LLM-provider work: `db` healthy, `enrich` exits 0 having made no model calls, the API serves 656 products out of Postgres with the GIN and family indexes in place, the UI and the PDF-region renderer both respond, and re-running `enrich` leaves the row count unchanged.

`.env` is **not** copied into the image — verified inside the running container — while `docker compose` substitutes its values into the environment at runtime. Secrets reach the process, never the image layers.

Walk the system:

```bash
python -m sourced.demo             # live: runs the pipeline as it narrates
python -m sourced.demo --replay    # from data/demo.json - no pipeline, no model, no db
```

Five acts: a sparse row becoming a commerce record, the sibling trap being refused, authority beating a contradicting listing, two datasheets forcing an abstention, and the measured numbers with what they are worth. `--replay` is the fallback for risk R10 — a demo that needs the network is not a demo.

Enrich one row live:

```bash
curl -X POST localhost:8000/api/enrich -H 'content-type: application/json' \
  -d '{"mpn":"035094515-10RG","manufacturer":"TE","description_fragment":"CONN HEADER R/A 10POS 1.25MM GOLD"}'
```

The LLM tier is opt-in, because it is slow and costs tokens:

```bash
python -m sourced.eval.llm_experiment --sample 40          # extraction: gates on vs off
python -m sourced.eval.description_experiment --sample 20  # generation: risk R8, audited
python -m sourced.eval.run --llm                           # full evaluation with the tier on
```

Configure a provider in `.env` — either `ANTHROPIC_API_KEY` (doc 03's choice, with prefix caching) or `FEATHERLESS_API_KEY` for any OpenAI-compatible endpoint. Everything else runs without either.

## What is in the box

| Path | Stage | What it does |
|---|---|---|
| `sourced/discovery/` | 0 | MPN normalisation, manufacturer aliases, hybrid retrieval, **match verification** |
| `sourced/taxonomy/` | 1 | Offline leaf index, retrieve-then-constrain with hard code validation |
| `sourced/candidates/` | 2 | Rules (lexicon, dimensional grammar, MPN decoding), tables, single LLM call |
| `sourced/adjudicate/` | 3 | Evidence quality → source authority → agreement; conflict ⇒ abstention |
| `sourced/validate/` | 4 | L1 per-attribute · L2 relational · L3 family coherence |
| `sourced/confidence/` | 5 | Observable features, fitted calibration, reliability diagram |
| `sourced/commerce/` | 6 | Deterministic title, constrained description with claim traceability |
| `sourced/store/` | 7 | Idempotent upsert, provenance, source-change detection |
| `sourced/eval/` | — | Metrics, ablations, adversarial tests, LLM A/B, RESULTS.md |
| `sourced/candidates/providers.py` | 2 | Anthropic and OpenAI-compatible providers behind one structured call |
| `schemas/` | — | Two category schemas, the abbreviation lexicon, alias table, taxonomy |
| `sourced/demo.py` | — | Five-act walkthrough, live or replayed from a captured run |
| `web/` | — | Product view: per-attribute confidence, tier badge, abstention reason, highlighted source region |

Clicking any published value in the UI renders the PDF page with the exact cell it was read from outlined. Hovering a phrase in the generated description reveals the attribute that licensed it.

## Two categories, one pipeline

`electrical_connector` and `pipe_fitting` share the corpus, the source index, the taxonomy, the calibration model and every stage of the pipeline. Nothing about a category lives in code — an attribute set, its criticality tiers, its relational rules and its lookups are a YAML file, which is the claim doc 02 makes and the reason for carrying a second vertical.

The fittings vertical is the one doc 05 calls the richest abbreviation soup, and the rules tier decomposes the brief's own example with no document and no model:

```
1/2IN X 3/4IN BRS 90 ELL FIP 150#
  -> nominal_size 0.5 in · nominal_size_secondary 0.75 in · body_material brass
     bend_angle 90 · form_factor elbow · end_connection_1 female_iron_pipe
     pressure_class class_150
```

Adding it surfaced three things worth knowing:

- **Fractional inches were being torn in half.** The configuration-dependent-value split that handles `230 V / 400 V` was also splitting `1/4 in` into 1 and 4, so a quarter-inch fitting published as a one-inch fitting with a table cell as its provenance. Doc 00 names fractional inches as the recurring failure in this domain; it was, and there is a gate for it now.
- **Calibration had to be conditioned on the schema.** Doc 03 fits per criticality tier. With two categories that is under-specified — one model fitted across both cost the stronger category about twenty points of auto-publish rate. Confidence is now fitted per (category, tier), with the tier still setting the threshold.
- **Routing is a count, not a similarity.** `ELL` decodes to `form_factor`, `SMD` to `mounting_type`, and each key belongs to one schema, so the vote is auditable. Retrieval still picks the taxonomy leaf, and the hard existence check still makes an invented code impossible.
- **A record must not contradict itself.** A pipe bushing routed correctly to the fitting schema was still labelled `Battery`, because that is what similarity returned. The leaf is now reconciled against the chosen schema, and refined a second time once the attributes say what the part is — doc 03 §1.2 passes `attributes` to the classifier for exactly this. The schema is never revisited: extracting against one attribute set and filing under another is the failure being avoided.

## Where the design deviates from the documents

Stated here rather than buried, because a plan that quietly matched its outcome would be the less honest artefact:

- **Dense retrieval is TF-IDF + SVD, not sentence-transformers + FAISS.** Same hybrid shape, same lexical dominance (which is the part ADR-007 turns on), no 2.5 GB torch dependency.
- **The taxonomy is internal.** UNSPSC and ETIM tables need a licensed or registered download; `sourced/taxonomy/index.py` imports either from a CSV in `data/`. The retrieve-then-constrain mechanism is unchanged.
- **`internal_record` was added to the source-type list.** The sparse input row is itself a claim about the part, and the rules tier reads it. Labelling it `distributor_page` would launder the weakest evidence in the system into a web source, so it gets its own type and the lowest authority rank.
- **Source authority is floored rather than `1/rank` in match verification.** Verification asks whether a document *describes* this part; how much its values are worth is Stage 3's question, and ADR-004 answers it there.
- **MPN matching is boundary-anchored, not substring.** Matching on fully normalised text let a truncated part number match the longer part it prefixes — ADR-002's failure by another route. There is a gate for it.
- **The UI is a served static page, not Next.js.** Same acceptance criterion, no build step.
- **No queue.** Doc 01 names Redis and `arq`; at this scale neither was needed, so neither is there.
- **The LLM tier is opt-in and measured apart from the headline.** Three separate surfaces — extraction, classification, description — each switchable, because each is a different expense. Only extraction is on by default when a provider exists.
- **Calibration is fitted per (category, criticality), not per criticality alone.** Doc 03 specifies the tier; two categories made that under-specified. Stated in RESULTS.md with the cost of getting it wrong.
- **Schema selection is separated from taxonomy assignment.** A leaf says what the part is; the schema says which attribute set to extract against. Seventy-one of seventy-three leaves have no populated schema, so conflating them silently routes records into the wrong attribute set.

## Submission

| Document | What it is |
|---|---|
| **[docs/SUBMISSION.md](docs/SUBMISSION.md)** | The prototype deck, slide by slide — problem, validation strategy, scalability, architecture, features, cost |
| **[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)** | A 3-minute demo script with the exact beats, plus setup and a recording checklist |
| **[docs/RESULTS.md](docs/RESULTS.md)** | Every measured number, the ablations, the adversarial suite, and the limitations |

## Documentation

| Doc | Contents |
|---|---|
| [00 — Problem analysis](docs/00-PROBLEM-ANALYSIS.md) | Requirement decomposition, what the brief actually asks for, why obvious approaches fail |
| [01 — Architecture](docs/01-ARCHITECTURE.md) | Stage-by-stage system design and data flow |
| [02 — Data model](docs/02-DATA-MODEL.md) | Schemas, provenance record, database design |
| [03 — Pipeline spec](docs/03-PIPELINE-SPEC.md) | Implementation detail per stage, algorithms, thresholds |
| [04 — Evaluation](docs/04-EVALUATION.md) | Label strategy, metrics, calibration, ablations |
| [05 — Data sources](docs/05-DATA-SOURCES.md) | Where data comes from, access status, legal position |
| [06 — Build plan](docs/06-BUILD-PLAN.md) | Ordered task list with acceptance criteria |
| [07 — Risk register](docs/07-RISK-REGISTER.md) | What will break and the mitigation |
| [08 — Decisions](docs/08-DECISIONS.md) | Architecture decisions with rationale and real-world precedent |
| **[RESULTS](docs/RESULTS.md)** | **Measured numbers, ablations, calibration, stated limitations** |

## Status

57 gate tests green. Every item in doc 06's definition of done is closed:

- [x] `docker compose up` works from a clean clone — verified against a fresh volume
- [x] Every claim in this README is a measured number from the held-out split
- [x] The wrong-part robustness test passes — 19 of 19 traps refused
- [x] Every published value has provenance down to page and region
- [x] Abstentions carry a reason and a resolution hint
- [x] The reliability diagram is in [docs/RESULTS.md](docs/RESULTS.md)
- [x] Known limitations are stated plainly

The wrong-part robustness test doc 06 calls the single most important test in the build passes on every trap in the corpus.

Nine bugs were found by running the things that were supposed to be formalities. The Fahrenheit-to-Celsius conversion silently dropped its unit, so 185 °F published as 185 °C. A truncated part number still matched the longer part it prefixes. The Postgres path failed on a UUID column mismatch and an insert ordering that SQLite had been accepting because it does not enforce foreign keys unless asked. Fractional inches were being split in half by the alternative-value separator. Span containment accepted a real span paired with an invented value. The ablation sweep silently switched the model tier on, turning an eighteen-batch deterministic run into thousands of API calls that simply stopped making progress. Claim offsets addressed the wrong characters, putting the UI's hover highlight a space out. And the description's strip-untraceable-claims defence let fabricated numbers through inside otherwise-honest sentences. Each has a gate now.
