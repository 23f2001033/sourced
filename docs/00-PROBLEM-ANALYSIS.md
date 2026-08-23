# 00 — Problem Analysis

## The brief, decomposed literally

> "Participants are invited to build AI-powered solutions that can transform **limited product information** into **rich**, **reliable**, and **commerce-ready** product intelligence — focusing on **data enrichment**, **validation**, and **explainable outputs**."

Each phrase is a requirement, not decoration.

| Phrase | Concrete demand | Failure mode if ignored |
|---|---|---|
| limited product information | Input is a sparse SKU row, not a supplied document | You build a datasheet parser, which is a different product |
| rich | Coverage across a category-appropriate attribute set | Six attributes filled out of ninety |
| reliable | Measured precision; abstain rather than guess | Plausible fabricated specifications |
| commerce-ready | Title, description, facets, category, completeness | A PIM record, not a sellable product |
| data enrichment | Fill gaps *from sources* | Fill gaps from model memory, unsourced |
| validation | Per-attribute, cross-attribute, cross-SKU | Individually plausible values that contradict each other |
| explainable outputs | Why this value, **and why this abstention** | Black box, or a blank field with no reason |

## The four stated outcomes

1. **Structured Data Generation** — generate structured product information from limited inputs
2. **Accuracy & Consistency** — improve accuracy *and consistency* of product data
3. **AI Validation & Enrichment** — validate and enrich product information using AI
4. **Scalable Catalog Engine** — build scalable solutions for large product catalogs

Note that **accuracy and consistency are separate requirements.** Accuracy is per-SKU correctness. Consistency is catalog-level uniformity. A catalog can be accurate and inconsistent — every value correct, expressed in six different unit conventions, with sibling products having wildly different completeness. Most solutions address only accuracy.

## What the domain actually looks like

Verified characteristics of industrial product data:

- Distributors carry tens of thousands of SKUs where the record is frequently **just a manufacturer part number and a truncated description**.
- A complete record requires **80–120 attributes per product**. Manual remediation creates a backlog that never clears.
- **Normalisation is the hardest and most valuable stage.** `1/2 in`, `0.5"`, `12.7mm` and `half inch` are one value. Nothing arrives consistent.
- Output must satisfy **multiple competing standards** — ETIM for one customer, UNSPSC for another, a bespoke ERP shape for a third. These do not map cleanly onto each other.
- Descriptions are **compressed abbreviation soup**: `1/2IN X 3/4IN BRS 90 ELL FIP 150#`.

## Why the obvious approaches fail

### "Put the PDF in an LLM and ask for JSON"

Three problems.

**It loses tables.** Datasheets are predominantly tabular. Naive text chunking destroys row-to-column association, and the model confidently reads a value from the wrong row. This is the single largest source of wrong values in this domain.

**It has no provenance.** The brief explicitly asks for explainable outputs. A JSON blob cannot tell a data steward which page a value came from, so every field needs human re-verification, which defeats the purpose.

**It does not scale economically.** One LLM call per attribute across 120 attributes and a million SKUs is not a viable unit cost. Even one call per SKU at catalog scale is significant.

### "Extract, then have another LLM check it"

An LLM checking another LLM is **weaker evidence than a deterministic check**, not stronger — particularly when both are the same model with a different prompt, where failure modes are largely shared. It also doubles cost and latency.

Deterministic gates (does the claimed evidence span literally appear in the source, does the unit parse, is the value in range for this category, is it consistent with related attributes) are free, fast, and auditable. An LLM critic is useful only as a last-resort tier on values that survived everything cheaper.

### "Ask the model how confident it is"

**Self-reported LLM confidence is not calibrated.** A model asked to rate its own certainty produces numbers that look meaningful and do not track correctness. An abstention policy built on them is built on noise.

Confidence must be computed from observable signals and fitted against labelled data. See [04 — Evaluation](04-EVALUATION.md).

### "Start from the datasheet"

This is the subtle one, and the reason for Stage 0.

The brief says **limited** product information, scattered across sources. If the datasheet were already attached to the SKU, the problem would largely be solved. The distributor's actual state is a part number and a fragment.

So the first hard problem is **finding and verifying the right source document**, and it is skipped by every solution that begins with a file upload. It also matters for safety: given an MPN and no retrieved document, an LLM will generate attributes from parametric memory. Those attributes will be plausible, unsourced, and sometimes wrong, and a provenance layer will faithfully record `source: none` while the value ships anyway.

## The insight the solution is built around

Everyone can generate attributes. The question a distributor actually asks is:

> **Which of these generated values can I publish without a human looking at them, and can you prove it?**

That reframes the target metric from *coverage* to **auto-publish rate at a stated precision**, which is a business number, not a model number. It makes abstention a feature rather than a failure, and it makes calibration load-bearing rather than decorative.

## Non-goals

Stated explicitly so scope does not drift:

- Not a full PIM. No workflow, versioning, or user management beyond what the demo needs.
- Not a storefront. Commerce-ready output is produced and exported, not rendered as a shop.
- Not a general-purpose document parser. Scoped to one product vertical, deliberately.
- Not attempting every industry standard. One taxonomy done correctly beats three done partially.
