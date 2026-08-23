"""Pipe fitting corpus (doc 05, the second vertical).

The vertical doc 05 calls the richest abbreviation soup, and the reason for
carrying a second category at all: here the deterministic rules tier does real
work, because the sparse row itself carries most of the record.

Catalogue pages rather than datasheets: a family-wide specification block plus
a dimensions table with one row per size. As with connectors, per-part values
live in that table, so extraction must select the row for *this* part.
"""
from __future__ import annotations

import random
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm as MM
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from sourced import config
from sourced.ingest.normalize import normalise_text

# ------------------------------------------------------------------ vocabulary

MANUFACTURERS = [
    ("Anvil International", "anvil", ["0300", "0310", "0320"]),
    ("Merit Brass", "merit", ["3105", "3106", "3128"]),
    ("Smith-Cooper", "cooper", ["SC40", "SC80", "SC30"]),
    ("Ward Manufacturing", "ward", ["WM1", "WM2", "WM4"]),
    ("Matco-Norca", "matco", ["TH1", "TH2", "TH4"]),
]

SIZES = [0.25, 0.375, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0]
SIZE_FRACTIONS = {0.25: "1/4", 0.375: "3/8", 0.5: "1/2", 0.75: "3/4", 1.0: "1",
                  1.25: "1-1/4", 1.5: "1-1/2", 2.0: "2", 2.5: "2-1/2", 3.0: "3",
                  4.0: "4", 6.0: "6"}

FORMS = ["elbow", "tee", "coupling", "union", "nipple", "cap", "plug", "bushing",
         "cross", "adapter", "reducer"]
FORM_ABBR = {"elbow": "ELL", "tee": "TEE", "coupling": "CPLG", "union": "UNION",
             "nipple": "NIP", "cap": "CAP", "plug": "PLUG", "bushing": "BUSH",
             "cross": "CROSS", "adapter": "ADPT", "reducer": "RED"}
REDUCING_FORMS = {"bushing", "reducer"}

MATERIALS = ["brass", "bronze", "stainless_steel_304", "stainless_steel_316",
             "carbon_steel", "malleable_iron", "ductile_iron", "pvc", "cpvc"]
MATERIAL_ABBR = {"brass": "BRS", "bronze": "BRZ", "stainless_steel_304": "SS304",
                 "stainless_steel_316": "SS316", "carbon_steel": "CS",
                 "malleable_iron": "MI", "ductile_iron": "DI", "pvc": "PVC",
                 "cpvc": "CPVC"}
PLASTICS = {"pvc", "cpvc", "pex"}

ENDS = ["female_iron_pipe", "male_iron_pipe", "sweat", "socket_weld", "butt_weld",
        "flanged", "compression", "barb"]
END_ABBR = {"female_iron_pipe": "FIP", "male_iron_pipe": "MIP", "sweat": "SWT",
            "socket_weld": "SW", "butt_weld": "BW", "flanged": "FLG",
            "compression": "COMP", "barb": "BARB"}
THREADED_ENDS = {"female_iron_pipe", "male_iron_pipe"}

CLASS_CEILING = {"class_125": 200, "class_150": 300, "class_200": 400,
                 "class_300": 600, "class_600": 1200, "class_1000": 1000,
                 "class_2000": 2000, "class_3000": 3000}
CLASSES_BY_MATERIAL = {
    "brass": ["class_125", "class_150", "class_200"],
    "bronze": ["class_125", "class_150", "class_200"],
    "stainless_steel_304": ["class_150", "class_300", "class_1000"],
    "stainless_steel_316": ["class_150", "class_300", "class_3000"],
    "carbon_steel": ["class_150", "class_300", "class_2000", "class_3000"],
    "malleable_iron": ["class_150", "class_300"],
    "ductile_iron": ["class_150", "class_300"],
    "pvc": ["class_125", "class_150"],
    "cpvc": ["class_125", "class_150"],
}
TEMP_BY_MATERIAL = {
    "brass": [366, 400], "bronze": [400, 450],
    "stainless_steel_304": [800, 1000], "stainless_steel_316": [1000, 1200],
    "carbon_steel": [750, 1000], "malleable_iron": [550, 650],
    "ductile_iron": [500, 650], "pvc": [140, 150], "cpvc": [180, 200],
}

THREAD_STANDARDS = ["npt", "nptf", "bspt"]
SCHEDULES = ["schedule_40", "schedule_80", "schedule_160"]
FINISHES = ["natural", "galvanized", "black_oxide", "chrome_plated", "polished"]
FINISH_ABBR = {"natural": "", "galvanized": "GALV", "black_oxide": "BLKOX",
               "chrome_plated": "CHR", "polished": "POL"}

DISPLAY = {
    "elbow": "Elbow", "tee": "Tee", "coupling": "Coupling", "union": "Union",
    "nipple": "Nipple", "cap": "Cap", "plug": "Plug", "bushing": "Bushing",
    "cross": "Cross", "adapter": "Adapter", "reducer": "Reducer",
    "brass": "Brass", "bronze": "Bronze", "stainless_steel_304": "304 Stainless Steel",
    "stainless_steel_316": "316 Stainless Steel", "carbon_steel": "Carbon Steel",
    "malleable_iron": "Malleable Iron", "ductile_iron": "Ductile Iron",
    "pvc": "PVC", "cpvc": "CPVC",
    "female_iron_pipe": "Female Iron Pipe", "male_iron_pipe": "Male Iron Pipe",
    "sweat": "Sweat", "socket_weld": "Socket Weld", "butt_weld": "Butt Weld",
    "flanged": "Flanged", "compression": "Compression", "barb": "Barb",
    "npt": "NPT", "nptf": "NPTF", "bspt": "BSPT", "none": "None",
    "schedule_40": "Schedule 40", "schedule_80": "Schedule 80",
    "schedule_160": "Schedule 160",
    "natural": "Natural", "galvanized": "Galvanized", "black_oxide": "Black Oxide",
    "chrome_plated": "Chrome Plated", "polished": "Polished",
    "class_125": "Class 125", "class_150": "Class 150", "class_200": "Class 200",
    "class_300": "Class 300", "class_600": "Class 600", "class_1000": "Class 1000",
    "class_2000": "Class 2000", "class_3000": "Class 3000",
}

LABEL_KEYS = ["nominal_size", "nominal_size_secondary", "form_factor", "bend_angle",
              "end_connection_1", "end_connection_2", "body_material",
              "pressure_class", "max_working_pressure", "temperature_rating_max",
              "thread_standard", "wall_schedule", "lead_free_compliant", "finish"]

UNITS = {"nominal_size": "inch", "nominal_size_secondary": "inch",
         "max_working_pressure": "psi", "temperature_rating_max": "degF"}


def size_code(size: float) -> str:
    return f"{int(round(size * 100)):03d}"


def make_mpn(style: str, series: str, size: float, size2: float | None,
             form: str, index: int) -> str:
    code = size_code(size)
    form_code = {"elbow": "E", "tee": "T", "coupling": "C", "union": "U",
                 "nipple": "N", "cap": "P", "plug": "G", "bushing": "B",
                 "cross": "X", "adapter": "A", "reducer": "R"}[form]
    tail = f"X{size_code(size2)}" if size2 is not None else ""
    if style == "anvil":
        return f"{series}{code}{form_code}{tail}"
    if style == "merit":
        return f"{series}-{code}{form_code}{tail}"
    if style == "cooper":
        return f"{series}{form_code}-{code}{tail}"
    if style == "ward":
        return f"{series}-{form_code}{code}{tail}"
    return f"{form_code}{code}-{series}{tail}"


# ------------------------------------------------------------------ rendering

_styles = getSampleStyleSheet()
_H1 = ParagraphStyle("fh1", parent=_styles["Heading1"], fontSize=15, spaceAfter=4)
_H2 = ParagraphStyle("fh2", parent=_styles["Heading2"], fontSize=11, spaceBefore=10,
                     spaceAfter=4)
_P = ParagraphStyle("fp", parent=_styles["BodyText"], fontSize=8.5, leading=11)

_GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#888888")),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dddddd")),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 7.2),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
])


def fmt_size(size: float, metric: bool) -> str:
    if metric:
        return f"{size * 25.4:.1f} mm"
    return f'{SIZE_FRACTIONS.get(size, f"{size:g}")} in'


def fmt_pressure(psi: float, bar: bool) -> str:
    return f"{psi / 14.5038:.1f} bar" if bar else f"{psi:.0f} psi"


def fmt_temp(degf: float, celsius: bool) -> str:
    return f"{(degf - 32) * 5 / 9:.0f} degC" if celsius else f"{degf:.0f} degF"


def render_catalogue(path: Path, family: dict, parts: list[dict],
                     revision: str = "A") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    u = family["display"]
    doc = SimpleDocTemplate(
        str(path), pagesize=A4, leftMargin=16 * MM, rightMargin=16 * MM,
        topMargin=14 * MM, bottomMargin=14 * MM,
        title=f"{family['manufacturer']} {family['series']} Catalogue")

    flow = [
        Paragraph(f"{family['manufacturer']} — {family['series']} Series "
                  f"{DISPLAY[family['body_material']]} {DISPLAY[family['form_factor']]} "
                  f"Fittings, {DISPLAY[family['pressure_class']]}", _H1),
        Paragraph(f"Product Catalogue · Document {family['doc_no']} · "
                  f"Revision {revision}", _P),
        Spacer(1, 6),
        Paragraph(
            f"The {family['series']} Series is a range of "
            f"{DISPLAY[family['body_material']]} {DISPLAY[family['form_factor']].lower()} "
            f"fittings rated {DISPLAY[family['pressure_class']]}. Ends are "
            f"{DISPLAY[family['end_connection_1']].lower()} by "
            f"{DISPLAY[family['end_connection_2']].lower()}. "
            f"{'All parts are certified lead free to NSF 372. ' if family['lead_free_compliant'] else ''}"
            f"Supplied in a {DISPLAY[family['finish']].lower()} finish.", _P),
        Paragraph("Specifications", _H2),
    ]

    spec_rows = [["Parameter", "Value", "Conditions"],
                 ["Body Material", DISPLAY[family["body_material"]], "Wrought"],
                 ["Pressure Class", DISPLAY[family["pressure_class"]], "ASME designation"],
                 ["Max Temperature",
                  fmt_temp(family["temperature_rating_max"], u["celsius"]), "Continuous"],
                 ["End Connection 1", DISPLAY[family["end_connection_1"]], "Inlet"],
                 ["End Connection 2", DISPLAY[family["end_connection_2"]], "Outlet"],
                 ["Finish", DISPLAY[family["finish"]], "As supplied"],
                 ["Lead Free",
                  "Yes" if family["lead_free_compliant"] else "No", "NSF 372"]]
    if family.get("thread_standard"):
        spec_rows.insert(4, ["Thread Standard", DISPLAY[family["thread_standard"]],
                             "Taper thread"])
    if family.get("wall_schedule"):
        spec_rows.append(["Wall Schedule", DISPLAY[family["wall_schedule"]], "Nominal"])
    flow.append(Table(spec_rows, colWidths=[50 * MM, 62 * MM, 66 * MM], style=_GRID,
                      repeatRows=1))

    flow.append(Paragraph("Dimensions and Ordering", _H2))
    head = ["Part Number", "Size", "Size 2", "Max Working Pressure", "Bend Angle",
            "Weight"]
    rows = [head]
    for p in parts:
        rows.append([
            p["mpn"],
            fmt_size(p["nominal_size"], u["metric"]),
            fmt_size(p["nominal_size_secondary"], u["metric"])
            if p.get("nominal_size_secondary") else "—",
            fmt_pressure(p["max_working_pressure"], u["bar"]),
            f"{p['bend_angle']:g}" if p.get("bend_angle") else "—",
            f"{p['weight_lb']:.2f} lb",
        ])
    flow.append(Table(rows, colWidths=[44 * MM, 24 * MM, 24 * MM, 36 * MM,
                                       22 * MM, 22 * MM], style=_GRID, repeatRows=1))
    flow.append(Spacer(1, 8))
    flow.append(Paragraph(
        "Notes: Maximum working pressure is non-shock cold working pressure at "
        "100 degF. Ratings decrease with temperature; consult the derating table "
        f"in {family['doc_no']}-DR. Dimensions are nominal.", _P))
    doc.build(flow)


# ------------------------------------------------------------------ generation


def _fragment(part: dict, family: dict, rng: random.Random) -> str:
    """The abbreviation soup a distributor actually holds."""
    bits = [fmt_size(part["nominal_size"], False).replace(" in", "IN")]
    if part.get("nominal_size_secondary"):
        bits.append("X")
        bits.append(fmt_size(part["nominal_size_secondary"], False).replace(" in", "IN"))
    bits.append(MATERIAL_ABBR[family["body_material"]])
    if part.get("bend_angle"):
        bits.append(f"{part['bend_angle']:g}")
    bits.append(FORM_ABBR[family["form_factor"]])
    bits.append(END_ABBR[family["end_connection_1"]])
    if family["end_connection_2"] != family["end_connection_1"]:
        bits.append("X")
        bits.append(END_ABBR[family["end_connection_2"]])
    bits.append(family["pressure_class"].replace("class_", "") + "#")
    if family.get("finish") and FINISH_ABBR[family["finish"]]:
        bits.append(FINISH_ABBR[family["finish"]])
    if family.get("wall_schedule") and rng.random() < 0.5:
        bits.append(family["wall_schedule"].replace("schedule_", "SCH"))
    text = " ".join(bits)
    if rng.random() < 0.3:
        text = text[: rng.choice([26, 30, 34])].rstrip()
    return text


def build_families(rng: random.Random, n: int = 40) -> list[dict]:
    families = []
    for i in range(n):
        manufacturer, style, series_pool = MANUFACTURERS[i % len(MANUFACTURERS)]
        series = f"{series_pool[(i // len(MANUFACTURERS)) % len(series_pool)]}{i:02d}"
        material = rng.choice(MATERIALS)
        form = rng.choice(FORMS)
        end1 = rng.choice(ENDS)
        end2 = end1 if rng.random() < 0.65 else rng.choice(ENDS)
        pressure_class = rng.choice(CLASSES_BY_MATERIAL[material])
        threaded = end1 in THREADED_ENDS or end2 in THREADED_ENDS
        families.append({
            "family_id": f"fit{i:03d}",
            "manufacturer": manufacturer,
            "mpn_style": style,
            "series": series,
            "doc_no": f"CAT-{2000 + i}",
            "body_material": material,
            "form_factor": form,
            "end_connection_1": end1,
            "end_connection_2": end2,
            "pressure_class": pressure_class,
            "temperature_rating_max": rng.choice(TEMP_BY_MATERIAL[material]),
            "thread_standard": rng.choice(THREAD_STANDARDS) if threaded else None,
            "wall_schedule": rng.choice(SCHEDULES) if material not in PLASTICS
                             and rng.random() < 0.5 else None,
            "lead_free_compliant": material in ("brass", "bronze") or rng.random() < 0.5,
            "finish": rng.choice(FINISHES),
            "display": {
                "metric": rng.random() < 0.25,
                "bar": rng.random() < 0.2,
                "celsius": rng.random() < 0.2,
            },
        })
    return families


def build_parts(family: dict, rng: random.Random) -> list[dict]:
    sizes = sorted(rng.sample(SIZES, rng.randint(5, 9)))
    ceiling = CLASS_CEILING[family["pressure_class"]]
    parts = []
    for index, size in enumerate(sizes):
        secondary = None
        if family["form_factor"] in REDUCING_FORMS:
            smaller = [s for s in SIZES if s < size]
            if not smaller:
                continue
            secondary = rng.choice(smaller)
        # smaller sizes carry a higher working pressure, as they really do
        pressure = min(ceiling, round(ceiling * (1.0 if size <= 1.0 else 0.8), 0))
        parts.append({
            "mpn": make_mpn(family["mpn_style"], family["series"], size, secondary,
                            family["form_factor"], index),
            "manufacturer": family["manufacturer"],
            "family_id": family["family_id"],
            "nominal_size": size,
            "nominal_size_secondary": secondary,
            "form_factor": family["form_factor"],
            "bend_angle": (rng.choice([45, 90]) if family["form_factor"] == "elbow"
                           else None),
            "end_connection_1": family["end_connection_1"],
            "end_connection_2": family["end_connection_2"],
            "body_material": family["body_material"],
            "pressure_class": family["pressure_class"],
            "max_working_pressure": float(pressure),
            "temperature_rating_max": float(family["temperature_rating_max"]),
            "thread_standard": family["thread_standard"],
            "wall_schedule": family["wall_schedule"],
            "lead_free_compliant": family["lead_free_compliant"],
            "finish": family["finish"],
            "weight_lb": round(0.08 * size ** 2.2 + 0.05, 2),
        })
    return parts


def labels_for(part: dict) -> dict:
    out = {}
    for key in LABEL_KEYS:
        value = part.get(key)
        if value is None:
            continue
        out[key] = {"value": value, "unit": UNITS.get(key)}
    return out


def distributor_specs(part: dict, family: dict, corrupt_key: str | None,
                      rng: random.Random, source_type: str) -> dict:
    from sourced.corpus.generate import LISTING_ERROR_RATE, _corrupt_generic

    u = family["display"]
    specs = {
        "Nominal Size": fmt_size(part["nominal_size"], u["metric"]),
        "Fitting Type": DISPLAY[part["form_factor"]],
        "End Connection 1": DISPLAY[part["end_connection_1"]],
        "End Connection 2": DISPLAY[part["end_connection_2"]],
        "Body Material": DISPLAY[part["body_material"]],
        "Pressure Class": DISPLAY[part["pressure_class"]],
        "Max Working Pressure": fmt_pressure(part["max_working_pressure"], u["bar"]),
        "Max Temperature": fmt_temp(part["temperature_rating_max"], u["celsius"]),
        "Finish": DISPLAY[part["finish"]],
        "Lead Free": "Yes" if part["lead_free_compliant"] else "No",
    }
    if part.get("nominal_size_secondary"):
        specs["Size 2"] = fmt_size(part["nominal_size_secondary"], u["metric"])
    if part.get("thread_standard"):
        specs["Thread Standard"] = DISPLAY[part["thread_standard"]]
    if part.get("wall_schedule"):
        specs["Wall Schedule"] = DISPLAY[part["wall_schedule"]]

    if corrupt_key == "max_working_pressure":
        specs["Max Working Pressure"] = fmt_pressure(
            part["max_working_pressure"] * rng.choice([0.5, 2.0]), u["bar"])

    rate = LISTING_ERROR_RATE.get(source_type, 0.0)
    protected = {"Nominal Size"}
    for key in list(specs):
        if key in protected or (corrupt_key and key == "Max Working Pressure"):
            continue
        if rng.random() < rate:
            specs[key] = _corrupt_generic(specs[key], rng)
    return specs


def build(data: Path, rng: random.Random, n_families: int = 40) -> tuple[list, list]:
    """Render the fitting corpus. Returns (records, sources)."""
    from sourced.corpus.generate import (_split_for, render_distributor_page)

    pdf_dir, page_dir = data / "pdfs", data / "pages"
    families = build_families(rng, n_families)
    all_parts: list[dict] = []
    fam_parts: dict[str, list[dict]] = {}
    for family in families:
        parts = build_parts(family, rng)
        fam_parts[family["family_id"]] = parts
        all_parts.extend(parts)

    norm = {p["mpn"]: normalise_text(p["mpn"]) for p in all_parts}
    collisions = {a for a, na in norm.items()
                  for b, nb in norm.items() if a != b and na in nb}

    cohorts: dict[str, str] = {}
    for family in families:
        eligible = [p for p in fam_parts[family["family_id"]]
                    if p["mpn"] not in collisions]
        rng.shuffle(eligible)
        take = eligible[:4]
        if take:
            cohorts[take[0]["mpn"]] = "sibling_trap"
        for part in take[1:3]:
            cohorts[part["mpn"]] = "distributor_only"
        if len(take) > 3 and rng.random() < 0.6:
            cohorts[take[3]["mpn"]] = "conflict"
    for part in all_parts:
        cohorts.setdefault(part["mpn"], "normal")
    plain = [p for p in all_parts if cohorts[p["mpn"]] == "normal"]
    for part in rng.sample(plain, k=int(0.10 * len(plain))):
        cohorts[part["mpn"]] = "contradicted"

    sources: list[dict] = []
    part_sources: dict[str, list[dict]] = {p["mpn"]: [] for p in all_parts}

    for family in families:
        parts = fam_parts[family["family_id"]]
        listed = [p for p in parts
                  if cohorts[p["mpn"]] not in ("sibling_trap", "distributor_only")]
        if not listed:
            listed = parts[:1]
        sid = f"{family['family_id']}_cat"
        path = pdf_dir / f"{sid}.pdf"
        render_catalogue(path, family, listed)
        sources.append({"source_id": sid, "source_type": "manufacturer_datasheet",
                        "authority_rank": 1, "uri": config.store_uri(path),
                        "family_id": family["family_id"]})
        for part in listed:
            part_sources[part["mpn"]].append(
                {"source_id": sid, "source_type": "manufacturer_datasheet"})

        conflicted = [p for p in parts
                      if cohorts[p["mpn"]] == "conflict" and p in listed]
        if conflicted:
            revised = []
            for part in conflicted:
                copy = dict(part)
                copy["max_working_pressure"] = round(
                    part["max_working_pressure"] * rng.choice([0.5, 1.5]), 0)
                revised.append(copy)
            rsid = f"{family['family_id']}_cat_revB"
            rpath = pdf_dir / f"{rsid}.pdf"
            render_catalogue(rpath, family, revised, revision="B")
            sources.append({"source_id": rsid,
                            "source_type": "manufacturer_datasheet",
                            "authority_rank": 1, "uri": config.store_uri(rpath),
                            "family_id": family["family_id"]})
            for part in conflicted:
                part_sources[part["mpn"]].append(
                    {"source_id": rsid, "source_type": "manufacturer_datasheet"})

    for part in all_parts:
        cohort = cohorts[part["mpn"]]
        if cohort == "sibling_trap":
            continue
        family = next(f for f in families if f["family_id"] == part["family_id"])
        if cohort == "distributor_only":
            n_pages = rng.choice([1, 1, 2])
        elif cohort == "contradicted":
            n_pages = 1
        else:
            n_pages = 1 if rng.random() < 0.55 else 0
        for n in range(n_pages):
            corrupt = "max_working_pressure" if cohort == "contradicted" else None
            stype = "distributor_page" if rng.random() < 0.7 else "marketplace"
            sid = f"{normalise_text(part['mpn'])}_pg{n}"
            path = page_dir / f"{sid}.json"
            render_distributor_page(
                path, part, distributor_specs(part, family, corrupt, rng, stype),
                stype, rng.choice(["Delta Pipe Supply", "Keystone Industrial",
                                   "Northgate Valve"]),
                title=(f"{part['manufacturer']} {part['mpn']} - "
                       f"{fmt_size(part['nominal_size'], False)} "
                       f"{DISPLAY[part['body_material']]} "
                       f"{DISPLAY[part['form_factor']]}"))
            sources.append({"source_id": sid, "source_type": stype,
                            "authority_rank": config.AUTHORITY_RANK[stype],
                            "uri": config.store_uri(path),
                            "family_id": part["family_id"]})
            part_sources[part["mpn"]].append({"source_id": sid, "source_type": stype})

    records = []
    for part in all_parts:
        family = next(f for f in families if f["family_id"] == part["family_id"])
        show_manufacturer = rng.random() >= 0.15
        records.append({
            "sku_input": {
                "mpn": part["mpn"],
                "manufacturer": part["manufacturer"] if show_manufacturer else None,
                "description_fragment": _fragment(part, family, rng),
                "internal_sku": f"SKU-{normalise_text(part['mpn'])[:10]}",
            },
            "category": "pipe_fitting",
            "family_id": part["family_id"],
            "cohort": cohorts[part["mpn"]],
            "expected_sources": part_sources[part["mpn"]],
            "split": _split_for(part["mpn"]),
            "labels": labels_for(part),
            "label_provenance": "synthetic_generator_ground_truth",
            "hand_audited": False,
        })
    return records, sources
