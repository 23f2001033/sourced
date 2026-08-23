"""Rules tier (doc 03 2.1).

Anything a rule can decide, a rule decides. This tier reads the sparse input
row itself — the abbreviation soup a distributor actually holds — and costs
nothing, which is what the LLM-avoidance metric is made of.

Every candidate carries a span that is verbatim in the chunk it cites, exactly
as the LLM tier must, so the same deterministic gate applies to all three tiers.
"""
from __future__ import annotations

import functools
import io
import re
from pathlib import Path

import yaml

from sourced import config
from sourced.ingest.chunks import Chunk
from sourced.ingest.normalize import (normalise_categorical, parse_mixed_fraction,
                                      to_canonical)
from sourced.models import Candidate, Evidence, SkuInput
from sourced.registry import CategorySchema

INPUT_SOURCE_ID = "input_row"


@functools.lru_cache(maxsize=1)
def _lexicon() -> dict:
    path = Path(config.SCHEMAS) / "lexicon.yaml"
    return yaml.safe_load(io.open(path, encoding="utf-8"))


def input_chunk(sku: SkuInput) -> Chunk:
    """The sparse row rendered as a chunk, so rule evidence is checkable by the
    same span-containment gate as everything else."""
    return Chunk(
        chunk_id=f"{INPUT_SOURCE_ID}:{sku.mpn}",
        source_id=INPUT_SOURCE_ID,
        source_type="internal_record",
        page=None,
        text=sku.description_fragment or "",
        locator="inferred",
    )


def _evidence(chunk: Chunk, span: str) -> Evidence:
    return Evidence(
        source_id=chunk.source_id,
        source_type=chunk.source_type,
        page=chunk.page,
        bbox=chunk.bbox,
        span=span,
        chunk_id=chunk.chunk_id,
        locator=chunk.locator,
    )


def _token_candidates(text: str, chunk: Chunk, schema: CategorySchema) -> list[Candidate]:
    out: list[Candidate] = []
    tokens = _lexicon()["tokens"]
    upper = text.upper()
    for abbr, entry in tokens.items():
        # One abbreviation may fill different attributes in different
        # categories: `BRS` is a contact material on a connector and a body
        # material on a fitting. Entries that name several are filtered against
        # the schema selected for this SKU, so only the applicable one fires.
        readings = entry if isinstance(entry, list) else [entry]
        readings = [r for r in readings if schema.spec(r["key"]) is not None]
        if not readings:
            continue

        # a token must not be a fragment of a longer code: `SN` inside `SN/PB`
        # is the tin-lead token, not the tin token
        pattern = rf"(?<![A-Z0-9/]){re.escape(abbr)}(?![A-Z0-9/])"
        m = re.search(pattern, upper)
        if not m:
            continue
        span = text[m.start():m.end()]
        for reading in readings:
            out.append(Candidate(
                canonical_key=reading["key"], value=reading["value"], unit=None,
                raw_text=span, tier="rule", evidence=_evidence(chunk, span),
                producer=f"lexicon:{abbr}"))
    return out


def _pattern_candidates(text: str, chunk: Chunk, schema: CategorySchema) -> list[Candidate]:
    out: list[Candidate] = []
    for pat in _lexicon()["patterns"]:
        key = pat["key"]
        spec = schema.spec(key)
        if spec is None:
            continue
        m = re.search(pat["regex"], text, re.I)
        if not m:
            continue
        raw = m.group(pat.get("group", 1))

        # A pattern may name a controlled value rather than a quantity:
        # `map` translates a captured token (`MIP` -> male_iron_pipe), `format`
        # builds one from a captured number (`150#` -> class_150). Either way
        # the result is checked against the schema's vocabulary before it is
        # allowed to become a candidate.
        if "map" in pat or "format" in pat:
            token = raw.strip().upper()
            if "map" in pat:
                mapped = pat["map"].get(token)
            else:
                number = re.fullmatch(r"(\d+)(?:\.0+)?", token)
                mapped = (pat["format"].format(value=number.group(1))
                          if number else None)
            if mapped is None or (spec.values and mapped not in spec.values):
                continue
            out.append(Candidate(
                canonical_key=key, value=mapped, unit=None, raw_text=m.group(0),
                tier="rule", evidence=_evidence(chunk, m.group(0)),
                producer=f"pattern:{pat['id']}"))
            continue

        try:
            value = parse_mixed_fraction(raw)
        except ValueError:
            continue
        unit = pat.get("unit")
        if unit and spec.canonical_unit:
            q = to_canonical(value, unit, spec.canonical_unit)
            if q is not None:
                value, unit = round(float(q.magnitude), 6), spec.canonical_unit
        if spec.type == "quantity" and spec.canonical_unit is None:
            value = int(round(value))
        out.append(Candidate(
            canonical_key=key, value=value, unit=unit, raw_text=m.group(0),
            tier="rule", evidence=_evidence(chunk, m.group(0)),
            producer=f"pattern:{pat['id']}"))
    return out


def candidates_from_text(text: str, chunk: Chunk, schema: CategorySchema) -> list[Candidate]:
    if not text:
        return []
    return _token_candidates(text, chunk, schema) + _pattern_candidates(text, chunk, schema)


def rule_candidates(sku: SkuInput, schema: CategorySchema) -> list[Candidate]:
    """Rules over the sparse input row. The only tier that produces anything at
    all when no document exists — which is why its evidence is ranked lowest."""
    chunk = input_chunk(sku)
    return candidates_from_text(chunk.text, chunk, schema)


# --------------------------------------------------------------- MPN decoding


class MpnDecoder:
    """MPN structure decoding (doc 03 2.1, step 4).

    Manufacturers encode attributes in part numbers. This is learnable without
    supervision: group by manufacturer, find the shared prefix that identifies a
    family, and for each varying segment correlate its values against attributes
    already extracted by the cheaper tiers. Where a position correlates above
    the threshold across a family, a decoder has been learned.

    Doc 06 puts this fifth on the cut list: valuable, not load-bearing. It is
    therefore off by default and enabled explicitly.
    """

    SEGMENT = re.compile(r"\d+|[A-Za-z]+")
    MIN_FAMILY = 5
    MIN_CORRELATION = 0.9

    def __init__(self) -> None:
        self.rules: dict[tuple[str, int], dict] = {}   # (family_key, seg_idx) -> rule

    @staticmethod
    def _family_key(manufacturer: str | None, mpn: str) -> str:
        segs = MpnDecoder.SEGMENT.findall(mpn.upper())
        return f"{manufacturer or '?'}|{segs[0] if segs else ''}|{len(segs)}"

    def fit(self, observations: list[tuple[str | None, str, dict]]) -> "MpnDecoder":
        """observations: (manufacturer, mpn, {key: value}) already resolved by
        the rules and tables tiers."""
        groups: dict[str, list[tuple[list[str], dict]]] = {}
        for manufacturer, mpn, attrs in observations:
            groups.setdefault(self._family_key(manufacturer, mpn), []).append(
                (self.SEGMENT.findall(mpn.upper()), attrs))

        for fam_key, members in groups.items():
            if len(members) < self.MIN_FAMILY:
                continue
            n_segs = min(len(s) for s, _ in members)
            for idx in range(n_segs):
                seg_values = {s[idx] for s, _ in members}
                if len(seg_values) < 2:            # constant segment decodes nothing
                    continue
                keys = {k for _, a in members for k in a}
                for key in keys:
                    mapping: dict[str, set] = {}
                    for segs, attrs in members:
                        if key not in attrs:
                            continue
                        mapping.setdefault(segs[idx], set()).add(
                            normalise_categorical(attrs[key]))
                    covered = sum(len(v) for v in mapping.values())
                    if len(mapping) < 2 or covered == 0:
                        continue
                    unambiguous = sum(1 for v in mapping.values() if len(v) == 1)
                    if unambiguous / len(mapping) >= self.MIN_CORRELATION:
                        self.rules[(fam_key, idx)] = {
                            "key": key,
                            "map": {k: next(iter(v)) for k, v in mapping.items() if len(v) == 1},
                            "support": len(mapping),
                        }
        return self

    def decode(self, sku: SkuInput, schema: CategorySchema) -> list[Candidate]:
        segs = self.SEGMENT.findall(sku.mpn.upper())
        fam_key = self._family_key(sku.manufacturer, sku.mpn)
        chunk = Chunk(chunk_id=f"mpn:{sku.mpn}", source_id=INPUT_SOURCE_ID,
                      source_type="internal_record", text=sku.mpn, locator="inferred")
        out: list[Candidate] = []
        for idx, seg in enumerate(segs):
            rule = self.rules.get((fam_key, idx))
            if not rule or seg not in rule["map"]:
                continue
            spec = schema.spec(rule["key"])
            if spec is None:
                continue
            raw = rule["map"][seg]
            value: float | str = raw
            if spec.type == "quantity":
                try:
                    value = float(raw)
                except ValueError:
                    continue
                if spec.canonical_unit is None:
                    value = int(value)
            out.append(Candidate(
                canonical_key=rule["key"], value=value,
                unit=spec.canonical_unit, raw_text=seg, tier="rule",
                evidence=_evidence(chunk, seg),
                producer=f"mpn_decode:{fam_key}:{idx}"))
        return out
