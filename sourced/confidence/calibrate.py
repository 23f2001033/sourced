"""Confidence calibration (doc 03 5, doc 04 Calibration measurement).

Confidence is a fitted function of observable signals, not a self-report. It is
fitted on the calibration split only, and reported with a reliability diagram
and an Expected Calibration Error, because a threshold on an uncalibrated score
is a threshold on noise.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

from sourced import config
from sourced.confidence.features import FEATURE_NAMES, vector

MODEL_PATH = config.DATA / "calibration.pkl"
# A prior used when a criticality tier has too few labelled examples to fit.
# Deliberately conservative: an unfitted tier should not publish freely.
FALLBACK_PRIOR = 0.5
MIN_FIT_ROWS = 30


def group_key(criticality: str, category: str | None = None) -> str:
    """The population a confidence prediction belongs to.

    Doc 03 fits per criticality tier. With more than one category in the
    catalogue that is under-specified: a `safety` attribute read from a
    connector datasheet and one read from a fitting catalogue have different
    evidence profiles and different error rates, so a single model fitted
    across both is mis-specified and drags the better category down. The tier
    remains the policy boundary; the category is what makes the fit honest.
    """
    return f"{category}|{criticality}" if category else criticality


class Calibrator:
    """One logistic model per (category, criticality tier).

    Falls back to the tier alone, then to the observed base rate, so a category
    with too few labelled rows to fit still gets a defensible number instead of
    a fabricated one.
    """

    def __init__(self) -> None:
        self.models: dict[str, LogisticRegression] = {}
        self.base_rates: dict[str, float] = {}
        self.n_rows: dict[str, int] = {}

    def fit(self, rows: list[tuple[str, dict[str, float], bool]]) -> "Calibrator":
        """rows: (group, features, correct), group from `group_key`.

        Each row is also fitted into its bare criticality tier, so the fallback
        path has a real model behind it rather than only a base rate.
        """
        by_tier: dict[str, list[tuple[dict[str, float], bool]]] = {}
        for group, feats, correct in rows:
            by_tier.setdefault(group, []).append((feats, correct))
            if "|" in group:
                by_tier.setdefault(group.split("|", 1)[1], []).append((feats, correct))

        for tier, items in by_tier.items():
            y = np.array([1 if c else 0 for _, c in items])
            self.base_rates[tier] = float(y.mean()) if len(y) else FALLBACK_PRIOR
            self.n_rows[tier] = len(items)
            if len(items) < MIN_FIT_ROWS or len(set(y.tolist())) < 2:
                continue                        # degenerate: fall back to base rate
            X = np.array([vector(f) for f, _ in items])
            model = LogisticRegression(max_iter=2000, C=1.0, class_weight=None)
            model.fit(X, y)
            self.models[tier] = model
        return self

    def predict(self, criticality: str, feature_map: dict[str, float],
                category: str | None = None) -> float:
        for key in (group_key(criticality, category), criticality):
            model = self.models.get(key)
            if model is not None:
                return float(model.predict_proba(
                    np.array([vector(feature_map)]))[0][1])
        for key in (group_key(criticality, category), criticality):
            if key in self.base_rates:
                return float(self.base_rates[key])
        return FALLBACK_PRIOR

    def coefficients(self, criticality: str) -> dict[str, float]:
        model = self.models.get(criticality)
        if model is None:
            return {}
        return {name: round(float(c), 4)
                for name, c in zip(FEATURE_NAMES, model.coef_[0])}

    def save(self, path: Path | None = None) -> Path:
        path = Path(path or MODEL_PATH)
        path.write_bytes(pickle.dumps(self))
        return path

    @staticmethod
    def load(path: Path | None = None) -> "Calibrator | None":
        path = Path(path or MODEL_PATH)
        if not path.exists():
            return None
        try:
            return pickle.loads(path.read_bytes())
        except Exception:
            return None

    @property
    def fitted(self) -> bool:
        return bool(self.models)


# ------------------------------------------------------------------ reporting


def reliability(predictions: list[tuple[float, bool]], bins: int = 10) -> list[dict]:
    """Bucket predictions by predicted confidence, compare against observed
    accuracy. If it tracks the diagonal, the abstention threshold means
    something."""
    rows = []
    edges = [(i / bins, (i + 1) / bins) for i in range(bins)]
    for lo, hi in edges:
        subset = [(c, ok) for c, ok in predictions
                  if (lo <= c < hi) or (hi == 1.0 and c == 1.0)]
        if not subset:
            continue
        rows.append({
            "bin": f"{lo:.1f}-{hi:.1f}",
            "n": len(subset),
            "mean_confidence": round(sum(c for c, _ in subset) / len(subset), 4),
            "observed_accuracy": round(sum(1 for _, ok in subset if ok) / len(subset), 4),
        })
    return rows


def expected_calibration_error(rows: list[dict], total: int) -> float:
    if not total:
        return 0.0
    return round(sum(r["n"] / total * abs(r["mean_confidence"] - r["observed_accuracy"])
                     for r in rows), 4)


def abstention_curve(predictions: list[tuple[float, bool]],
                     steps: int = 21) -> list[dict]:
    """Sweep the publish threshold; plot precision against coverage. This is the
    artefact that answers 'how much can I publish unreviewed, and at what cost
    in coverage'."""
    total = len(predictions)
    out = []
    for i in range(steps):
        threshold = i / (steps - 1)
        kept = [ok for c, ok in predictions if c >= threshold]
        out.append({
            "threshold": round(threshold, 3),
            "coverage": round(len(kept) / total, 4) if total else 0.0,
            "precision": round(sum(1 for ok in kept if ok) / len(kept), 4) if kept else None,
            "n": len(kept),
        })
    return out


def write_reliability_figure(rows: list[dict], path: Path, title: str) -> Path | None:
    """Reliability on top, bin counts below.

    A reliability curve alone is misleading when the predictions concentrate:
    a bin holding one prediction plots as prominently as a bin holding fourteen
    hundred. The count panel is what stops the reader over-reading the tail.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax, counts) = plt.subplots(
        2, 1, figsize=(4.8, 5.4), dpi=140, sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08})

    ax.plot([0, 1], [0, 1], "--", color="#999", linewidth=1, label="perfect calibration")
    if rows:
        total = sum(r["n"] for r in rows) or 1
        x = [r["mean_confidence"] for r in rows]
        y = [r["observed_accuracy"] for r in rows]
        sizes = [18 + 220 * (r["n"] / total) for r in rows]
        ax.plot(x, y, "-", color="#1f5fb4", linewidth=1.4, zorder=2)
        ax.scatter(x, y, s=sizes, color="#1f5fb4", zorder=3,
                   label="observed (area = share of predictions)")
        for r in rows:
            # labels near the right edge flip to the left so they stay on canvas
            near_edge = r["mean_confidence"] > 0.9
            ax.annotate(f"n={r['n']}", (r["mean_confidence"], r["observed_accuracy"]),
                        textcoords="offset points",
                        xytext=(-34, -14) if near_edge else (7, -10),
                        fontsize=6.5, color="#555")
    ax.set_ylabel("observed accuracy")
    ax.set_title(title, fontsize=9)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.legend(fontsize=6.5, loc="upper left")

    if rows:
        counts.bar([r["mean_confidence"] for r in rows], [r["n"] for r in rows],
                   width=0.055, color="#8fabd4", edgecolor="#1f5fb4", linewidth=0.6)
        counts.set_yscale("log")
    counts.set_xlabel("predicted confidence")
    counts.set_ylabel("predictions", fontsize=8)
    counts.set_xlim(0, 1)
    counts.grid(alpha=0.25, linewidth=0.5, axis="y")
    counts.tick_params(labelsize=7)

    # the shared-x subplots are laid out explicitly; tight_layout cannot handle
    # the fixed hspace and warns
    fig.subplots_adjust(left=0.15, right=0.97, top=0.93, bottom=0.09, hspace=0.08)
    fig.savefig(path)
    plt.close(fig)
    return path


def write_abstention_figure(curves: dict[str, list[dict]], path: Path) -> Path | None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.2, 4.2), dpi=140)
    colours = {"safety": "#b4331f", "functional": "#1f5fb4", "cosmetic": "#3f8f3f"}
    for tier, rows in curves.items():
        points = [(r["coverage"], r["precision"]) for r in rows if r["precision"] is not None]
        if not points:
            continue
        ax.plot([p[0] for p in points], [p[1] for p in points], "o-", markersize=3,
                linewidth=1.4, label=tier, color=colours.get(tier))
        threshold = config.PUBLISH_THRESHOLDS.get(tier)
        if threshold is not None:
            at = [r for r in rows if abs(r["threshold"] - threshold) < 0.03
                  and r["precision"] is not None]
            if at:
                ax.scatter([at[0]["coverage"]], [at[0]["precision"]], s=60,
                           facecolors="none", edgecolors=colours.get(tier), linewidths=1.4)
    ax.set_xlabel("coverage (share of values published)")
    ax.set_ylabel("precision of published values")
    ax.set_title("Abstention curve by criticality tier\n"
                 "(ring marks the configured publish threshold)", fontsize=9)
    ax.grid(alpha=0.25, linewidth=0.5)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def dump_json(obj, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return path
