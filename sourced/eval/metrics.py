"""Metrics, mapped to the four stated outcomes (doc 04).

Comparison is tolerance-aware throughout: `0.5 in` equals `12.7 mm`. Canonical
magnitudes are compared through pint, never strings.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sourced.ingest.normalize import to_canonical, values_match
from sourced.models import ProductRecord
from sourced.registry import CategorySchema

EXPECT_NOT_LOCATED = {"sibling_trap"}


@dataclass
class Outcome:
    """One (product, attribute) evaluation row."""

    mpn: str
    key: str
    criticality: str
    resolution: str
    confidence: float
    correct: bool | None          # None when there is no label to compare against
    predicted: object = None
    label: object = None
    tier: str | None = None
    cohort: str = ""
    abstention_code: str | None = None
    checks_failed: list[str] = field(default_factory=list)


def evaluate_record(record: ProductRecord, labels: dict[str, dict], cohort: str,
                    schema: CategorySchema) -> list[Outcome]:
    rows: list[Outcome] = []
    for key, attr in record.attributes.items():
        label = labels.get(key)
        correct: bool | None
        if label is None:
            correct = None
        elif attr.value is None:
            correct = None
        else:
            correct = values_match(attr.value, attr.unit,
                                   label["value"], label.get("unit"))
        rows.append(Outcome(
            mpn=record.mpn, key=key, criticality=attr.criticality,
            resolution=attr.resolution, confidence=attr.confidence,
            correct=correct, predicted=attr.value, label=label and label["value"],
            tier=attr.tier, cohort=cohort,
            abstention_code=(attr.abstention_reason.code
                             if attr.abstention_reason else None),
            checks_failed=[n for n, c in attr.checks.items() if not c.passed],
        ))
    return rows


# ------------------------------------------------- outcome 1: data generation


def source_location_metrics(records: list[ProductRecord], cohorts: dict[str, str]
                            ) -> dict:
    locatable = [r for r in records if cohorts.get(r.mpn) not in EXPECT_NOT_LOCATED]
    traps = [r for r in records if cohorts.get(r.mpn) in EXPECT_NOT_LOCATED]
    located = [r for r in locatable if r.source_status == "verified"]
    trap_leaks = [r for r in traps if r.source_status == "verified"]
    return {
        "source_location_rate": _rate(len(located), len(locatable)),
        "records_with_a_locatable_source": len(locatable),
        "wrong_part_traps": len(traps),
        "wrong_part_traps_correctly_refused": len(traps) - len(trap_leaks),
        "wrong_part_leak_rate": _rate(len(trap_leaks), len(traps)),
    }


def coverage_metrics(records: list[ProductRecord], schema: CategorySchema,
                     inputs: dict[str, dict]) -> dict:
    required = set(schema.merchandising_required or schema.required_keys)
    located = [r for r in records if r.source_status == "verified"]

    filled = sum(sum(1 for k in required
                     if k in r.attributes and r.attributes[k].resolution == "published")
                 for r in located)
    total = len(located) * len(required)

    no_mfr = [r for r in located
              if not (inputs.get(r.mpn, {}) or {}).get("manufacturer")]
    filled_no_mfr = sum(sum(1 for k in required
                            if k in r.attributes
                            and r.attributes[k].resolution == "published")
                        for r in no_mfr)
    return {
        "attribute_coverage": _rate(filled, total),
        "coverage_from_fragment_alone": _rate(filled_no_mfr,
                                              len(no_mfr) * len(required)),
        "records_scored": len(located),
        "records_without_manufacturer": len(no_mfr),
    }


# ------------------------------------------- outcome 2: accuracy & consistency


def accuracy_metrics(rows: list[Outcome]) -> dict:
    published = [r for r in rows if r.resolution == "published" and r.correct is not None]
    labelled = [r for r in rows if r.label is not None]
    filled = [r for r in labelled if r.predicted is not None
              and r.resolution in ("published", "review")]

    by_tier = {}
    for tier in ("safety", "functional", "cosmetic"):
        subset = [r for r in published if r.criticality == tier]
        by_tier[tier] = {"precision": _rate(sum(1 for r in subset if r.correct),
                                            len(subset)),
                         "n": len(subset)}

    errors = Counter()
    for r in published:
        if r.correct is False:
            errors[_error_class(r)] += 1

    return {
        "precision_published": _rate(sum(1 for r in published if r.correct),
                                     len(published)),
        "published_values_scored": len(published),
        "precision_by_criticality": by_tier,
        "recall": _rate(len(filled), len(labelled)),
        "error_taxonomy": dict(errors),
    }


def _error_class(row: Outcome) -> str:
    if row.predicted is None:
        return "missing"
    if isinstance(row.predicted, (int, float)) and isinstance(row.label, (int, float)):
        try:
            ratio = abs(float(row.predicted) / float(row.label)) if row.label else 0
        except ZeroDivisionError:
            ratio = 0
        if 0.9 <= ratio <= 1.1:
            return "near_miss"
        if ratio in (1000.0, 0.001) or 20 <= ratio <= 2000 or 0.0005 <= ratio <= 0.05:
            return "wrong_unit"
        return "wrong_value"
    return "wrong_value"


def consistency_metrics(records: list[ProductRecord], schema: CategorySchema) -> dict:
    canonical = {a.key: a.canonical_unit for a in schema.attributes}
    uniform = total = 0
    coherence_flags = 0
    scored_attributes = 0
    raw_keys: dict[str, set] = {}

    for record in records:
        for key, attr in record.attributes.items():
            if attr.value is None or attr.resolution == "abstained":
                continue
            scored_attributes += 1
            unit = canonical.get(key)
            if unit is not None:
                total += 1
                if attr.unit == unit or to_canonical(attr.value, attr.unit, unit):
                    uniform += 1
            if any(not c.passed for n, c in attr.checks.items() if c.level == "family"):
                coherence_flags += 1
            if attr.winning_candidate is not None:
                raw_keys.setdefault(key, set()).add(attr.winning_candidate.raw_text[:40])

    per_family_coverage = _coverage_variance(records, schema)
    return {
        "unit_uniformity": _rate(uniform, total),
        "family_coherence_flags_per_1000": (round(coherence_flags / scored_attributes * 1000, 2)
                                            if scored_attributes else 0.0),
        "coverage_variance_within_family": per_family_coverage,
        "distinct_raw_keys_per_canonical_key": {k: len(v) for k, v in
                                                sorted(raw_keys.items())[:5]},
    }


def _coverage_variance(records: list[ProductRecord], schema: CategorySchema) -> float:
    from statistics import pstdev

    from sourced.validate.family import group_families

    required = schema.merchandising_required or schema.required_keys
    spreads = []
    for members in group_families(records).values():
        if len(members) < 3:
            continue
        ratios = [sum(1 for k in required
                      if k in m.attributes and m.attributes[k].resolution == "published")
                  / len(required) for m in members]
        spreads.append(pstdev(ratios) if len(ratios) > 1 else 0.0)
    return round(sum(spreads) / len(spreads), 4) if spreads else 0.0


# ---------------------------------------- outcome 3: validation & enrichment


def validation_metrics(rows: list[Outcome], records: list[ProductRecord]) -> dict:
    labelled = [r for r in rows if r.label is not None]
    published = [r for r in labelled if r.resolution == "published"]
    abstained = [r for r in labelled if r.resolution == "abstained"]

    false_abstentions = [r for r in abstained
                         if r.correct is True]      # value was right, withheld anyway
    escalations = [r for r in rows if r.resolution == "review"]

    caught_by_level = Counter()
    for r in rows:
        for check in r.checks_failed:
            caught_by_level[check] += 1

    return {
        "auto_publish_rate": _rate(len(published), len(labelled)),
        "abstention_rate": _rate(len(abstained), len(labelled)),
        "abstention_reasons": dict(Counter(r.abstention_code for r in abstained
                                           if r.abstention_code)),
        "false_abstention_rate": _rate(len(false_abstentions), len(abstained)),
        "escalation_load_per_sku": (round(len(escalations) / len(records), 3)
                                    if records else 0.0),
        "checks_failed_counts": dict(caught_by_level),
    }


# ---------------------------------------------- outcome 4: scalable engine


def engine_metrics(records: list[ProductRecord], elapsed: float) -> dict:
    llm_calls = sum(r.telemetry.get("llm_calls", 0) for r in records)
    from_rules = sum(r.telemetry.get("attributes_from_rules", 0) for r in records)
    from_tables = sum(r.telemetry.get("attributes_from_tables", 0) for r in records)
    from_llm = sum(r.telemetry.get("attributes_from_llm", 0) for r in records)
    resolved = from_rules + from_tables + from_llm
    return {
        "skus_processed": len(records),
        "wall_seconds": round(elapsed, 2),
        "throughput_skus_per_min": (round(len(records) / elapsed * 60, 1)
                                    if elapsed > 0 else 0.0),
        "llm_calls_total": llm_calls,
        "llm_calls_per_sku": round(llm_calls / len(records), 3) if records else 0.0,
        "llm_avoidance_rate": _rate(from_rules + from_tables, resolved),
        "attributes_by_tier": {"rule": from_rules, "table": from_tables, "llm": from_llm},
        "span_rejections": sum(r.telemetry.get("candidates_rejected_span", 0)
                               for r in records),
    }


def _rate(numerator: float, denominator: float) -> float | None:
    if not denominator:
        return None
    return round(numerator / denominator, 4)
