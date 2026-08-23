# 07 — Risk Register

Ordered by expected damage. Each entry states the failure, the early warning, and the response.

---

## R1 — API key does not arrive in time
**Likelihood:** medium · **Impact:** severe — the labelling strategy depends on it

Mouser states 1–2 business days for approval. Applying on 20 August could mean access on the 22nd, one day before submission.

**Early warning:** no key by end of day 21.
**Mitigation:** Digi-Key is self-service with an immediate sandbox and is therefore the primary. Register for both on hour zero.
**Fallback:** JSON-LD extraction via `extruct` from manufacturer and distributor pages, plus a hand-audited set expanded from 30 to roughly 80 SKUs. Costs about a day, which is why the trigger must be checked on day one rather than day three.

---

## R2 — The system extracts from a sibling part's datasheet
**Likelihood:** high without the gate · **Impact:** severe — it invalidates the entire trust claim

A datasheet for a different part in the same family scores well on every soft signal: manufacturer matches, description tokens overlap, layout is identical. Only the part number distinguishes them.

**Early warning:** the wrong-part robustness test fails.
**Mitigation:** MPN presence in the document is a **hard requirement**, not a weighted signal. The check runs before any extraction.
**Detection:** the wrong-part test is a build gate on day 2, not an optional extra.

This is the failure mode most likely to be present and unnoticed in competing submissions.

---

## R3 — No labelled set, so every accuracy claim is an assertion
**Likelihood:** high if labels are deferred · **Impact:** severe

The natural instinct is to build the pipeline first and evaluate at the end. Under a three-day deadline, "the end" does not arrive.

**Early warning:** day 1 closes without `eval_labels` populated and split.
**Mitigation:** labels are day 1, task 2. The pipeline is built against them, not before them.
**Consequence if it slips:** the submission becomes a demo rather than a measured system, which forfeits the differentiator entirely.

---

## R4 — Table extraction loses row-to-column association
**Likelihood:** medium · **Impact:** high — produces confidently wrong values that pass every downstream check

A value read from the wrong row is internally consistent, correctly formatted, in range, and wrong.

**Early warning:** the day 1 acceptance check — every silver-label value visible in the extracted chunks — fails on more than a couple of PDFs.
**Mitigation:** row-level chunks with headers prepended, so each chunk is self-describing. Cell bounding boxes retained so a human can verify visually.
**Fallback:** if `pdfplumber` struggles on the chosen corpus, prefer a different manufacturer's datasheets over adopting a heavier layout model mid-build.

---

## R5 — Silent model failover voids the precision guarantee
**Likelihood:** medium · **Impact:** high and invisible

Confidence thresholds fitted on one model do not transfer to another. A failover mid-run means published values carry a guarantee that no longer holds, with nothing in the output indicating it.

**Mitigation:** record the model in provenance, penalise confidence on the fallback path, and mark `degraded_path` as a calibration feature so the fitted model learns the penalty rather than having it guessed.

---

## R6 — Cost blows up through per-attribute LLM calls
**Likelihood:** medium · **Impact:** medium — undermines the scalability claim

Looping tiers per attribute across 120 attributes means up to 120 calls per SKU.

**Early warning:** measured cost per SKU exceeds roughly $0.02 on the dev set.
**Mitigation:** one structured call per SKU covering all unresolved attributes. Prefix caching on the identical schema and instruction block. Rules and tables resolve a large share before any call happens.
**Measurement:** LLM avoidance rate is a reported metric, so the lever stays visible.

---

## R7 — Calibration is fitted on the data used to report results
**Likelihood:** medium · **Impact:** high — inflates every reported number

Easy to do accidentally under time pressure.

**Mitigation:** three-way split created on day 1 before any fitting. The held-out set is touched exactly once, for final reporting.

---

## R8 — Generated description reintroduces hallucination
**Likelihood:** medium · **Impact:** high — it contradicts the system's own claim

The description is the only generative surface, so it is the only place fabrication can re-enter after all the extraction gates.

**Mitigation:** generation constrained to published attributes only; claim-to-attribute alignment; untraceable spans stripped programmatically rather than trusted.
**Test:** assert that every claim in a sample of generated descriptions maps to a published attribute.

---

## R9 — Scope creep consumes the evaluation
**Likelihood:** high · **Impact:** severe

The tempting components — multi-agent loops, graph databases, a second category, a polished UI — are all more visible than the evaluation and all less valuable.

**Mitigation:** the cut list in [06 — Build Plan](06-BUILD-PLAN.md) is ordered in advance, so cutting under pressure is a lookup rather than a judgment call made while tired.

---

## R10 — Demo fails live
**Likelihood:** medium · **Impact:** medium

Network, rate limits, a cold cache.

**Mitigation:** pre-processed records in the database so the UI demonstrates without live inference. A recorded run as backup. `docker compose up` verified from a clean clone, not from the development machine.

---

## R11 — IP transfer on winning
**Likelihood:** certain if you win · **Impact:** depends entirely on intent

The rules state that IP for winning solutions transfers to the organisers.

**Consideration:** if any component is intended for reuse in another project or a portfolio, know that before submitting rather than after. This is a decision, not a risk to mitigate.

---

## R12 — Public repository before submission
**Likelihood:** self-inflicted · **Impact:** low to medium

A public repository during an open submission window is visible to other participants.

**Mitigation:** keep it private until the deadline passes, then make it public for the portfolio value. There is no benefit to publishing early and a small, real downside.
