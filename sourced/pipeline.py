"""The pipeline (doc 01 System shape).

Stage 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6, in that order, with the terminal states
and abstentions each stage is entitled to produce.

Ablation flags exist so doc 04's ablation table is produced by the same code
path that produces the headline numbers, rather than by a separate script that
may drift from it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from sourced import config
from sourced.adjudicate.rank import adjudicate
from sourced.candidates.llm import ExtractionStats, llm_candidates, select_context
from sourced.candidates.rules import MpnDecoder, rule_candidates
from sourced.candidates.tables import table_candidates
from sourced.commerce import completeness as completeness_mod
from sourced.commerce import description as description_mod
from sourced.commerce.title import build_title
from sourced.confidence.calibrate import Calibrator
from sourced.confidence.features import features as extract_features
from sourced.discovery.manufacturer import resolve as resolve_manufacturer
from sourced.discovery.mpn import family_prefix, normalise_mpn
from sourced.discovery.retrieve import SourceIndex
from sourced.discovery.verify import verify_match
from sourced.ingest.chunks import Chunk, Document
from sourced.models import (AbstentionReason, AttributeValue, Candidate,
                            CommerceOutput, ProductRecord, SkuInput, SourceLink)
from sourced.registry import CategorySchema, load_schema
from sourced.taxonomy.classify import classify, refine_category, schema_for
from sourced.taxonomy.index import TaxonomyIndex, default_index
from sourced.validate.attribute import validate_attribute
from sourced.validate.relational import rule_severity, validate_relational


@dataclass
class Options:
    """Ablation switches. All True is the full system."""

    verify_sources: bool = True
    span_containment: bool = True
    rules_tier: bool = True
    tables_tier: bool = True
    llm_tier: bool = True                   # Stage 2.3 extraction
    # The model is three separate expenses, not one. Extraction fires only on
    # attributes the cheap tiers left unresolved, which is the cost lever doc 03
    # designs for. Classification and description would fire on every record,
    # so they are opt-in and measured separately.
    llm_classification: bool = False        # Stage 1 constrain step
    llm_description: bool = False           # Stage 6 generation
    relational_validation: bool = True      # Level 2
    family_validation: bool = True          # Level 3, applied by the batch runner
    calibrated_confidence: bool = True
    mpn_decoding: bool = False              # doc 06 cut list item 5
    classify_category: bool = True
    commerce_output: bool = True
    retrieval_k: int = 25
    default_category: str = "electrical_connector"


@dataclass
class Telemetry:
    llm_calls: int = 0
    attributes_from_rules: int = 0
    attributes_from_tables: int = 0
    attributes_from_llm: int = 0
    candidates_generated: int = 0
    candidates_rejected_span: int = 0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


class Pipeline:
    def __init__(self, index: SourceIndex, options: Options | None = None,
                 calibrator: Calibrator | None = None,
                 taxonomy: TaxonomyIndex | None = None,
                 decoder: MpnDecoder | None = None):
        self.index = index
        self.options = options or Options()
        self.calibrator = calibrator
        self.taxonomy = taxonomy or default_index()
        self.decoder = decoder
        # accumulated across the batch, so the tier's cost and rejection rate
        # are measurable rather than anecdotal
        self.llm_stats = ExtractionStats()

    # ------------------------------------------------------------ stage 0
    def discover(self, sku: SkuInput) -> tuple[list[tuple[Document, float, str]], list[Document]]:
        candidates = self.index.retrieve(sku, k=self.options.retrieval_k)
        verified: list[tuple[Document, float, str]] = []
        for doc in candidates:
            if not self.options.verify_sources:
                # ablation: accept the top retrieval without the match gate
                verified.append((doc, 0.5, "accepted without verification"))
                continue
            result = verify_match(sku, doc)
            if result.matched:
                evidence = (f"{result.evidence.span} in {result.evidence.chunk_id}"
                            if result.evidence else "")
                verified.append((doc, result.score, evidence))
        if not self.options.verify_sources:
            verified = verified[:3]
        verified.sort(key=lambda v: (v[0].authority_rank, -v[1]))
        return verified, candidates

    # ------------------------------------------------------------ run
    def run(self, sku: SkuInput, schema: CategorySchema | None = None) -> ProductRecord:
        started = time.perf_counter()
        telemetry = Telemetry()

        manufacturer_resolved, _ = resolve_manufacturer(sku.manufacturer)
        record = ProductRecord(
            mpn=sku.mpn,
            mpn_normalised=normalise_mpn(sku.mpn),
            manufacturer=sku.manufacturer,
            manufacturer_resolved=manufacturer_resolved,
        )

        verified, considered = self.discover(sku)
        if not verified:
            record.source_status = "not_located"
            record.abstention = AbstentionReason(
                code="no_source_located",
                detail=(f"No document containing MPN {sku.mpn} was found among "
                        f"{len(considered)} retrieved candidates."),
                resolution_hint=("Supply a manufacturer datasheet or catalog page "
                                 "for this part number."),
            )
            record.telemetry = _telemetry_dict(telemetry, started)
            return record

        record.source_status = "verified"
        record.sources = [
            SourceLink(source_id=doc.source_id, source_type=doc.source_type,
                       authority_rank=doc.authority_rank, match_confidence=round(score, 4),
                       match_evidence=evidence, uri=doc.uri,
                       content_hash=doc.content_hash)
            for doc, score, evidence in verified]
        record.source_content_hash = _combined_hash(record.sources)
        match_confidence = max(s.match_confidence for s in record.sources)

        # ---------------------------------------------------------- stage 1
        if schema is None:
            schema = self._select_schema(sku, record)
        elif self.options.classify_category:
            # the caller fixed the attribute set, but the record should still
            # say what the part is; a stored record with no category is not a
            # catalogue entry
            assignment = classify(sku, index=self.taxonomy,
                                  use_llm=self.options.llm_classification)
            schema_for(assignment, self.taxonomy, sku=sku)   # reconciles the leaf
            record.category = assignment
        record.attribute_schema = schema.category

        # ---------------------------------------------------------- stage 2
        chunks: dict[str, Chunk] = {}
        candidates: list[Candidate] = []

        if self.options.rules_tier:
            rule_cands = rule_candidates(sku, schema)
            for c in rule_cands:
                chunks[c.evidence.chunk_id] = _chunk_for(sku, c)
            candidates.extend(rule_cands)
            if self.options.mpn_decoding and self.decoder is not None:
                decoded = self.decoder.decode(sku, schema)
                for c in decoded:
                    chunks[c.evidence.chunk_id] = _chunk_for(sku, c)
                candidates.extend(decoded)

        if self.options.tables_tier:
            for doc, _, _ in verified:
                for c in doc.chunks:
                    chunks[c.chunk_id] = c
                candidates.extend(table_candidates(sku, doc, schema))

        resolved_keys = {c.canonical_key for c in candidates}
        unresolved = [k for k in schema.keys if k not in resolved_keys]
        if self.options.llm_tier and config.LLM_ENABLED and unresolved:
            context = select_context(sku, [c for doc, _, _ in verified for c in doc.chunks])
            for c in context:
                chunks[c.chunk_id] = c
            from sourced.candidates.llm import SELF_CONSISTENCY_N

            llm_cands = llm_candidates(sku, schema, unresolved, context,
                                       stats=self.llm_stats)
            telemetry.llm_calls += SELF_CONSISTENCY_N
            candidates.extend(llm_cands)

        telemetry.candidates_generated = len(candidates)

        # ---------------------------------------------------- stage 4 level 1
        surviving: list[Candidate] = []
        for candidate in candidates:
            spec = schema.spec(candidate.canonical_key)
            if spec is None:
                continue
            checks = validate_attribute(candidate, spec, chunks)
            if self.options.span_containment and not (
                    checks["span_present"].passed
                    and checks["value_supported_by_span"].passed):
                telemetry.candidates_rejected_span += 1
                self.llm_stats.rejected_span_unsupported += (
                    1 if candidate.tier == "llm"
                    and checks["span_present"].passed else 0)
                continue
            if not (checks["type_conformant"].passed and checks["enum_valid"].passed
                    and checks["unit_parsed"].passed):
                continue
            surviving.append(candidate)

        # ---------------------------------------------------------- stage 3
        by_key: dict[str, list[Candidate]] = {}
        for candidate in surviving:
            by_key.setdefault(candidate.canonical_key, []).append(candidate)

        attributes: dict[str, AttributeValue] = {}
        for key in schema.keys:
            spec = schema.spec(key)
            resolution = adjudicate(by_key.get(key, []), spec)
            attr = AttributeValue(
                canonical_key=key,
                raw_key=resolution.winner.raw_text if resolution.winner else "",
                criticality=spec.criticality if spec else "functional",
                resolution="abstained",
                agreement_count=max(1, resolution.independent_sources),
            )
            if resolution.winner is None:
                attr.abstention_reason = resolution.abstention
                attr.rejected_candidates = resolution.rejected
                attributes[key] = attr
                continue

            winner = resolution.winner
            attr.value = winner.value
            attr.unit = winner.unit
            attr.raw_text = winner.raw_text
            attr.winning_candidate = winner
            attr.rejected_candidates = resolution.rejected
            attr.alternatives = winner.alternatives
            attr.checks = validate_attribute(winner, spec, chunks) if spec else {}
            attr.resolution = "review" if resolution.close_call else "published"
            attr.features = {"close_call": float(resolution.close_call)}
            attributes[key] = attr

            telemetry_key = {"rule": "attributes_from_rules",
                             "table": "attributes_from_tables",
                             "llm": "attributes_from_llm"}[winner.tier]
            setattr(telemetry, telemetry_key, getattr(telemetry, telemetry_key) + 1)

        record.attributes = attributes

        # ---------------------------------------------------- stage 4 level 2
        if self.options.relational_validation:
            relational = validate_relational(attributes, schema)
            for key, checks in relational.items():
                if key in attributes:
                    attributes[key].checks.update(checks)
                    for rule_id, result in checks.items():
                        if not result.passed and rule_severity(schema, rule_id) == "error":
                            _abstain(attributes[key], "failed_validation",
                                     result.detail or rule_id,
                                     "Two extracted values cannot both be true. "
                                     "Re-check the source rows they came from.")

        # ---------------------------------------------------------- stage 5
        self._apply_confidence(record, match_confidence)

        # Stage 1, second pass. The schema is fixed; only the taxonomy leaf can
        # move, now that the attributes say what the part actually is.
        if self.options.classify_category:
            refine_category(record.category, record.attribute_schema,
                            {k: a.value for k, a in record.attributes.items()
                             if a.resolution == "published" and a.value is not None},
                            sku, self.taxonomy)

        # ---------------------------------------------------------- stage 6
        if self.options.commerce_output:
            self._build_commerce(record, schema)

        record.telemetry = _telemetry_dict(telemetry, started)
        record.telemetry["match_confidence"] = round(match_confidence, 4)
        return record

    # ------------------------------------------------------------ helpers
    def _select_schema(self, sku: SkuInput, record: ProductRecord) -> CategorySchema:
        if not self.options.classify_category:
            return load_schema(self.options.default_category)
        assignment = classify(sku, index=self.taxonomy,
                              use_llm=self.options.llm_classification)
        record.category = assignment
        name = schema_for(assignment, self.taxonomy, sku=sku)
        if name is None:
            # the category was identified but no attribute set is populated for
            # it; falling back to whichever schema is first is how a fitting
            # gets extracted against a connector's attributes
            record.telemetry["schema_unresolved"] = 1.0
            name = self.options.default_category
        try:
            return load_schema(name)
        except FileNotFoundError:
            return load_schema(self.options.default_category)

    def _apply_confidence(self, record: ProductRecord, match_confidence: float) -> None:
        score_and_decide(record, self.calibrator, self.options.calibrated_confidence,
                         match_confidence)

    def _build_commerce(self, record: ProductRecord, schema: CategorySchema) -> None:
        title, template, inputs = build_title(record, schema)
        text, claims, generator = description_mod.generate(
            record, schema, use_llm=self.options.llm_description)
        record.commerce = CommerceOutput(
            title=title, title_template=template, title_inputs=inputs,
            description=text, description_claims=claims,
            description_generator=generator,
            facets=description_mod.facets(record, schema))
        record.completeness = completeness_mod.score(record, schema)


def score_and_decide(record: ProductRecord, calibrator: Calibrator | None,
                     calibrated: bool, match_confidence: float | None = None) -> None:
    """Stage 5. Separated from `Pipeline.run` because it must be re-run after
    Level 3 family checks, which need the whole batch to exist first, and after
    the calibrator has been fitted on the calibration split."""
    if match_confidence is None:
        match_confidence = float(record.telemetry.get("match_confidence", 0.0))

    for attr in record.attributes.values():
        if attr.winning_candidate is None:
            continue
        close_call = bool(attr.features.get("close_call"))
        feature_map = extract_features(attr, match_confidence=match_confidence,
                                       degraded_path=False, close_call=close_call)
        attr.features = feature_map

        if calibrated and calibrator is not None:
            attr.confidence = calibrator.predict(attr.criticality, feature_map,
                                                 record.attribute_schema)
        else:
            # ablation / unfitted path: a plausible hand-set score of exactly the
            # kind doc 04 argues against trusting
            attr.confidence = _heuristic_confidence(feature_map)

        threshold = config.PUBLISH_THRESHOLDS.get(attr.criticality, 0.95)
        failed = [name for name, check in attr.checks.items() if not check.passed]
        if failed:
            _abstain(attr, "failed_validation",
                     "; ".join(f"{n}: {attr.checks[n].detail or 'failed'}" for n in failed),
                     "A deterministic check rejected this value. Inspect the cited "
                     "source region.")
        elif attr.confidence < threshold:
            _abstain(attr, "below_threshold",
                     f"calibrated confidence {attr.confidence:.3f} is below the "
                     f"{attr.criticality} threshold of {threshold}",
                     "A second independent source, or a stronger evidence locator, "
                     "would raise this above the threshold.")
        elif (attr.criticality == "safety" and config.SAFETY_REQUIRES_DUAL_SOURCE
              and attr.agreement_count < 2):
            attr.resolution = "review"
            attr.abstention_reason = None
        else:
            attr.resolution = "published"
            attr.abstention_reason = None


def _abstain(attr: AttributeValue, code: str, detail: str, hint: str) -> None:
    attr.resolution = "abstained"
    attr.abstention_reason = AbstentionReason(code=code, detail=detail,  # type: ignore[arg-type]
                                              resolution_hint=hint)


def _heuristic_confidence(feature_map: dict[str, float]) -> float:
    """Uncalibrated baseline used by the calibration ablation: a plausible
    hand-set score of exactly the kind doc 04 argues against trusting."""
    score = 0.55
    score += 0.20 * feature_map.get("locator_table_cell", 0.0)
    score += 0.10 * feature_map.get("locator_structured_field", 0.0)
    score += 0.10 * feature_map.get("tier_table", 0.0)
    score += 0.10 * feature_map.get("agreement_count", 0.0)
    score += 0.10 * feature_map.get("source_authority", 0.0)
    score -= 0.25 * (1 - feature_map.get("span_present", 1.0))
    score -= 0.15 * feature_map.get("locator_inferred", 0.0)
    return max(0.0, min(0.999, score))


def _chunk_for(sku: SkuInput, candidate: Candidate) -> Chunk:
    from sourced.candidates.rules import INPUT_SOURCE_ID

    text = (sku.description_fragment or "") if candidate.evidence.chunk_id.startswith(
        f"{INPUT_SOURCE_ID}:") else sku.mpn
    return Chunk(chunk_id=candidate.evidence.chunk_id, source_id=candidate.evidence.source_id,
                 source_type=candidate.evidence.source_type, text=text,
                 locator=candidate.evidence.locator)


def _combined_hash(sources: list[SourceLink]) -> str:
    import hashlib

    joined = "|".join(sorted(f"{s.source_id}:{s.content_hash}" for s in sources))
    return "sha256:" + hashlib.sha256(joined.encode()).hexdigest()


def _telemetry_dict(telemetry: Telemetry, started: float) -> dict[str, float]:
    telemetry.seconds = round(time.perf_counter() - started, 4)
    return {
        "llm_calls": float(telemetry.llm_calls),
        "attributes_from_rules": float(telemetry.attributes_from_rules),
        "attributes_from_tables": float(telemetry.attributes_from_tables),
        "attributes_from_llm": float(telemetry.attributes_from_llm),
        "candidates_generated": float(telemetry.candidates_generated),
        "candidates_rejected_span": float(telemetry.candidates_rejected_span),
        "seconds": telemetry.seconds,
    }


def family_key(record: ProductRecord) -> tuple[str | None, str]:
    return (record.manufacturer_resolved, family_prefix(record.mpn_normalised))


__all__ = ["Pipeline", "Options", "Telemetry", "family_key"]
