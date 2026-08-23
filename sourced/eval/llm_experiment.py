"""Measuring the LLM tier (doc 04, outcome 3 and 4).

Until a provider was configured this tier was implemented and never run, which
made three claims untestable:

  1. that the model adds coverage on exactly the attributes the deterministic
     tiers cannot reach, rather than duplicating them;
  2. that what it adds is correct;
  3. that span containment is a real gate (ADR-006) rather than a nice idea --
     a rejection rate of zero would mean the gate is decorative.

The design is a paired A/B on the same SKUs: the pipeline runs with the tier
off and then on, and the difference is attributed. It runs on a sample rather
than the whole split, because open-weight inference here costs 12-80 seconds a
call under contention, and the sample size is reported alongside every number
it produces.

    python -m sourced.eval.llm_experiment --sample 40
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from sourced import config
from sourced.candidates.providers import get_provider
from sourced.confidence.calibrate import Calibrator
from sourced.discovery.retrieve import SourceIndex
from sourced.eval import labels as label_mod
from sourced.ingest.loader import load_corpus
from sourced.ingest.normalize import values_match
from sourced.models import ProductRecord
from sourced.pipeline import Options, Pipeline
from sourced.registry import load_schema

RESULT_PATH = config.DATA / "llm_experiment.json"


def _resolved(product: ProductRecord) -> dict[str, tuple]:
    return {key: (attr.value, attr.unit, attr.resolution, attr.tier)
            for key, attr in product.attributes.items()
            if attr.winning_candidate is not None}


def stratified_sample(records: list[dict], size: int, seed: int = 7) -> list[dict]:
    """Proportional across categories, and only SKUs that have a source: a SKU
    with no located source never reaches Stage 2, so it cannot exercise the
    tier either way."""
    rng = random.Random(seed)
    by_category: dict[str, list[dict]] = {}
    for record in records:
        if record["cohort"] == "sibling_trap":
            continue
        by_category.setdefault(record["category"], []).append(record)

    total = sum(len(v) for v in by_category.values())
    chosen: list[dict] = []
    for category, items in sorted(by_category.items()):
        take = max(1, round(size * len(items) / total))
        chosen += rng.sample(items, min(take, len(items)))
    return chosen[:size]


def run(sample_size: int = 40, seed: int = 7,
        arms: str = "both") -> dict:
    corpus = label_mod.load_corpus_records()
    test = label_mod.split(corpus, "test")
    sample = stratified_sample(test, sample_size, seed)

    index = SourceIndex(load_corpus())
    calibrator = Calibrator.load()
    schemas = {c: load_schema(c) for c in {r["category"] for r in corpus}}

    provider = get_provider()
    if provider is None:
        return {"error": "no LLM provider configured"}

    from sourced.candidates.llm import SELF_CONSISTENCY_N

    baseline = Pipeline(index, Options(llm_tier=False), calibrator=calibrator)
    withllm = Pipeline(index, Options(llm_tier=True), calibrator=calibrator)
    # The third arm is what makes ADR-006 falsifiable. With the gates off, the
    # model's proposals go straight into the record, so the precision of
    # ungated LLM output can be compared against the gated one on the same SKUs.
    # The ungated arm answers a question about the *gates*, not about
    # self-consistency, so a sampling sweep can skip it and halve the calls.
    ungated = (Pipeline(index, Options(llm_tier=True, span_containment=False),
                        calibrator=calibrator) if arms == "both" else None)

    added: list[dict] = []
    ungated_rows: list[dict] = []
    unchanged = 0
    started = time.perf_counter()

    for record in sample:
        sku = label_mod.sku_of(record)
        schema = schemas[record["category"]]
        labels = label_mod.labels_of(record)

        before = baseline.run(sku, schema)
        after = withllm.run(sku, schema)
        if before.source_status != "verified":
            continue

        before_keys = _resolved(before)
        after_keys = _resolved(after)
        new_keys = [k for k in after_keys if k not in before_keys]
        if not new_keys:
            unchanged += 1

        loose = ungated.run(sku, schema) if ungated is not None else None
        for key, attr in (loose.attributes.items() if loose is not None else []):
            if key in before_keys or attr.winning_candidate is None:
                continue
            if attr.winning_candidate.tier != "llm":
                continue
            label = labels.get(key)
            correct = None
            if attr.value is not None:
                correct = (values_match(attr.value, attr.unit, label["value"],
                                        label.get("unit")) if label is not None
                           else False)     # no label means the attribute does
                                           # not exist on this part at all
            ungated_rows.append({
                "mpn": record["sku_input"]["mpn"], "key": key,
                "value": attr.value, "unit": attr.unit,
                "label": None if label is None else label["value"],
                "correct": correct, "resolution": attr.resolution,
                "had_label": label is not None,
            })

        for key in new_keys:
            attr = after.attributes[key]
            label = labels.get(key)
            correct = None
            if label is not None and attr.value is not None:
                correct = values_match(attr.value, attr.unit,
                                       label["value"], label.get("unit"))
            evidence = attr.evidence
            added.append({
                "mpn": record["sku_input"]["mpn"],
                "category": record["category"],
                "key": key,
                "value": attr.value,
                "unit": attr.unit,
                "label": None if label is None else label["value"],
                "correct": correct,
                "resolution": attr.resolution,
                "confidence": round(attr.confidence, 4),
                "tier": attr.tier,
                "span": (evidence.span if evidence else None),
                "chunk_id": (evidence.chunk_id if evidence else None),
            })

    elapsed = time.perf_counter() - started
    stats = withllm.llm_stats
    usage = provider.usage

    ungated_scored = [a for a in ungated_rows if a["correct"] is not None]
    ungated_published = [a for a in ungated_rows if a["resolution"] == "published"]
    ungated_published_scored = [a for a in ungated_published
                                if a["correct"] is not None]

    scored = [a for a in added if a["correct"] is not None]
    published = [a for a in added if a["resolution"] == "published"]
    published_scored = [a for a in published if a["correct"] is not None]

    return {
        "provider": provider.name,
        "model": config.LLM_MODEL,
        "self_consistency_n": SELF_CONSISTENCY_N,
        "sample": {
            "requested": sample_size,
            "skus_run": len(sample),
            "by_category": {c: sum(1 for r in sample if r["category"] == c)
                            for c in sorted({r["category"] for r in sample})},
            "split": "test (held out)",
        },
        "contribution": {
            "attributes_added": len(added),
            "attributes_added_per_sku": (round(len(added) / len(sample), 3)
                                         if sample else None),
            "skus_unchanged_by_the_tier": unchanged,
            "added_and_published": len(published),
            "precision_of_added_values": (
                round(sum(1 for a in scored if a["correct"]) / len(scored), 4)
                if scored else None),
            "precision_of_added_published_values": (
                round(sum(1 for a in published_scored if a["correct"])
                      / len(published_scored), 4) if published_scored else None),
            "added_by_key": _count(a["key"] for a in added),
            "wrong_values": [a for a in added if a["correct"] is False][:10],
        },
        "gates": stats.as_dict(),
        "gates_removed": {
            "note": ("the same SKUs with span containment and the value/span "
                     "pairing check disabled, so the model's proposals reach "
                     "the record unfiltered"),
            "llm_values_in_record": len(ungated_rows),
            "llm_values_published": len(ungated_published),
            "precision_of_ungated_llm_values": (
                round(sum(1 for a in ungated_scored if a["correct"])
                      / len(ungated_scored), 4) if ungated_scored else None),
            "precision_of_ungated_published": (
                round(sum(1 for a in ungated_published_scored if a["correct"])
                      / len(ungated_published_scored), 4)
                if ungated_published_scored else None),
            "values_for_attributes_the_part_does_not_have": sum(
                1 for a in ungated_rows if not a["had_label"]),
            "examples": [a for a in ungated_rows if a["correct"] is False][:8],
        },
        "cost": {
            **usage.as_dict(),
            "wall_seconds": round(elapsed, 1),
            "seconds_per_sku": round(elapsed / len(sample), 2) if sample else None,
            "prompt_tokens_per_sku": (round(usage.prompt_tokens / len(sample), 1)
                                      if sample else None),
        },
        "detail": added,
        "ungated_detail": ungated_rows,
    }


def _count(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--arms", choices=["both", "gated"], default="both",
                        help="'gated' skips the ungated comparison arm")
    parser.add_argument("--out", default=None,
                        help="write to this path instead of the default")
    args = parser.parse_args()

    result = run(args.sample, args.seed, arms=args.arms)
    target = Path(args.out) if args.out else RESULT_PATH
    target.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    summary = {k: v for k, v in result.items() if k != "detail"}
    if "contribution" in summary:
        summary["contribution"] = {k: v for k, v in summary["contribution"].items()
                                   if k != "wrong_values"}
    print(json.dumps(summary, indent=2, default=str))
    print(f"\nwrote {RESULT_PATH}")
