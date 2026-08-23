"""Batch evaluation (doc 04, doc 06 3.1).

Split discipline is enforced structurally: calibration is fitted on the
calibration split alone, and the held-out test split is touched only when the
final numbers are computed.

    python -m sourced.eval.run              full report into docs/RESULTS.md
    python -m sourced.eval.run --quick      dev split only, no ablations
"""
from __future__ import annotations

import argparse
import copy
import json
import time
from dataclasses import dataclass, field

from sourced import config
from sourced.candidates.rules import MpnDecoder
from sourced.confidence.calibrate import (Calibrator, abstention_curve,
                                          expected_calibration_error, reliability)
from sourced.discovery.retrieve import SourceIndex
from sourced.eval import labels as label_mod
from sourced.eval import metrics as metric_mod
from sourced.eval.metrics import Outcome
from sourced.ingest.loader import load_corpus
from sourced.models import ProductRecord
from sourced.pipeline import Options, Pipeline, score_and_decide
from sourced.registry import load_schema
from sourced.taxonomy.index import default_index
from sourced.validate.family import apply_family_checks, family_checks, group_families


@dataclass
class BatchResult:
    records: list[ProductRecord] = field(default_factory=list)
    by_mpn: dict[str, ProductRecord] = field(default_factory=dict)
    elapsed: float = 0.0


def run_batch(pipeline: Pipeline, records: list[dict], schema, apply_family: bool = True,
              calibrator: Calibrator | None = None, calibrated: bool = True) -> BatchResult:
    started = time.perf_counter()
    out: list[ProductRecord] = []
    for record in records:
        out.append(pipeline.run(label_mod.sku_of(record), schema))
    elapsed = time.perf_counter() - started

    if apply_family:
        canonical_units = {a.key: a.canonical_unit for a in schema.attributes}
        for members in group_families(out).values():
            for product in members:
                apply_family_checks(product, family_checks(product, members,
                                                           canonical_units))

    # Stage 5 runs again now that Level 3 checks exist and the calibrator is fitted
    for product in out:
        if product.source_status == "verified":
            score_and_decide(product, calibrator, calibrated)
            if pipeline.options.commerce_output:
                pipeline._build_commerce(product, schema)

    return BatchResult(records=out, by_mpn={r.mpn: r for r in out}, elapsed=elapsed)


def outcomes_for(batch: BatchResult, records: list[dict], schema) -> list[Outcome]:
    rows: list[Outcome] = []
    for record in records:
        product = batch.by_mpn.get(record["sku_input"]["mpn"])
        if product is None:
            continue
        rows.extend(metric_mod.evaluate_record(product, label_mod.labels_of(record),
                                               record["cohort"], schema))
    return rows


def calibration_rows(batch: BatchResult, records: list[dict], schema):
    """(group, features, correct) for every value that has a label.

    The group carries the category as well as the criticality tier, so one
    fitted model does not average two categories together.
    """
    from sourced.confidence.calibrate import group_key

    rows = []
    for record in records:
        product = batch.by_mpn.get(record["sku_input"]["mpn"])
        if product is None or product.source_status != "verified":
            continue
        labels = label_mod.labels_of(record)
        for key, attr in product.attributes.items():
            if attr.winning_candidate is None or key not in labels:
                continue
            from sourced.ingest.normalize import values_match

            correct = values_match(attr.value, attr.unit, labels[key]["value"],
                                   labels[key].get("unit"))
            rows.append((group_key(attr.criticality, schema.category),
                         attr.features, correct))
    return rows


def metrics_for(batch: BatchResult, records: list[dict], schema) -> dict:
    rows = outcomes_for(batch, records, schema)
    cohorts = {r["sku_input"]["mpn"]: r["cohort"] for r in records}
    inputs = {r["sku_input"]["mpn"]: r["sku_input"] for r in records}
    return {
        "structured_data_generation": {
            **metric_mod.source_location_metrics(batch.records, cohorts),
            **metric_mod.coverage_metrics(batch.records, schema, inputs),
        },
        "accuracy": metric_mod.accuracy_metrics(rows),
        "consistency": metric_mod.consistency_metrics(batch.records, schema),
        "validation_and_enrichment": metric_mod.validation_metrics(rows, batch.records),
        "scalable_engine": metric_mod.engine_metrics(batch.records, batch.elapsed),
    }


def calibration_report(batch: BatchResult, records: list[dict], schema) -> dict:
    """Reliability and abstention curves on the held-out split."""
    predictions: list[tuple[float, bool]] = []
    by_tier: dict[str, list[tuple[float, bool]]] = {}
    for record in records:
        product = batch.by_mpn.get(record["sku_input"]["mpn"])
        if product is None:
            continue
        labels = label_mod.labels_of(record)
        for key, attr in product.attributes.items():
            if attr.winning_candidate is None or key not in labels:
                continue
            from sourced.ingest.normalize import values_match

            correct = values_match(attr.value, attr.unit, labels[key]["value"],
                                   labels[key].get("unit"))
            predictions.append((attr.confidence, correct))
            by_tier.setdefault(attr.criticality, []).append((attr.confidence, correct))

    rows = reliability(predictions)
    return {
        "n": len(predictions),
        "reliability": rows,
        "expected_calibration_error": expected_calibration_error(rows, len(predictions)),
        "ece_by_criticality": {
            tier: expected_calibration_error(reliability(items), len(items))
            for tier, items in by_tier.items()},
        "abstention_curve": {tier: abstention_curve(items)
                             for tier, items in by_tier.items()},
        "_predictions": predictions,
        "_by_tier": by_tier,
    }


# ------------------------------------------------------------------ ablations

ABLATIONS = {
    "full_system": {},
    "no_source_verification": {"verify_sources": False},
    "no_span_containment": {"span_containment": False},
    "no_relational_validation_l2": {"relational_validation": False},
    "no_family_coherence_l3": {"family_validation": False},
    "no_rules_tier": {"rules_tier": False},
    "no_tables_tier": {"tables_tier": False},
    "uncalibrated_confidence": {"calibrated_confidence": False},
    "with_mpn_decoding": {"mpn_decoding": True},
}


def run_ablations(index: SourceIndex, test_records: list[dict], schema,
                  calibrator: Calibrator, decoder: MpnDecoder | None,
                  use_llm: bool = False) -> dict:
    """Every variant inherits the run's LLM setting.

    Building `Options(**overrides)` here silently took the dataclass default,
    which is `llm_tier=True`. With a provider configured that turned an
    eighteen-batch deterministic sweep into thousands of model calls, and the
    run simply stopped making progress.
    """
    results = {}
    for name, overrides in ABLATIONS.items():
        options = Options(llm_tier=use_llm, **overrides)
        pipeline = Pipeline(index, options, calibrator=calibrator, decoder=decoder)
        batch = run_batch(pipeline, test_records, schema,
                          apply_family=options.family_validation,
                          calibrator=calibrator,
                          calibrated=options.calibrated_confidence)
        m = metrics_for(batch, test_records, schema)
        cal = calibration_report(batch, test_records, schema)
        results[name] = {
            "precision_published": m["accuracy"]["precision_published"],
            "auto_publish_rate": m["validation_and_enrichment"]["auto_publish_rate"],
            "attribute_coverage": m["structured_data_generation"]["attribute_coverage"],
            "wrong_part_leak_rate": m["structured_data_generation"]["wrong_part_leak_rate"],
            "family_flags_per_1000": m["consistency"]["family_coherence_flags_per_1000"],
            "expected_calibration_error": cal["expected_calibration_error"],
        }
    return results


# -------------------------------------------------------- adversarial testing


def injected_fabrication_test(index: SourceIndex, records: list[dict], schema,
                              calibrator: Calibrator, n: int = 60) -> dict:
    """Corrupt known-correct values with plausible-but-wrong ones, then measure
    what fraction the validation stack intercepts and at which level."""
    from sourced.ingest.normalize import values_match

    pipeline = Pipeline(index, Options(llm_tier=False), calibrator=calibrator)
    subset = [r for r in records if r["cohort"] in ("normal", "contradicted")][:n]
    batch = run_batch(pipeline, subset, schema, calibrator=calibrator)

    corruptions = [
        ("sibling_value", _corrupt_sibling),
        ("unit_swap", _corrupt_unit),
        ("adjacent_row", _corrupt_adjacent),
        ("out_of_range", _corrupt_range),
    ]

    levels = ("attribute", "relational", "family", "threshold")
    caught_by_level = {level: 0 for level in levels}
    total = intercepted = 0

    all_products = batch.records
    canonical_units = {a.key: a.canonical_unit for a in schema.attributes}
    for name, corrupt in corruptions:
        for record in subset:
            product = batch.by_mpn.get(record["sku_input"]["mpn"])
            if product is None or product.source_status != "verified":
                continue
            published = [k for k, a in product.attributes.items()
                         if a.resolution == "published" and a.value is not None]
            if not published:
                continue
            key = published[hash(name + record["sku_input"]["mpn"]) % len(published)]
            probe = copy.deepcopy(product)
            attr = probe.attributes[key]
            original = attr.value
            corrupt(attr, schema, all_products, key)
            if values_match(attr.value, attr.unit, original, attr.unit):
                continue
            total += 1

            # every level is asked independently: a first-match-wins attribution
            # credits Level 1 for everything and makes Levels 2 and 3 look inert
            caught = _revalidate(probe, key, schema, all_products, canonical_units,
                                 calibrator)
            if caught:
                intercepted += 1
                for level in caught:
                    caught_by_level[level] += 1

    return {
        "corruptions_injected": total,
        "intercepted": intercepted,
        "interception_rate": round(intercepted / total, 4) if total else None,
        "caught_by_level": caught_by_level,
        "caught_by_level_rate": {level: (round(count / total, 4) if total else None)
                                 for level, count in caught_by_level.items()},
    }


def _revalidate(product: ProductRecord, key: str, schema, family: list[ProductRecord],
                canonical_units, calibrator) -> list[str]:
    """Which validation levels catch this corruption, each asked independently."""
    from sourced.validate.attribute import validate_attribute
    from sourced.validate.relational import validate_relational

    attr = product.attributes[key]
    candidate = attr.winning_candidate
    spec = schema.spec(key)
    if candidate is None or spec is None:
        return []

    caught: list[str] = []

    # Level 1: type, enum, unit, range, and whether the value is still readable
    # out of the span it claims to come from
    candidate = candidate.model_copy(update={"value": attr.value, "unit": attr.unit})
    checks = validate_attribute(candidate, spec, {})
    if not (checks["type_conformant"].passed and checks["enum_valid"].passed
            and checks["unit_parsed"].passed and checks["range_plausible"].passed):
        caught.append("attribute")
    elif not _value_supported_by_span(attr, spec):
        caught.append("attribute")

    relational = validate_relational(product.attributes, schema)
    if any(not c.passed for c in relational.get(key, {}).values()):
        caught.append("relational")

    peers = [p for p in family if p.id != product.id]
    fam = family_checks(product, peers, canonical_units)
    if any(not c.passed for c in fam.get(key, {}).values()):
        caught.append("family")

    probe = copy.deepcopy(product)
    for other in probe.attributes.values():          # Stage 5 sees the fresh checks
        other.checks = {n: c for n, c in other.checks.items() if c.level != "family"}
    probe.attributes[key].checks.update(fam.get(key, {}))
    probe.attributes[key].checks.update(relational.get(key, {}))
    score_and_decide(probe, calibrator, True)
    if probe.attributes[key].resolution != "published":
        caught.append("threshold")
    return caught


def _value_supported_by_span(attr, spec) -> bool:
    """The corrupted value must still be readable out of the cited span. This is
    the span check doing its real job: it binds the value to the text."""
    from sourced.candidates.tables import coerce

    candidate = attr.winning_candidate
    if candidate is None:
        return False
    parsed, _ = coerce(candidate.raw_text, spec)
    if parsed is None:
        return False
    from sourced.ingest.normalize import values_match

    return values_match(attr.value, attr.unit, parsed, spec.canonical_unit)


def _corrupt_sibling(attr, schema, products, key):
    for other in products:
        peer = other.attributes.get(key)
        if peer and peer.value is not None and peer.value != attr.value:
            attr.value = peer.value
            return


def _corrupt_unit(attr, schema, products, key):
    if isinstance(attr.value, (int, float)) and not isinstance(attr.value, bool):
        attr.value = float(attr.value) * 1000.0


def _corrupt_adjacent(attr, schema, products, key):
    spec = schema.spec(key)
    if spec and spec.type == "enum" and spec.values:
        for value in spec.values:
            if value != attr.value:
                attr.value = value
                return
    _corrupt_sibling(attr, schema, products, key)


def _corrupt_range(attr, schema, products, key):
    spec = schema.spec(key)
    if spec and spec.plausible_range and isinstance(attr.value, (int, float)):
        attr.value = float(spec.plausible_range[1]) * 10
    else:
        _corrupt_adjacent(attr, schema, products, key)


def degraded_input_test(index: SourceIndex, records: list[dict], schema,
                        calibrator: Calibrator, n: int = 40) -> dict:
    """Truncated descriptions, missing manufacturer, malformed MPN. Confidence
    must fall rather than stay high."""
    from sourced.models import SkuInput

    pipeline = Pipeline(index, Options(llm_tier=False), calibrator=calibrator)
    subset = [r for r in records if r["cohort"] == "normal"][:n]

    variants = {
        "baseline": lambda s: s,
        "no_manufacturer": lambda s: s.model_copy(update={"manufacturer": None}),
        "no_description": lambda s: s.model_copy(update={"description_fragment": None}),
        "malformed_mpn": lambda s: s.model_copy(
            update={"mpn": s.mpn.lower().replace("-", " ")}),
        "truncated_mpn": lambda s: s.model_copy(update={"mpn": s.mpn[:-2]}),
    }

    out = {}
    for name, mutate in variants.items():
        located = published = 0
        confidences: list[float] = []
        for record in subset:
            sku: SkuInput = mutate(label_mod.sku_of(record))
            product = pipeline.run(sku, schema)
            if product.source_status == "verified":
                located += 1
                score_and_decide(product, calibrator, True)
                for attr in product.attributes.values():
                    if attr.winning_candidate is not None:
                        confidences.append(attr.confidence)
                        if attr.resolution == "published":
                            published += 1
        out[name] = {
            "source_location_rate": round(located / len(subset), 4) if subset else None,
            "published_values": published,
            "mean_confidence": (round(sum(confidences) / len(confidences), 4)
                                if confidences else None),
        }
    return out


def reverification_test(index: SourceIndex, records: list[dict], schema,
                        calibrator: Calibrator) -> dict:
    """Cost of processing a source revision versus a full re-run (doc 04)."""
    from sourced.store import upsert as upsert_mod
    from sourced.store.changes import on_source_updated, sku_lookup_from
    from sourced.store.models import create_all

    create_all()
    pipeline = Pipeline(index, Options(llm_tier=False), calibrator=calibrator)
    subset = records[:80]
    batch = run_batch(pipeline, subset, schema, calibrator=calibrator)
    for product in batch.records:
        upsert_mod.upsert_product(product)

    revised = next((s for s in _datasheet_ids(batch.records)), None)
    if revised is None:
        return {"note": "no datasheet-backed product in the sample"}

    report = on_source_updated(revised, "sha256:revised", pipeline,
                               sku_lookup_from(subset))
    full_run_attributes = sum(len(p.attributes) for p in batch.records)
    return {
        "revised_source": revised,
        "products_linked": report.products_linked,
        "products_reprocessed": report.products_reprocessed,
        "attributes_examined": report.attributes_examined,
        "attributes_changed": report.attributes_changed,
        "catalogue_attributes_in_a_full_run": full_run_attributes,
        "reverification_cost_vs_full_run": (
            round(report.attributes_examined / full_run_attributes, 4)
            if full_run_attributes else None),
    }


def _datasheet_ids(products: list[ProductRecord]) -> list[str]:
    seen = []
    for product in products:
        for link in product.sources:
            if link.source_type == "manufacturer_datasheet" and link.source_id not in seen:
                seen.append(link.source_id)
    return seen


# ------------------------------------------------------------------ entrypoint


def classification_routing(records: list[dict], taxonomy) -> dict:
    """Does Stage 1 route each SKU to the right attribute schema?

    Only measurable with more than one populated category, which is the point
    of carrying a second. A wrong route is not a small error: the record is
    then extracted against an attribute set that does not describe the part.
    """
    from sourced.taxonomy.classify import classify, schema_for

    total = correct = assigned = invented = 0
    confusion: dict[str, dict[str, int]] = {}
    codes = taxonomy.codes
    for record in records:
        sku = label_mod.sku_of(record)
        assignment = classify(sku, index=taxonomy)
        if assignment is not None:
            assigned += 1
            if assignment.code not in codes:
                invented += 1
        chosen = schema_for(assignment, taxonomy, sku=sku)
        expected = record["category"]
        total += 1
        correct += int(chosen == expected)
        confusion.setdefault(expected, {})
        confusion[expected][str(chosen)] = confusion[expected].get(str(chosen), 0) + 1
    return {
        "records": total,
        "schema_routing_accuracy": round(correct / total, 4) if total else None,
        "records_with_a_category": assigned,
        "invented_codes": invented,
        "confusion": confusion,
    }


def aggregate_metrics(rows: list[Outcome], products: list[ProductRecord],
                      cohorts: dict[str, str], elapsed: float) -> dict:
    """The category-independent half of the report, over every category at once.

    Coverage and consistency are defined against a category's attribute set, so
    they stay per-category; accuracy, abstention and throughput are not.
    """
    return {
        "source_location": metric_mod.source_location_metrics(products, cohorts),
        "accuracy": metric_mod.accuracy_metrics(rows),
        "validation_and_enrichment": metric_mod.validation_metrics(rows, products),
        "scalable_engine": metric_mod.engine_metrics(products, elapsed),
    }


def main(quick: bool = False, out_path=None, use_llm: bool = False) -> dict:
    corpus = label_mod.load_corpus_records()
    index = SourceIndex(load_corpus())
    taxonomy = default_index()

    categories = sorted({r["category"] for r in corpus})
    schemas = {c: load_schema(c) for c in categories}

    # The reported run is deterministic unless the LLM tier is asked for. It
    # is a separate, slower and non-reproducible expense, so it is opt-in and
    # the mode is recorded in the report rather than inferred from a key.
    def opts(**overrides) -> Options:
        return Options(llm_tier=use_llm, **overrides)

    def subset(split_name: str, category: str) -> list[dict]:
        return [r for r in label_mod.split(corpus, split_name)
                if r["category"] == category]

    dev = label_mod.split(corpus, "dev")
    calibration = label_mod.split(corpus, "calibration")
    test = label_mod.split(corpus, "test")

    # --- the MPN decoder is learned on dev only, never on test
    observations = []
    for category in categories:
        dev_batch = run_batch(Pipeline(index, opts()), subset("dev", category),
                              schemas[category], calibrator=None, calibrated=False)
        observations += [
            (p.manufacturer_resolved, p.mpn,
             {k: a.value for k, a in p.attributes.items() if a.value is not None})
            for p in dev_batch.records]
    decoder = MpnDecoder().fit(observations)

    # --- one calibration model, fitted on the calibration split of every
    #     category at once: the claim is one calibrated system rather than one
    #     per vertical, and the features are category-independent by design
    cal_rows = []
    for category in categories:
        records = subset("calibration", category)
        cal_batch = run_batch(Pipeline(index, opts(), decoder=decoder), records,
                              schemas[category], calibrator=None, calibrated=False)
        cal_rows += calibration_rows(cal_batch, records, schemas[category])
    calibrator = Calibrator().fit(cal_rows)
    calibrator.save()

    report = {
        "dataset": {
            "corpus_summary": json.loads((config.DATA / "corpus_summary.json")
                                         .read_text(encoding="utf-8")),
            "labels": label_mod.label_noise_report(corpus),
            "splits": {"dev": len(dev), "calibration": len(calibration),
                       "test": len(test)},
            "categories": categories,
            "attributes_per_category": {c: len(schemas[c].attributes)
                                        for c in categories},
            "taxonomy_leaves": len(taxonomy.leaves),
            "llm_tier_used_in_this_run": use_llm,
            "llm_provider_configured": config.LLM_ENABLED,
            "llm_provider": config.LLM_PROVIDER or None,
            "llm_model": config.LLM_MODEL if config.LLM_ENABLED else None,
        },
        "calibration_fit": {
            "rows_by_tier": calibrator.n_rows,
            "base_rates": {k: round(v, 4) for k, v in calibrator.base_rates.items()},
            "fitted_tiers": sorted(calibrator.models),
            "coefficients": {tier: calibrator.coefficients(tier)
                             for tier in sorted(calibrator.models)},
        },
    }

    if quick:
        report["dev_metrics"] = {}
        for category in categories:
            records = subset("dev", category)
            batch = run_batch(Pipeline(index, opts(), calibrator=calibrator,
                                       decoder=decoder),
                              records, schemas[category], calibrator=calibrator)
            report["dev_metrics"][category] = metrics_for(batch, records,
                                                          schemas[category])
        return report

    # --- held-out test split, touched exactly once
    pipeline = Pipeline(index, opts(), calibrator=calibrator, decoder=decoder)
    per_category: dict[str, dict] = {}
    all_rows: list[Outcome] = []
    all_products: list[ProductRecord] = []
    all_predictions: list[tuple[float, bool]] = []
    by_tier: dict[str, list[tuple[float, bool]]] = {}
    elapsed = 0.0

    for category in categories:
        records = subset("test", category)
        batch = run_batch(pipeline, records, schemas[category], calibrator=calibrator)
        elapsed += batch.elapsed
        cal = calibration_report(batch, records, schemas[category])
        predictions = cal.pop("_predictions")
        tiers = cal.pop("_by_tier")
        per_category[category] = {
            "metrics": metrics_for(batch, records, schemas[category]),
            "calibration": cal,
        }
        all_rows += outcomes_for(batch, records, schemas[category])
        all_products += batch.records
        all_predictions += predictions
        for tier, items in tiers.items():
            by_tier.setdefault(tier, []).extend(items)

    cohorts = {r["sku_input"]["mpn"]: r["cohort"] for r in test}
    report["by_category"] = per_category
    report["test_metrics"] = aggregate_metrics(all_rows, all_products, cohorts, elapsed)

    rows = reliability(all_predictions)
    report["calibration"] = {
        "n": len(all_predictions),
        "reliability": rows,
        "expected_calibration_error": expected_calibration_error(
            rows, len(all_predictions)),
        "ece_by_criticality": {
            tier: expected_calibration_error(reliability(items), len(items))
            for tier, items in by_tier.items()},
        "abstention_curve": {tier: abstention_curve(items)
                             for tier, items in by_tier.items()},
    }

    from sourced.confidence.calibrate import (write_abstention_figure,
                                              write_reliability_figure)
    write_reliability_figure(rows, config.FIG_DIR / "reliability.png",
                             "Reliability - held-out test split, both categories")
    write_abstention_figure(report["calibration"]["abstention_curve"],
                            config.FIG_DIR / "abstention.png")

    report["category_routing"] = classification_routing(test, taxonomy)

    # The ablation suite runs per category. Doc 05 claims the deterministic
    # rules tier does real work in the abbreviation-soup vertical and less in
    # the electronics one; running `no_rules_tier` against both is what turns
    # that from a claim into a number.
    report["ablations_by_category"] = {
        category: run_ablations(index, subset("test", category), schemas[category],
                                calibrator, decoder, use_llm=use_llm)
        for category in categories}

    primary = max(categories, key=lambda c: len(subset("test", c)))
    primary_records = subset("test", primary)
    report["ablation_category"] = primary
    report["ablations"] = report["ablations_by_category"][primary]
    report["adversarial"] = {
        "injected_fabrication": injected_fabrication_test(
            index, primary_records, schemas[primary], calibrator),
        "degraded_input": degraded_input_test(index, primary_records,
                                              schemas[primary], calibrator),
    }
    report["catalog_operations"] = reverification_test(
        index, [r for r in corpus if r["category"] == primary], schemas[primary],
        calibrator)

    out_path = out_path or (config.DATA / "results.json")
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--llm", action="store_true",
                        help="enable the LLM extraction tier (slow, costs tokens)")
    args = parser.parse_args()
    result = main(quick=args.quick, use_llm=args.llm)
    print(json.dumps(result.get("test_metrics") or result.get("dev_metrics"),
                     indent=2, default=str))
    if "category_routing" in result:
        print(json.dumps(result["category_routing"], indent=2, default=str))
