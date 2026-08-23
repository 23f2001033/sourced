# Hack2Skill submission form — paste-ready answers

Four required fields. Every figure below is measured on the held-out test split
and reproducible with `python -m sourced.eval.report`.

---

## 1. Provide a brief overview of your solution and how it solves the problem.

**The field caps at 2,056 characters.** This is 2011 — copy everything between the rules.

---

A distributor's starting point is not a datasheet. It is a row like "MPN: 3GAA132214-ADE | MFR: ABB | DESC: MOT 3GAA132 5.5KW 4P B3" - no document attached, from which a sellable record needs 80-120 attributes.

Hand that fragment to an LLM and you get answers that are plausible, unsourced and occasionally wrong. In industrial supply a wrong pressure rating means the wrong part arrives on a job site. Confidently wrong is worse than absent.

Sourced is a seven-stage enrichment engine built around that constraint.

It finds the source first. From a bare part number it retrieves candidate documents and verifies the document actually names that part before reading a value. No verified source means no extraction: the record returns as no_source_located, with the reason and what would fix it. All 24 "sibling trap" parts in the held-out set - where a family member's datasheet matches every signal except the part number - were refused. Disable that gate and every one leaks.

It publishes with provenance: every value carries the document, page and bounding box it came from, and clicking it outlines that exact cell on the rendered PDF.

It calibrates its own confidence rather than asking the model - fitted from observable signals against a separate split, published as a reliability diagram (ECE 0.006). Thresholds are set per criticality tier: a wrong housing colour is a cosmetic defect, a wrong current rating is a fire.

Measured on 207 held-out SKUs across two verticals: 99.6% precision on 1,897 published values, 76.9% auto-publishable with no human review, 100% source location, 24/24 wrong-part traps refused, 100% of 176 injected fabrications caught. Run an open-weight LLM through the pipeline and the deterministic gates reject 88.6% of its proposals, taking the precision of what survives from 33.3% to 100%.

It scales because a category is a YAML file, not a code change: two verticals share one pipeline at 4,400+ SKUs/min.

Where it cannot answer, it says why - and that is the point.

---

## 2. Share the link to your live prototype demonstrating the core functionality.

```
https://sourced-peach.vercel.app
```

Live and public — no login, no cold start. It serves a **snapshot of a real
pipeline run**: 656 enriched records with their full provenance, and the source
pages they were read from. Click any published value and the exact cell it came
from is outlined on the rendered PDF. 380 records have a page-level citation to
outline; the rest cite structured listing rows, which have no geometry to draw.

Two things a reviewer should try, in order:

1. The record it opens on — click through the attributes and watch the outlined
   region move around the datasheet page.
2. Set the filter to **no source located** — 93 records that returned an
   explained refusal instead of a guess. That is the thesis of the project.

What the hosted page deliberately does not do is enrich a *new* part number: the
pipeline carries scikit-learn, pdfplumber and a retrieval index built over every
source document at start-up, which no serverless function can hold. That path
runs locally in one command and is what the demo video shows:

```
docker compose up   ->   http://localhost:8000
```

The page says this plainly rather than implying otherwise.

## 3. Share the GitHub Repository link

```
https://github.com/23f2001033/sourced
```

Live and public. Verified after pushing:

- [x] `.env` is **not** on the remote — the API key never left this machine
- [x] `docs/RESULTS.md`, `docs/figures/*.png` and the deck PDF are all present
- [x] Repository is **public**, default branch `main`

---

## 4. Share the link to a short demo video showcasing your solution.

Record from [DEMO_SCRIPT.md](DEMO_SCRIPT.md) — it has the beats, timings and
setup. Upload unlisted to YouTube or Drive and paste the link.

The five shots that matter, in order:

1. The sparse input row
2. A record with the PDF region outlined — the whole pitch in one frame
3. The sibling trap being refused, with its reason
4. The same pipeline on a pipe fitting
5. The reliability diagram and the gates-on / gates-off comparison

---

## One-line fallback

If any field wants a single sentence:

> Sourced turns a bare part number into a commerce-ready record with the
> document, page and region every value came from — and returns an explained
> refusal instead of a plausible guess.
