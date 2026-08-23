# Demo script — 3 minutes

Short sentences. One idea per beat. Every number on screen is real and
reproducible.

**Before you record:** run the setup below once, keep two windows open — a
browser on `http://localhost:8000` and a terminal — and rehearse once so the
clicks land on the beat.

---

## Setup (do this before recording)

```bash
pip install -e ".[dev]"
python -m sourced.corpus.build              # builds the corpus: PDFs, listings, labels
python -m sourced.eval.report --persist --fresh   # evaluates and loads the catalogue
uvicorn sourced.api.routes:app --port 8000        # leave this running
```

Takes about six minutes. Then open `http://localhost:8000`.

**Safety net:** `python -m sourced.demo --replay` narrates the whole system
from a recorded run — no pipeline, no model, no database. If anything fails
live, switch to that window and keep talking.

---

## The script

### 0:00 — The problem (20s)

> "A distributor doesn't start with a datasheet. They start with a row like
> this."

**Show:** the input line on screen or a slide.

```
MPN: 3GAA132214-ADE   MFR: ABB   DESC: MOT 3GAA132 5.5KW 4P B3
```

> "No document attached. They need eighty to a hundred and twenty attributes
> from that.
>
> Hand it to an LLM and you get answers. Some of them are wrong, and none of
> them tell you where they came from. In industrial supply, a wrong pressure
> rating means the wrong part arrives on a job site. **Confidently wrong is
> worse than absent.**"

---

### 0:20 — A row becomes a record (40s)

**Do:** click a SKU in the left pane.

> "This is Sourced. Same kind of row in — part number, brand, a fragment of
> description.
>
> It found the datasheet itself. It verified the datasheet actually names this
> part before reading a single value from it.
>
> Twelve attributes published. Two held for review. One refused."

**Do:** click the `pitch` row. The right pane fills.

> "And this is the point of the whole system. Every published value knows where
> it came from — the document, the page, the exact cell."

**Do:** point at the outlined region in the PDF image.

> "That red box is the cell it was read from. Not a citation the model wrote.
> The coordinates the extractor recorded."

---

### 1:00 — The refusal (30s)

**Do:** filter the list to `no source located`, click one.

> "Now the interesting part.
>
> This part's datasheet isn't in the corpus. Its *sibling's* is — same
> manufacturer, same series, same layout, overlapping description. Every soft
> signal matches. Only the part number is different.
>
> Most systems will happily read the sibling's datasheet and give you values
> that are formatted correctly, in range, and wrong.
>
> This one refuses — and tells you why, and what would fix it."

**Show:** the abstention banner.

> "Twenty-four traps in the held-out set. Twenty-four refusals. Turn that gate
> off and every single one leaks."

---

### 1:30 — When sources disagree (20s)

**Do:** open a record with a `sources_conflict` abstention.

> "Two manufacturer datasheets. Different current ratings for the same part.
>
> It doesn't average them. It doesn't take the newer one. It abstains and names
> both — because current rating is a safety attribute, and guessing on a safety
> attribute is how you start a fire.
>
> A wrong housing colour is a cosmetic defect. They do not share a threshold."

---

### 1:50 — The same pipeline, a different vertical (25s)

**Do:** switch to a pipe fitting record.

> "This is a pipe fitting, not a connector. Different attributes, different
> physics, different documents — same pipeline. A category is a YAML file, not
> a code change.
>
> And here the row itself does the work."

**Show:** the fragment.

```
1/2IN X 3/4IN BRS 90 ELL FIP 150#
```

> "Seven attributes out of that — size, reducing size, brass body, ninety
> degree bend, elbow, female iron pipe, class one fifty. No document. No model.
> Just rules.
>
> It routed to the right attribute set a hundred percent of the time, and never
> invented a taxonomy code."

---

### 2:15 — Why you can trust it (30s)

**Show:** `docs/figures/reliability.png`.

> "Everyone shows confidence scores. Almost nobody shows theirs are calibrated.
> That's our reliability diagram — predicted confidence against what actually
> happened. Calibration error, six thousandths.
>
> And we tested the guard rails by removing them."

**Show:** the gates table from RESULTS.md.

> "We ran a language model through the pipeline. It made forty-four proposals.
> The deterministic checks threw out eighty-nine percent of them — spans that
> weren't in the document it cited, values the cited text didn't support.
>
> With the gates on, what survived was a hundred percent correct. With them
> off, thirty-three percent — and it invented values for eight attributes those
> parts don't have.
>
> The cheapest check in the system does the most work."

---

### 2:45 — Close (15s)

> "Ninety-nine point six percent precision. Seventy-seven percent
> auto-publishable with no human review. Five thousand SKUs a minute. A source
> revision costs seven percent of a full re-run.
>
> Every number measured on a held-out split, and the results document reports
> the three tests that showed nothing — including one place our own design
> prediction was wrong.
>
> Sourced. It shows its work, and it says no."

---

## If you have 60 seconds instead of 3 minutes

Cut to three beats:

1. **0:00–0:20** — the sparse row, and the record with the PDF region outlined
2. **0:20–0:40** — the sibling trap, refused with a reason
3. **0:40–1:00** — 99.6% precision, 24/24 traps, gates take a model from 33% to 100%

---

## Recording checklist

- [ ] Browser at 100% zoom, one window, no bookmarks bar
- [ ] Terminal font large enough to read at 1080p
- [ ] `uvicorn` already running before you hit record
- [ ] `python -m sourced.demo --replay` open in a second tab as the fallback
- [ ] `docs/figures/reliability.png` open and ready
- [ ] Say the numbers out loud — they are the differentiator

---

## Submission checklist

```bash
# 1. Confirm the repo is clean and the secret is not in it
git init
git add -A
git status --short | grep "\.env$" && echo "STOP: .env is staged" || echo "ok: .env excluded"

# 2. First commit
git commit -m "Sourced — product intelligence for industrial commerce"

# 3. Push to a PRIVATE repo first; make it public after the deadline
#    (your own risk register, R12: a public repo during an open submission
#     window is visible to other participants)
git remote add origin https://github.com/<you>/sourced.git
git push -u origin main
```

- [ ] `.env` is **not** in the repo — it holds a live API key
- [ ] Rotate that key once the submission is in; it has been in a chat log
- [ ] `docs/RESULTS.md` and `docs/figures/*.png` are committed — the deck cites them
- [ ] README quickstart works from a clean clone
- [ ] Repo public before the deadline passes
