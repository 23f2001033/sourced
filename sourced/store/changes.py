"""Source change detection and selective re-verification (doc 03 7).

When a datasheet is revised, only the products linked to it are re-verified,
and only the attributes whose extracted value actually changed. Re-verifying
only what changed is the difference between a catalog engine and a batch
script, and the saving is a reported metric.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from sourced.models import ProductRecord, SkuInput
from sourced.store.models import Product, ProductSource, Source, session


@dataclass
class ReverificationReport:
    source_id: str
    previous_hash: str | None = None
    new_hash: str | None = None
    products_linked: int = 0
    products_reprocessed: int = 0
    attributes_changed: int = 0
    attributes_examined: int = 0
    changed_by_product: dict[str, list[str]] = field(default_factory=dict)
    skipped_unchanged: bool = False

    @property
    def selective_fraction(self) -> float:
        """Share of a full re-run this revision actually cost."""
        if not self.attributes_examined:
            return 0.0
        return round(self.attributes_changed / self.attributes_examined, 4)


def stored_hash(source_id: str, db=None) -> str | None:
    own = db is None
    db = db or session()
    try:
        row = db.get(Source, source_id)
        return row.content_hash if row else None
    finally:
        if own:
            db.close()


def products_linked_to(source_id: str, db=None) -> list[Product]:
    own = db is None
    db = db or session()
    try:
        stmt = (select(Product).join(ProductSource,
                                     ProductSource.product_id == Product.id)
                .where(ProductSource.source_id == source_id))
        return list(db.execute(stmt).scalars())
    finally:
        if own:
            db.close()


def diff_against_stored(stored: Product, fresh: ProductRecord) -> list[str]:
    """Which attributes actually moved. Compared on the stored value text, so a
    re-extraction that lands on the same value costs nothing downstream."""
    before = stored.attributes or {}
    changed: list[str] = []
    for key, attr in fresh.attributes.items():
        old = before.get(key) or {}
        old_value = old.get("value")
        old_unit = old.get("unit")
        old_resolution = old.get("resolution")
        if (str(old_value) != str(attr.value) or old_unit != attr.unit
                or old_resolution != attr.resolution):
            changed.append(key)
    return changed


def on_source_updated(source_id: str, new_hash: str, pipeline, sku_lookup,
                      db=None) -> ReverificationReport:
    """pipeline: a Pipeline instance. sku_lookup: mpn_normalised -> SkuInput."""
    own = db is None
    db = db or session()
    try:
        report = ReverificationReport(source_id=source_id, new_hash=new_hash)
        report.previous_hash = stored_hash(source_id, db)
        if report.previous_hash == new_hash:
            report.skipped_unchanged = True
            return report

        linked = products_linked_to(source_id, db)
        report.products_linked = len(linked)
        for stored in linked:
            sku = sku_lookup(stored.mpn_normalised)
            if sku is None:
                continue
            fresh = pipeline.run(sku)
            report.products_reprocessed += 1
            report.attributes_examined += len(fresh.attributes)
            changed = diff_against_stored(stored, fresh)
            if changed:
                report.changed_by_product[stored.mpn] = changed
                report.attributes_changed += len(changed)

        row = db.get(Source, source_id)
        if row is not None:
            row.content_hash = new_hash
            db.commit()
        return report
    finally:
        if own:
            db.close()


def sku_lookup_from(records: list[dict]):
    from sourced.discovery.mpn import normalise_mpn

    table = {normalise_mpn(r["sku_input"]["mpn"]): SkuInput(**r["sku_input"])
             for r in records}
    return table.get
