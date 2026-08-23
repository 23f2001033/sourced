# Hack2Skill submission form — paste-ready answers

Four required fields. Every figure below is measured on the held-out test split
and reproducible with `python -m sourced.eval.report`.

---

## 1. Provide a brief overview of your solution and how it solves the problem.

> Copy everything between the rules.

---


---

## 2. Share the link to your live prototype demonstrating the core functionality.

**You do not have a deployed URL yet.** The prototype runs locally in one
command, but this field wants a link. Two options:

- **Deploy it** (recommended if you have 30 minutes) — Render, Railway or Fly
  all accept the existing `Dockerfile`. Postgres is optional; `SOURCED_DB_URL`
  defaults to SQLite, so a single web service works.
- **Point at the repo's run instructions** if a URL is not required to be live:

```
https://github.com/23f2001033/sourced#running-it
(one command: docker compose up  ->  http://localhost:8000)
```

Check whether the form validates this as a URL before relying on the second
option.

---

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
