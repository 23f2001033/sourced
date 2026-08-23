"""Measuring the generated description (ADR-013, risk R8).

The description is the only generative surface in the system and therefore the
only place fabrication can re-enter after every extraction gate. Doc 07 lists
that as risk R8; doc 08 claims three defences: generation is fed only published
attributes, every claim is aligned to the attribute that licensed it, and spans
that cannot be traced are stripped.

None of that was ever tested against a model, because the composed fallback
runs when no provider is configured and its traceability is exact by
construction -- which proves nothing.

The audit here is deliberately **independent of the pipeline's own alignment**.
Trusting `_align_claims` to grade itself would be circular, so the check is
external and blunt: every number that appears in the finished copy must appear
among the published attribute values. A specification the model invented --
"IP67", "rated to 600 V", "5000 mating cycles" -- introduces a number that no
published attribute can account for, and that is detectable without asking the
system whether it thinks it behaved.

    python -m sourced.eval.description_experiment --sample 20
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time

from sourced import config
from sourced.candidates.providers import get_provider
from sourced.commerce import description as description_mod
from sourced.commerce.title import render_value
from sourced.confidence.calibrate import Calibrator
from sourced.discovery.retrieve import SourceIndex
from sourced.eval import labels as label_mod
from sourced.ingest.loader import load_corpus
from sourced.models import ProductRecord
from sourced.pipeline import Options, Pipeline
from sourced.registry import CategorySchema, load_schema

RESULT_PATH = config.DATA / "description_experiment.json"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Numbers that are part of a name rather than a claim: UL94 V-0, 304 stainless,
# NSF 372. Counting them as fabrications would manufacture a finding.
_SAFE_CONTEXTS = re.compile(
    r"(?:UL\s*94|NSF|ASME|ANSI|ISO|IEC|RoHS|Class|Schedule|Sch|PA6T|304|316)",
    re.I)


def published_numbers(product: ProductRecord) -> set[str]:
    """Every number a published attribute could licence, in the forms copy
    might use: `0.75`, `3/4`, `75`, and the rendered display value."""
    allowed: set[str] = set()
    for attr in product.attributes.values():
        if attr.resolution != "published" or attr.value is None:
            continue
        for text in (str(attr.value), render_value(attr), str(attr.raw_text)):
            for token in _NUMBER.findall(text):
                allowed.add(_canon(token))
                # a trailing-zero variant of the same magnitude
                try:
                    allowed.add(_canon(f"{float(token):g}"))
                except ValueError:
                    pass
    return allowed


def _canon(token: str) -> str:
    try:
        value = float(token)
    except ValueError:
        return token
    return f"{value:g}"


def without_identifiers(text: str, product: ProductRecord) -> str:
    """Blank out the part number and manufacturer before scanning.

    `B400-TH209X125` contains 400, 209 and 125. None of them is a claim about
    the product, and counting them as fabrications flagged the deterministic
    control -- which is exact by construction, and so was proof the audit was
    wrong rather than the copy.
    """
    for identifier in filter(None, [product.mpn, product.mpn_normalised,
                                    product.manufacturer_resolved,
                                    product.manufacturer]):
        text = text.replace(identifier, " ")
    return text


def unlicensed_numbers(text: str, allowed: set[str]) -> list[str]:
    """Numbers in the copy that no published attribute accounts for."""
    found = []
    for match in _NUMBER.finditer(text or ""):
        token = match.group(0)
        if _canon(token) in allowed:
            continue
        window = text[max(0, match.start() - 12):match.end() + 12]
        if _SAFE_CONTEXTS.search(window):
            continue
        found.append(token)
    return found


def audit(product: ProductRecord, schema: CategorySchema, text: str,
          claims: list) -> dict:
    allowed = published_numbers(product)
    unlicensed = unlicensed_numbers(without_identifiers(text, product), allowed)
    untraceable = [c for c in claims if c.source_attribute is None]
    published_keys = {k for k, a in product.attributes.items()
                      if a.resolution == "published"}
    return {
        "characters": len(text),
        "claims": len(claims),
        "claims_untraceable_after_stripping": len(untraceable),
        "unlicensed_numbers": unlicensed,
        "claims_citing_a_key_that_is_not_published": [
            c.source_attribute for c in claims
            if c.source_attribute is not None
            and c.source_attribute not in published_keys],
        "spans_align_with_text": all(
            text[c.span_start:c.span_end] == c.text_span for c in claims),
    }


def run(sample_size: int = 20, seed: int = 11) -> dict:
    provider = get_provider()
    if provider is None:
        return {"error": "no LLM provider configured"}

    corpus = label_mod.load_corpus_records()
    test = label_mod.split(corpus, "test")
    rng = random.Random(seed)
    candidates = [r for r in test if r["cohort"] != "sibling_trap"]
    sample = rng.sample(candidates, min(sample_size, len(candidates)))

    index = SourceIndex(load_corpus())
    calibrator = Calibrator.load()
    schemas = {c: load_schema(c) for c in {r["category"] for r in corpus}}
    pipeline = Pipeline(index, Options(llm_tier=False), calibrator=calibrator)

    generated_rows, composed_rows, examples = [], [], []
    started = time.perf_counter()

    for record in sample:
        sku = label_mod.sku_of(record)
        schema = schemas[record["category"]]
        product = pipeline.run(sku, schema)
        if product.source_status != "verified":
            continue
        if not any(a.resolution == "published" for a in product.attributes.values()):
            continue

        composed_text, composed_claims = description_mod.compose(product, schema)
        composed_rows.append(audit(product, schema, composed_text, composed_claims))

        text, claims, generator = description_mod.generate(product, schema,
                                                           use_llm=True)
        row = audit(product, schema, text, claims)
        row["generator"] = generator
        generated_rows.append(row)

        # keep every description the audit flagged, plus a few clean ones:
        # characterising a failure rate from whichever rows happened to come
        # first is how a single odd case becomes a general claim
        flagged = bool(row["unlicensed_numbers"])
        clean_kept = sum(1 for e in examples if not e["unlicensed_numbers"])
        if flagged or clean_kept < 3:
            examples.append({
                "mpn": product.mpn,
                "category": record["category"],
                "published_attributes": len(
                    [a for a in product.attributes.values()
                     if a.resolution == "published"]),
                "generated": text,
                "composed": composed_text,
                "unlicensed_numbers": row["unlicensed_numbers"],
                "generator": generator,
            })

    elapsed = time.perf_counter() - started

    def summarise(rows: list[dict]) -> dict:
        if not rows:
            return {"descriptions": 0}
        with_unlicensed = [r for r in rows if r["unlicensed_numbers"]]
        return {
            "descriptions": len(rows),
            "mean_characters": round(
                sum(r["characters"] for r in rows) / len(rows), 1),
            "mean_claims": round(sum(r["claims"] for r in rows) / len(rows), 2),
            "descriptions_with_an_unlicensed_number": len(with_unlicensed),
            "unlicensed_number_rate": round(len(with_unlicensed) / len(rows), 4),
            "total_unlicensed_numbers": sum(
                len(r["unlicensed_numbers"]) for r in rows),
            "claims_left_untraceable": sum(
                r["claims_untraceable_after_stripping"] for r in rows),
            "claims_citing_an_unpublished_key": sum(
                len(r["claims_citing_a_key_that_is_not_published"]) for r in rows),
            "all_claim_spans_align": all(r["spans_align_with_text"] for r in rows),
        }

    fell_back = sum(1 for r in generated_rows
                    if r.get("generator", "").startswith("composed"))
    return {
        "provider": provider.name,
        "model": config.LLM_MODEL,
        "sample": {"requested": sample_size, "descriptions_audited": len(generated_rows),
                   "split": "test (held out)"},
        "generated": summarise(generated_rows),
        "composed_control": summarise(composed_rows),
        "generation_failures_falling_back_to_composed": fell_back,
        "cost": {**provider.usage.as_dict(),
                 "wall_seconds": round(elapsed, 1),
                 "seconds_per_description": (round(elapsed / len(generated_rows), 2)
                                             if generated_rows else None)},
        "examples": examples,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=20)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    result = run(args.sample, args.seed)
    RESULT_PATH.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "examples"},
                     indent=2, default=str))
    print(f"\nwrote {RESULT_PATH}")
