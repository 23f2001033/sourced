"""Distributor / marketplace listing ingestion.

These carry structured key-value fields rather than page geometry, so their
chunks use locator `structured_field` and have no bounding box. Adjudication
ranks that locator below `table_cell`, and their source type below a
manufacturer datasheet.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sourced.ingest.chunks import Cell, Chunk, Document


def load_page(path: str | Path, source_id: str | None = None) -> Document:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    sid = source_id or path.stem
    stype = payload.get("source_type", "distributor_page")
    doc = Document(
        source_id=sid,
        source_type=stype,                 # type: ignore[arg-type]
        uri=path.as_posix(),
        content_hash="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        page_count=1,
    )
    doc.chunks.append(Chunk(
        chunk_id=f"{sid}:head", source_id=sid, source_type=stype,   # type: ignore[arg-type]
        text=payload.get("title", ""), locator="prose", page=1))

    # the MPN and manufacturer fields of a listing are structured slots, not
    # prose: the listing is *about* the part named in them
    for name, key in (("Manufacturer Part Number", "mpn"), ("Manufacturer", "manufacturer")):
        value = payload.get(key)
        if value:
            doc.chunks.append(Chunk(
                chunk_id=f"{sid}:{key}", source_id=sid,
                source_type=stype,                                  # type: ignore[arg-type]
                page=1, text=f"{name}: {value}", locator="structured_field",
                cells=[Cell(header=name, value=str(value))]))

    for i, (key, value) in enumerate(payload.get("specifications", {}).items()):
        doc.chunks.append(Chunk(
            chunk_id=f"{sid}:f{i}", source_id=sid, source_type=stype,  # type: ignore[arg-type]
            page=1, text=f"{key}: {value}", locator="structured_field",
            cells=[Cell(header=str(key), value=str(value))]))

    doc.full_text = "\n".join(c.text for c in doc.chunks)
    return doc
