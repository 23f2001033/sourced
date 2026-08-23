"""Export the enriched catalogue as a static site.

The pipeline itself cannot run on a serverless host: it carries scikit-learn
and pdfplumber, builds a retrieval index over every source document at start-up,
and reads a corpus that is generated rather than committed. Deploying it there
would produce a site that loads and then fails.

So what ships is a **snapshot of a real run**, not a re-implementation: the same
records the pipeline produced, the same provenance, the same rendered pages. The
bounding box is drawn in the browser over the page image, which keeps the one
interaction that matters -- click a value, see the exact cell it was read from --
working with no server at all.

What a snapshot cannot do is enrich a part number it has never seen. That is
stated on the page rather than implied away.

    python -m sourced.export_static --out dist
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from sqlalchemy import select

from sourced import config
from sourced.store.models import Product, Provenance, session

ROOT = config.ROOT
PAGE_RESOLUTION = 110


def _summarise(row: Product) -> dict:
    completeness = row.completeness or {}
    commerce = row.commerce or {}
    return {
        "mpn": row.mpn,
        "manufacturer": row.manufacturer_resolved or row.manufacturer,
        "source_status": row.source_status,
        "category_code": row.category_code,
        "category_label": row.category_label,
        "schema": row.attributes and None,
        "title": commerce.get("title"),
        "published": completeness.get("published_count", 0),
        "review": completeness.get("review_count", 0),
        "abstained": completeness.get("abstained_count", 0),
        "required_filled": completeness.get("required_filled", 0),
        "required_total": completeness.get("required_total", 0),
        "abstention": row.abstention,
    }


def _provenance(prov: Provenance) -> dict:
    return {
        "canonical_key": prov.canonical_key,
        "value_text": prov.value_text,
        "unit": prov.unit,
        "raw_text": prov.raw_text,
        "tier": prov.tier,
        "resolution": prov.resolution,
        "confidence": prov.confidence,
        "criticality": prov.criticality,
        "source_id": prov.source_id,
        "source_type": prov.source_type,
        "page": prov.page,
        "bbox": prov.bbox,
        "span": prov.span,
        "locator": prov.locator,
        "checks": prov.checks,
        "abstention_code": prov.abstention_code,
        "abstention_detail": prov.abstention_detail,
        "resolution_hint": prov.resolution_hint,
        "model_id": prov.model_id,
    }


def slug(mpn: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in mpn).strip("-").lower()


def export_pages(out: Path, needed: set[tuple[str, int]]) -> dict:
    """Render each cited PDF page once. The browser draws the box."""
    import pdfplumber

    uris = {}
    index_path = config.DATA / "sources.jsonl"
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            entry = json.loads(line)
            uris[entry["source_id"]] = entry["uri"]

    pages_dir = out / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, dict] = {}

    for source_id, page_no in sorted(needed):
        uri = uris.get(source_id)
        if not uri:
            continue
        path = config.resolve_uri(uri)
        if path.suffix.lower() != ".pdf" or not path.exists():
            continue
        name = f"{source_id}-p{page_no}.png"
        target = pages_dir / name
        with pdfplumber.open(path) as pdf:
            if page_no < 1 or page_no > len(pdf.pages):
                continue
            page = pdf.pages[page_no - 1]
            if not target.exists():
                page.to_image(resolution=PAGE_RESOLUTION).save(str(target))
            sizes[f"{source_id}:{page_no}"] = {
                "file": f"pages/{name}",
                "width": float(page.width),
                "height": float(page.height),
            }
    return sizes


def build(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    records_dir = out / "records"
    records_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict] = []
    needed_pages: set[tuple[str, int]] = set()
    cited: dict[str, set[str]] = {}

    with session() as db:
        products = list(db.execute(select(Product).order_by(Product.mpn)).scalars())
        for row in products:
            provenance = list(db.execute(
                select(Provenance).where(Provenance.product_id == row.id)).scalars())
            record = {
                **_summarise(row),
                "attributes": row.attributes or {},
                "commerce": row.commerce,
                "completeness": row.completeness,
                "telemetry": row.telemetry,
                "provenance": [_provenance(p) for p in provenance],
            }
            (records_dir / f"{slug(row.mpn)}.json").write_text(
                json.dumps(record, separators=(",", ":"), default=str),
                encoding="utf-8")

            summary = _summarise(row)
            summary["slug"] = slug(row.mpn)
            index.append(summary)

            for p in provenance:
                if p.source_id and p.page and p.bbox:
                    needed_pages.add((p.source_id, int(p.page)))
                    cited.setdefault(summary["slug"], set()).add(
                        f"{p.source_id}:{int(p.page)}")

    sizes = export_pages(out, needed_pages)

    # A record whose every citation is a listing row has nothing to outline.
    # The front end opens on one that does, so the first frame shows the point.
    for summary in index:
        summary["has_page"] = any(
            key in sizes for key in cited.get(summary["slug"], ()))

    (out / "index.json").write_text(
        json.dumps({"items": index, "total": len(index), "pages": sizes},
                   separators=(",", ":"), default=str), encoding="utf-8")

    for name in ("results.json", "llm_experiment.json", "description_experiment.json",
                 "corpus_summary.json"):
        source = config.DATA / name
        if source.exists():
            shutil.copy(source, out / name)

    figures = out / "figures"
    figures.mkdir(exist_ok=True)
    for figure in (config.FIG_DIR).glob("*.png"):
        shutil.copy(figure, figures / figure.name)

    # the front end lives in the repo, not in the build output
    for asset in (ROOT / "web-static").glob("*"):
        if asset.is_file():
            shutil.copy(asset, out / asset.name)

    return {
        "records": len(index),
        "page_images": len(sizes),
        "bytes": sum(f.stat().st_size for f in out.rglob("*") if f.is_file()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "dist"))
    args = parser.parse_args()

    stats = build(Path(args.out))
    print(json.dumps({**stats, "megabytes": round(stats["bytes"] / 2**20, 1)},
                     indent=2))
