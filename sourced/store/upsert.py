"""Idempotent persistence (doc 03 7).

Keyed on (manufacturer_resolved, mpn_normalised). Reprocessing updates in
place: no duplicates, no churn. This is what makes it a catalog engine rather
than a batch script.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import delete, select

from sourced.discovery.mpn import family_prefix
from sourced.models import ProductRecord
from sourced.store.models import (EvalLabel, Product, ProductSource, Provenance,
                                  Source, session)


def _json(model) -> dict | list | None:
    if model is None:
        return None
    return json.loads(model.model_dump_json())


def upsert_product(record: ProductRecord, db=None) -> str:
    own_session = db is None
    db = db or session()
    try:
        stmt = select(Product).where(
            Product.mpn_normalised == record.mpn_normalised,
            Product.manufacturer_resolved.is_(record.manufacturer_resolved)
            if record.manufacturer_resolved is None
            else Product.manufacturer_resolved == record.manufacturer_resolved)
        row = db.execute(stmt).scalar_one_or_none()
        if row is None:
            row = Product(id=str(record.id), mpn=record.mpn,
                          mpn_normalised=record.mpn_normalised)
            db.add(row)

        row.mpn = record.mpn
        row.manufacturer = record.manufacturer
        row.manufacturer_resolved = record.manufacturer_resolved
        row.mpn_family = family_prefix(record.mpn_normalised)
        row.source_status = record.source_status
        row.category_taxonomy = record.category.taxonomy if record.category else None
        row.category_code = record.category.code if record.category else None
        row.category_label = record.category.label if record.category else None
        row.attributes = {k: _json(v) for k, v in record.attributes.items()}
        row.commerce = _json(record.commerce)
        row.completeness = _json(record.completeness)
        row.abstention = _json(record.abstention)
        row.telemetry = record.telemetry
        row.source_content_hash = record.source_content_hash
        row.updated_at = datetime.now(timezone.utc)
        db.flush()

        _replace_provenance(db, row.id, record)
        _replace_sources(db, row.id, record)
        db.commit()
        return row.id
    finally:
        if own_session:
            db.close()


def _replace_provenance(db, product_id: str, record: ProductRecord) -> None:
    db.execute(delete(Provenance).where(Provenance.product_id == product_id))
    for key, attr in record.attributes.items():
        candidate = attr.winning_candidate
        evidence = candidate.evidence if candidate else None
        db.add(Provenance(
            product_id=product_id,
            canonical_key=key,
            value_text=None if attr.value is None else str(attr.value),
            unit=attr.unit,
            raw_text=attr.raw_text,
            tier=candidate.tier if candidate else None,
            resolution=attr.resolution,
            confidence=attr.confidence,
            criticality=attr.criticality,
            source_id=evidence.source_id if evidence else None,
            source_type=evidence.source_type if evidence else None,
            page=evidence.page if evidence else None,
            bbox=list(evidence.bbox) if evidence and evidence.bbox else None,
            span=evidence.span if evidence else None,
            locator=evidence.locator if evidence else None,
            checks={k: _json(v) for k, v in attr.checks.items()},
            abstention_code=attr.abstention_reason.code if attr.abstention_reason else None,
            abstention_detail=attr.abstention_reason.detail if attr.abstention_reason else None,
            resolution_hint=(attr.abstention_reason.resolution_hint
                             if attr.abstention_reason else None),
            model_id=candidate.producer if candidate else None,
        ))


def _replace_sources(db, product_id: str, record: ProductRecord) -> None:
    db.execute(delete(ProductSource).where(ProductSource.product_id == product_id))

    # the source rows must be in the database before anything references them:
    # SQLAlchemy's unit of work is free to order two pending inserts either way,
    # and Postgres checks the foreign key immediately
    for link in record.sources:
        existing = db.get(Source, link.source_id)
        if existing is None:
            db.add(Source(id=link.source_id, source_type=link.source_type,
                          authority_rank=link.authority_rank, uri=link.uri,
                          content_hash=link.content_hash or ""))
        else:
            existing.content_hash = link.content_hash or existing.content_hash
            existing.uri = link.uri or existing.uri
    db.flush()

    for link in record.sources:
        db.add(ProductSource(product_id=product_id, source_id=link.source_id,
                             match_confidence=link.match_confidence,
                             match_evidence=link.match_evidence))


def load_labels(records: list[dict], db=None) -> int:
    """Labels live in eval_labels and nowhere the pipeline can read them."""
    from sourced.discovery.manufacturer import resolve
    from sourced.discovery.mpn import normalise_mpn

    own_session = db is None
    db = db or session()
    try:
        db.execute(delete(EvalLabel))
        n = 0
        for record in records:
            sku = record["sku_input"]
            manufacturer, _ = resolve(sku.get("manufacturer"))
            for key, label in record["labels"].items():
                db.add(EvalLabel(
                    mpn_normalised=normalise_mpn(sku["mpn"]),
                    manufacturer_resolved=manufacturer or "",
                    canonical_key=key,
                    value_text=str(label["value"]),
                    unit=label.get("unit"),
                    label_source=record.get("label_provenance", "unknown"),
                    audited=bool(record.get("hand_audited")),
                    split=record.get("split"),
                ))
                n += 1
        db.commit()
        return n
    finally:
        if own_session:
            db.close()
