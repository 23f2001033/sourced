"""PDF ingestion (doc 03 §2.2, ADR-009).

Table rows are emitted as self-describing chunks with the header prepended, so
a retrieved row carries its own column semantics. Every cell keeps its bounding
box, which is what powers the highlighted-source panel in the UI.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber

from sourced.ingest.chunks import Cell, Chunk, Document


def content_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _cell_bbox(table, r: int, c: int):
    try:
        cell = table.rows[r].cells[c]
    except (IndexError, AttributeError):
        return None
    if cell is None:
        return None
    return tuple(float(v) for v in cell)


def load_pdf(path: str | Path, source_id: str | None = None,
             source_type: str = "manufacturer_datasheet") -> Document:
    path = Path(path)
    sid = source_id or path.stem
    doc = Document(
        source_id=sid,
        source_type=source_type,          # type: ignore[arg-type]
        uri=path.as_posix(),
        content_hash=content_hash(path),
    )
    texts: list[str] = []
    with pdfplumber.open(path) as pdf:
        doc.page_count = len(pdf.pages)
        for pageno, page in enumerate(pdf.pages, 1):
            page_text = page.extract_text() or ""
            texts.append(page_text)

            table_bboxes = []
            for t_idx, table in enumerate(page.find_tables()):
                table_bboxes.append(table.bbox)
                rows = table.extract()
                if not rows:
                    continue
                header = [(h or "").strip() for h in rows[0]]
                for r_idx, row in enumerate(rows[1:], start=1):
                    values = [(v or "").strip() for v in row]
                    if not any(values):
                        continue
                    cells = [
                        Cell(header=h, value=v, bbox=_cell_bbox(table, r_idx, c_idx))
                        for c_idx, (h, v) in enumerate(zip(header, values))
                    ]
                    row_bbox = _row_bbox(cells) or tuple(float(x) for x in table.bbox)
                    doc.chunks.append(
                        Chunk(
                            chunk_id=f"{sid}:p{pageno}:t{t_idx}:r{r_idx}",
                            source_id=sid,
                            source_type=source_type,   # type: ignore[arg-type]
                            page=pageno,
                            text=" | ".join(f"{h}: {v}" for h, v in zip(header, values) if h or v),
                            bbox=row_bbox,
                            locator="table_cell",
                            cells=cells,
                        )
                    )

            # prose that is not inside a table
            for line_idx, line in enumerate(_prose_lines(page, table_bboxes)):
                doc.chunks.append(
                    Chunk(
                        chunk_id=f"{sid}:p{pageno}:l{line_idx}",
                        source_id=sid,
                        source_type=source_type,       # type: ignore[arg-type]
                        page=pageno,
                        text=line["text"],
                        bbox=line["bbox"],
                        locator="prose",
                    )
                )
    doc.full_text = "\n".join(texts)
    return doc


def _row_bbox(cells: list[Cell]):
    boxes = [c.bbox for c in cells if c.bbox]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _prose_lines(page, table_bboxes) -> list[dict]:
    out: list[dict] = []
    for word_line in _group_words_into_lines(page.extract_words()):
        bbox = (min(w["x0"] for w in word_line), min(w["top"] for w in word_line),
                max(w["x1"] for w in word_line), max(w["bottom"] for w in word_line))
        if any(_inside(bbox, tb) for tb in table_bboxes):
            continue
        text = " ".join(w["text"] for w in word_line).strip()
        if text:
            out.append({"text": text, "bbox": bbox})
    return out


def _group_words_into_lines(words, tol: float = 2.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines


def _inside(inner, outer, pad: float = 2.0) -> bool:
    return (inner[0] >= outer[0] - pad and inner[1] >= outer[1] - pad
            and inner[2] <= outer[2] + pad and inner[3] <= outer[3] + pad)
