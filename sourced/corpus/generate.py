"""Synthetic corpus generation (doc 05 Corpus construction, doc 06 1.1).

Produces the pair the evaluation needs: sparse SKU inputs as the pipeline's
input, and ground-truth attributes held in a separate file the pipeline has no
code path to.

The corpus deliberately contains the cases the system claims to handle:

  normal            part appears in its family datasheet
  distributor_only  part is absent from the datasheet but listed by a distributor
  sibling_trap      part is absent everywhere; only its *siblings'* datasheet exists
  conflict          two manufacturer datasheets disagree on a safety attribute
  contradicted      a distributor page contradicts the datasheet

`sibling_trap` is the wrong-part robustness test of doc 04 and the day-2 build
gate of doc 06. Correct behaviour is `no_source_located`, not extraction from
the sibling.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from sourced import config
from sourced.corpus import spec as S
from sourced.corpus.render import (fmt_current, fmt_pitch, fmt_temp,
                                   render_datasheet, render_distributor_page)
from sourced.ingest.normalize import normalise_text

SEED = 20260821
N_FAMILIES = 52
PARTS_PER_FAMILY = (5, 9)


def _split_for(mpn: str) -> str:
    h = int(hashlib.sha256(f"split:{mpn}".encode()).hexdigest(), 16) % 100
    return "dev" if h < 40 else ("calibration" if h < 70 else "test")


def _fragment(part: dict, rng: random.Random) -> str:
    bits = ["CONN", "HEADER", S.ORIENT_ABBR[part["orientation"]],
            f"{part['pole_count']}POS"]
    pitch = part["pitch"]
    if rng.random() < 0.4 and pitch in S.PITCH_INCH_DISPLAY:
        bits.append(S.PITCH_INCH_DISPLAY[pitch] + '"')
    else:
        bits.append(f"{pitch:.2f}MM".replace(".00", ""))
    bits.append(S.PLATING_ABBR[part["contact_plating"]])
    bits.append(S.MOUNT_ABBR[part["mounting_type"]])
    text = " ".join(bits)
    if rng.random() < 0.35:                      # truncated, as real rows are
        text = text[: rng.choice([24, 28, 32])].rstrip()
    return text


def build_families(rng: random.Random) -> list[dict]:
    families = []
    for i in range(N_FAMILIES):
        mfr, style, series_pool = S.MANUFACTURERS[i % len(S.MANUFACTURERS)]
        series = series_pool[(i // len(S.MANUFACTURERS)) % len(series_pool)]
        pitch = rng.choice(S.PITCHES)
        voltage = rng.choice(S.VOLTAGE_BY_PITCH[pitch])
        tmin = rng.choice(S.TEMP_MINS)
        tmax = rng.choice(S.TEMP_MAXS)
        fam = {
            "family_id": f"fam{i:03d}",
            "manufacturer": mfr,
            "mpn_style": style,
            # the family index is folded into every series, not only the
            # numeric ones: two families sharing a series would mint the same
            # MPN, which breaks both the uniqueness constraint and the
            # sibling_trap premise
            "series": f"{series}{i:02d}",
            "doc_no": f"DS-{1000 + i}",
            "pitch": pitch,
            "voltage_rating": voltage,
            "operating_temp_min": tmin,
            "operating_temp_max": tmax,
            "housing_material": rng.choice(S.HOUSING_MATERIALS),
            "flammability_rating": rng.choice(S.FLAMMABILITY),
            "mounting_type": rng.choice(S.MOUNTINGS),
            "termination": rng.choice(S.TERMINATIONS),
            "rohs_compliant": rng.random() < 0.9,
            "contact_resistance": rng.choice(S.CONTACT_RESISTANCES) if rng.random() < 0.7 else None,
            "display": {
                "inch_pitch": rng.random() < 0.3,
                "fahrenheit": rng.random() < 0.15,
                "milliamp": pitch <= 2.0 and rng.random() < 0.5,
            },
        }
        families.append(fam)
    return families


def build_parts(fam: dict, rng: random.Random) -> list[dict]:
    n = rng.randint(*PARTS_PER_FAMILY)
    poles = rng.sample(S.POLE_COUNTS, n)
    parts = []
    for j, pc in enumerate(sorted(poles)):
        plating = rng.choice(S.PLATINGS)
        material = (rng.choice(S.GOLD_COMPATIBLE_MATERIALS) if plating == "gold"
                    else rng.choice(S.ALL_MATERIALS))
        orient = rng.choice(S.ORIENTATIONS)
        base = S.CURRENT_BY_PITCH[fam["pitch"]]
        current = round(base * (1.0 if plating == "gold" else 0.9), 1)
        parts.append({
            "mpn": S.make_mpn(fam["mpn_style"], fam["series"], pc, j + 1, orient, plating),
            "manufacturer": fam["manufacturer"],
            "family_id": fam["family_id"],
            "pole_count": pc,
            "pitch": fam["pitch"],
            "orientation": orient,
            "contact_plating": plating,
            "contact_material": material,
            "current_rating": current,
            "housing_colour": rng.choice(S.COLOURS) if rng.random() < 0.85 else None,
            "mounting_type": fam["mounting_type"],
            "voltage_rating": fam["voltage_rating"],
            "operating_temp_min": fam["operating_temp_min"],
            "operating_temp_max": fam["operating_temp_max"],
            "housing_material": fam["housing_material"],
            "flammability_rating": fam["flammability_rating"],
            "termination": fam["termination"],
            "rohs_compliant": fam["rohs_compliant"],
            "contact_resistance": fam["contact_resistance"],
        })
    return parts


LABEL_KEYS = ["pole_count", "pitch", "current_rating", "voltage_rating",
              "flammability_rating", "operating_temp_min", "operating_temp_max",
              "contact_material", "contact_plating", "housing_material",
              "mounting_type", "orientation", "termination", "contact_resistance",
              "housing_colour", "rohs_compliant"]

UNITS = {"pitch": "mm", "current_rating": "A", "voltage_rating": "V",
         "operating_temp_min": "degC", "operating_temp_max": "degC",
         "contact_resistance": "milliohm"}


def labels_for(part: dict) -> dict:
    out = {}
    for k in LABEL_KEYS:
        v = part.get(k)
        if v is None:
            continue
        out[k] = {"value": v, "unit": UNITS.get(k)}
    return out


# Listings carry errors. This is the whole reason source authority is ranked:
# a marketplace listing is a weaker claim than a manufacturer datasheet, and the
# calibration model can only learn that if the corpus actually reflects it.
LISTING_ERROR_RATE = {"distributor_page": 0.07, "marketplace": 0.20}

NEVER_CORRUPT = {"Number of Positions"}   # the position count is in the MPN itself


def _corrupt_spec(key: str, value: str, part: dict, rng: random.Random) -> str:
    """Replace one listing field with a plausible-but-wrong value."""
    if key in ("Contact Plating",):
        return S.DISPLAY[rng.choice([p for p in S.PLATINGS
                                     if p != part["contact_plating"]])]
    if key == "Mounting Type":
        return S.DISPLAY[rng.choice([m for m in S.MOUNTINGS
                                     if m != part["mounting_type"]])]
    if key == "Orientation":
        return S.DISPLAY[rng.choice([o for o in S.ORIENTATIONS
                                     if o != part["orientation"]])]
    if key == "Housing Material":
        return S.DISPLAY[rng.choice([h for h in S.HOUSING_MATERIALS
                                     if h != part["housing_material"]])]
    if key == "Flammability Rating":
        return S.DISPLAY[rng.choice([f for f in set(S.FLAMMABILITY)
                                     if f != part["flammability_rating"]])]
    if key == "Colour":
        return S.DISPLAY[rng.choice([c for c in S.COLOURS
                                     if c != part["housing_colour"]])]
    if key == "RoHS Status":
        return "Not Compliant" if part["rohs_compliant"] else "RoHS Compliant"
    # numeric fields: shift by a plausible catalogue step rather than a typo
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    if not match:
        return value
    number = float(match.group(0))
    shifted = number * rng.choice([0.5, 0.8, 1.25, 2.0])
    rendered = f"{shifted:.0f}" if number == int(number) else f"{shifted:.1f}"
    return value[:match.start()] + rendered + value[match.end():]


def _corrupt_generic(value: str, rng: random.Random) -> str:
    """Shift a rendered field to a plausible-but-wrong value.

    Numeric fields move by a catalogue step rather than a typo, because a typo
    is caught by a range check and a catalogue step is not.
    """
    match = re.search(r"[-+]?\d+(?:\.\d+)?", value)
    if not match:
        return value
    number = float(match.group(0))
    shifted = number * rng.choice([0.5, 0.8, 1.25, 2.0])
    rendered = f"{shifted:.0f}" if number == int(number) else f"{shifted:.1f}"
    return value[:match.start()] + rendered + value[match.end():]


def _distributor_specs(part: dict, fam: dict, corrupt_key: str | None,
                       rng: random.Random, source_type: str = "distributor_page") -> dict:
    """A distributor listing carries a subset of fields, in its own wording."""
    u = fam["display"]
    specs = {
        "Number of Positions": str(part["pole_count"]),
        "Pitch": fmt_pitch(part["pitch"], u["inch_pitch"]),
        "Current Rating": fmt_current(part["current_rating"], u["milliamp"]),
        "Voltage Rating": f"{part['voltage_rating']:.0f} V",
        "Contact Plating": S.DISPLAY[part["contact_plating"]],
        "Mounting Type": S.DISPLAY[part["mounting_type"]],
        "Orientation": S.DISPLAY[part["orientation"]],
        "Operating Temperature Min": fmt_temp(part["operating_temp_min"], u["fahrenheit"]),
        "Operating Temperature Max": fmt_temp(part["operating_temp_max"], u["fahrenheit"]),
        "Housing Material": S.DISPLAY[part["housing_material"]],
        "Flammability Rating": S.DISPLAY[part["flammability_rating"]],
        "RoHS Status": "RoHS Compliant" if part["rohs_compliant"] else "Not Compliant",
    }
    if part.get("housing_colour"):
        specs["Colour"] = S.DISPLAY[part["housing_colour"]]
    if corrupt_key == "voltage_rating":
        wrong = rng.choice([v for v in [50, 125, 250, 300, 600]
                            if v != part["voltage_rating"]])
        specs["Voltage Rating"] = f"{wrong:.0f} V"

    rate = LISTING_ERROR_RATE.get(source_type, 0.0)
    for key in list(specs):
        if key in NEVER_CORRUPT or (corrupt_key and key == "Voltage Rating"):
            continue
        if rng.random() < rate:
            specs[key] = _corrupt_spec(key, specs[key], part, rng)
    return specs


def clear_media(data: Path) -> None:
    for d in (data / "pdfs", data / "pages"):
        d.mkdir(parents=True, exist_ok=True)
        for old in d.iterdir():
            old.unlink()


def generate(out_dir: Path | None = None, seed: int = SEED, clear: bool = True,
             write: bool = True, rng: random.Random | None = None):
    rng = rng or random.Random(seed)
    data = Path(out_dir or config.DATA)
    pdf_dir, page_dir = data / "pdfs", data / "pages"
    if clear:
        clear_media(data)
    else:
        for d in (pdf_dir, page_dir):
            d.mkdir(parents=True, exist_ok=True)

    families = build_families(rng)
    all_parts: list[dict] = []
    fam_parts: dict[str, list[dict]] = {}
    for fam in families:
        parts = build_parts(fam, rng)
        fam_parts[fam["family_id"]] = parts
        all_parts.extend(parts)

    # guard the sibling_trap premise: no MPN may be a substring of another
    norm = {p["mpn"]: normalise_text(p["mpn"]) for p in all_parts}
    collisions = {a for a, na in norm.items()
                  for b, nb in norm.items() if a != b and na in nb}

    # ---- cohort assignment ------------------------------------------------
    cohorts: dict[str, str] = {}
    for fam in families:
        parts = fam_parts[fam["family_id"]]
        eligible = [p for p in parts if p["mpn"] not in collisions]
        rng.shuffle(eligible)
        take = eligible[:4]
        if take:
            cohorts[take[0]["mpn"]] = "sibling_trap"
        # parts a datasheet does not list individually but distributors stock
        # anyway are common, and they are the only records whose values come
        # from a low-authority source alone
        for part in take[1:3]:
            cohorts[part["mpn"]] = "distributor_only"
        if len(take) > 3 and rng.random() < 0.6:
            cohorts[take[3]["mpn"]] = "conflict"
    for p in all_parts:
        cohorts.setdefault(p["mpn"], "normal")
    plain = [p for p in all_parts if cohorts[p["mpn"]] == "normal"]
    for p in rng.sample(plain, k=int(0.10 * len(plain))):
        cohorts[p["mpn"]] = "contradicted"

    # ---- render sources ---------------------------------------------------
    sources: list[dict] = []
    part_sources: dict[str, list[dict]] = {p["mpn"]: [] for p in all_parts}

    for fam in families:
        parts = fam_parts[fam["family_id"]]
        listed = [p for p in parts
                  if cohorts[p["mpn"]] not in ("sibling_trap", "distributor_only")]
        if not listed:
            listed = parts[:1]
        sid = f"{fam['family_id']}_ds"
        path = pdf_dir / f"{sid}.pdf"
        render_datasheet(path, fam, listed)
        sources.append({"source_id": sid, "source_type": "manufacturer_datasheet",
                        "authority_rank": 1, "uri": config.store_uri(path),
                        "family_id": fam["family_id"]})
        for p in listed:
            part_sources[p["mpn"]].append({"source_id": sid,
                                           "source_type": "manufacturer_datasheet"})

        # a revision datasheet that disagrees on current rating -> sources_conflict
        conflicted = [p for p in parts if cohorts[p["mpn"]] == "conflict" and p in listed]
        if conflicted:
            rev_parts = []
            for p in conflicted:
                q = dict(p)
                q["current_rating"] = round(p["current_rating"] * rng.choice([0.5, 1.6]), 1)
                rev_parts.append(q)
            rsid = f"{fam['family_id']}_ds_revB"
            rpath = pdf_dir / f"{rsid}.pdf"
            render_datasheet(rpath, fam, rev_parts, revision="B")
            sources.append({"source_id": rsid, "source_type": "manufacturer_datasheet",
                            "authority_rank": 1, "uri": config.store_uri(rpath),
                            "family_id": fam["family_id"]})
            for p in conflicted:
                part_sources[p["mpn"]].append({"source_id": rsid,
                                               "source_type": "manufacturer_datasheet"})

    for p in all_parts:
        cohort = cohorts[p["mpn"]]
        if cohort == "sibling_trap":
            continue
        fam = next(f for f in families if f["family_id"] == p["family_id"])
        if cohort == "distributor_only":
            n_pages = rng.choice([1, 1, 2])
        elif cohort == "contradicted":
            n_pages = 1
        else:
            n_pages = 1 if rng.random() < 0.55 else 0
        for n in range(n_pages):
            corrupt = "voltage_rating" if cohort == "contradicted" else None
            stype = "distributor_page" if rng.random() < 0.7 else "marketplace"
            sid = f"{normalise_text(p['mpn'])}_pg{n}"
            path = page_dir / f"{sid}.json"
            render_distributor_page(
                path, p, _distributor_specs(p, fam, corrupt, rng, stype), stype,
                rng.choice(["Alpha Supply", "Beta Electronics", "Gamma Components"]))
            sources.append({"source_id": sid, "source_type": stype,
                            "authority_rank": config.AUTHORITY_RANK[stype],
                            "uri": config.store_uri(path),
                            "family_id": p["family_id"]})
            part_sources[p["mpn"]].append({"source_id": sid, "source_type": stype})

    # ---- corpus records ---------------------------------------------------
    records = []
    for p in all_parts:
        show_mfr = rng.random() >= 0.15          # 15% arrive with no manufacturer
        records.append({
            "sku_input": {
                "mpn": p["mpn"],
                "manufacturer": p["manufacturer"] if show_mfr else None,
                "description_fragment": _fragment(p, rng),
                "internal_sku": f"SKU-{normalise_text(p['mpn'])[:10]}",
            },
            "category": "electrical_connector",
            "family_id": p["family_id"],
            "cohort": cohorts[p["mpn"]],
            "expected_sources": part_sources[p["mpn"]],
            "split": _split_for(p["mpn"]),
            "labels": labels_for(p),
            "label_provenance": "synthetic_generator_ground_truth",
            "hand_audited": False,
        })

    if not write:
        # composed into a larger, multi-category corpus by sourced.corpus.build,
        # which owns the writing so the files stay consistent with each other
        return records, sources

    (data / "corpus.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    (data / "sources.jsonl").write_text(
        "\n".join(json.dumps(s) for s in sources) + "\n", encoding="utf-8")

    summary = {
        "skus": len(records),
        "families": len(families),
        "datasheets": sum(1 for s in sources if s["source_type"] == "manufacturer_datasheet"),
        "distributor_pages": sum(1 for s in sources
                                 if s["source_type"] != "manufacturer_datasheet"),
        "cohorts": {c: sum(1 for r in records if r["cohort"] == c)
                    for c in ["normal", "contradicted", "conflict",
                              "distributor_only", "sibling_trap"]},
        "splits": {s: sum(1 for r in records if r["split"] == s)
                   for s in ["dev", "calibration", "test"]},
        "label_keys": len(LABEL_KEYS),
        "mpn_substring_collisions": len(collisions),
    }
    (data / "corpus_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    import pprint
    pprint.pp(generate())
