"""Build the whole corpus, across every category (doc 06 1.1).

Two categories now share one corpus, one source index and one pipeline. That
is the claim doc 02 makes about the canonical model — industry standards and
attribute sets are data, not separate pipelines — and it is only worth anything
if it is exercised.

    python -m sourced.corpus.build
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from sourced import config
from sourced.corpus import fittings
from sourced.corpus.generate import SEED, clear_media, generate

COHORTS = ["normal", "contradicted", "conflict", "distributor_only", "sibling_trap"]


def build(out_dir: Path | None = None, seed: int = SEED) -> dict:
    data = Path(out_dir or config.DATA)
    clear_media(data)
    rng = random.Random(seed)

    records, sources = generate(out_dir=data, clear=False, write=False, rng=rng)
    fitting_records, fitting_sources = fittings.build(data, rng)
    records += fitting_records
    sources += fitting_sources

    # a part number must identify one part across the whole catalogue, not just
    # within its own category
    seen: dict[str, str] = {}
    duplicates = []
    for record in records:
        mpn = record["sku_input"]["mpn"]
        if mpn in seen:
            duplicates.append(mpn)
        seen[mpn] = record["category"]

    (data / "corpus.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (data / "sources.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sources) + "\n", encoding="utf-8")

    categories = sorted({r["category"] for r in records})
    summary = {
        "skus": len(records),
        "categories": categories,
        "datasheets": sum(1 for s in sources
                          if s["source_type"] == "manufacturer_datasheet"),
        "distributor_pages": sum(1 for s in sources
                                 if s["source_type"] != "manufacturer_datasheet"),
        "cohorts": {c: sum(1 for r in records if r["cohort"] == c) for c in COHORTS},
        "splits": {s: sum(1 for r in records if r["split"] == s)
                   for s in ["dev", "calibration", "test"]},
        "duplicate_mpns": len(duplicates),
        "by_category": {
            category: {
                "skus": sum(1 for r in records if r["category"] == category),
                "cohorts": {c: sum(1 for r in records
                                   if r["category"] == category and r["cohort"] == c)
                            for c in COHORTS},
                "splits": {s: sum(1 for r in records
                                  if r["category"] == category and r["split"] == s)
                           for s in ["dev", "calibration", "test"]},
                "label_keys": len({k for r in records if r["category"] == category
                                   for k in r["labels"]}),
            }
            for category in categories
        },
    }
    (data / "corpus_summary.json").write_text(json.dumps(summary, indent=2),
                                              encoding="utf-8")
    return summary


if __name__ == "__main__":
    import pprint

    pprint.pp(build())
