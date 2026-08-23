"""FastAPI surface (doc 06 3.4).

The contract is frozen before the UI is written: the UI reads these shapes and
nothing else.

    uvicorn sourced.api.routes:app --reload
"""
from __future__ import annotations

import io
import json
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from sourced import config
from sourced.confidence.calibrate import Calibrator
from sourced.discovery.retrieve import SourceIndex
from sourced.ingest.loader import load_corpus
from sourced.models import SkuInput
from sourced.pipeline import Options, Pipeline
from sourced.store.models import Product, Provenance, create_all, session

app = FastAPI(title="Sourced", version="0.1.0",
              description="Product intelligence for industrial commerce")

WEB_DIR = Path(config.ROOT) / "web"


@lru_cache(maxsize=1)
def _pipeline() -> Pipeline:
    return Pipeline(SourceIndex(load_corpus()), Options(), calibrator=Calibrator.load())


@lru_cache(maxsize=1)
def _corpus_index() -> dict[str, dict]:
    path = config.DATA / "corpus.jsonl"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            out[record["sku_input"]["mpn"]] = record
    return out


@lru_cache(maxsize=1)
def _source_uris() -> dict[str, str]:
    path = config.DATA / "sources.jsonl"
    if not path.exists():
        return {}
    return {json.loads(line)["source_id"]: json.loads(line)["uri"]
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


class EnrichRequest(BaseModel):
    mpn: str
    manufacturer: str | None = None
    description_fragment: str | None = None
    internal_sku: str | None = None
    persist: bool = False


# ---------------------------------------------------------------- catalogue


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "llm_tier_enabled": config.LLM_ENABLED,
            "db": config.DB_URL.split("://")[0]}


@app.get("/api/products")
def list_products(limit: int = Query(100, le=1000), offset: int = 0,
                  status: str | None = None) -> dict:
    with session() as db:
        stmt = select(Product).order_by(Product.mpn)
        if status:
            stmt = stmt.where(Product.source_status == status)
        rows = list(db.execute(stmt.limit(limit).offset(offset)).scalars())
        total = len(list(db.execute(select(Product.id)).scalars()))
    return {"total": total, "items": [_summarise(r) for r in rows]}


@app.get("/api/products/{mpn}")
def get_product(mpn: str) -> dict:
    from sourced.discovery.mpn import normalise_mpn

    with session() as db:
        row = db.execute(select(Product).where(
            Product.mpn_normalised == normalise_mpn(mpn))).scalars().first()
        if row is None:
            raise HTTPException(404, f"no stored record for {mpn}")
        provenance = list(db.execute(select(Provenance).where(
            Provenance.product_id == row.id)).scalars())
    return {**_expand(row),
            "provenance": [_provenance_dict(p) for p in provenance]}


@app.get("/api/products/{mpn}/provenance/{key}")
def get_provenance(mpn: str, key: str) -> dict:
    from sourced.discovery.mpn import normalise_mpn

    with session() as db:
        row = db.execute(select(Product).where(
            Product.mpn_normalised == normalise_mpn(mpn))).scalars().first()
        if row is None:
            raise HTTPException(404, f"no stored record for {mpn}")
        prov = db.execute(select(Provenance).where(
            Provenance.product_id == row.id,
            Provenance.canonical_key == key)).scalars().first()
    if prov is None:
        raise HTTPException(404, f"no provenance for {key}")
    attribute = (row.attributes or {}).get(key, {})
    return {"attribute": attribute, "provenance": _provenance_dict(prov)}


@app.post("/api/enrich")
def enrich(request: EnrichRequest) -> dict:
    """Run the pipeline live on one sparse row."""
    sku = SkuInput(mpn=request.mpn, manufacturer=request.manufacturer,
                   description_fragment=request.description_fragment,
                   internal_sku=request.internal_sku)
    record = _pipeline().run(sku)
    if request.persist:
        from sourced.store.upsert import upsert_product

        create_all()
        upsert_product(record)
    return json.loads(record.model_dump_json())


@app.get("/api/results")
def results() -> dict:
    path = config.DATA / "results.json"
    if not path.exists():
        raise HTTPException(404, "run `python -m sourced.eval.run` first")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------ source viewer


@app.get("/api/source/{source_id}")
def get_source(source_id: str) -> dict:
    uri = _source_uris().get(source_id)
    if uri is None:
        raise HTTPException(404, f"unknown source {source_id}")
    path = config.resolve_uri(uri)
    payload = {"source_id": source_id, "uri": uri, "kind": path.suffix.lstrip(".")}
    if path.suffix.lower() == ".json" and path.exists():
        payload["content"] = json.loads(path.read_text(encoding="utf-8"))
    return payload


@app.get("/api/source/{source_id}/page/{page}.png")
def source_page(source_id: str, page: int, bbox: str | None = None,
                resolution: int = 120) -> Response:
    """Render a PDF page with the cited region highlighted. This is the
    acceptance criterion of doc 06 3.4: clicking a published value shows the
    document, page and region it came from."""
    import pdfplumber

    uri = _source_uris().get(source_id)
    path = config.resolve_uri(uri) if uri else None
    if path is None or path.suffix.lower() != ".pdf" or not path.exists():
        raise HTTPException(404, f"{source_id} is not a rendered document")

    with pdfplumber.open(path) as pdf:
        if page < 1 or page > len(pdf.pages):
            raise HTTPException(404, f"page {page} out of range")
        image = pdf.pages[page - 1].to_image(resolution=resolution)
        if bbox:
            try:
                x0, top, x1, bottom = (float(v) for v in bbox.split(","))
                image.draw_rect((x0, top, x1, bottom), stroke="#d64545",
                                stroke_width=3, fill=(214, 69, 69, 38))
            except ValueError:
                pass
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
    return Response(buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/figures/{name}.png")
def figure(name: str) -> FileResponse:
    path = config.FIG_DIR / f"{name}.png"
    if not path.exists():
        raise HTTPException(404, f"{name}.png has not been generated")
    return FileResponse(path, media_type="image/png")


# ------------------------------------------------------------------ helpers


def _summarise(row: Product) -> dict:
    completeness = row.completeness or {}
    commerce = row.commerce or {}
    return {
        "mpn": row.mpn,
        "manufacturer": row.manufacturer_resolved or row.manufacturer,
        "source_status": row.source_status,
        "category_code": row.category_code,
        "category_label": row.category_label,
        "title": commerce.get("title"),
        "published": completeness.get("published_count", 0),
        "review": completeness.get("review_count", 0),
        "abstained": completeness.get("abstained_count", 0),
        "required_filled": completeness.get("required_filled", 0),
        "required_total": completeness.get("required_total", 0),
        "abstention": row.abstention,
    }


def _expand(row: Product) -> dict:
    return {
        **_summarise(row),
        "mpn_normalised": row.mpn_normalised,
        "attributes": row.attributes or {},
        "commerce": row.commerce,
        "completeness": row.completeness,
        "telemetry": row.telemetry,
        "source_content_hash": row.source_content_hash,
        "updated_at": str(row.updated_at),
    }


def _provenance_dict(prov: Provenance) -> dict:
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


if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
