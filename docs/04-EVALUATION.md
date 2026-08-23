# 04 — Evaluation

The evaluation is not a final validation step. It is built first, because every design decision downstream of it is only meaningful if it can be measured.

---

## The labelling problem

**Data availability is not ground-truth availability.** Product data is abundant online. That does not make it a label. If a value is extracted from a manufacturer datasheet and then "verified" against a distributor page that copied the same datasheet, nothing has been measured. The check is circular and a technical judge will identify it immediately.

A usable label must come from a **different pipeline than the one being evaluated.**

### Label sources, in order of strength

| Source | Independence | Volume | Cost |
|---|---|---|---|
| Hand audit against the datasheet | Highest — human reading the primary document | ~30 SKUs | 1 hour |
| Distributor parametric API | Good — separately curated by the distributor's own data team | Thousands | Free, API key |
| schema.org / JSON-LD on manufacturer pages | Moderate — same origin as the datasheet | Hundreds | Free |
| Published taxonomy definitions | Definitional, for classification only | Complete | Free |

### The strategy

**Silver labels at volume, gold audit at the margin.**

1. Pull structured parametric attributes from a distributor API for N products, along with the linked manufacturer datasheet PDF. This yields the pair the system needs: **input = the PDF, labels = the API's structured fields.**
2. Hand-audit a random subset of ~30 SKUs against the datasheets directly.
3. **Report the measured label-noise rate from that audit.**

Saying *"silver labels from distributor parametric data, with 4% label noise measured on a hand-audited 30-SKU subset"* is both more honest and more defensible than claiming hand labels are perfect. It also tells a judge the reported precision has a known error bar.

### Split discipline

- **Development set** — used while building rules, thresholds and prompts
- **Calibration set** — used only to fit the confidence model
- **Held-out test set** — touched once, for the final reported numbers

Fitting calibration on the same data used to report precision inflates every number. Keep them separate from the first hour.

---

## Metrics, mapped to the stated outcomes

### Outcome 1 — Structured Data Generation

| Metric | Definition |
|---|---|
| **Source location rate** | share of sparse SKU inputs for which a source was found *and verified* |
| **Attribute coverage** | filled / category-required, over located records |
| **Coverage from fragment alone** | coverage where the input had no manufacturer field |

Source location rate is the metric that reflects "limited product information." A system that only works when handed a datasheet scores zero here.

### Outcome 2 — Accuracy & Consistency

Accuracy and consistency are separate requirements and need separate metrics.

**Accuracy**

| Metric | Definition |
|---|---|
| Precision (published) | of values published, share correct after normalised comparison |
| Precision by criticality tier | reported separately for safety / functional / cosmetic |
| Recall | of attributes present in labels, share the system filled |
| Error taxonomy | wrong-value / wrong-unit / wrong-row / stale-source, counted |

**Consistency**

| Metric | Definition |
|---|---|
| Unit uniformity | share of catalog attributes expressed in the canonical unit |
| Family coherence violation rate | outliers flagged per 1,000 SKUs |
| Coverage variance within family | spread of attribute completeness across sibling SKUs |
| Key collision rate | distinct raw keys mapped to one canonical key without review |

Comparison must be **tolerance-aware**: `0.5 in` equals `12.7 mm`. Compare canonical magnitudes through `pint`, never strings.

```python
def values_match(predicted, label, rel_tol=0.01) -> bool:
    if is_quantity(predicted) and is_quantity(label):
        p = to_canonical(predicted); l = to_canonical(label)
        if p.dimensionality != l.dimensionality:
            return False
        return math.isclose(p.magnitude, l.magnitude, rel_tol=rel_tol)
    return normalise_categorical(predicted) == normalise_categorical(label)
```

### Outcome 3 — AI Validation & Enrichment

| Metric | Definition |
|---|---|
| **Auto-publish rate @ threshold** | the business metric — share publishable with no human review |
| Errors caught per validation level | L1 / L2 / L3 attribution |
| False abstention rate | correct values wrongly withheld |
| Escalation load | attributes requiring review, per SKU |
| Hallucination interception | injected-fabrication catch rate (see below) |

**Auto-publish rate at a stated precision is the number a distributor actually buys.** Coverage alone is not.

### Outcome 4 — Scalable Catalog Engine

| Metric | Definition |
|---|---|
| Throughput | SKUs per minute, end to end |
| Cost per 1,000 SKUs | measured from token counts, not estimated |
| LLM avoidance rate | share of attributes resolved without any model call |
| Cache hit rate | semantic + prefix cache |
| **Re-verification cost** | cost to process a source revision vs a full re-run |

Re-verification cost is what separates a catalog engine from a batch script, and nobody else will report it.

---

## Calibration measurement

The distinguishing artefact.

**Reliability diagram.** Bucket predictions by predicted confidence, plot observed accuracy per bucket against the diagonal.

```python
def reliability(predictions, labels, bins=10):
    rows = []
    for lo, hi in bin_edges(bins):
        subset = [p for p in predictions if lo <= p.confidence < hi]
        if not subset:
            continue
        rows.append({
            "bin": f"{lo:.1f}–{hi:.1f}",
            "n": len(subset),
            "mean_confidence": mean(p.confidence for p in subset),
            "observed_accuracy": mean(correct(p, labels) for p in subset),
        })
    return rows

def expected_calibration_error(rows, total):
    return sum(r["n"] / total * abs(r["mean_confidence"] - r["observed_accuracy"])
               for r in rows)
```

**Report Expected Calibration Error alongside precision.** A system with 92% precision and an ECE of 0.03 is materially more trustworthy than one with 94% precision and an ECE of 0.30, because the first one's abstention threshold means something.

**Abstention curve.** Sweep the publish threshold and plot precision against coverage, one curve per criticality tier. This is the artefact that answers *"how much can I publish unreviewed, and at what cost in coverage."*

---

## Ablations

Run these and report them honestly, including where a component does not help.

| Variant | Question it answers |
|---|---|
| Full system | baseline |
| No source verification (accept top retrieval) | does the match gate prevent wrong-part extraction |
| No span-containment check | how much fabrication does the cheapest gate catch |
| No relational validation (L2) | do cross-attribute rules earn their complexity |
| No family coherence (L3) | does catalog-level checking find anything unique |
| LLM-only, no rules or tables | what do the deterministic tiers actually contribute |
| Uncalibrated (self-reported confidence) | does calibration change the publish decision |

**If a component does not move the numbers, say so.** An honest negative result is stronger evidence of rigour than a suspiciously uniform set of wins, and a judge who spots an unearned claim discounts everything else.

---

## Adversarial and robustness testing

**Injected fabrication test.** Take verified-correct records, corrupt a known set of values (plausible but wrong — a voltage from a sibling part, a unit swapped, a value from an adjacent table row). Measure what fraction the validation stack intercepts, and at which level.

This directly measures the system's central claim rather than asserting it.

**Wrong-part robustness.** Feed an MPN whose datasheet is absent from the corpus but whose *sibling* part's datasheet is present. Correct behaviour is `no_source_located`, not extraction from the sibling. This is the highest-value single test in the suite, because it is the failure that would be most damaging in production and the one every naive system commits.

**Degraded input.** Truncated descriptions, missing manufacturer, malformed MPNs, scanned rather than digital PDFs. Confirm graceful degradation and that confidence drops accordingly rather than staying high.

---

## Reporting template

```
DATASET
  Vertical:                 ...
  SKUs (held-out test):     ...
  Labels:                   distributor parametric API (silver)
  Label noise:              ...% measured on 30-SKU hand audit
  Attributes per category:  ...

STRUCTURED DATA GENERATION
  Source location rate:     ...%
  Attribute coverage:       ...%

ACCURACY & CONSISTENCY
  Precision (published):    ...%   [safety ...%  functional ...%  cosmetic ...%]
  Recall:                   ...%
  Unit uniformity:          ...%
  Family coherence flags:   ... per 1,000 SKUs

VALIDATION & ENRICHMENT
  Auto-publish rate:        ...%  at per-tier thresholds
  Injected-fabrication catch: ...%
  False abstention rate:    ...%
  Escalation load:          ... attributes/SKU

SCALABLE CATALOG ENGINE
  Throughput:               ... SKUs/min
  Cost per 1,000 SKUs:      $...
  LLM avoidance:            ...% of attributes
  Re-verification cost:     ...% of a full re-run

CALIBRATION
  Expected Calibration Error: ...
  Reliability diagram:      [figure]
  Abstention curve:         [figure, per criticality tier]

ABLATIONS
  [table]

KNOWN LIMITATIONS
  [stated plainly]
```

A stated limitations section is not a weakness. Every system has failure modes; the ones that name them are the ones that measured them.
