"""Persistence (doc 02 Database schema).

One database, two shapes: JSON where the structure varies by category,
relational where it must be queried and joined. The DDL in migrations/ is the
Postgres form; these models are the same schema expressed portably so the
stack also runs on SQLite without a container.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, create_engine, event)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from sourced import config

JSONType = JSON().with_variant(JSONB(), "postgresql")

# migrations/001_init.sql declares these columns as UUID, which is the right
# Postgres type and the one doc 02 specifies. SQLite has no UUID type, so the
# column is a 36-character string there. Without the variant the model binds
# VARCHAR against a UUID column and Postgres rejects the insert -- a failure
# SQLite can never surface.
UUIDType = String(36).with_variant(PG_UUID(as_uuid=False), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id = Column(UUIDType, primary_key=True, default=_uuid)
    mpn = Column(Text, nullable=False)
    mpn_normalised = Column(Text, nullable=False)
    manufacturer = Column(Text)
    manufacturer_resolved = Column(Text)
    source_status = Column(Text, nullable=False)
    category_taxonomy = Column(Text)
    category_code = Column(Text)
    category_label = Column(Text)
    attributes = Column(JSONType, nullable=False, default=dict)
    commerce = Column(JSONType)
    completeness = Column(JSONType)
    abstention = Column(JSONType)
    telemetry = Column(JSONType)
    source_content_hash = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    # idempotency: reprocessing must update, never duplicate
    __table_args__ = (
        UniqueConstraint("manufacturer_resolved", "mpn_normalised", name="uq_product"),
        # family index supports Level 3 coherence checks without a full scan
        Index("idx_products_family", "manufacturer_resolved", "mpn_family"),
    )

    # left-prefix of the normalised MPN, materialised because SQLite cannot
    # index an expression portably
    mpn_family = Column(Text, index=True)


class Provenance(Base):
    __tablename__ = "provenance"

    id = Column(UUIDType, primary_key=True, default=_uuid)
    product_id = Column(UUIDType, ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=False)
    canonical_key = Column(Text, nullable=False)
    value_text = Column(Text)
    unit = Column(Text)
    raw_text = Column(Text)
    tier = Column(Text)
    resolution = Column(Text, nullable=False)
    confidence = Column(Float)
    criticality = Column(Text)
    source_id = Column(Text)
    source_type = Column(Text)
    page = Column(Integer)
    bbox = Column(JSONType)
    span = Column(Text)
    locator = Column(Text)
    checks = Column(JSONType)
    abstention_code = Column(Text)
    abstention_detail = Column(Text)
    resolution_hint = Column(Text)
    model_id = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (Index("idx_prov_lookup", "product_id", "canonical_key"),)


class Source(Base):
    __tablename__ = "sources"

    id = Column(Text, primary_key=True)
    source_type = Column(Text, nullable=False)
    authority_rank = Column(Integer, nullable=False)
    uri = Column(Text)
    content_hash = Column(Text, nullable=False)
    fetched_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    page_count = Column(Integer)


class ProductSource(Base):
    __tablename__ = "product_sources"

    product_id = Column(UUIDType, ForeignKey("products.id", ondelete="CASCADE"),
                        primary_key=True)
    source_id = Column(Text, ForeignKey("sources.id"), primary_key=True)
    match_confidence = Column(Float, nullable=False)
    match_evidence = Column(Text)


class EvalLabel(Base):
    """Labels live here and nowhere the pipeline can read them (doc 05)."""

    __tablename__ = "eval_labels"

    mpn_normalised = Column(Text, primary_key=True)
    manufacturer_resolved = Column(Text, primary_key=True)
    canonical_key = Column(Text, primary_key=True)
    value_text = Column(Text)
    unit = Column(Text)
    label_source = Column(Text, nullable=False)
    audited = Column(Boolean, nullable=False, default=False)
    audit_verdict = Column(Text)
    split = Column(Text)


_engine = None
_Session = None


def engine(url: str | None = None):
    global _engine, _Session
    if _engine is None or url is not None:
        _engine = create_engine(url or config.DB_URL, future=True)
        if _engine.dialect.name == "sqlite":
            _enforce_sqlite_foreign_keys(_engine)
        _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def _enforce_sqlite_foreign_keys(bound) -> None:
    """SQLite ignores foreign keys unless asked to enforce them.

    Left off, the local path silently accepts writes that Postgres rejects,
    which is how an insert ordering bug reached `docker compose up` without a
    single test failing.
    """

    @event.listens_for(bound, "connect")
    def _set_pragma(dbapi_connection, _record):     # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def session():
    if _Session is None:
        engine()
    return _Session()


def create_all(url: str | None = None) -> None:
    Base.metadata.create_all(engine(url))


def drop_all(url: str | None = None) -> None:
    Base.metadata.drop_all(engine(url))
