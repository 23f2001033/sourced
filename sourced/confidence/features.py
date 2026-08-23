"""Confidence features (doc 03 5, ADR-005).

Every feature is observable. None of them is the model's opinion of itself:
self-reported LLM confidence produces numbers that look meaningful and do not
track correctness, so an abstention policy built on them is built on noise.
"""
from __future__ import annotations

from sourced.models import AttributeValue

FEATURE_NAMES = [
    "tier_rule", "tier_table", "tier_llm",
    "locator_table_cell", "locator_structured_field", "locator_inferred",
    "span_present", "unit_parsed", "enum_valid", "range_plausible",
    "relational_ok", "family_coherent",
    "agreement_count", "source_authority", "self_consistency",
    "match_confidence", "degraded_path", "close_call",
]


def _passed(attr: AttributeValue, name: str, default: bool = True) -> float:
    check = attr.checks.get(name)
    return float(default if check is None else check.passed)


def _all_of_level(attr: AttributeValue, level: str, default: bool = True) -> float:
    checks = [c for c in attr.checks.values() if c.level == level]
    return float(default if not checks else all(c.passed for c in checks))


def features(attr: AttributeValue, match_confidence: float = 0.0,
             degraded_path: bool = False, close_call: bool = False) -> dict[str, float]:
    candidate = attr.winning_candidate
    tier = candidate.tier if candidate else ""
    locator = candidate.evidence.locator if candidate else ""
    authority = candidate.evidence.authority_rank if candidate else 9
    consistency = candidate.consistency_ratio if candidate else 1.0

    return {
        "tier_rule": float(tier == "rule"),
        "tier_table": float(tier == "table"),
        "tier_llm": float(tier == "llm"),
        "locator_table_cell": float(locator == "table_cell"),
        "locator_structured_field": float(locator == "structured_field"),
        "locator_inferred": float(locator == "inferred"),
        "span_present": _passed(attr, "span_present"),
        "unit_parsed": _passed(attr, "unit_parsed"),
        "enum_valid": _passed(attr, "enum_valid"),
        "range_plausible": _passed(attr, "range_plausible"),
        "relational_ok": _all_of_level(attr, "relational"),
        "family_coherent": _all_of_level(attr, "family"),
        "agreement_count": min(attr.agreement_count, 3) / 3,
        "source_authority": 1.0 / max(1, authority),
        "self_consistency": float(consistency),
        "match_confidence": float(match_confidence),
        "degraded_path": float(degraded_path),
        "close_call": float(close_call),
    }


def vector(feature_map: dict[str, float]) -> list[float]:
    return [float(feature_map.get(name, 0.0)) for name in FEATURE_NAMES]
