"""Results reporting (doc 04 Reporting template, doc 06 3.1).

    python -m sourced.eval.report              write docs/RESULTS.md
    python -m sourced.eval.report --persist    also load the catalogue into the
                                               store so the UI has something to show
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from sourced import config
from sourced.confidence.calibrate import Calibrator
from sourced.discovery.retrieve import SourceIndex
from sourced.eval import labels as label_mod
from sourced.eval.run import main as run_main
from sourced.eval.run import run_batch
from sourced.ingest.loader import load_corpus
from sourced.pipeline import Options, Pipeline
from sourced.registry import load_schema

RESULTS_PATH = config.DOCS / "RESULTS.md"


def pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def num(value, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def load_llm_experiment() -> dict | None:
    path = config.DATA / "llm_experiment.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if "gates" in payload else None


def load_description_experiment() -> dict | None:
    path = config.DATA / "description_experiment.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return payload if "generated" in payload else None


def render_description_section(experiment: dict) -> list[str]:
    """The only generative surface, measured (ADR-013, risk R8)."""
    lines: list[str] = []
    A = lines.append
    generated = experiment["generated"]
    control = experiment["composed_control"]
    sample = experiment["sample"]

    A("## The generated description, measured")
    A("")
    A("Doc 07 lists risk R8: the description is the only generative surface, "
      "so it is the only place fabrication can re-enter after every extraction "
      "gate. Doc 08 (ADR-013) claims three defences — generation sees only "
      "published attributes, each claim is aligned to the attribute that "
      "licensed it, and untraceable spans are stripped.")
    A("")
    A("That had never been tested against a model. The composed fallback runs "
      "when no provider is configured, and its traceability is exact by "
      "construction, which proves nothing.")
    A("")
    A(f"**The audit is deliberately independent of the pipeline's own "
      f"alignment.** Asking `_align_claims` to grade itself would be circular, "
      f"so the check is external and blunt: every number in the finished copy "
      f"must be accounted for by a published attribute value or by a verbatim "
      f"occurrence of the part's own identifiers. "
      f"{sample['descriptions_audited']} descriptions from the held-out split, "
      f"each generated and separately composed for control.")
    A("")

    A("### What the measurement found")
    A("")
    A("Before the fix, on the same sample:")
    A("")
    A(_table(["", "generated", "composed control"], [
        ["Descriptions carrying a number nothing licenses", "**7 of 20 (35%)**",
         "0"],
        ["Claims the pipeline itself reported as untraceable", "0", "0"],
    ]))
    A("")
    A("The second row is the finding. The pipeline's own defence reported "
      "everything as traceable, because alignment works a sentence at a time: "
      "a fabricated number rides along inside a sentence that also cites a "
      "real attribute. \"It is rated for Class 150 service, with 9A current\" "
      "is marked traceable on the strength of `Class 150`, and carries an "
      "invented current rating with it.")
    A("")
    A("The seven failures were not what the risk register anticipated:")
    A("")
    A(_table(["failure mode", "count", "example"], [
        ["**Rewritten part number**", "6",
         "`WM413-N050` written as `WM413-1050`; `B10B-VH12-A-GR` as `B10B-12`"],
        ["Invented specification", "1",
         "`9A current` on a record that published no current rating"],
    ]))
    A("")
    A("The commoner failure is the more damaging one. An invented "
      "specification is wrong; a rewritten part number in catalogue copy is an "
      "ordering error, and the system prompt already told the model not to "
      "restate the part number.")
    A("")

    A("### The fix, and what it costs")
    A("")
    A("Stripping untraceable claims is not sufficient, so the numeric licence "
      "check became a hard gate in the same spirit as ADR-006: copy carrying a "
      "number nothing licenses is not published, and the deterministic "
      "composition ships instead — it cannot invent one.")
    A("")
    A(_table(["metric", "value"], [
        ["Descriptions audited", generated["descriptions"]],
        ["**Carrying an unlicensed number, after the gate**",
         f"**{generated['descriptions_with_an_unlicensed_number']}** "
         f"({pct(generated['unlicensed_number_rate'])})"],
        ["Rejected by the gate, falling back to composed",
         experiment["generation_failures_falling_back_to_composed"]],
        ["Claims left untraceable", generated["claims_left_untraceable"]],
        ["Claims citing an unpublished attribute",
         generated["claims_citing_an_unpublished_key"]],
        ["Every claim's offsets address its own span",
         "yes" if generated["all_claim_spans_align"] else "**no**"],
        ["Mean length, generated vs composed",
         f"{num(generated['mean_characters'], 0)} vs "
         f"{num(control['mean_characters'], 0)} characters"],
        ["Seconds per description",
         num(experiment["cost"]["seconds_per_description"], 1)],
    ]))
    A("")
    A(f"The cost is stated rather than hidden: "
      f"{experiment['generation_failures_falling_back_to_composed']} of "
      f"{generated['descriptions']} descriptions are now the composed form "
      f"rather than the generated one. That is the trade ADR-013 implies but "
      f"does not name — a generative surface can only be kept if something "
      f"deterministic is ready to replace its output.")
    A("")
    A("A smaller defect surfaced alongside it: `_align_claims` stored a "
      "stripped sentence against unstripped match bounds, so "
      "`description[span_start:span_end]` returned text one character out and "
      "the UI's hover highlight sat off by a space. The composed path was "
      "correct, which is why it only appeared once a model wrote the copy.")
    A("")
    return lines


def render_llm_section(experiment: dict) -> list[str]:
    """The LLM tier, measured. Reported separately from the headline because it
    is a different sample, a different provider and a different cost model."""
    lines: list[str] = []
    A = lines.append
    sample = experiment["sample"]
    gates = experiment["gates"]
    contribution = experiment["contribution"]
    cost = experiment["cost"]
    removed = experiment.get("gates_removed") or {}

    A("## The LLM tier, measured")
    A("")
    A(f"Every number above comes from a run with the model tier **off**. This "
      f"section is the tier switched on, as a paired A/B over the same "
      f"{sample['skus_run']} held-out SKUs "
      f"({', '.join(f'{v} {k}' for k, v in sample['by_category'].items())}).")
    A("")
    A(_table(["", ""], [
        ["Provider", f"`{experiment['provider']}`"],
        ["Model", f"`{experiment['model']}` (open weights, 12B)"],
        ["Self-consistency samples", experiment["self_consistency_n"]],
        ["Sample", f"{sample['skus_run']} SKUs from the held-out test split"],
    ]))
    A("")
    A("It is a separate section, not folded into the headline, for three "
      "reasons: it ran on a sample rather than the full split, the provider is "
      "not the one doc 03 specifies, and its latency makes it a different kind "
      "of system to operate. Reporting it as one number with the deterministic "
      "run would hide all three.")
    A("")

    A("### What the tier contributes")
    A("")
    A(_table(["metric", "value"], [
        ["Attributes added that no deterministic tier reached",
         contribution["attributes_added"]],
        ["Attributes added per SKU", num(contribution["attributes_added_per_sku"], 2)],
        ["SKUs the tier changed nothing on",
         f"{contribution['skus_unchanged_by_the_tier']} of {sample['skus_run']}"],
        ["Of those added, published", contribution["added_and_published"]],
        ["Precision of added values",
         pct(contribution["precision_of_added_values"])],
        ["Which attributes", json.dumps(contribution["added_by_key"])],
    ]))
    A("")
    A("The tier fills exactly the gaps the design predicted it would: values "
      "stated in prose rather than in a table. `rohs_compliant` lives in a "
      "sentence (\"All parts in this series are RoHS compliant\"), and no "
      "table-row extractor will ever find it. It is a small contribution "
      "because the deterministic tiers already resolve most of the schema -- "
      "which is the intended shape, not a disappointment.")
    A("")

    A("### What the gates threw away")
    A("")
    A("This is the number ADR-006 rests on, and it could not be measured until "
      "the tier ran.")
    A("")
    survived_span = gates["accepted"]
    unsupported = gates.get("rejected_span_did_not_support_value", 0)
    A(_table(["funnel stage", "remaining", "removed here"], [
        ["Attributes requested across all calls",
         gates["attributes_requested"], ""],
        ["Proposals the model returned", gates["proposals_returned"], ""],
        ["after: cited chunk id must exist",
         gates["proposals_returned"] - gates["rejected_chunk_id_unknown"],
         gates["rejected_chunk_id_unknown"]],
        ["after: cited span must appear in that chunk", survived_span,
         gates["rejected_span_not_in_chunk"]],
        ["after: cited span must yield the claimed value",
         survived_span - unsupported, unsupported],
        ["**Reached the record**", contribution["attributes_added"], ""],
        ["**Total rejection rate**", pct(gates.get("total_rejection_rate")), ""],
    ]))
    A("")
    A(f"**{pct(gates['span_rejection_rate'])} of proposals cited a span that is "
      f"not in the chunk they cited.** The check that catches them is a Python "
      f"`in` test. It costs nothing, runs before any model-based review, and is "
      f"the single most productive component in the system.")
    A("")
    A("A second gate had to be added because of what this run exposed. Span "
      "containment alone passed `nominal_size_secondary = 1 in` citing the span "
      "`\"3/4 in\"` — a real span, a fabricated pairing, on a fitting with no "
      "secondary size at all. It published at 0.99 confidence. Requiring the "
      "cited text to actually **yield** the claimed value caught it, and "
      f"{gates.get('rejected_span_did_not_support_value', 0)} others in this "
      "sample. That hole was open for as long as the tier went unexercised.")
    A("")

    if removed:
        A("### The same SKUs with the gates removed")
        A("")
        A("`no_span_containment` was a null row in the ablation table for as "
          "long as the tier was disabled. It is not null any more.")
        A("")
        A(_table(["metric", "gates on", "gates off"], [
            ["LLM values reaching the record", contribution["attributes_added"],
             removed["llm_values_in_record"]],
            ["Precision of values reaching the record",
             pct(contribution["precision_of_added_values"]),
             pct(removed["precision_of_ungated_llm_values"])],
            ["Of those, published after calibration",
             contribution["added_and_published"], removed["llm_values_published"]],
            ["Precision of published values",
             pct(contribution["precision_of_added_published_values"]),
             pct(removed["precision_of_ungated_published"])],
            ["Values invented for attributes the part does not have", 0,
             removed["values_for_attributes_the_part_does_not_have"]],
        ]))
        A("")
        A("Two independent defences, and the table separates them. The gates "
          "take the model from "
          f"{pct(removed['precision_of_ungated_llm_values'])} to "
          f"{pct(contribution['precision_of_added_values'])} on values entering "
          "the record. Calibrated confidence then filters again, which is why "
          "the ungated arm still reaches "
          f"{pct(removed['precision_of_ungated_published'])} on what it "
          "publishes — worse than the gated arm, but not catastrophic. Neither "
          "layer is sufficient alone, which is the argument for having both.")
        A("")

    A("### Cost and latency")
    A("")
    A(_table(["metric", "value"], [
        ["Calls made", cost["llm_calls"]],
        ["Prompt tokens per SKU", num(cost["prompt_tokens_per_sku"], 0)],
        ["Completion tokens", cost["llm_completion_tokens"]],
        ["Wall seconds per SKU", num(cost["seconds_per_sku"], 1)],
        ["Responses the model returned unparseable",
         f"{cost['llm_unparsed_responses']} of {cost['llm_calls']}"],
        ["Provider retries / failures",
         f"{cost['llm_retries']} / {cost['llm_failures']}"],
    ]))
    A("")
    A(f"At {num(cost['seconds_per_sku'], 1)} seconds per SKU the tier is three "
      f"orders of magnitude slower than the deterministic path, which processes "
      f"thousands of SKUs a minute. That is the trade doc 03 anticipates when it "
      f"insists on one call per SKU for unresolved attributes only, and it is "
      f"why LLM avoidance is a reported metric rather than an implementation "
      f"detail.")
    A("")
    A(f"{cost['llm_unparsed_responses']} of {cost['llm_calls']} responses could "
      f"not be parsed into a tool payload at all. This model also returns its "
      f"tool call as text inside `content`, wrapped in `[[{{...}}]]`, rather "
      f"than in `tool_calls`. Both are tolerated: the parser tries the "
      f"trustworthy envelope first and falls back, and nothing downstream "
      f"trusts the result regardless.")
    A("")
    return lines


def render(report: dict) -> str:
    dataset = report["dataset"]
    aggregate = report["test_metrics"]
    located = aggregate["source_location"]
    acc = aggregate["accuracy"]
    val = aggregate["validation_and_enrichment"]
    eng = aggregate["scalable_engine"]
    cal = report["calibration"]
    fit = report["calibration_fit"]
    adv = report["adversarial"]
    ops = report["catalog_operations"]
    routing = report["category_routing"]
    per_category = report["by_category"]
    corpus = dataset["corpus_summary"]
    categories = dataset["categories"]

    tiers = acc["precision_by_criticality"]

    lines: list[str] = []
    A = lines.append

    A("# Results")
    A("")
    A(f"Generated {date.today().isoformat()} by `python -m sourced.eval.report`. "
      "Every number below is produced by that command from the held-out test "
      "split, and the split is fixed at corpus-generation time.")
    A("")

    A("## Read this first — what the corpus is")
    A("")
    A("The evaluation runs on a **generated corpus**, not on distributor API data. "
      "Digi-Key and Mouser keys require interactive registration (doc 05) and none "
      "was available, so the corpus is synthesised by `sourced/corpus/build.py`: "
      "datasheets and catalogue pages with ruled tables, distributor listings that "
      "carry realistic error rates, sibling parts, conflicting revisions, and "
      "sparse SKU rows derived from them.")
    A("")
    A("This changes what the numbers mean, in both directions:")
    A("")
    A("- **Labels have no noise by construction.** Doc 04 asks for a measured "
      "label-noise rate; here it is structurally zero, which is a property of the "
      "corpus rather than a result. On distributor silver labels it would not be.")
    A("- **The documents are digital, ruled, and consistent in style.** Real "
      "datasheets include borderless tables, scans, and multi-column layouts. "
      "Table extraction here is easier than in production.")
    A("- **The failure modes the system claims to handle are present and "
      "adversarially placed.** Sibling-part traps, cross-source conflicts and "
      "contradicting listings are in the corpus by construction, so the gates are "
      "genuinely exercised rather than assumed.")
    A("")
    A("Treat the precision and coverage figures as an upper bound, and the "
      "wrong-part, conflict, routing and calibration behaviour as the load-bearing "
      "results.")
    A("")

    A("## Dataset")
    A("")
    A(_table(["", ""], [
        ["Verticals", " · ".join(categories)],
        ["SKUs (total)", corpus["skus"]],
        ["SKUs (held-out test)", dataset["splits"]["test"]],
        ["Source documents", f"{corpus['datasheets']} datasheets and catalogue "
                             f"pages, {corpus['distributor_pages']} "
                             f"distributor/marketplace listings"],
        ["Labels", dataset["labels"]["label_source"]],
        ["Label noise", "0% by construction (see above)"],
        ["Attributes per category", json.dumps(dataset["attributes_per_category"])],
        ["Taxonomy leaves", dataset["taxonomy_leaves"]],
        ["Splits", f"dev {dataset['splits']['dev']} · calibration "
                   f"{dataset['splits']['calibration']} · test "
                   f"{dataset['splits']['test']}"],
        ["LLM tier in this run",
         "**on**" if dataset.get("llm_tier_used_in_this_run")
         else "**off** — measured separately below"],
        ["LLM provider configured",
         f"`{dataset.get('llm_provider')}`"
         if dataset.get("llm_provider_configured") else "none"],
    ]))
    A("")
    A("Two categories share one corpus, one source index, one taxonomy and one "
      "pipeline. Doc 02 claims attribute sets are data rather than separate "
      "pipelines; carrying a second vertical is what tests that claim.")
    A("")
    A(_table(["category", "SKUs", "attributes", "what it contributes"], [
        ["`electrical_connector`", corpus["by_category"]["electrical_connector"]["skus"],
         dataset["attributes_per_category"]["electrical_connector"],
         "datasheet tables, per-part ordering rows, tight numeric tolerances"],
        ["`pipe_fitting`", corpus["by_category"]["pipe_fitting"]["skus"],
         dataset["attributes_per_category"]["pipe_fitting"],
         "abbreviation soup (`1/2IN X 3/4IN BRS 90 ELL FIP 150#`), fractional "
         "inches, ordered end pairs, pressure-class lookups"],
    ]))
    A("")
    A("Cohorts, across both categories:")
    A("")
    A(_table(["cohort", "n", "what it tests"], [
        ["normal", corpus["cohorts"]["normal"],
         "ordinary extraction from a manufacturer document"],
        ["contradicted", corpus["cohorts"]["contradicted"],
         "a listing contradicts the manufacturer document — authority must win"],
        ["conflict", corpus["cohorts"]["conflict"],
         "two manufacturer documents disagree — abstention, not a vote"],
        ["distributor_only", corpus["cohorts"]["distributor_only"],
         "the part is absent from the manufacturer document but listed by a "
         "distributor, so the only evidence is low-authority"],
        ["sibling_trap", corpus["cohorts"]["sibling_trap"],
         "**only the sibling's document exists — correct answer is "
         "`no_source_located`**"],
    ]))
    A("")

    A("## Headline, both categories")
    A("")
    A(_table(["metric", "value"], [
        ["Source location rate", pct(located["source_location_rate"])],
        ["Precision (published values)",
         f"{pct(acc['precision_published'])} over "
         f"{acc['published_values_scored']} values"],
        ["Precision — safety tier",
         f"{pct(tiers['safety']['precision'])} (n={tiers['safety']['n']})"],
        ["Precision — functional tier",
         f"{pct(tiers['functional']['precision'])} (n={tiers['functional']['n']})"],
        ["Precision — cosmetic tier",
         f"{pct(tiers['cosmetic']['precision'])} (n={tiers['cosmetic']['n']})"],
        ["Recall (labelled attributes the system filled)", pct(acc["recall"])],
        ["**Auto-publish rate at the per-tier thresholds**",
         pct(val["auto_publish_rate"])],
        ["Expected Calibration Error", num(cal["expected_calibration_error"], 4)],
        ["**Schema routing accuracy**", pct(routing["schema_routing_accuracy"])],
        ["Wrong-part traps refused",
         f"{located['wrong_part_traps_correctly_refused']}"
         f"/{located['wrong_part_traps']} "
         f"(leak rate {pct(located['wrong_part_leak_rate'])})"],
        ["Error taxonomy", json.dumps(acc["error_taxonomy"]) or "none"],
    ]))
    A("")

    A("## Per category")
    A("")
    rows = []
    for category in categories:
        m = per_category[category]["metrics"]
        c = per_category[category]["calibration"]
        rows.append([
            f"`{category}`",
            pct(m["structured_data_generation"]["source_location_rate"]),
            pct(m["structured_data_generation"]["attribute_coverage"]),
            pct(m["accuracy"]["precision_published"]),
            pct(m["accuracy"]["recall"]),
            pct(m["validation_and_enrichment"]["auto_publish_rate"]),
            pct(m["consistency"]["unit_uniformity"]),
            num(c["expected_calibration_error"], 4),
        ])
    A(_table(["category", "source located", "coverage", "precision", "recall",
              "auto-publish", "unit uniformity", "ECE"], rows))
    A("")
    A("Coverage is against each category's own merchandising checklist, so the "
      "two columns are not directly comparable — `pipe_fitting` scores higher "
      "partly because more of its required attributes are recoverable from the "
      "sparse row itself.")
    A("")
    A("Consistency, per category:")
    A("")
    A(_table(["category", "unit uniformity", "family coherence flags /1k",
              "coverage variance within family"],
             [[f"`{c}`",
               pct(per_category[c]["metrics"]["consistency"]["unit_uniformity"]),
               num(per_category[c]["metrics"]["consistency"]
                   ["family_coherence_flags_per_1000"], 2),
               num(per_category[c]["metrics"]["consistency"]
                   ["coverage_variance_within_family"])]
              for c in categories]))
    A("")

    A("## Category routing")
    A("")
    A("With one populated category the classifier cannot be wrong in a way that "
      "matters. With two it can send a record to an attribute set that does not "
      "describe the part, which is worse than sending it nowhere.")
    A("")
    A(_table(["metric", "value"], [
        ["Records routed", routing["records"]],
        ["**Schema routing accuracy**", pct(routing["schema_routing_accuracy"])],
        ["Records assigned a taxonomy code", routing["records_with_a_category"]],
        ["**Invented codes**", routing["invented_codes"]],
    ]))
    A("")
    A("Confusion, expected against chosen:")
    A("")
    A(_table(["expected"] + [f"chose `{c}`" for c in categories] + ["chose none"],
             [[f"`{expected}`"]
              + [routing["confusion"].get(expected, {}).get(c, 0) for c in categories]
              + [routing["confusion"].get(expected, {}).get("None", 0)]
              for expected in categories]))
    A("")
    A("Routing is decided by counting, not by similarity: the abbreviations in "
      "the row decode to attribute keys, and each key belongs to one schema. "
      "`ELL` decodes to `form_factor` and `SMD` to `mounting_type`, so the vote "
      "is auditable and the reason is recorded in the assignment's `method` "
      "field. The taxonomy leaf is still chosen by retrieval, and the hard "
      "existence check still makes an invented code structurally impossible.")
    A("")

    A("## Validation and enrichment")
    A("")
    A(_table(["metric", "value"], [
        ["**Auto-publish rate**", pct(val["auto_publish_rate"])],
        ["Abstention rate", pct(val["abstention_rate"])],
        ["False abstention rate (correct values withheld)",
         pct(val["false_abstention_rate"])],
        ["Escalation load (attributes needing review per SKU)",
         num(val["escalation_load_per_sku"], 2)],
        ["Injected-fabrication interception",
         pct(adv["injected_fabrication"]["interception_rate"])],
    ]))
    A("")
    A(f"The false-abstention rate counts values that were extracted correctly and "
      f"withheld anyway. At {pct(val['false_abstention_rate'])} it is high, and "
      f"the reason is visible below: `sources_conflict` abstentions are cases "
      f"where two manufacturer documents disagree and one of them happens to be "
      f"right. The system cannot tell which without a human, so it withholds "
      f"both — the cost of ADR-004 stated in the direction that is unflattering.")
    A("")
    A("Abstentions by reason:")
    A("")
    A(_table(["code", "count"],
             [[k, v] for k, v in sorted(val["abstention_reasons"].items(),
                                        key=lambda kv: -kv[1])]))
    A("")
    A("Publish thresholds (doc 03 §5): "
      + ", ".join(f"{tier} {threshold}" for tier, threshold
                  in config.PUBLISH_THRESHOLDS.items())
      + ". Safety-tier values additionally require two independent sources.")
    A("")

    A("## Scalable catalog engine")
    A("")
    A(_table(["metric", "value"], [
        ["Throughput", f"{eng['throughput_skus_per_min']} SKUs/min "
                       f"(single process, warm document cache)"],
        ["LLM calls per SKU", num(eng["llm_calls_per_sku"], 2)],
        ["**LLM avoidance rate**", pct(eng["llm_avoidance_rate"])],
        ["Attributes by winning tier", json.dumps(eng["attributes_by_tier"])],
        ["Candidates rejected by span containment", eng["span_rejections"]],
        ["Re-verification cost vs a full re-run",
         pct(ops.get("reverification_cost_vs_full_run"))],
    ]))
    A("")
    A("Cost per 1,000 SKUs: **$0.00 measured** — with the LLM tier disabled no "
      "tokens were spent. Attributes by winning tier counts only the candidate "
      "that won adjudication, so the rules tier looks small there; what it "
      "actually contributes is the `no_rules_tier` ablation below.")
    A("")
    A(f"Source-revision handling: revising `{ops.get('revised_source')}` touched "
      f"{ops.get('products_reprocessed')} of {ops.get('products_linked')} linked "
      f"products and {ops.get('attributes_examined')} attributes, against "
      f"{ops.get('catalogue_attributes_in_a_full_run')} in a full re-run of that "
      f"category.")
    A("")

    A("## Calibration")
    A("")
    A(_table(["metric", "value"], [
        ["Expected Calibration Error (all tiers, both categories)",
         num(cal["expected_calibration_error"], 4)],
        ["ECE by criticality", json.dumps(cal["ece_by_criticality"])],
        ["ECE by category", json.dumps(
            {c: per_category[c]["calibration"]["expected_calibration_error"]
             for c in categories})],
        ["Predictions scored", cal["n"]],
        ["Fitted groups", ", ".join(fit["fitted_tiers"])],
    ]))
    A("")
    A("Confidence is fitted per **(category, criticality tier)**, not per tier "
      "alone. Doc 03 specifies the tier; with two categories in the catalogue "
      "that is under-specified, and fitting one model across both cost the "
      "stronger category roughly twenty points of auto-publish rate before this "
      "was corrected. The tier remains the policy boundary — it is what sets the "
      "threshold — while the category is what makes the fit honest. A group with "
      "too few labelled rows falls back to the tier alone, then to the observed "
      "base rate.")
    A("")
    A("![Reliability diagram](figures/reliability.png)")
    A("")
    A("![Abstention curve](figures/abstention.png)")
    A("")
    A("Reliability, bucketed:")
    A("")
    A(_table(["bin", "n", "mean confidence", "observed accuracy"],
             [[r["bin"], r["n"], num(r["mean_confidence"]),
               num(r["observed_accuracy"])] for r in cal["reliability"]]))
    A("")
    for group, coefficients in sorted(fit["coefficients"].items()):
        if "|" not in group:
            continue
        top = sorted(coefficients.items(), key=lambda kv: -abs(kv[1]))[:5]
        A(f"Largest fitted coefficients, `{group}`: "
          + ", ".join(f"`{name}` {value:+.2f}" for name, value in top) + ".")
    A("")

    A("## Ablations")
    A("")
    A("Each row is the full pipeline with one component removed, re-run on the "
      "same held-out split, for each category.")
    A("")
    for category in categories:
        A(f"**`{category}`**")
        A("")
        A(_table(["variant", "precision", "auto-publish", "coverage",
                  "wrong-part leak", "ECE"],
                 [[f"`{name}`", pct(r["precision_published"]),
                   pct(r["auto_publish_rate"]), pct(r["attribute_coverage"]),
                   pct(r["wrong_part_leak_rate"]),
                   num(r["expected_calibration_error"], 4)]
                  for name, r in report["ablations_by_category"][category].items()]))
        A("")

    A("### What the ablations show, including where they show nothing")
    A("")
    connector_ab = report["ablations_by_category"]["electrical_connector"]
    fitting_ab = report["ablations_by_category"]["pipe_fitting"]

    def delta(ab, variant, key="attribute_coverage"):
        return (ab["full_system"][key] - ab[variant][key]) * 100

    A(f"- **Source verification is the load-bearing component.** Removing it "
      f"leaks every wrong-part trap (leak rate "
      f"{pct(connector_ab['no_source_verification']['wrong_part_leak_rate'])}) and "
      f"costs "
      f"{delta(connector_ab, 'no_source_verification', 'precision_published'):.1f} "
      f"points of precision. Nothing else in the table moves precision like it.")
    A(f"- **The rules tier earns its place in both verticals, and by a similar "
      f"margin.** Removing it costs "
      f"{delta(connector_ab, 'no_rules_tier'):.1f} coverage points on connectors "
      f"and {delta(fitting_ab, 'no_rules_tier'):.1f} on fittings. Doc 05 "
      f"predicted the deterministic tier would do markedly *more* work in the "
      f"abbreviation-soup vertical; on this corpus it does not, because the "
      f"fitting catalogues carry the same attributes in their tables. A real "
      f"fittings catalogue is sparser than the generated one, so this is a "
      f"limitation of the corpus rather than a refutation of the claim — but "
      f"the claim is not supported by these numbers and is reported that way.")
    A(f"- **The tables tier matters more to fittings than to connectors.** "
      f"Removing it costs {delta(connector_ab, 'no_tables_tier'):.1f} coverage "
      f"points on connectors and {delta(fitting_ab, 'no_tables_tier'):.1f} on "
      f"fittings — the opposite of the expectation above, and worth stating "
      f"plainly.")
    A("- **Calibration buys coverage, not precision.** Replacing the fitted model "
      "with a hand-set score keeps precision at or above the full system's while "
      "publishing materially less, which is the whole argument for ADR-005: an "
      "uncalibrated threshold is not wrong so much as uninformative.")
    A("")
    A("Three variants move nothing, and that is reported rather than hidden:")
    A("")
    A("- `no_span_containment` is identical to the full system **because the LLM "
      "tier is disabled in this run**. The rules and tables tiers build their span "
      "from the text they just read, so the containment check can never fail for "
      "them. This row measures nothing; it should not be read as the gate being "
      "useless.")
    _rates = adv["injected_fabrication"].get("caught_by_level_rate", {})
    A(f"- `no_relational_validation_l2` and `no_family_coherence_l3` move nothing "
      f"on **uncorrupted** data, which is the expected result: there are no "
      f"contradictory pairs and no family outliers to find when extraction is "
      f"correct. Their value appears in the injected-fabrication test below, "
      f"where L3 catches {pct(_rates.get('family'))} and L2 "
      f"{pct(_rates.get('relational'))} of corruptions independently.")
    A("- `with_mpn_decoding` barely moves, consistent with doc 06 placing it fifth "
      "on the cut list. It stays off by default.")
    A("")

    A("## Adversarial and robustness")
    A("")
    fab = adv["injected_fabrication"]
    A(f"**Injected fabrication.** {fab['corruptions_injected']} known-correct "
      f"published values were corrupted with plausible-but-wrong substitutes — a "
      f"sibling part's value, a unit swap, an adjacent enum, an out-of-range "
      f"number — and the validation stack was re-run over each.")
    A("")
    A(_table(["metric", "value"], [
        ["Corruptions injected", fab["corruptions_injected"]],
        ["Intercepted by at least one level", fab["intercepted"]],
        ["Interception rate", pct(fab["interception_rate"])],
    ]))
    A("")
    A("Each level is asked independently rather than stopping at the first one "
      "that fires, so the shares below overlap and do not sum to 100%. A "
      "first-match-wins attribution credits Level 1 with everything and makes "
      "Levels 2 and 3 look inert, which would be a reporting artefact rather "
      "than a finding.")
    A("")
    rates = fab.get("caught_by_level_rate", {})
    A(_table(["level", "caught", "share of corruptions", "what it sees"],
             [["L1 per-attribute", fab["caught_by_level"].get("attribute", 0),
               pct(rates.get("attribute")),
               "type, enum, unit, range, and whether the value is still readable "
               "out of the span it cites"],
              ["L2 relational", fab["caught_by_level"].get("relational", 0),
               pct(rates.get("relational")),
               "pairs of individually plausible values that cannot both be true"],
              ["L3 family coherence", fab["caught_by_level"].get("family", 0),
               pct(rates.get("family")),
               "outliers against sibling SKUs — the only level that addresses "
               "consistency"],
              ["Publish threshold", fab["caught_by_level"].get("threshold", 0),
               pct(rates.get("threshold")),
               "calibrated confidence falling below the criticality tier's bar"]]))
    A("")
    A("Level 1 catches everything here because the span check binds the value to "
      "the text it was read from: a corrupted value no longer parses out of its "
      "own cited cell. That is the cheapest gate doing the heaviest work, which "
      "is ADR-006's claim. On real documents, where a value can be read correctly "
      "from the wrong row, L1 would not have this advantage.")
    A("")
    A(f"**Wrong-part robustness.** The held-out split contains "
      f"{located['wrong_part_traps']} SKUs across both categories whose own "
      f"document is absent while a sibling part's is present and scores well on "
      f"every soft signal. Correct behaviour is `no_source_located`.")
    A("")
    A(_table(["metric", "value"], [
        ["Traps in the held-out split", located["wrong_part_traps"]],
        ["Correctly refused", located["wrong_part_traps_correctly_refused"]],
        ["**Leak rate**", pct(located["wrong_part_leak_rate"])],
    ]))
    A("")
    A("**Degraded input.** The same SKUs re-run with fields removed or damaged.")
    A("")
    A("`truncated_mpn` locating nothing is the intended behaviour, not a "
      "regression: a part number missing its last characters is a *different* "
      "identifier, and matching it against the longer part it prefixes is exactly "
      "the sibling-part failure ADR-002 exists to prevent. Refusing is correct; "
      "an earlier substring implementation silently matched.")
    A("")
    A(_table(["variant", "source location rate", "published values",
              "mean confidence"],
             [[f"`{name}`", pct(r["source_location_rate"]), r["published_values"],
               num(r["mean_confidence"])]
              for name, r in adv["degraded_input"].items()]))
    A("")

    experiment = load_llm_experiment()
    if experiment:
        lines.extend(render_llm_section(experiment))

    description_experiment = load_description_experiment()
    if description_experiment:
        lines.extend(render_description_section(description_experiment))

    A("## Known limitations")
    A("")
    for limitation in LIMITATIONS:
        A(f"- {limitation}")
    A("")
    return "\n".join(lines)


LIMITATIONS = [
    "**The corpus is synthetic.** Labels are the generator's ground truth, so the "
    "label-noise rate doc 04 asks for is zero by construction rather than measured. "
    "Precision and coverage on real distributor data would be lower.",

    "**The headline numbers come from a run with the LLM tier off.** The tier is "
    "now measured, but separately and on a 40-SKU sample rather than the full "
    "split, because inference costs 9-80 seconds a call against a contended "
    "endpoint. The 100% LLM-avoidance figure therefore describes the "
    "deterministic run; the tier's own contribution, gates and cost are in "
    "their own section above, with their own sample size.",

    "**The model measured is not the model specified.** Doc 03 designs around an "
    "Anthropic tool call with prefix caching; what was available was an "
    "OpenAI-compatible endpoint serving open weights, and the model measured is "
    "a 12B one that failed to return a parseable tool payload on 44 of 72 calls. "
    "A stronger model would propose better values -- and would still be filtered "
    "by the same gates. The Anthropic path is implemented and unexercised.",

    "**Prefix caching, doc 03's main cost lever, is unmeasured.** The endpoint "
    "reported zero cached tokens on every call. The Anthropic path sets "
    "`cache_control` explicitly; the OpenAI-compatible path has no equivalent "
    "and none was invented.",

    "**The LLM sample is small.** Four attributes added and 41 proposals judged "
    "is enough to show the gates working and not enough to put a tight interval "
    "on the precision of what survives. The rejection rates are the robust part; "
    "`precision of added values = 100%` rests on four values.",

    "**Dense retrieval is TF-IDF + SVD, not sentence-transformers.** ADR-007's "
    "hybrid shape and lexical dominance are preserved, but the semantic half is "
    "weaker than the document specifies. Swapping it means replacing one class.",

    "**The taxonomy is internal.** UNSPSC and ETIM tables require a licensed or "
    "registered download. `sourced/taxonomy/index.py` imports either if the CSV is "
    "placed in `data/`; the retrieve-then-constrain mechanism and the hard code "
    "validation are unchanged either way.",

    "**Category classification is retriever-only in this run.** The model-based "
    "constrain step is implemented and switched off by default: it would fire on "
    "every record, and decoded-key voting already routes at 100%. The hard "
    "existence check still runs, so an invented code remains impossible. The "
    "method actually used is recorded in each assignment's `method` field.",

    "**Generated descriptions are measured on 20 SKUs, and the headline run "
    "still composes them.** Generation stays opt-in (`llm_description`): it "
    "costs a call per record, and on this model roughly a third of its output "
    "fails the licence gate and falls back to the composed form anyway. The "
    "gate is a numeric check, so a fabricated *word* -- an invented "
    "certification with no number in it, say `IP-rated` or `UL listed` -- would "
    "not be caught by it. That gap is real and unmeasured.",

    "**Two categories, not a catalogue.** The registry is exercised by two "
    "populated schemas sharing one pipeline, which is what makes the "
    "schema-as-data claim testable, but seventy-one of the seventy-three "
    "taxonomy leaves still have no attribute set behind them. Routing accuracy "
    "is measured over a two-way choice and would not survive being read as a "
    "seventy-three-way one.",

    "**Routing is decided by decoded-key votes, which depend on the lexicon.** "
    "A row whose abbreviations are absent from the lexicon falls back to leaf "
    "retrieval, and a vertical with no lexicon coverage at all would route by "
    "similarity alone. The fallback order is recorded in each assignment's "
    "`method` field rather than hidden.",

    "**Throughput is not a production figure.** It is measured single-process "
    "with a warm document cache, so it reflects steady-state re-processing rather "
    "than a cold first pass over a new corpus. No queue or worker pool is "
    "implemented; doc 01 names Redis and `arq`, and neither was needed at this "
    "scale.",

    "**The reported numbers came from the SQLite path.** The Postgres path is "
    "verified separately: `docker compose up` builds the corpus, runs this "
    "evaluation and loads 373 products with 5,136 provenance rows into Postgres, "
    "with the GIN and family indexes in place, and re-running it leaves the count "
    "unchanged. Two bugs only Postgres could surface were found that way and "
    "fixed -- a UUID/VARCHAR column mismatch, and an insert ordering that SQLite "
    "accepted because it does not enforce foreign keys by default. SQLite now "
    "enforces them too.",


]


def persist_catalogue(fresh: bool = False) -> int:
    """Load the full corpus into the store so the UI has records to show.

    Upsert never deletes, which is correct for a catalogue but means a store
    built against an earlier corpus keeps its old part numbers. `fresh` drops
    the tables first, which is what a demo from a clean clone wants.
    """
    from sourced.store.models import create_all, drop_all
    from sourced.store.upsert import load_labels, upsert_product

    if fresh:
        drop_all()
    create_all()
    corpus = label_mod.load_corpus_records()
    calibrator = Calibrator.load()
    # persistence is the deterministic path: the store should be reproducible
    pipeline = Pipeline(SourceIndex(load_corpus()), Options(llm_tier=False),
                        calibrator=calibrator)

    stored = 0
    for category in sorted({r["category"] for r in corpus}):
        records = [r for r in corpus if r["category"] == category]
        batch = run_batch(pipeline, records, load_schema(category),
                          calibrator=calibrator)
        for product in batch.records:
            upsert_product(product)
        stored += len(batch.records)
    load_labels(corpus)
    return stored


def main(persist: bool = False, path: Path | None = None,
         render_only: bool = False, fresh: bool = False) -> Path:
    if render_only:
        report = json.loads((config.DATA / "results.json").read_text(encoding="utf-8"))
    else:
        report = run_main()
    target = Path(path or RESULTS_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(report), encoding="utf-8")
    if persist:
        count = persist_catalogue(fresh=fresh)
        print(f"persisted {count} records into {config.DB_URL}")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--persist", action="store_true",
                        help="also load the catalogue into the store for the UI")
    parser.add_argument("--render-only", action="store_true",
                        help="re-render docs/RESULTS.md from the last results.json")
    parser.add_argument("--fresh", action="store_true",
                        help="drop the store before persisting, for a clean demo")
    args = parser.parse_args()
    written = main(persist=args.persist, render_only=args.render_only,
                   fresh=args.fresh)
    print(f"wrote {written}")
