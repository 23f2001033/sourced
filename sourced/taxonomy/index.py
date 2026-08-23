"""Offline taxonomy leaf index (doc 03 1.1, ADR-008).

Every leaf is embedded once. Asking a model to pick from tens of thousands of
codes fails twice — the list does not fit in context, and the model emits codes
that do not exist — so retrieval narrows the choice to a handful of real codes
before the model is asked anything.

If a licensed UNSPSC or ETIM table is dropped into data/ as CSV with columns
(code, title, definition), it is loaded in preference to the internal
taxonomy. The mechanism does not change.
"""
from __future__ import annotations

import csv
import functools
import io
from pathlib import Path

import numpy as np
import yaml
from pydantic import BaseModel
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from sourced import config


class Leaf(BaseModel):
    code: str
    title: str
    definition: str = ""
    path: list[str] = []
    maps_to_category: str | None = None
    taxonomy: str = "internal"

    def as_text(self) -> str:
        trail = " > ".join(self.path)
        return f"{self.title}. {trail}. {self.definition}".strip()


def _load_csv(path: Path, taxonomy: str) -> list[Leaf]:
    rows = list(csv.DictReader(io.open(path, encoding="utf-8-sig")))
    return [Leaf(code=str(r["code"]).strip(), title=str(r.get("title", "")).strip(),
                 definition=str(r.get("definition", "")).strip(),
                 path=[p for p in str(r.get("path", "")).split(">") if p.strip()],
                 maps_to_category=(r.get("maps_to_category") or None),
                 taxonomy=taxonomy)
            for r in rows if r.get("code")]


@functools.lru_cache(maxsize=1)
def load_leaves() -> list[Leaf]:
    for name, taxonomy in (("unspsc.csv", "unspsc"), ("etim.csv", "etim")):
        path = config.DATA / name
        if path.exists():
            leaves = _load_csv(path, taxonomy)
            if leaves:
                return leaves
    raw = yaml.safe_load(io.open(Path(config.SCHEMAS) / "taxonomy_internal.yaml",
                                 encoding="utf-8"))
    return [Leaf(taxonomy=raw.get("taxonomy", "internal"), **leaf)
            for leaf in raw["leaves"]]


class TaxonomyIndex:
    """Embed leaves once, retrieve top-k at classification time."""

    def __init__(self, leaves: list[Leaf] | None = None):
        self.leaves = leaves or load_leaves()
        texts = [leaf.as_text() for leaf in self.leaves]
        self.vectorizer = TfidfVectorizer(analyzer="word", ngram_range=(1, 2),
                                          sublinear_tf=True, min_df=1,
                                          dtype=np.float32)
        X = self.vectorizer.fit_transform(texts)
        n_comp = max(2, min(160, X.shape[1] - 1, len(self.leaves) - 1))
        self.svd = TruncatedSVD(n_components=n_comp, random_state=0)
        self.matrix = normalize(self.svd.fit_transform(X))
        self.codes = {leaf.code for leaf in self.leaves}

    def search(self, text: str, k: int = 20) -> list[tuple[Leaf, float]]:
        q = normalize(self.svd.transform(self.vectorizer.transform([text or ""])))
        scores = (self.matrix @ q.T).ravel()
        order = np.argsort(-scores)[:k]
        return [(self.leaves[i], float(scores[i])) for i in order]

    def by_code(self, code: str) -> Leaf | None:
        for leaf in self.leaves:
            if leaf.code == code:
                return leaf
        return None


@functools.lru_cache(maxsize=1)
def default_index() -> TaxonomyIndex:
    return TaxonomyIndex()
