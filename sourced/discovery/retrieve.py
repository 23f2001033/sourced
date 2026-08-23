"""Candidate retrieval (doc 03 0.3, ADR-007).

Hybrid, weighted toward lexical. This is the one place in the system where
BM25 matters more than embeddings: a part number is an exact-match token that
dense retrieval handles poorly.

Deviation from doc 01: the dense side is a TF-IDF -> truncated-SVD projection
(latent semantic indexing) rather than sentence-transformers + FAISS. It is the
same retrieve-and-fuse shape with the same interface and no 2.5 GB torch
dependency; BM25 remains dominant, which is the part ADR-007 turns on. Swapping
in a sentence-transformer means replacing `_DenseIndex` alone.
"""
from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
from rank_bm25 import BM25Okapi
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from sourced.discovery.mpn import mpn_variants
from sourced.ingest.chunks import Document
from sourced.models import SkuInput

_TOKEN = re.compile(r"[A-Za-z]+|\d+")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text or "")]


class _DenseIndex:
    """TF-IDF over character n-grams, projected by SVD. Semantic-ish matching
    for description fragments; deliberately not the primary signal."""

    def __init__(self, texts: list[str], dim: int = 192):
        # float32 throughout: the SVD component matrix is the largest array the
        # process holds, and single precision is well inside the tolerance of a
        # cosine similarity used only for ranking. This run first surfaced as an
        # allocation failure on a loaded machine, which is a poor reason to be
        # carrying double precision.
        self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                          min_df=1, max_features=60000,
                                          dtype=np.float32)
        X = self.vectorizer.fit_transform(texts)
        n_comp = max(2, min(dim, X.shape[1] - 1, max(2, X.shape[0] - 1)))
        self.svd = TruncatedSVD(n_components=n_comp, random_state=0)
        self.matrix = normalize(self.svd.fit_transform(X))

    def search(self, query: str, k: int) -> list[int]:
        q = normalize(self.svd.transform(self.vectorizer.transform([query or ""])))
        scores = (self.matrix @ q.T).ravel()
        return list(np.argsort(-scores)[:k])


def rrf_fuse(rankings: list[list[str]], weights: tuple[float, ...],
             k: int, c: int = 60) -> list[str]:
    """Reciprocal Rank Fusion. Parameter-light, no score normalisation.
    c = 60 is the value from the original paper and works untuned."""
    scores: dict[str, float] = defaultdict(float)
    for ranking, w in zip(rankings, weights):
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] += w / (c + rank)
    return sorted(scores, key=lambda d: scores[d], reverse=True)[:k]


class SourceIndex:
    """Retrieval over the source corpus. Built once, queried per SKU."""

    def __init__(self, docs: dict[str, Document]):
        self.docs = docs
        self.ids = list(docs)
        texts = [self._doc_text(docs[i]) for i in self.ids]
        self.bm25 = BM25Okapi([tokenize(t) for t in texts]) if texts else None
        self.dense = _DenseIndex(texts) if texts else None

    @staticmethod
    def _doc_text(doc: Document) -> str:
        return doc.full_text or " ".join(c.text for c in doc.chunks)

    def retrieve(self, sku: SkuInput, k: int = 20) -> list[Document]:
        if not self.ids:
            return []
        query = " ".join(mpn_variants(sku.mpn))
        lexical_scores = self.bm25.get_scores(tokenize(query))
        lexical = [self.ids[i] for i in np.argsort(-lexical_scores)[:50]]

        dense_query = sku.description_fragment or sku.mpn
        dense = [self.ids[i] for i in self.dense.search(dense_query, 50)]

        fused = rrf_fuse([lexical, dense], weights=(0.7, 0.3), k=k)
        return [self.docs[i] for i in fused]
