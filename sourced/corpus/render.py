"""Render a family datasheet to PDF, and a distributor page to JSON.

Ruled tables, real text layer, per-cell coordinates — the corpus is digital
PDF, which is the stated assumption in doc 06's cut list (OCR path is cut).
"""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm as MM
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from sourced.corpus.spec import DISPLAY, PITCH_INCH_DISPLAY

_styles = getSampleStyleSheet()
_H1 = ParagraphStyle("h1", parent=_styles["Heading1"], fontSize=15, spaceAfter=4)
_H2 = ParagraphStyle("h2", parent=_styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4)
_P = ParagraphStyle("p", parent=_styles["BodyText"], fontSize=8.5, leading=11)

_GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 7.2),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
])


def fmt_pitch(pitch_mm: float, use_inch: bool) -> str:
    if use_inch and pitch_mm in PITCH_INCH_DISPLAY:
        return f'{PITCH_INCH_DISPLAY[pitch_mm]} in'
    return f"{pitch_mm:.2f} mm"


def fmt_temp(c: float, use_f: bool) -> str:
    return f"{c * 9 / 5 + 32:.0f} degF" if use_f else f"{c:.0f} degC"


def fmt_current(a: float, use_ma: bool) -> str:
    return f"{a * 1000:.0f} mA" if use_ma else f"{a:.1f} A"


def render_datasheet(path: Path, family: dict, parts: list[dict], revision: str = "A") -> None:
    """One family datasheet. Family-wide specs in one table, per-part values in
    an ordering table — so extraction must select the correct row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=16 * MM, rightMargin=16 * MM, topMargin=14 * MM, bottomMargin=14 * MM,
        title=f"{family['manufacturer']} {family['series']} Series Datasheet",
    )
    u = family["display"]
    flow = []
    flow.append(Paragraph(
        f"{family['manufacturer']} — {family['series']} Series "
        f"{fmt_pitch(family['pitch'], u['inch_pitch'])} Pitch Wire-to-Board Connector Header",
        _H1))
    flow.append(Paragraph(
        f"Product Datasheet · Document {family['doc_no']} · Revision {revision}", _P))
    flow.append(Spacer(1, 6))
    flow.append(Paragraph(
        f"The {family['series']} Series is a {fmt_pitch(family['pitch'], u['inch_pitch'])} pitch "
        f"wire-to-board header intended for {DISPLAY[family['mounting_type']].lower()} assembly. "
        f"The housing is moulded from {DISPLAY[family['housing_material']]} rated "
        f"{DISPLAY[family['flammability_rating']]}. Contacts are terminated by "
        f"{DISPLAY[family['termination']].lower()}. All parts in this series are "
        f"{'RoHS compliant' if family['rohs_compliant'] else 'not RoHS compliant'}.",
        _P))

    flow.append(Paragraph("Specifications", _H2))
    spec_rows = [["Parameter", "Value", "Conditions"]]
    spec_rows.append(["Pitch", fmt_pitch(family["pitch"], u["inch_pitch"]), "Nominal centreline"])
    spec_rows.append(["Voltage Rating", f"{family['voltage_rating']:.0f} V AC/DC", "Working voltage"])
    spec_rows.append(["Operating Temperature Min",
                      fmt_temp(family["operating_temp_min"], u["fahrenheit"]), "Ambient"])
    spec_rows.append(["Operating Temperature Max",
                      fmt_temp(family["operating_temp_max"], u["fahrenheit"]), "Ambient"])
    spec_rows.append(["Housing Material", DISPLAY[family["housing_material"]], "Moulded"])
    spec_rows.append(["Flammability Rating", DISPLAY[family["flammability_rating"]], "Housing"])
    spec_rows.append(["Mounting Type", DISPLAY[family["mounting_type"]], "PCB"])
    spec_rows.append(["Termination", DISPLAY[family["termination"]], "Wire side"])
    if family.get("contact_resistance") is not None:
        spec_rows.append(["Contact Resistance",
                          f"{family['contact_resistance']:.0f} mOhm", "Low level, initial"])
    flow.append(Table(spec_rows, colWidths=[52 * MM, 62 * MM, 64 * MM], style=_GRID, repeatRows=1))

    flow.append(Paragraph("Ordering Information", _H2))
    head = ["Part Number", "Positions", "Orientation", "Contact Plating",
            "Contact Material", "Current Rating", "Housing Colour"]
    rows = [head]
    for p in parts:
        rows.append([
            p["mpn"],
            str(p["pole_count"]),
            DISPLAY[p["orientation"]],
            DISPLAY[p["contact_plating"]],
            DISPLAY[p["contact_material"]],
            fmt_current(p["current_rating"], u["milliamp"]),
            DISPLAY[p["housing_colour"]] if p.get("housing_colour") else "—",
        ])
    flow.append(Table(rows, colWidths=[40 * MM, 18 * MM, 22 * MM, 24 * MM, 30 * MM, 24 * MM, 20 * MM],
                      style=_GRID, repeatRows=1))

    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "Notes: Current rating is per contact with all contacts energised at 20 degC ambient. "
        "Values are nominal and subject to change without notice. "
        f"Refer to application specification {family['doc_no']}-AS for processing guidance.", _P))
    doc.build(flow)


def render_distributor_page(path: Path, part: dict, attrs: dict, source_type: str,
                            distributor: str, title: str | None = None) -> None:
    """A distributor listing: structured key/value fields, no page geometry.

    Shared across categories, so the title is either supplied by the caller or
    composed from whatever identifying fields the part happens to carry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if title is None:
        title = " ".join(str(bit) for bit in [
            part.get("manufacturer"), part.get("mpn"),
            f"{part['pole_count']} Position Connector Header"
            if part.get("pole_count") is not None else None,
        ] if bit)
    payload = {
        "distributor": distributor,
        "source_type": source_type,
        "mpn": part["mpn"],
        "manufacturer": part["manufacturer"],
        "title": title,
        "specifications": attrs,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
