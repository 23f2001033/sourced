"""Scripted demo (doc 06 3.5).

Walks the five claims the system exists to support, in order, on real records.
Each act states what it is about to show, then shows it, so the narration lives
in the artefact rather than in someone's memory.

    python -m sourced.demo                 live - runs the pipeline as it speaks
    python -m sourced.demo --replay        from data/demo.json, no inference
    python -m sourced.demo --record        run live and write data/demo.json

Risk R10 says the demo will fail live at some point: a cold cache, a rate
limit, a machine that is not this one. `--record` captures a run; `--replay`
plays it back with no pipeline, no model and no database. The fallback is a
file in the repository, not a promise.

Narration is deliberately plain ASCII, and the few decorative glyphs degrade to
ASCII when the console cannot encode them. A demo that dies on the presenter's
console is not a fallback.
"""
from __future__ import annotations

import argparse
import json
import sys

from sourced import config

DEMO_PATH = config.DATA / "demo.json"

GREEN = "\033[32m"
RED = "\033[31m"
AMBER = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"

UNICODE_GLYPHS = {"rule": "─", "ok": "✓", "bad": "✗",
                  "hold": "⊘", "dot": "·", "dash": "—"}
ASCII_GLYPHS = {"rule": "-", "ok": "+", "bad": "x",
                "hold": "~", "dot": "-", "dash": "--"}


def _glyphs() -> dict[str, str]:
    """UTF-8 first; plain ASCII if the stream still cannot carry the glyphs.

    The default Windows console is cp1252 and encodes none of these.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")      # type: ignore[union-attr]
    except Exception:
        pass
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        "".join(UNICODE_GLYPHS.values()).encode(encoding)
        return UNICODE_GLYPHS
    except (UnicodeEncodeError, LookupError):
        return ASCII_GLYPHS


G = _glyphs()
RULE = G["rule"] * 78


class Out:
    """Every line goes through here, so encoding safety is handled once rather
    than remembered at each call site."""

    def __init__(self) -> None:
        self.colour = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{OFF}" if self.colour else text

    def raw(self, text: str = "") -> None:
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"))

    def act(self, number: int, title: str, claim: str) -> None:
        self.raw()
        self.raw(RULE)
        self.raw(self._c(BOLD, f"ACT {number} {G['dash']} {title}"))
        self.raw(self._c(DIM, claim))
        self.raw(RULE)

    def line(self, text: str = "") -> None:
        self.raw(text)

    def good(self, text: str) -> None:
        self.raw(self._c(GREEN, f"  {G['ok']} ") + text)

    def bad(self, text: str) -> None:
        self.raw(self._c(RED, f"  {G['bad']} ") + text)

    def hold(self, text: str) -> None:
        self.raw(self._c(AMBER, f"  {G['hold']} ") + text)

    def dim(self, text: str) -> None:
        self.raw(self._c(DIM, "    " + text))


# --------------------------------------------------------------- act builders


def _attr_line(key: str, attr: dict) -> str:
    value = attr.get("value")
    unit = attr.get("unit") or ""
    shown = "-" if value is None else f"{value} {unit}".strip()
    return f"{key:22} {shown:22} conf={attr.get('confidence', 0):.3f}"


def build(records: list[dict], pipeline, schemas: dict) -> dict:
    """Run the pipeline and capture everything the demo narrates."""
    from sourced.models import SkuInput

    def run(record):
        return pipeline.run(SkuInput(**record["sku_input"]),
                            schemas[record["category"]])

    def pick(cohort, category=None):
        for record in records:
            if record["cohort"] != cohort:
                continue
            if category and record["category"] != category:
                continue
            product = run(record)
            if cohort != "sibling_trap" and product.source_status != "verified":
                continue
            return record, product
        return None, None

    captured: dict = {}

    # act 1 - a sparse row becomes a commerce record
    record, product = pick("normal", "electrical_connector")
    captured["act1"] = {
        "input": record["sku_input"],
        "record": json.loads(product.model_dump_json()),
        "labels": record["labels"],
    }

    # act 2 - the same pipeline, a different vertical
    from sourced.candidates.rules import rule_candidates
    from sourced.taxonomy.classify import classify, schema_for, schema_votes
    from sourced.taxonomy.index import default_index

    record, product = pick("normal", "pipe_fitting")
    sku = SkuInput(**record["sku_input"])
    taxonomy = default_index()
    assignment = classify(sku, index=taxonomy)
    # schema_for can rewrite the assignment, so resolve it before reading it
    chosen_schema = schema_for(assignment, taxonomy, sku=sku)
    captured["act2"] = {
        "input": record["sku_input"],
        "record": json.loads(product.model_dump_json()),
        "rule_only": sorted(
            [{"key": c.canonical_key, "value": c.value, "unit": c.unit,
              "span": c.evidence.span, "producer": c.producer}
             for c in rule_candidates(sku, schemas["pipe_fitting"])],
            key=lambda c: c["key"]),
        "routing": {
            "code": assignment.code if assignment else None,
            "label": assignment.label if assignment else None,
            "method": assignment.method if assignment else None,
            "votes": schema_votes(sku.description_fragment or ""),
            "schema": chosen_schema,
        },
    }

    # act 3 - the sibling trap
    record, product = pick("sibling_trap")
    siblings = [r["sku_input"]["mpn"] for r in records
                if r["family_id"] == record["family_id"]
                and r["sku_input"]["mpn"] != record["sku_input"]["mpn"]][:4]
    captured["act3"] = {
        "input": record["sku_input"],
        "family_id": record["family_id"],
        "siblings_present_in_corpus": siblings,
        "record": json.loads(product.model_dump_json()),
    }

    # act 4 - authority beats a contradicting listing
    act4 = None
    for candidate in [r for r in records if r["cohort"] == "contradicted"]:
        product = run(candidate)
        key = ("voltage_rating" if candidate["category"] == "electrical_connector"
               else "max_working_pressure")
        attr = product.attributes.get(key)
        if attr and attr.value is not None and attr.rejected_candidates:
            act4 = {"input": candidate["sku_input"], "key": key,
                    "record": json.loads(product.model_dump_json()),
                    "labels": candidate["labels"]}
            break
    captured["act4"] = act4

    # act 5 - two documents disagree
    act5 = None
    for candidate in [r for r in records if r["cohort"] == "conflict"]:
        product = run(candidate)
        key = ("current_rating" if candidate["category"] == "electrical_connector"
               else "max_working_pressure")
        attr = product.attributes.get(key)
        if (attr is not None and attr.resolution == "abstained"
                and attr.abstention_reason
                and attr.abstention_reason.code == "sources_conflict"):
            act5 = {"input": candidate["sku_input"], "key": key,
                    "record": json.loads(product.model_dump_json())}
            break
    captured["act5"] = act5

    # act 6 - the measured claims
    experiment_path = config.DATA / "llm_experiment.json"
    experiment = None
    if experiment_path.exists():
        payload = json.loads(experiment_path.read_text(encoding="utf-8"))
        if "gates" in payload:
            experiment = {
                "model": payload["model"],
                "sample": payload["sample"]["skus_run"],
                "proposals": payload["gates"]["proposals_returned"],
                "rejection_rate": payload["gates"].get("total_rejection_rate"),
                "span_miss": payload["gates"]["span_rejection_rate"],
                "precision_gated": payload["contribution"]["precision_of_added_values"],
                "precision_ungated": (payload.get("gates_removed") or {}).get(
                    "precision_of_ungated_llm_values"),
            }

    results_path = config.DATA / "results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text(encoding="utf-8"))
        metrics = results["test_metrics"]
        located = metrics["source_location"]
        captured["act6"] = {
            "source_location_rate": located["source_location_rate"],
            "precision_published": metrics["accuracy"]["precision_published"],
            "precision_by_criticality": metrics["accuracy"]["precision_by_criticality"],
            "auto_publish_rate": metrics["validation_and_enrichment"]["auto_publish_rate"],
            "llm_avoidance_rate": metrics["scalable_engine"]["llm_avoidance_rate"],
            "expected_calibration_error": results["calibration"]["expected_calibration_error"],
            "routing_accuracy": results["category_routing"]["schema_routing_accuracy"],
            "invented_codes": results["category_routing"]["invented_codes"],
            "wrong_part_traps": located["wrong_part_traps"],
            "wrong_part_traps_correctly_refused":
                located["wrong_part_traps_correctly_refused"],
            "injected_fabrication":
                results["adversarial"]["injected_fabrication"]["interception_rate"],
            "reverification": results["catalog_operations"].get(
                "reverification_cost_vs_full_run"),
            "llm_tier_enabled": results["dataset"].get("llm_tier_used_in_this_run",
                                                       False),
            "llm_experiment": experiment,
            "by_category": {
                category: {
                    "coverage": c["metrics"]["structured_data_generation"]
                                 ["attribute_coverage"],
                    "precision": c["metrics"]["accuracy"]["precision_published"],
                    "auto_publish": c["metrics"]["validation_and_enrichment"]
                                     ["auto_publish_rate"],
                }
                for category, c in results["by_category"].items()},
        }
    return captured


# ------------------------------------------------------------------ narration


def narrate(captured: dict, out: Out) -> None:
    _act1(captured["act1"], out)
    _act2(captured["act2"], out)
    _act3(captured["act3"], out)
    _act4(captured.get("act4"), out)
    _act5(captured.get("act5"), out)
    _act6(captured.get("act6"), out)


def _act1(data: dict, out: Out) -> None:
    out.act(1, "A sparse row becomes a commerce record",
            "This is the distributor's actual starting point. No attached document.")
    sku = data["input"]
    out.line(f"  INPUT   mpn:  {sku['mpn']}")
    out.line(f"          mfr:  {sku['manufacturer'] or '(absent)'}")
    out.line(f"          desc: {sku['description_fragment'] or '(absent)'}")
    out.line()

    record = data["record"]
    out.line(f"  Stage 0 verified {len(record['sources'])} source(s):")
    for source in record["sources"]:
        out.dim(f"{source['source_id']} ({source['source_type']}) "
                f"match={source['match_confidence']:.2f} "
                f"via {source['match_evidence']}")
    out.line()

    attributes = record["attributes"]
    published = {k: v for k, v in attributes.items() if v["resolution"] == "published"}
    review = {k: v for k, v in attributes.items() if v["resolution"] == "review"}
    abstained = {k: v for k, v in attributes.items() if v["resolution"] == "abstained"}

    out.line(f"  {len(published)} published {G['dot']} {len(review)} review "
             f"{G['dot']} {len(abstained)} abstained")
    out.line()
    for key, attr in list(published.items())[:6]:
        out.good(_attr_line(key, attr))
    for key, attr in list(review.items())[:2]:
        out.hold(_attr_line(key, attr) + "   needs a second source")
    for key, attr in list(abstained.items())[:2]:
        reason = attr.get("abstention_reason") or {}
        out.bad(f"{key:22} {'-':22} {reason.get('code', '')}")
        out.dim(reason.get("resolution_hint", ""))
    out.line()

    example = next(iter(published.items()), None)
    if example:
        key, attr = example
        evidence = (attr.get("winning_candidate") or {}).get("evidence") or {}
        out.line("  Provenance for one value. This is what makes it publishable:")
        out.dim(f"{key} = {attr['value']} {attr.get('unit') or ''}")
        out.dim(f"source {evidence.get('source_id')} "
                f"page {evidence.get('page')} "
                f"locator {evidence.get('locator')}")
        out.dim(f"bbox {evidence.get('bbox')}")
        out.dim(f"cited span: {evidence.get('span')!r}")
    out.line()

    commerce = record.get("commerce") or {}
    out.line(f"  TITLE   {commerce.get('title', '')}")
    out.line(f"  DESC    {commerce.get('description', '')[:200]}")
    claims = commerce.get("description_claims", [])
    untraceable = sum(1 for c in claims if c["source_attribute"] is None)
    out.line()
    out.good(f"{len(claims)} description claims, {untraceable} untraceable")
    out.dim("hovering a phrase in the UI reveals the attribute that licensed it, "
            "and that attribute's own provenance")


def _act2(data: dict, out: Out) -> None:
    out.act(2, "The same pipeline, a different vertical",
            "A pipe fitting. No new code, no new pipeline - one more YAML file.")
    sku = data["input"]
    out.line(f"  INPUT   mpn:  {sku['mpn']}")
    out.line(f"          desc: {sku['description_fragment'] or '(absent)'}")
    out.line()

    routing = data["routing"]
    out.line("  Stage 1 has to pick an attribute set before anything is extracted.")
    out.dim(f"decoded-key votes: {routing['votes']}")
    out.good(f"schema {routing['schema']}   taxonomy {routing['code']} "
             f"({routing['label']})")
    out.dim(f"method: {routing['method']}")
    out.line()
    out.line("  The abbreviations in the row decode to attribute keys, and each key")
    out.line("  belongs to one schema. The route is a count, not a similarity.")
    out.line()

    out.line("  What the rules tier alone recovers from that row - no document, "
             "no model:")
    for candidate in data["rule_only"]:
        unit = f" {candidate['unit']}" if candidate["unit"] else ""
        out.good(f"{candidate['key']:24} = {str(candidate['value']) + unit:22} "
                 f"from {candidate['span']!r}")
    out.line()
    out.line("  Doc 00 opens with this exact fragment shape. Everything above is "
             "free,")
    out.line("  deterministic, and auditable down to the characters it fired on.")
    out.line()

    record = data["record"]
    published = {k: v for k, v in record["attributes"].items()
                 if v["resolution"] == "published"}
    out.line(f"  With the catalogue page verified, the record publishes "
             f"{len(published)} attributes.")
    commerce = record.get("commerce") or {}
    out.line(f"  TITLE   {commerce.get('title', '')}")


def _act3(data: dict, out: Out) -> None:
    out.act(3, "The sibling trap",
            "The failure every naive system commits, and the one that would be "
            "most damaging in production.")
    sku = data["input"]
    out.line(f"  INPUT   mpn:  {sku['mpn']}")
    out.line(f"          mfr:  {sku['manufacturer'] or '(absent)'}")
    out.line(f"          desc: {sku['description_fragment'] or '(absent)'}")
    out.line()
    out.line("  This part's own datasheet is NOT in the corpus. Its siblings' is:")
    for sibling in data["siblings_present_in_corpus"]:
        out.dim(f"{sibling}   same family, same manufacturer, same layout, "
                f"overlapping description")
    out.line()
    out.line("  A soft-scoring matcher accepts that document: every signal agrees "
             "except the part number.")
    out.line("  Extraction from it would be internally consistent, correctly "
             "formatted, in range, and wrong.")
    out.line()

    record = data["record"]
    if record["source_status"] == "not_located":
        abstention = record["abstention"]
        out.good(f"source_status = {record['source_status']}")
        out.dim(abstention["detail"])
        out.dim(f"hint: {abstention['resolution_hint']}")
        out.line()
        out.line("  No values were produced. Nothing was inferred from model memory.")
        out.line("  MPN presence is a hard gate, not a weighted signal (ADR-002).")
    else:
        out.bad(f"source_status = {record['source_status']} - the gate leaked")


def _act4(data: dict, out: Out) -> None:
    out.act(4, "Authority outranks agreement",
            "A distributor listing contradicts the manufacturer datasheet.")
    record = data["record"]
    key = data["key"]
    attr = record["attributes"].get(key) or {}
    label = (data["labels"].get(key) or {}).get("value")

    out.line(f"  INPUT   mpn: {data['input']['mpn']}")
    out.line()
    out.line(f"  Candidates for {key}:")
    winner = attr.get("winning_candidate") or {}
    evidence = winner.get("evidence") or {}
    out.good(f"{winner.get('value')} {winner.get('unit') or ''}  from "
             f"{evidence.get('source_type')} ({evidence.get('source_id')})")
    for rejected in attr.get("rejected_candidates", []):
        candidate = rejected["candidate"]
        candidate_evidence = candidate.get("evidence") or {}
        out.bad(f"{candidate.get('value')} {candidate.get('unit') or ''}  from "
                f"{candidate_evidence.get('source_type')} "
                f"({candidate_evidence.get('source_id')})")
        out.dim(f"rejected: {rejected['reason']}")
    out.line()
    out.line(f"  Ground truth: {label}")
    out.line()
    out.line("  Three listings copying one wrong value are not three independent "
             "confirmations.")
    out.line("  Ranking by authority before counting agreement is what separates "
             "them (ADR-004).")


def _act5(data: dict | None, out: Out) -> None:
    out.act(5, "When it cannot know, it says so",
            "Two manufacturer datasheets disagree. That is a conflict, not an "
            "election.")
    if data is None:
        out.dim("no conflicting-datasheet case in this sample")
        return
    record = data["record"]
    out.line(f"  INPUT   mpn: {data['input']['mpn']}")
    out.line()
    out.line("  Verified sources:")
    for source in record["sources"]:
        out.dim(f"{source['source_id']} ({source['source_type']}, "
                f"authority {source['authority_rank']})")
    out.line()
    attr = record["attributes"][data["key"]]
    reason = attr["abstention_reason"]
    out.hold(f"{data['key']} {G['dash']} {reason['code']}")
    out.dim(reason["detail"])
    out.dim(f"hint: {reason['resolution_hint']}")
    out.line()
    out.line(f"  {data['key']} is a safety-tier attribute. A wrong housing colour "
             f"is a cosmetic")
    out.line("  defect; a wrong current rating is a fire. They do not share a "
             "publish threshold")
    out.line("  (ADR-012). A blank field is not actionable; that reason and hint "
             "are a work item.")


def _act6(data: dict | None, out: Out) -> None:
    out.act(6, "The numbers, and what they are worth",
            "Measured on the held-out split, not asserted.")
    if data is None:
        out.dim("run `python -m sourced.eval.report` first")
        return

    def pct(value):
        return "n/a" if value is None else f"{value * 100:.1f}%"

    tiers = data["precision_by_criticality"]
    out.good(f"source location rate        {pct(data['source_location_rate'])}")
    out.good(f"schema routing accuracy     {pct(data['routing_accuracy'])}   "
             f"invented taxonomy codes: {data['invented_codes']}")
    out.good(f"precision (published)       {pct(data['precision_published'])}   "
             f"safety {pct(tiers['safety']['precision'])} {G['dot']} "
             f"functional {pct(tiers['functional']['precision'])} {G['dot']} "
             f"cosmetic {pct(tiers['cosmetic']['precision'])}")
    out.good(f"auto-publish rate           {pct(data['auto_publish_rate'])}")
    for category, stats in data.get("by_category", {}).items():
        out.dim(f"{category:22} coverage {pct(stats['coverage'])} "
                f"{G['dot']} precision {pct(stats['precision'])} "
                f"{G['dot']} auto-publish {pct(stats['auto_publish'])}")
    out.good(f"expected calibration error  {data['expected_calibration_error']}")
    out.good(f"wrong-part traps refused    "
             f"{data['wrong_part_traps_correctly_refused']}"
             f"/{data['wrong_part_traps']}")
    out.good(f"fabrications intercepted    {pct(data['injected_fabrication'])}")
    out.good(f"source revision vs re-run   {pct(data['reverification'])}")
    out.line()
    experiment = data.get("llm_experiment")
    if experiment:
        out.line()
        out.line(f"  The model tier, measured separately on {experiment['sample']} "
                 f"SKUs ({experiment['model'].split('/')[-1]}):")
        out.dim(f"{experiment['proposals']} proposals returned, "
                f"{pct(experiment['rejection_rate'])} rejected by the gates")
        out.dim(f"{pct(experiment['span_miss'])} cited a span that is not in the "
                f"chunk they cited")
        out.dim(f"precision of what survives {pct(experiment['precision_gated'])}, "
                f"versus {pct(experiment['precision_ungated'])} with the gates off")
        out.line()
        out.line("  That is the architecture's central bet, and it was an "
                 "assertion until the tier ran.")

    out.line()
    out.line("  Two things these numbers are NOT:")
    out.dim("the corpus is generated, so labels carry no noise and the PDFs are "
            "cleaner than production")
    if not data.get("llm_tier_enabled"):
        out.dim("the LLM tier is implemented but disabled, so 100% LLM avoidance "
                "is a property of this run")
    out.line()
    out.line("  Both are stated in docs/RESULTS.md, which also reports the three "
             "ablations that move")
    out.line("  nothing. An honest negative is stronger evidence of rigour than a "
             "uniform set of wins.")


# ------------------------------------------------------------------ entrypoint


def live_capture() -> dict:
    from sourced.confidence.calibrate import Calibrator
    from sourced.discovery.retrieve import SourceIndex
    from sourced.eval.labels import load_corpus_records
    from sourced.ingest.loader import load_corpus
    from sourced.pipeline import Options, Pipeline
    from sourced.registry import load_schema

    records = load_corpus_records()
    schemas = {category: load_schema(category)
               for category in {r["category"] for r in records}}
    # deterministic on purpose: this is the artefact that has to work when the
    # network does not, and 20-80 seconds per record would make it unusable
    pipeline = Pipeline(SourceIndex(load_corpus()), Options(llm_tier=False),
                        calibrator=Calibrator.load())
    return build(records, pipeline, schemas)


def main(replay: bool = False, record: bool = False) -> int:
    out = Out()
    out.raw()
    out.raw(out._c(BOLD, "  SOURCED - product intelligence for industrial commerce"))
    out.raw(out._c(DIM, "  A sparse SKU row in. A commerce record with provenance "
                        "out, or an explained refusal."))

    if replay:
        if not DEMO_PATH.exists():
            out.raw(f"\n  no capture at {DEMO_PATH}")
            out.raw("  run `python -m sourced.demo --record` first")
            return 1
        captured = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
        out.raw(out._c(DIM, f"  [replaying {DEMO_PATH.name}: no pipeline, no model, "
                            f"no database]"))
    else:
        captured = live_capture()

    narrate(captured, out)

    if record:
        DEMO_PATH.write_text(json.dumps(captured, indent=2, default=str),
                             encoding="utf-8")
        out.raw(f"\n  captured to {DEMO_PATH}")

    out.raw()
    out.raw(RULE)
    out.raw(f"  docs/RESULTS.md {G['dot']} web UI at / {G['dot']} pytest for the gates")
    out.raw(RULE)
    out.raw()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", action="store_true",
                        help="narrate from data/demo.json without running anything")
    parser.add_argument("--record", action="store_true",
                        help="run live and capture the run for replay")
    args = parser.parse_args()
    raise SystemExit(main(replay=args.replay, record=args.record))
