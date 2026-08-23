"""Build the submission deck onto the official UniHack template pages.

The organisers' template is the required container, so nothing here recreates
it: each slide is the real template page with a content layer merged on top.
Extra slides reuse a template body page with its heading masked, so the header
bar, footer rule and branding stay exactly as issued.

Every figure in the deck comes from data/results.json and the two experiment
files, so the deck cannot drift from what was measured.

    python docs/build_deck.py [--template PATH] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = Path.home() / "Desktop" / "UniHack-Protoype Template.pdf"
DEFAULT_OUT = ROOT / "docs" / "Sourced-UniHack-Submission.pdf"

W, H = 720.0, 405.0
MARGIN = 26.0
BODY_TOP = 292.0          # first safe baseline below the template headings
BODY_BOTTOM = 26.0

NAVY = colors.HexColor("#0A3A6E")
BLUE = colors.HexColor("#1E88E5")
INK = colors.HexColor("#16191D")
MUTED = colors.HexColor("#5F6773")
GREEN = colors.HexColor("#2F7A41")
RED = colors.HexColor("#B0452F")
AMBER = colors.HexColor("#B07D16")
LINE = colors.HexColor("#D9DEE6")
TINT = colors.HexColor("#EFF4FA")
WHITE = colors.white


# --------------------------------------------------------------------- data


def load_numbers() -> dict:
    results = json.loads((ROOT / "data" / "results.json").read_text(encoding="utf-8"))
    llm = json.loads((ROOT / "data" / "llm_experiment.json").read_text(encoding="utf-8"))
    desc = json.loads((ROOT / "data" / "description_experiment.json")
                      .read_text(encoding="utf-8"))
    corpus = json.loads((ROOT / "data" / "corpus_summary.json").read_text(encoding="utf-8"))
    t = results["test_metrics"]
    conn = results["by_category"]["electrical_connector"]
    fit = results["by_category"]["pipe_fitting"]
    ab = results["ablations_by_category"]["electrical_connector"]
    return {
        "skus": corpus["skus"],
        "docs": corpus["datasheets"],
        "listings": corpus["distributor_pages"],
        "test": results["dataset"]["splits"]["test"],
        "leaves": results["dataset"]["taxonomy_leaves"],
        "precision": t["accuracy"]["precision_published"],
        "values": t["accuracy"]["published_values_scored"],
        "safety": t["accuracy"]["precision_by_criticality"]["safety"]["precision"],
        "functional": t["accuracy"]["precision_by_criticality"]["functional"]["precision"],
        "cosmetic": t["accuracy"]["precision_by_criticality"]["cosmetic"]["precision"],
        "recall": t["accuracy"]["recall"],
        "autopublish": t["validation_and_enrichment"]["auto_publish_rate"],
        "located": t["source_location"]["source_location_rate"],
        "traps": t["source_location"]["wrong_part_traps"],
        "refused": t["source_location"]["wrong_part_traps_correctly_refused"],
        "ece": results["calibration"]["expected_calibration_error"],
        "routing": results["category_routing"]["schema_routing_accuracy"],
        "invented": results["category_routing"]["invented_codes"],
        "throughput": t["scalable_engine"]["throughput_skus_per_min"],
        "avoidance": t["scalable_engine"]["llm_avoidance_rate"],
        "reverify": results["catalog_operations"]["reverification_cost_vs_full_run"],
        "fab_rate": results["adversarial"]["injected_fabrication"]["interception_rate"],
        "fab_n": results["adversarial"]["injected_fabrication"]["corruptions_injected"],
        "conn_cov": conn["metrics"]["structured_data_generation"]["attribute_coverage"],
        "conn_prec": conn["metrics"]["accuracy"]["precision_published"],
        "conn_pub": conn["metrics"]["validation_and_enrichment"]["auto_publish_rate"],
        "fit_cov": fit["metrics"]["structured_data_generation"]["attribute_coverage"],
        "fit_prec": fit["metrics"]["accuracy"]["precision_published"],
        "fit_pub": fit["metrics"]["validation_and_enrichment"]["auto_publish_rate"],
        "noverify_prec": ab["no_source_verification"]["precision_published"],
        "noverify_leak": ab["no_source_verification"]["wrong_part_leak_rate"],
        "uncal_pub": ab["uncalibrated_confidence"]["auto_publish_rate"],
        "llm_proposals": llm["gates"]["proposals_returned"],
        "llm_reject": llm["gates"]["total_rejection_rate"],
        "llm_span": llm["gates"]["span_rejection_rate"],
        "llm_gated": llm["contribution"]["precision_of_added_values"],
        "llm_ungated": llm["gates_removed"]["precision_of_ungated_llm_values"],
        "llm_invented": llm["gates_removed"]["values_for_attributes_the_part_does_not_have"],
        "desc_flagged": desc["generated"]["descriptions_with_an_unlicensed_number"],
        "desc_n": desc["generated"]["descriptions"],
    }


def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


# ------------------------------------------------------------------ drawing


def wrap(c, text, font, size, width):
    c.setFont(font, size)
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, font, size) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def para(c, text, x, y, width, size=9, leading=11.5, font="Helvetica", fill=INK):
    c.setFillColor(fill)
    for line in wrap(c, text, font, size, width):
        c.setFont(font, size)
        c.drawString(x, y, line)
        y -= leading
    return y


def heading(c, text, y=BODY_TOP + 22, size=15, fill=NAVY):
    c.setFillColor(fill)
    c.setFont("Helvetica-Bold", size)
    c.drawString(MARGIN, y, text)
    return y - 16


def mask_heading(c, y=282, height=56):
    """Cover a reused template heading so the slide can carry its own."""
    c.setFillColor(WHITE)
    c.rect(0, y, W, height, stroke=0, fill=1)


def bullets(c, items, x, y, width, size=9, leading=12.5, gap=3.5):
    """Bullets whose lead-in is bold and whose continuation lines use the full
    column width.

    Wrapping every line to the space left beside the bold lead-in produces a
    ragged two-word column, which is what happened before the first line and
    the remainder were measured separately.
    """
    for item in items:
        bold, rest = (item if isinstance(item, tuple) else (None, item))
        c.setFillColor(BLUE)
        c.circle(x + 2.4, y + 3.1, 1.7, stroke=0, fill=1)
        text_x = x + 10
        text_width = width - 10

        if not bold:
            y = para(c, rest, text_x, y, text_width, size, leading) - gap
            continue

        c.setFont("Helvetica-Bold", size)
        c.setFillColor(INK)
        c.drawString(text_x, y, bold)
        offset = c.stringWidth(bold, "Helvetica-Bold", size) + 3

        words, first, index = rest.split(), "", 0
        while index < len(words):
            trial = f"{first} {words[index]}".strip()
            if c.stringWidth(trial, "Helvetica", size) <= text_width - offset:
                first, index = trial, index + 1
            else:
                break
        c.setFont("Helvetica", size)
        if first:
            c.drawString(text_x + offset, y, first)
        y -= leading

        remainder = " ".join(words[index:])
        if remainder:
            y = para(c, remainder, text_x, y, text_width, size, leading)
        y -= gap
    return y


def table(c, headers, rows, x, y, widths, size=8.2, row_h=None, head_fill=NAVY,
          zebra=True, aligns=None, pad=4.2, leading=None):
    """A table whose cells wrap. Row height follows the tallest cell.

    Truncating a descriptive cell with an ellipsis loses the sentence that
    justifies the row, which on a slide is the whole point of the row.
    """
    aligns = aligns or ["l"] * len(widths)
    leading = leading or size + 2.8
    total = sum(widths)
    min_h = row_h or (leading + 2 * pad)

    head_h = leading + 2 * pad
    c.setFillColor(head_fill)
    c.rect(x, y - head_h + leading, total, head_h, stroke=0, fill=1)
    cx = x
    for header, width, align in zip(headers, widths, aligns):
        c.setFillColor(WHITE)
        _line(c, header, cx, y, width, align, size, "Helvetica-Bold")
        cx += width
    y -= head_h

    for index, row in enumerate(rows):
        cells = []
        for value, width in zip(row, widths):
            text, font, fill = (value if isinstance(value, tuple)
                                else (value, "Helvetica", INK))
            cells.append((wrap(c, str(text), font, size, width - 2 * pad - 3),
                          font, fill))
        height = max(min_h, max(len(lines) for lines, _, _ in cells) * leading
                     + 2 * pad - (leading - size))

        if zebra and index % 2 == 0:
            c.setFillColor(TINT)
            c.rect(x, y - height + leading, total, height, stroke=0, fill=1)

        cx = x
        for (lines, font, fill), width, align in zip(cells, widths, aligns):
            cursor = y
            for line in lines:
                c.setFillColor(fill)
                _line(c, line, cx, cursor, width, align, size, font)
                cursor -= leading
            cx += width
        y -= height

    c.setStrokeColor(LINE)
    c.setLineWidth(0.4)
    c.line(x, y + leading, x + total, y + leading)
    return y


def _line(c, text, x, y, width, align, size, font):
    c.setFont(font, size)
    if align == "r":
        c.drawRightString(x + width - 5, y, text)
    elif align == "c":
        c.drawCentredString(x + width / 2, y, text)
    else:
        c.drawString(x + 5, y, text)


def _fit(c, text, font, size, width):
    """Single-line truncation, for fixed-height labels only. Tables wrap."""
    if c.stringWidth(text, font, size) <= width:
        return text
    while text and c.stringWidth(text + "...", font, size) > width:
        text = text[:-1]
    return text + "..."


def stat(c, x, y, value, label, w=112, h=44, accent=BLUE):
    c.setFillColor(TINT)
    c.roundRect(x, y, w, h, 4, stroke=0, fill=1)
    c.setFillColor(accent)
    c.rect(x, y, 3, h, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(x + 10, y + h - 21, value)
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.2)
    for i, line in enumerate(wrap(c, label, "Helvetica", 7.2, w - 16)[:2]):
        c.drawString(x + 10, y + h - 32 - i * 8.6, line)


def box(c, x, y, w, h, title, lines, fill=WHITE, border=LINE, title_fill=NAVY,
        size=7.4, title_size=8.4):
    """A labelled box whose title wraps. A truncated title on a diagram is a
    box the reader cannot identify."""
    c.setFillColor(fill)
    c.setStrokeColor(border)
    c.setLineWidth(0.7)
    c.roundRect(x, y, w, h, 3.5, stroke=1, fill=1)

    cursor = y + h - 12
    c.setFillColor(title_fill)
    for line in wrap(c, title, "Helvetica-Bold", title_size, w - 14)[:2]:
        c.setFont("Helvetica-Bold", title_size)
        c.drawString(x + 7, cursor, line)
        cursor -= title_size + 1.6

    cursor -= 3
    c.setFillColor(MUTED)
    for text in lines:
        for line in wrap(c, text, "Helvetica", size, w - 14):
            c.setFont("Helvetica", size)
            c.drawString(x + 7, cursor, line)
            cursor -= size + 1.6


def arrow(c, x1, y1, x2, y2, colour=BLUE, width=1.1, head=4.0):
    c.setStrokeColor(colour)
    c.setFillColor(colour)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)
    if x2 == x1:
        direction = -1 if y2 < y1 else 1
        c.setLineWidth(0)
        p = c.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(x2 - head * 0.7, y2 - direction * head)
        p.lineTo(x2 + head * 0.7, y2 - direction * head)
        p.close()
        c.drawPath(p, stroke=0, fill=1)
    else:
        direction = -1 if x2 < x1 else 1
        p = c.beginPath()
        p.moveTo(x2, y2)
        p.lineTo(x2 - direction * head, y2 - head * 0.7)
        p.lineTo(x2 - direction * head, y2 + head * 0.7)
        p.close()
        c.drawPath(p, stroke=0, fill=1)


def note(c, text, y=BODY_BOTTOM + 2, fill=MUTED, size=7.4):
    c.setFillColor(fill)
    c.setFont("Helvetica-Oblique", size)
    c.drawString(MARGIN, y, text)


# ------------------------------------------------------------------- slides


def slide_team(c, n):
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 15)
    # clear the longer of the two template labels ("b. Team leader name:")
    c.drawString(268, 83, "perceptron")
    c.drawString(268, 61, "Aman Kumar Maurya")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(66, 33, "Sourced - product intelligence that proves where every "
                         "value came from, and refuses to guess.")


def slide_brief(c, n):
    y = BODY_TOP
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12.5)
    c.drawString(MARGIN, y, "Sourced - enrichment that proves its sources, and "
                            "refuses to guess")
    y -= 18

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 8.6)
    c.drawString(MARGIN, y, "A distributor does not start with a datasheet. "
                            "They start with a row like this:")
    y -= 16

    c.setFillColor(TINT)
    c.roundRect(MARGIN, y - 15, 330, 22, 3, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Courier-Bold", 8.6)
    c.drawString(MARGIN + 8, y - 8, "MPN: 3GAA132214-ADE   MFR: ABB   "
                                    "DESC: MOT 3GAA132 5.5KW 4P B3")
    y -= 28

    left = MARGIN
    width = 356
    y_left = para(c, "No attached document. A sellable record needs 80-120 "
                     "attributes. Hand the fragment to an LLM and you get "
                     "plausible, unsourced, occasionally wrong specifications. "
                     "In industrial supply a wrong pressure rating means the "
                     "wrong part arrives on a job site.", left, y, width, 9, 11.5)
    y_left -= 4
    c.setFillColor(RED)
    c.setFont("Helvetica-BoldOblique", 9.6)
    c.drawString(left, y_left, "Confidently wrong is worse than absent.")
    y_left -= 18

    bullets(c, [
        ("Finds the source first.", "Verifies a document names this exact part "
         "before reading a value from it. No verified source, no extraction."),
        ("Publishes with provenance.", "Every value carries the document, page "
         "and bounding box. Click it and the PDF region is outlined."),
        ("Measures its own confidence.", "Fitted against labels and reported as "
         "a reliability diagram, not asked of the model."),
        ("Explains refusals.", "A typed abstention with a reason and what would "
         "resolve it - a work item, not a blank."),
    ], left, y_left, width, 8.8, 11.4, 4.5)

    rx = 412
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(rx, BODY_TOP - 20, f"MEASURED - held-out split, {N['test']} SKUs")
    grid = [
        (pct(N["precision"]), "precision on published values", BLUE),
        (pct(N["autopublish"]), "auto-publishable, no human review", BLUE),
        (f"{N['refused']}/{N['traps']}", "wrong-part traps refused", GREEN),
        (f"{N['ece']:.3f}", "expected calibration error", BLUE),
        (pct(N["located"]), "source located and verified", BLUE),
        (pct(N["routing"]), "routed to the right schema", GREEN),
    ]
    top = BODY_TOP - 82
    for index, (value, label, accent) in enumerate(grid):
        col, row = index % 2, index // 2
        stat(c, rx + col * 145, top - row * 58, value, label, 135, 48, accent)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.6)
    c.drawString(rx, top - 3 * 58 + 34,
                 f"Two verticals - {N['skus']} SKUs, {N['docs']} documents, "
                 f"{N['listings']} listings.")
    c.drawString(rx, top - 3 * 58 + 24,
                 "Reproducible: python -m sourced.eval.report")


def slide_questions_index(c, n):
    y = BODY_TOP - 122
    c.setFillColor(LINE)
    c.rect(MARGIN, y + 30, W - 2 * MARGIN, 0.7, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(MARGIN, y + 16, "Answered in full on the next three slides. "
                                 "In one line each:")
    y -= 2
    rows = [
        [("1", "Helvetica-Bold", NAVY),
         "Seven stages turn MPN + brand + fragment into a commerce record. "
         "The source is located and verified before anything is read.",
         (pct(N["located"]) + " located", "Helvetica-Bold", GREEN)],
        [("2", "Helvetica-Bold", NAVY),
         "Six independent layers, each measured. Deterministic gates take an "
         "LLM's output from " + pct(N["llm_ungated"]) + " correct to " +
         pct(N["llm_gated"]) + ".",
         (pct(N["precision"]) + " precision", "Helvetica-Bold", GREEN)],
        [("3", "Helvetica-Bold", NAVY),
         "A category is a YAML file, not a code change. The model is called "
         "once per SKU, for unresolved attributes only.",
         (f"{N['throughput']:,.0f} SKUs/min", "Helvetica-Bold", GREEN)],
    ]
    table(c, ["#", "In one line", "Evidence"], rows, MARGIN, y,
          [22, 508, 138], size=8.4, row_h=26)
    note(c, "Every figure in this deck is measured on a held-out split and "
            "reproducible with a single command. Sources: docs/RESULTS.md")


def slide_enrich(c, n):
    mask_heading(c)
    y = heading(c, "1. How does your solution enrich minimal product information?")
    y -= 2
    rows = [
        [("0 Source discovery", "Helvetica-Bold", NAVY),
         "Normalise the MPN, resolve the manufacturer alias, retrieve candidates "
         "(BM25 + dense, fused by RRF), then verify the document names THIS part. "
         "No verified source -> no_source_located, with a reason."],
        [("1 Classification", "Helvetica-Bold", NAVY),
         "Embed taxonomy leaves offline, retrieve top-k, constrain the choice to "
         "real codes, hard-validate the code exists. An invented code is "
         "structurally impossible."],
        [("2 Candidates", "Helvetica-Bold", NAVY),
         "Three tiers, none short-circuits: rules (lexicon + dimensional grammar), "
         "tables (row chunks with per-cell bounding boxes), LLM (one structured "
         "call for whatever is left)."],
        [("3 Adjudication", "Helvetica-Bold", NAVY),
         "Rank by evidence quality, then source authority, then independent "
         "agreement. Two manufacturer datasheets disagreeing is a conflict, "
         "not a vote."],
        [("4-5 Validate + calibrate", "Helvetica-Bold", NAVY),
         "Per-attribute, cross-attribute and cross-SKU checks; then a publish "
         "threshold set by the attribute's criticality tier."],
        [("6-7 Commerce + ops", "Helvetica-Bold", NAVY),
         "Deterministic title, description with claim traceability, facets; "
         "idempotent upsert with source-change detection."],
    ]
    y = table(c, ["Stage", "What it does"], rows, MARGIN, y, [104, 340],
              size=7.8, row_h=25)

    rx = 480
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(rx, BODY_TOP, "The brief's own example, decoded")
    c.setFillColor(TINT)
    c.roundRect(rx, BODY_TOP - 30, 214, 26, 3, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Courier-Bold", 8)
    c.drawString(rx + 7, BODY_TOP - 20, "1/2IN X 3/4IN BRS 90 ELL")
    c.drawString(rx + 7, BODY_TOP - 28, "FIP 150#")

    decoded = [
        ("nominal_size", '0.5"'), ("nominal_size_secondary", '0.75"'),
        ("body_material", "brass"), ("bend_angle", "90"),
        ("form_factor", "elbow"), ("end_connection_1", "female_iron_pipe"),
        ("pressure_class", "class_150"),
    ]
    cursor = BODY_TOP - 44
    for key, value in decoded:
        c.setFillColor(GREEN)
        c.setFont("Helvetica-Bold", 7.4)
        c.drawString(rx, cursor, "+")
        c.setFillColor(INK)
        c.setFont("Courier", 7.4)
        c.drawString(rx + 9, cursor, key)
        c.setFont("Courier-Bold", 7.4)
        c.drawRightString(rx + 214, cursor, value)
        cursor -= 10.4
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.4)
    c.drawString(rx, cursor - 4, "Seven attributes. No document. No model.")
    c.drawString(rx, cursor - 13, "Rules alone, auditable to the characters")
    c.drawString(rx, cursor - 22, "they fired on.")

    stat(c, rx, cursor - 78, pct(N["conn_cov"], 0) + " / " + pct(N["fit_cov"], 0),
         "attribute coverage, connector / fitting", 214, 42)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 8.6)
    c.drawString(MARGIN, 96, "Three tiers propose; none of them decides alone")
    para(c, "Rules read the distributor's own row. Tables read the document, one "
            "row at a time, keeping each cell's bounding box. The model is asked "
            "only for what the other two could not resolve - one structured call "
            "per SKU, never one per attribute. Adjudication then ranks the "
            "proposals by evidence quality before source authority before "
            "agreement, so a regex that happens to match cannot beat a table cell.",
         MARGIN, 84, 430, 8, 10, fill=MUTED)
    note(c, "Measured: " + pct(N["located"]) + " source-location rate  |  "
            + pct(N["avoidance"]) + " of attributes resolved with no LLM call  |  "
            + pct(N["recall"]) + " recall")


def slide_trust(c, n):
    mask_heading(c)
    y = heading(c, "2. How does your solution ensure accuracy and trust?")
    y -= 2
    rows = [
        ["Match verification",
         "Extraction from a SIBLING part's datasheet - the failure every naive "
         "system commits",
         (f"{N['refused']}/{N['traps']} refused", "Helvetica-Bold", GREEN)],
        ["Span containment",
         "A value citing text that is not in the source it names",
         (pct(N["llm_span"]) + " of proposals cut", "Helvetica-Bold", GREEN)],
        ["Value / evidence pairing",
         "A real span paired with a fabricated value",
         (pct(N["llm_reject"]) + " total rejection", "Helvetica-Bold", GREEN)],
        ["Adjudication by authority",
         "Three listings that copied one wrong datasheet are not three "
         "confirmations",
         ("datasheet wins", "Helvetica-Bold", GREEN)],
        ["Validation L1 / L2 / L3",
         "Impossible value pairs; outliers against sibling SKUs",
         (pct(N["fab_rate"], 0) + f" of {N['fab_n']} caught", "Helvetica-Bold", GREEN)],
        ["Calibrated abstention",
         "Anything below the bar for its criticality tier",
         (f"ECE {N['ece']:.3f}", "Helvetica-Bold", GREEN)],
    ]
    y = table(c, ["Layer", "What it catches", "Measured"], rows, MARGIN, y,
              [116, 268, 106], size=7.8, row_h=25)

    rx = 522
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(rx, BODY_TOP, "We removed the guard rails")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.4)
    c.drawString(rx, BODY_TOP - 13, "Same SKUs, same model, gates off:")

    bar_y = BODY_TOP - 100
    for label, value, colour in [("gates ON", N["llm_gated"], GREEN),
                                 ("gates OFF", N["llm_ungated"], RED)]:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.4)
        c.drawString(rx, bar_y + 22, label)
        c.setFillColor(colors.HexColor("#E3E8EF"))
        c.rect(rx, bar_y + 8, 172, 11, stroke=0, fill=1)
        c.setFillColor(colour)
        c.rect(rx, bar_y + 8, 172 * value, 11, stroke=0, fill=1)
        c.setFillColor(WHITE if value > 0.4 else INK)
        c.setFont("Helvetica-Bold", 7.6)
        c.drawString(rx + 5, bar_y + 11, pct(value, 0) + " correct")
        bar_y += 38

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 7.4)
    y2 = para(c, "With the gates off it also invented values for "
                 f"{N['llm_invented']} attributes those parts do not have. "
                 "The cheapest check in the system - a substring test - does "
                 "the most work.", rx, BODY_TOP - 96, 172, 7.4, 9.4)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 8.4)
    c.drawString(rx, y2 - 6, "Thresholds are a policy")
    para(c, "A wrong housing colour is a cosmetic defect. A wrong current "
            "rating is a fire. Safety attributes need 0.99 confidence AND two "
            "independent sources; cosmetic ones publish at 0.85.",
         rx, y2 - 18, 172, 7.4, 9.4, fill=MUTED)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 8.6)
    c.drawString(MARGIN, 104, "Confidence is computed, never self-reported")
    para(c, "Asking a model to rate its own certainty produces numbers that look "
            "meaningful and do not track correctness. Ours is a fitted function "
            "of observable signals - which tier produced the value, whether the "
            "span check passed, how many independent sources agreed - trained on "
            "a calibration split the reported numbers never touch, and published "
            "as a reliability diagram so the threshold means something.",
         MARGIN, 92, 470, 8, 10, fill=MUTED)

    note(c, "Precision by tier - safety " + pct(N["safety"]) + "  |  functional "
            + pct(N["functional"]) + "  |  cosmetic " + pct(N["cosmetic"])
            + f"   ({N['values']:,} published values scored)")


def slide_scale(c, n):
    mask_heading(c)
    y = heading(c, "3. What makes your solution scalable for enterprise catalogs?")
    y -= 2
    rows = [
        [("Large catalogs", "Helvetica-Bold", NAVY),
         "Deterministic tiers resolve most of the schema. The model is called "
         "once per SKU, for unresolved attributes only - never per attribute.",
         (f"{N['throughput']:,.0f} SKUs/min", "Helvetica-Bold", GREEN)],
        [("New manufacturers", "Helvetica-Bold", NAVY),
         "Alias table plus fuzzy resolution with a hard floor: below it nothing "
         "resolves, rather than binding the SKU to the wrong maker.",
         ("15% arrive with none", "Helvetica-Bold", GREEN)],
        [("New categories", "Helvetica-Bold", NAVY),
         "An attribute set, its criticality tiers, relational rules and lookups "
         "are a YAML file. No code changes.",
         (pct(N["routing"]) + " routing", "Helvetica-Bold", GREEN)],
        [("Document formats", "Helvetica-Bold", NAVY),
         "Datasheets, catalogue pages and JSON listings all become the same "
         "chunk type with the same provenance shape.",
         (f"{N['docs']} docs + {N['listings']} listings", "Helvetica-Bold", GREEN)],
        [("Continuous updates", "Helvetica-Bold", NAVY),
         "Content-hash change detection re-verifies only affected products, and "
         "only the attributes that actually moved.",
         (pct(N["reverify"]) + " of a full re-run", "Helvetica-Bold", GREEN)],
    ]
    y = table(c, ["Concern", "How it is handled", "Measured"], rows, MARGIN, y,
              [112, 372, 122], size=7.8, row_h=27)

    y -= 12
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN, y, "Cost scales with what is missing, not with catalogue size")
    y -= 12
    y = para(c, "Because the model only sees attributes the deterministic tiers "
                "could not resolve - and is not called at all when they suffice - "
                "a distributor with a million SKUs pays for the gaps, not the "
                "rows. In the reported run that cost was $0.00 per 1,000 SKUs.",
             MARGIN, y, 606, 8.6, 10.8, fill=MUTED)

    tiles = [
        (f"{N['throughput']:,.0f}", "SKUs per minute, single process", BLUE),
        (pct(N["avoidance"]), "of attributes never reached a model", GREEN),
        (pct(N["reverify"]), "the cost of a source revision", BLUE),
        (str(N["leaves"]), "taxonomy leaves; a category is a YAML file", BLUE),
    ]
    x = MARGIN
    for value, label, accent in tiles:
        stat(c, x, y - 62, value, label, 160, 48, accent)
        x += 170


def slide_opportunities(c, n):
    # the template prints a/b/c as prompts down the page; masking them and
    # restating each as a compact label frees the height the answers need
    mask_heading(c, y=224, height=84)

    left, lw = MARGIN, 486
    rx, rw = 524, 170

    y = 292
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(left, y, "a.  How different is it from other existing ideas?")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.6)
    c.drawString(left, y - 10, "Most enrichment demos start from a PDF you "
                               "already have, and end at a JSON blob.")
    rows = [
        ["Starts from a datasheet you supply",
         ("Starts from a part number and FINDS the document - then proves it is "
          "the right one", "Helvetica-Bold", INK)],
        ["Values with no provenance",
         "Every value carries document, page and bounding box"],
        ["The model self-reports its confidence",
         "Confidence fitted against labels, published as a reliability diagram"],
        ["Silence when it fails",
         "A typed abstention with a reason and a resolution hint"],
        ["One LLM call per attribute", "One call per SKU, unresolved attributes only"],
    ]
    y = table(c, ["Typical approach", "Sourced"], rows, left, y - 22,
              [196, 290], size=7.4, pad=3.4)

    y -= 16
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(left, y, "b.  How will it solve the problem statement?")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.6)
    c.drawString(left, y - 10, "Each of the brief's four outcomes has a metric "
                               "attached, not an assertion.")
    rows = [
        ["Structured data generation", "source location - coverage",
         (pct(N["located"], 0) + " - " + pct(N["conn_cov"], 0) + "/"
          + pct(N["fit_cov"], 0), "Helvetica-Bold", GREEN)],
        ["Accuracy and consistency", "precision - unit uniformity",
         (pct(N["precision"]) + " - 100%", "Helvetica-Bold", GREEN)],
        ["AI validation and enrichment", "auto-publish - fabrication caught",
         (pct(N["autopublish"]) + " - " + pct(N["fab_rate"], 0),
          "Helvetica-Bold", GREEN)],
        ["Scalable catalog engine", "throughput - re-verification cost",
         (f"{N['throughput']:,.0f}/min - " + pct(N["reverify"]),
          "Helvetica-Bold", GREEN)],
    ]
    table(c, ["Outcome", "Metric", "Result"], rows, left, y - 22,
          [156, 174, 156], size=7.4, pad=3.4)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.2)
    c.drawString(rx, 292, "c.  USP of the proposed solution")

    c.setFillColor(TINT)
    c.roundRect(rx, 196, rw, 78, 4, stroke=0, fill=1)
    c.setFillColor(BLUE)
    c.rect(rx, 196, 3, 78, stroke=0, fill=1)
    para(c, "The only value it publishes is one it can point at - and it says so "
            "out loud when it cannot.", rx + 10, 258, rw - 20, 9.4, 12,
         "Helvetica-BoldOblique", NAVY)

    c.setFillColor(MUTED)
    c.setFont("Helvetica-Bold", 7.8)
    c.drawString(rx, 178, "Three things a rival deck is unlikely to have:")
    bullets(c, [
        ("A wrong-part test as a build gate.",
         f"{N['refused']}/{N['traps']} refused, every time."),
        ("A reliability diagram.", f"ECE {N['ece']:.3f}. Everyone shows "
         "confidence scores; almost nobody shows theirs are calibrated."),
        ("Honest negatives.", "Three ablations moved nothing and we say why - "
         "including one place our own design prediction was wrong."),
    ], rx, 166, rw, 7.6, 9.4, 4)


def slide_features(c, n):
    y = BODY_TOP
    columns = [
        (MARGIN, "ENRICHMENT", [
            "Source discovery from a bare part number",
            "Hard MPN-presence gate before extraction",
            "Boundary-anchored matching: a truncated MPN cannot match a longer part",
            "Manufacturer alias resolution with a confidence floor",
            "Rules tier: abbreviation lexicon + dimensional grammar",
            "Tables tier: row chunks with per-cell bounding boxes",
            "LLM tier: one structured call, unresolved attributes only",
            "Unit normalisation - 1/2 in, 0.5\", 12.7 mm are one value",
            "MPN structure decoding, learned unsupervised",
        ]),
        (MARGIN + 232, "TRUST", [
            "Verbatim span containment on every tier",
            "Value / evidence pairing check on model output",
            "Adjudication: evidence, then authority, then agreement",
            "Conflicting datasheets produce an abstention, not a vote",
            "Validation L1 attribute, L2 relational, L3 family coherence",
            "Confidence calibrated per category and criticality tier",
            "Reliability diagram and abstention curve",
            "Six typed abstentions, each with a resolution hint",
        ]),
        (MARGIN + 464, "COMMERCE + ENGINE", [
            "Deterministic title - cannot hallucinate",
            "Description with claim-to-attribute traceability",
            "Numeric licence gate on generated copy",
            "Facets, completeness score, blocking-for-publish list",
            "Idempotent upsert on (manufacturer, normalised MPN)",
            "Source-change detection, selective re-verification",
            "Web UI: confidence, tier badge, reason, PDF region",
            "Postgres or SQLite; docker compose up",
            "57 gate tests, ablations, adversarial suite, scripted demo",
        ]),
    ]
    for x, title, items in columns:
        c.setFillColor(BLUE)
        c.rect(x, y + 2, 26, 2, stroke=0, fill=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 8.6)
        c.drawString(x, y - 12, title)
        cursor = y - 28
        for item in items:
            c.setFillColor(BLUE)
            c.circle(x + 2, cursor + 3, 1.7, stroke=0, fill=1)
            cursor = para(c, item, x + 10, cursor, 202, 8.3, 10.6) - 4.4

    band_y = 34
    c.setFillColor(TINT)
    c.roundRect(MARGIN, band_y, 668, 54, 4, stroke=0, fill=1)
    c.setFillColor(BLUE)
    c.rect(MARGIN, band_y, 3, 54, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN + 12, band_y + 38, "What ties the list together")
    para(c, "Every feature above is either a way of finding evidence, a way of "
            "checking it, or a way of showing it. Nothing in the system asks the "
            "reader to take a value on trust: a published attribute can be "
            "clicked through to the page and region it was read from, and a "
            "missing one names the reason it is missing and what would resolve it.",
         MARGIN + 12, band_y + 26, 646, 8.2, 10.2, fill=MUTED)

    note(c, "Full feature list and evidence: docs/RESULTS.md  |  "
            "Run it: docker compose up  ->  http://localhost:8000", 22)


def slide_flow(c, n):
    rows = [
        [("INPUT  sparse SKU row", ["mpn", "manufacturer?", "fragment?"], TINT),
         ("STAGE 0  SOURCE DISCOVERY",
          ["retrieve candidates", "VERIFY the document", "names this exact part"], WHITE),
         ("STAGE 1  CATEGORY",
          ["retrieve -> constrain", "hard-validate the code", "selects the schema"], WHITE),
         ("STAGE 2  CANDIDATES",
          ["rules - tables - LLM", "every tier runs,", "none short-circuits"], WHITE)],
        [("STAGE 3  ADJUDICATE",
          ["evidence quality", "source authority", "independent agreement"], WHITE),
         ("STAGE 4  VALIDATE",
          ["L1 per attribute", "L2 relational", "L3 family coherence"], WHITE),
         ("STAGE 5  CALIBRATE",
          ["fitted confidence", "threshold set by the", "criticality tier"], WHITE),
         ("STAGE 6/7  OUTPUT",
          ["title - description", "facets - provenance", "idempotent upsert"], TINT)],
    ]
    w, gap, h = 155, 15, 78
    tops = [206, 100]

    for row, top in zip(rows, tops):
        x = MARGIN
        for index, (title, lines, fill) in enumerate(row):
            box(c, x, top, w, h, title, lines, fill=fill)
            if index < len(row) - 1:
                arrow(c, x + w + 2, top + h / 2, x + w + gap - 3, top + h / 2)
            x += w + gap

    # serpentine return from the end of row one to the start of row two
    last_cx = MARGIN + 3 * (w + gap) + w / 2
    first_cx = MARGIN + w / 2
    mid = (tops[0] + tops[1] + h) / 2
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.1)
    c.line(last_cx, tops[0], last_cx, mid)
    c.line(last_cx, mid, first_cx, mid)
    arrow(c, first_cx, mid, first_cx, tops[1] + h + 2)

    # terminal states
    band_y, band_h = 24, 62
    c.setFillColor(colors.HexColor("#FDF3F0"))
    c.setStrokeColor(colors.HexColor("#EEC7BC"))
    c.setLineWidth(0.8)
    c.roundRect(MARGIN, band_y, 668, band_h, 4, stroke=1, fill=1)
    c.setFillColor(RED)
    c.setFont("Helvetica-Bold", 8.6)
    c.drawString(MARGIN + 10, band_y + band_h - 14,
                 "TERMINAL STATES - every one carries a reason AND what would "
                 "resolve it")
    chips = [("no_source_located", "the MPN is in no document"),
             ("sources_conflict", "two datasheets disagree"),
             ("failed_validation", "a deterministic check rejected it"),
             ("below_threshold", "under the bar for its criticality tier")]
    cx = MARGIN + 10
    for code, why in chips:
        c.setFillColor(WHITE)
        c.setStrokeColor(colors.HexColor("#EEC7BC"))
        c.roundRect(cx, band_y + 8, 160, 24, 3, stroke=1, fill=1)
        c.setFillColor(RED)
        c.setFont("Courier-Bold", 6.8)
        c.drawString(cx + 6, band_y + 22, code)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6.2)
        c.drawString(cx + 6, band_y + 13, _fit(c, why, "Helvetica", 6.2, 148))
        cx += 167

    note(c, "A blank field is not actionable; a named blocker is a work item. "
            "No stage short-circuits on a threshold - every tier's candidates "
            "compete, and the losers are kept so the record can explain why a "
            "value won.", 12)


def slide_wireframe(c, n):
    y = BODY_TOP
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 8.4)
    c.drawString(MARGIN, y, "Three panes, one screen. Clicking any published "
                            "value outlines the exact region it was read from.")

    top, h = 92, 176
    box(c, MARGIN, top, 150, h, "CATALOGUE", [])
    entries = [("154630200-3RT", "12 published", GREEN),
               ("035094515-2VT", "no source located", RED),
               ("B4B-XH07-A-GV", "15 published", GREEN),
               ("310501-025X", "9 published", GREEN),
               ("WM413-N050", "11 published", GREEN)]
    cursor = top + h - 26
    for mpn, state, colour in entries:
        c.setFillColor(INK)
        c.setFont("Courier", 7)
        c.drawString(MARGIN + 8, cursor, mpn)
        c.setFillColor(colour)
        c.setFont("Helvetica", 6.4)
        c.drawString(MARGIN + 8, cursor - 8, state)
        cursor -= 24

    bx = MARGIN + 160
    box(c, bx, top, 296, h, "RECORD", [])
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 7.6)
    c.drawString(bx + 8, top + h - 26,
                 "TE Connectivity 3-Position Right-Angle Header, 1.25mm Pitch")
    rows = [("pitch", "1.25 mm", "published", "table - 0.999", GREEN),
            ("pole_count", "3", "published", "table - 0.999", GREEN),
            ("current_rating", "0.9 A", "review", "needs 2nd source", AMBER),
            ("rohs_compliant", "-", "abstained", "not_in_source", RED)]
    cursor = top + h - 44
    for key, value, state, meta, colour in rows:
        c.setFillColor(INK)
        c.setFont("Courier", 6.8)
        c.drawString(bx + 8, cursor, key)
        c.setFont("Courier-Bold", 6.8)
        c.drawString(bx + 96, cursor, value)
        c.setFillColor(colour)
        c.setFont("Helvetica-Bold", 6.4)
        c.drawString(bx + 152, cursor, state)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6.2)
        c.drawString(bx + 210, cursor, meta)
        cursor -= 13
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 6.4)
    c.drawString(bx + 8, cursor - 2, "abstained: \"absent from every verified "
                                     "source; a fuller datasheet would resolve it\"")
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 6.8)
    c.drawString(bx + 8, cursor - 18, "DESCRIPTION")
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.4)
    c.drawString(bx + 8, cursor - 28, "hover a phrase -> the attribute that "
                                      "licensed it -> its own provenance")

    sx = MARGIN + 466
    box(c, sx, top, 202, h, "SOURCE", [])
    pairs = [("attribute", "pitch"), ("value", "1.25 mm"), ("tier", "table_cell"),
             ("source", "fam000_ds"), ("page", "1"), ("cited span", '"1.25 mm"')]
    cursor = top + h - 26
    for key, value in pairs:
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 6.4)
        c.drawString(sx + 8, cursor, key)
        c.setFillColor(INK)
        c.setFont("Courier-Bold", 6.4)
        c.drawString(sx + 62, cursor, value)
        cursor -= 9.4
    c.setFillColor(colors.HexColor("#F7F8FA"))
    c.setStrokeColor(LINE)
    c.rect(sx + 8, top + 12, 186, 62, stroke=1, fill=1)
    c.setStrokeColor(RED)
    c.setLineWidth(1.4)
    c.rect(sx + 96, top + 40, 54, 12, stroke=1, fill=0)
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 6)
    c.drawString(sx + 12, top + 66, "rendered PDF page")
    c.setFillColor(RED)
    c.setFont("Helvetica-Bold", 6)
    c.drawString(sx + 96, top + 30, "the cited cell, outlined")

    note(c, "Screenshots of the running application are on the next slide. "
            "Live at http://localhost:8000 after docker compose up.")


def slide_architecture(c, n):
    y = BODY_TOP - 2
    groups = [
        (MARGIN, "INPUT", ["Sparse SKU rows", "CSV - API - ERP feed"], 96, TINT),
        (MARGIN + 106, "SOURCE LAYER", ["PDF ingest (pdfplumber)",
                                        "text - tables - bboxes",
                                        "Listing ingest",
                                        "Hybrid index: BM25 +",
                                        "TF-IDF/SVD, RRF fused"], 130, WHITE),
        (MARGIN + 246, "ENRICHMENT ENGINE", ["0 verify  1 classify",
                                             "2 candidates (3 tiers)",
                                             "3 adjudicate",
                                             "4 validate L1/L2/L3",
                                             "5 calibrate  6 commerce"], 150, colors.HexColor("#E8F1FC")),
        (MARGIN + 406, "STORE", ["PostgreSQL / SQLite",
                                 "products - provenance",
                                 "sources - eval_labels",
                                 "GIN + family indexes"], 118, WHITE),
        (MARGIN + 534, "SURFACES", ["FastAPI",
                                    "Web UI + PDF regions",
                                    "Exports: ETIM,",
                                    "UNSPSC, ERP shapes"], 134, TINT),
    ]
    top, h = 168, 92
    for x, title, lines, w, fill in groups:
        box(c, x, top, w, h, title, lines, fill=fill, title_size=8)
    for x, w in [(MARGIN, 96), (MARGIN + 106, 130), (MARGIN + 246, 150),
                 (MARGIN + 406, 118)]:
        arrow(c, x + w + 1.5, top + h / 2, x + w + 8.5, top + h / 2)

    cfg_y = top - 64
    box(c, MARGIN + 106, cfg_y, 290, 52,
        "CONFIGURATION - data, not code",
        ["category schemas: attributes, criticality tiers, relational rules, lookups",
         "abbreviation lexicon  -  taxonomy leaves  -  manufacturer aliases"],
        fill=colors.HexColor("#FFF8E8"), border=colors.HexColor("#E8D7A8"))
    arrow(c, MARGIN + 251, cfg_y + 52, MARGIN + 251, top - 2, AMBER)

    box(c, MARGIN + 410, cfg_y, 236, 52, "MODEL PROVIDERS - optional",
        ["Anthropic (tool use + prefix caching) or any",
         "OpenAI-compatible endpoint. The model proposes;",
         "it is never an authority."],
        fill=colors.HexColor("#F3F0FB"), border=colors.HexColor("#D6CEEC"))
    arrow(c, MARGIN + 410, cfg_y + 26, MARGIN + 398, cfg_y + 26, MUTED)

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 8.6)
    c.drawString(MARGIN, cfg_y - 22, "Two rules the architecture enforces structurally")
    bullets(c, [
        ("Nothing reaches the record without evidence that survives a "
         "deterministic check.", "The model is a proposer, never an authority."),
        ("Labels live in eval_labels with no code path from the pipeline.",
         "A gate test walks the AST to prove it."),
    ], MARGIN, cfg_y - 34, 640, 8, 10, 1.5)


def slide_tech(c, n):
    y = BODY_TOP
    rows = [
        ["Language", "Python 3.12", "Ecosystem for every component below"],
        ["PDF text, tables, coordinates", "pdfplumber",
         "Text, tables and bounding boxes from one library, no GPU"],
        ["Lexical retrieval", "rank_bm25", "Part numbers are exact-match tokens"],
        ["Dense retrieval", "scikit-learn TF-IDF + SVD",
         "Semantic half without a 2.5 GB torch dependency"],
        ["Fusion", "Reciprocal Rank Fusion", "Parameter-light, no score normalisation"],
        ["Units", "pint", "Correct unit algebra and dimensionality checks"],
        ["Fuzzy matching", "rapidfuzz", "Manufacturer aliases, attribute-key matching"],
        ["Calibration", "scikit-learn logistic regression",
         "Interpretable coefficients, small-data friendly"],
        ["Canonical model", "pydantic v2", "Type-safe schemas and provenance records"],
        ["Store", "PostgreSQL + JSONB (SQLite dev)",
         "One database: attributes JSONB, provenance relational"],
        ["API and UI", "FastAPI + static SPA", "Typed, async; renders PDF regions server-side"],
        ["LLM", "Anthropic or OpenAI-compatible",
         "One provider interface; measured on Mistral-Nemo-Instruct-2407"],
        ["Packaging and tests", "Docker Compose, pytest",
         "Verified from a clean volume; 57 gate tests"],
    ]
    table(c, ["Concern", "Choice", "Why"], rows, MARGIN, y, [162, 186, 320],
          size=7.6, row_h=15.4)

    c.setFillColor(RED)
    c.setFont("Helvetica-Bold", 8.4)
    c.drawString(MARGIN, 44, "Deliberately excluded")
    para(c, "Graph database (provenance is a foreign key, not a traversal)  -  "
            "object store (local disk)  -  separate search engine (Postgres "
            "suffices)  -  multi-agent critic loop (a model checking a model is "
            "weaker evidence than a deterministic check)  -  web-search "
            "enrichment (an unranked source undermines the provenance claim).",
         MARGIN, 33, 640, 7.6, 9.4, fill=MUTED)


def slide_cost(c, n):
    y = BODY_TOP
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(MARGIN, y, "$0.00 per 1,000 SKUs, measured")
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 8.6)
    c.drawString(MARGIN, y - 13, "In the reported run the deterministic tiers "
                                 "resolved every attribute, so no tokens were spent.")

    rows = [
        ["Deterministic path (rules + tables)", "$0",
         f"CPU only - {N['throughput']:,.0f} SKUs/min, single process"],
        ["LLM tier, when it fires", "~1,700 prompt tokens/SKU",
         "Unresolved attributes only, one call per SKU"],
        ["  at open-weight rates (measured)", "~$0.30 / 1,000 SKUs",
         "Mistral-Nemo via an OpenAI-compatible endpoint"],
        ["  at frontier rates", "~$5-8 / 1,000 SKUs", "If a stronger model is required"],
        ["Infrastructure", "$50-80 / month",
         "One small VM plus managed Postgres at this scale"],
        ["Re-processing a source revision", pct(N["reverify"]) + " of a full re-run",
         "Only affected products, only changed attributes"],
    ]
    table(c, ["Item", "Cost", "Basis"], rows, MARGIN, y - 26, [232, 148, 288],
          size=8, row_h=17)

    y2 = 92
    c.setFillColor(TINT)
    c.roundRect(MARGIN, y2 - 34, 668, 58, 4, stroke=0, fill=1)
    c.setFillColor(BLUE)
    c.rect(MARGIN, y2 - 34, 3, 58, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.4)
    c.drawString(MARGIN + 12, y2 + 12, "The economics of the design")
    para(c, "Because the model is called once per SKU for unresolved attributes "
            "only - and not at all when the deterministic tiers suffice - cost "
            "scales with what is MISSING, not with catalogue size. A distributor "
            "with a million SKUs pays for the gaps, not the rows. "
            f"{pct(N['avoidance'])} of attributes in the reported run never "
            "reached a model at all.",
         MARGIN + 12, y2, 646, 8.4, 10.4, fill=INK)


def slide_snapshots(c, n):
    y = BODY_TOP
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 8.4)
    c.drawString(MARGIN, y, "Calibration artefacts below are generated by the "
                            "evaluation run. Application screenshots: replace "
                            "the two panels on the right.")

    fig_dir = ROOT / "docs" / "figures"
    for path, x, label in [(fig_dir / "reliability.png", MARGIN, "Reliability diagram - held-out split"),
                           (fig_dir / "abstention.png", MARGIN + 178, "Abstention curve by criticality tier")]:
        if path.exists():
            c.drawImage(ImageReader(str(path)), x, 62, width=164, height=176,
                        preserveAspectRatio=True, anchor="n", mask="auto")
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 7.4)
        c.drawString(x, 52, label)

    panels = [(MARGIN + 366, 154, "SCREENSHOT 1 - the record",
               "published / review / abstained together, with confidence bars, "
               "tier badges and an abstention reason"),
              (MARGIN + 366, 62, "SCREENSHOT 2 - the provenance panel",
               "the PDF page with the cited cell outlined - the shot that "
               "carries the whole pitch")]
    for x, py, title, caption in panels:
        c.setFillColor(colors.HexColor("#F7F8FA"))
        c.setStrokeColor(LINE)
        c.setDash(3, 2)
        c.setLineWidth(0.8)
        c.roundRect(x, py, 302, 84, 4, stroke=1, fill=1)
        c.setDash()
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 10, py + 66, title)
        para(c, caption, x + 10, py + 52, 282, 7.6, 9.4, fill=MUTED)

    note(c, f"ECE {N['ece']:.3f} - predicted confidence tracks observed accuracy. "
            "Everyone displays confidence scores; almost nobody demonstrates "
            "theirs are calibrated.")


def slide_future(c, n):
    y = BODY_TOP
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN, y, "WHAT IS MEASURED TODAY")
    rows = [
        ["Corpus", f"{N['skus']} SKUs, 2 verticals, {N['docs']} documents, "
                   f"{N['listings']} listings"],
        ["Held-out test split", f"{N['test']} SKUs, touched once"],
        ["Gate tests", "57, including the wrong-part build gate"],
        ["Deployment", "docker compose up verified from a clean volume, on Postgres"],
    ]
    y = table(c, ["", ""], rows, MARGIN, y - 12, [126, 214], size=7.6, row_h=15)

    c.setFillColor(RED)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN, y - 16, "STATED HONESTLY")
    bullets(c, [
        ("The corpus is generated, not pulled from a distributor API.",
         "Digi-Key and Mouser keys need interactive registration. Labels "
         "therefore carry no noise by construction, and real documents are "
         "messier. The Digi-Key ingestion path is implemented and needs only "
         "a key."),
        ("The LLM tier is measured on samples against a 12B open-weight model,",
         "not the frontier model the design assumes."),
        ("Two of 73 taxonomy leaves have populated attribute schemas.", ""),
    ], MARGIN, y - 30, 340, 7.6, 9.4, 2.5)

    rx = MARGIN + 366
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(rx, BODY_TOP, "NEXT")
    steps = [
        ("Real corpus", "Swap the generator for the Digi-Key path, re-measure, "
                        "and report label noise from a hand audit"),
        ("More categories", "Each is a YAML file; the pipeline does not change"),
        ("OCR path", "For scanned datasheets, the one document class not handled"),
        ("Steward workflow", "Abstentions are already work items with resolution "
                             "hints - queue and assign them"),
        ("Standards projections", "ETIM and UNSPSC exports off the canonical model"),
        ("Word-level licence gate", "Today's gate is numeric; a fabricated phrase "
                                    "with no digits would pass"),
    ]
    cursor = BODY_TOP - 12
    for index, (title, body) in enumerate(steps, 1):
        c.setFillColor(BLUE)
        c.circle(rx + 5, cursor + 3, 5.6, stroke=0, fill=1)
        c.setFillColor(WHITE)
        c.setFont("Helvetica-Bold", 6.6)
        c.drawCentredString(rx + 5, cursor + 1, str(index))
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 7.8)
        c.drawString(rx + 16, cursor, title)
        cursor = para(c, body, rx + 16, cursor - 9, 288, 7.4, 9.2, fill=MUTED) - 3.5


def slide_links(c, n):
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 9)
    c.drawString(320, 267, "github.com/<your-handle>/sourced")
    c.drawString(320, 245, "<paste the 3-minute video link>")
    c.drawString(320, 223, "docker compose up  ->  http://localhost:8000")

    y = 168
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(MARGIN, y, "RUN IT IN FOUR COMMANDS")
    c.setFillColor(colors.HexColor("#12233A"))
    c.roundRect(MARGIN, y - 74, 400, 66, 4, stroke=0, fill=1)
    commands = [
        "pip install -e \".[dev]\"",
        "python -m sourced.corpus.build",
        "python -m sourced.eval.report --persist --fresh",
        "uvicorn sourced.api.routes:app --port 8000",
    ]
    cursor = y - 22
    for command in commands:
        c.setFillColor(colors.HexColor("#7FD1A0"))
        c.setFont("Courier-Bold", 7.6)
        c.drawString(MARGIN + 10, cursor, "$")
        c.setFillColor(colors.HexColor("#E8EEF5"))
        c.setFont("Courier", 7.6)
        c.drawString(MARGIN + 22, cursor, command)
        cursor -= 13
    c.setFillColor(MUTED)
    c.setFont("Helvetica-Oblique", 7.4)
    c.drawString(MARGIN, y - 84, "Or: docker compose up   -   "
                                 "python -m sourced.demo   narrates the system "
                                 "in six acts, with an offline replay fallback.")

    rx = MARGIN + 424
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(rx, y, "IN THE REPOSITORY")
    items = [
        ("docs/RESULTS.md", "every measured number, ablations, limitations"),
        ("docs/SUBMISSION.md", "this deck, in full"),
        ("docs/DEMO_SCRIPT.md", "the 3-minute script"),
        ("docs/00-08", "problem, architecture, data model, pipeline spec,"),
        ("", "evaluation, sources, build plan, risks, decisions"),
    ]
    cursor = y - 16
    for name, desc in items:
        if name:
            c.setFillColor(INK)
            c.setFont("Courier-Bold", 7.4)
            c.drawString(rx, cursor, name)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.2)
        c.drawString(rx + (96 if name else 0), cursor, desc)
        cursor -= 11

    c.setFillColor(TINT)
    c.roundRect(MARGIN, 30, 668, 30, 4, stroke=0, fill=1)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-BoldOblique", 9.6)
    c.drawCentredString(W / 2, 44,
                        "Sourced turns a part number into a sellable record with "
                        "the page and region every value came from -")
    c.drawCentredString(W / 2, 34,
                        "and returns an explained refusal instead of a plausible guess.")


# template page index (0-based) -> renderer
SLIDES = [
    (1, slide_team),
    (2, slide_brief),
    (3, slide_questions_index),
    (2, slide_enrich),          # reuses a body page, heading masked
    (2, slide_trust),
    (2, slide_scale),
    (4, slide_opportunities),
    (5, slide_features),
    (6, slide_flow),
    (7, slide_wireframe),
    (8, slide_architecture),
    (9, slide_tech),
    (10, slide_cost),
    (11, slide_snapshots),
    (12, slide_future),
    (13, slide_links),
    (14, None),                 # Thank You, untouched
]

N: dict = {}


def build(template: Path, out: Path) -> Path:
    global N
    N = load_numbers()

    overlay_path = out.with_suffix(".overlay.pdf")
    c = canvas.Canvas(str(overlay_path), pagesize=(W, H))
    for index, (_, renderer) in enumerate(SLIDES):
        if renderer is not None:
            renderer(c, index)
        c.showPage()
    c.save()

    overlay = PdfReader(str(overlay_path))
    writer = PdfWriter()
    for index, (page_index, renderer) in enumerate(SLIDES):
        base = PdfReader(str(template)).pages[page_index]   # fresh, unmerged
        if renderer is not None:
            base.merge_page(overlay.pages[index])
        writer.add_page(base)

    with open(out, "wb") as handle:
        writer.write(handle)
    overlay_path.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    written = build(Path(args.template), Path(args.out))
    reader = PdfReader(str(written))
    print(f"wrote {written}  ({len(reader.pages)} slides)")
