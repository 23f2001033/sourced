"""Level 3 — cross-SKU family coherence (doc 03 4, ADR-010, ADR-011).

This is the level that serves the *consistency* outcome, needs no LLM, and
finds errors nothing else does. Median absolute deviation rather than standard
deviation, because the population being examined may itself contain the errors
being hunted.
"""
from __future__ import annotations

from statistics import median

from sourced.ingest.normalize import normalise_categorical, to_canonical
from sourced.models import AttributeValue, CheckResult, ProductRecord

MIN_SIBLINGS = 5
MAD_THRESHOLD = 5.0
COVERAGE_THRESHOLD = 0.9
UNIT_UNIFORMITY_THRESHOLD = 0.9


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def median_abs_deviation(values: list[float]) -> float:
    med = median(values)
    return median([abs(v - med) for v in values])


def family_checks(product: ProductRecord, family: list[ProductRecord],
                  canonical_units: dict[str, str | None] | None = None
                  ) -> dict[str, dict[str, CheckResult]]:
    """Returns {canonical_key: {check_name: CheckResult}}."""
    canonical_units = canonical_units or {}
    siblings = [f for f in family if f.id != product.id]
    results: dict[str, dict[str, CheckResult]] = {}
    if len(siblings) < MIN_SIBLINGS:
        return results

    for key, attr in product.attributes.items():
        if attr.value is None or attr.resolution == "abstained":
            continue
        peer_attrs = [f.attributes[key] for f in siblings
                      if key in f.attributes and f.attributes[key].value is not None
                      and f.attributes[key].resolution != "abstained"]
        if len(peer_attrs) < MIN_SIBLINGS:
            continue
        checks: dict[str, CheckResult] = {}

        if _numeric(attr.value):
            peers = [float(a.value) for a in peer_attrs if _numeric(a.value)]
            if len(peers) >= MIN_SIBLINGS:
                med = median(peers)
                mad = median_abs_deviation(peers)
                if mad > 0:
                    deviation = abs(float(attr.value) - med) / mad
                    ok = deviation <= MAD_THRESHOLD
                    checks["family_coherent"] = CheckResult(
                        passed=ok, level="family",
                        detail=None if ok else
                        (f"{attr.value} is a strong outlier against {len(peers)} "
                         f"sibling SKUs (median {med}, MAD {mad:g})"))
                elif float(attr.value) != med:
                    checks["family_coherent"] = CheckResult(
                        passed=False, level="family",
                        detail=(f"{attr.value} differs from a unanimous sibling "
                                f"value of {med} across {len(peers)} SKUs"))
        else:
            peers = [normalise_categorical(a.value) for a in peer_attrs]
            share = peers.count(normalise_categorical(attr.value)) / len(peers)
            if share == 0.0:
                checks["family_coherent"] = CheckResult(
                    passed=False, level="family",
                    detail=(f"{attr.value!r} appears on none of {len(peers)} "
                            f"sibling SKUs"))

        # unit uniformity: the same attribute expressed the same way catalogue-wide
        canonical = canonical_units.get(key)
        if canonical is not None:
            ok = attr.unit == canonical or to_canonical(
                attr.value, attr.unit, canonical) is not None
            checks["unit_uniform"] = CheckResult(
                passed=bool(ok), level="family",
                detail=None if ok else f"unit {attr.unit!r} is not the catalogue "
                                       f"convention {canonical!r}")
        if checks:
            results[key] = checks

    # coverage gap: siblings all have it, this one does not
    for key in {k for f in siblings for k in f.attributes}:
        if key in product.attributes and product.attributes[key].value is not None:
            continue
        present = sum(1 for f in siblings
                      if key in f.attributes and f.attributes[key].value is not None)
        if present / len(siblings) > COVERAGE_THRESHOLD:
            results.setdefault(key, {})["coverage_typical"] = CheckResult(
                passed=False, level="family",
                detail=(f"Present on {present}/{len(siblings)} family members "
                        f"but absent here"))
    return results


def apply_family_checks(product: ProductRecord,
                        results: dict[str, dict[str, CheckResult]]) -> None:
    for key, checks in results.items():
        attr = product.attributes.get(key)
        if attr is None:
            continue
        attr.checks.update(checks)


def group_families(products: list[ProductRecord], prefix: int = 8
                   ) -> dict[tuple[str | None, str], list[ProductRecord]]:
    from sourced.discovery.mpn import family_prefix

    groups: dict[tuple[str | None, str], list[ProductRecord]] = {}
    for p in products:
        key = (p.manufacturer_resolved, family_prefix(p.mpn_normalised, prefix))
        groups.setdefault(key, []).append(p)
    return groups


def attribute_of(product: ProductRecord, key: str) -> AttributeValue | None:
    return product.attributes.get(key)
