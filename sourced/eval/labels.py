"""Label handling and split discipline (doc 04, doc 05 Leakage discipline).

The corpus record's `labels` block is what the system is scored against and
must never reach it. Loading here is deliberately one-directional: nothing in
`sourced/` outside `sourced/eval/` imports this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from sourced import config
from sourced.models import SkuInput

SPLITS = ("dev", "calibration", "test")


def load_corpus_records(path: Path | None = None) -> list[dict]:
    path = Path(path or config.DATA / "corpus.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def split(records: list[dict], name: str) -> list[dict]:
    if name == "all":
        return records
    if name not in SPLITS:
        raise ValueError(f"unknown split {name!r}; expected one of {SPLITS}")
    return [r for r in records if r.get("split") == name]


def sku_of(record: dict) -> SkuInput:
    return SkuInput(**record["sku_input"])


def labels_of(record: dict) -> dict[str, dict]:
    return record.get("labels", {})


def label_noise_report(records: list[dict]) -> dict:
    """Doc 04 requires the measured label-noise rate to be reported rather than
    assumed. For a generated corpus the labels *are* the construction, so the
    rate is zero by construction — which is itself a limitation to state, not a
    result to claim."""
    audited = [r for r in records if r.get("hand_audited")]
    return {
        "label_source": records[0].get("label_provenance") if records else None,
        "audited_subset": len(audited),
        "measured_noise_rate": 0.0 if not audited else round(
            sum(1 for r in audited if r.get("audit_verdict") == "incorrect")
            / len(audited), 4),
        "noise_is_by_construction": all(
            r.get("label_provenance") == "synthetic_generator_ground_truth"
            for r in records),
    }
