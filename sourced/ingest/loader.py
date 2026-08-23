"""Load the whole source corpus into memory once, with an on-disk cache.

Parsing 80 PDFs takes a few seconds; the pipeline is run many times across
ablations, so the parsed form is cached and keyed by content hash.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

from sourced import config
from sourced.ingest.chunks import Document
from sourced.ingest.pages import load_page
from sourced.ingest.pdf import load_pdf

CACHE = config.DATA / "corpus_docs.pkl"


def _read_source_index(data: Path) -> list[dict]:
    path = data / "sources.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_corpus(data_dir: Path | None = None, use_cache: bool = True) -> dict[str, Document]:
    data = Path(data_dir or config.DATA)
    index = _read_source_index(data)
    cache = data / CACHE.name
    if use_cache and cache.exists():
        try:
            cached = pickle.loads(cache.read_bytes())
            if set(cached) == {s["source_id"] for s in index}:
                return cached
        except Exception:
            pass

    docs: dict[str, Document] = {}
    for entry in index:
        uri = config.resolve_uri(entry["uri"])
        if not uri.exists():
            continue
        if uri.suffix.lower() == ".pdf":
            docs[entry["source_id"]] = load_pdf(uri, entry["source_id"], entry["source_type"])
        else:
            docs[entry["source_id"]] = load_page(uri, entry["source_id"])
    if use_cache:
        cache.write_bytes(pickle.dumps(docs))
    return docs
