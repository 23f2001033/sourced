"""The tests doc 06 says may never be cut.

These are gates, not coverage. Each one corresponds to a claim the system makes
about itself, and a failure here invalidates the claim rather than degrading it.
"""
from __future__ import annotations

import pytest

from sourced import config
from sourced.candidates.rules import rule_candidates
from sourced.candidates.tables import table_candidates
from sourced.discovery.retrieve import SourceIndex
from sourced.discovery.verify import verify_match
from sourced.eval import labels as label_mod
from sourced.ingest.loader import load_corpus
from sourced.ingest.normalize import values_match
from sourced.models import Candidate, Evidence, SkuInput
from sourced.pipeline import Options, Pipeline
from sourced.registry import load_schema


@pytest.fixture(scope="session")
def corpus():
    records = label_mod.load_corpus_records()
    assert records, "run `python -m sourced.corpus.build` first"
    return records


@pytest.fixture(scope="session")
def docs():
    return load_corpus()


@pytest.fixture(scope="session")
def index(docs):
    return SourceIndex(docs)


@pytest.fixture(scope="session")
def schema():
    return load_schema("electrical_connector")


@pytest.fixture(scope="session")
def pipeline(index):
    """The LLM tier is off in the gates on purpose.

    These tests assert deterministic behaviour, and a suite that reaches a
    remote model is neither fast nor repeatable. The tier has its own measured
    experiment in `sourced/eval/llm_experiment.py`.
    """
    return Pipeline(index, Options(llm_tier=False))


# ------------------------------------------------------- the day-2 build gate


def test_wrong_part_robustness(corpus, index, pipeline, schema):
    """Given an MPN whose datasheet is absent but whose *sibling's* datasheet is
    present, the system must return `no_source_located` rather than extracting
    from the sibling. Doc 06 calls this the single most important test in the
    build."""
    traps = [r for r in corpus if r["cohort"] == "sibling_trap"]
    assert len(traps) >= 20, "corpus does not contain enough traps to be meaningful"

    leaks = []
    for record in traps:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        if product.source_status != "not_located":
            leaks.append((record["sku_input"]["mpn"],
                          [s.source_id for s in product.sources]))
        else:
            assert product.abstention is not None
            assert product.abstention.code == "no_source_located"
            assert product.abstention.resolution_hint

    assert not leaks, f"extracted from a sibling's datasheet for {leaks[:5]}"


def test_sibling_datasheet_exists_for_every_trap(corpus, docs):
    """The trap is only a trap if the sibling document is genuinely present and
    genuinely tempting."""
    traps = [r for r in corpus if r["cohort"] == "sibling_trap"]
    for record in traps[:10]:
        family = record["family_id"]
        assert f"{family}_ds" in docs, f"no sibling datasheet for {family}"


# --------------------------------------------------------- span containment


def test_every_candidate_cites_a_span_that_exists(corpus, index, schema, docs):
    """Doc 06 2.2: every produced candidate carries a span that literally
    appears in its cited chunk, or is rejected. Zero exceptions."""
    sample = [r for r in corpus if r["cohort"] == "normal"][:20]
    checked = 0
    for record in sample:
        sku = SkuInput(**record["sku_input"])
        chunks = {}
        candidates = list(rule_candidates(sku, schema))
        for candidate in candidates:
            chunks[candidate.evidence.chunk_id] = sku.description_fragment or ""
        for doc in index.retrieve(sku, k=25):
            if not verify_match(sku, doc).matched:
                continue
            for chunk in doc.chunks:
                chunks[chunk.chunk_id] = chunk.text
            candidates.extend(table_candidates(sku, doc, schema))

        for candidate in candidates:
            text = chunks.get(candidate.evidence.chunk_id)
            assert text is not None, f"{candidate.producer} cited a chunk that does not exist"
            assert candidate.evidence.span in text, (
                f"{candidate.producer} cited {candidate.evidence.span!r} which is not "
                f"in {candidate.evidence.chunk_id}")
            checked += 1
    assert checked > 100, "too few candidates checked for the gate to mean anything"


def test_span_gate_rejects_a_fabricated_citation(schema):
    """A candidate whose span is absent from its chunk must fail Level 1."""
    from sourced.ingest.chunks import Chunk
    from sourced.validate.attribute import check_span_present

    chunk = Chunk(chunk_id="c1", source_id="s1", source_type="manufacturer_datasheet",
                  text="Current Rating: 7.0 A", locator="table_cell")
    fabricated = Candidate(
        canonical_key="current_rating", value=12.0, unit="A", raw_text="12 A",
        tier="llm", producer="test",
        evidence=Evidence(source_id="s1", source_type="manufacturer_datasheet",
                          span="Current Rating: 12.0 A", chunk_id="c1",
                          locator="table_cell"))
    assert check_span_present(fabricated, {"c1": chunk}).passed is False


# -------------------------------------------------------------- adjudication


def test_datasheet_beats_contradicting_distributor_page(corpus, pipeline, schema):
    """Doc 06 2.3: a constructed case where a distributor page contradicts a
    datasheet must resolve to the datasheet value."""
    contradicted = [r for r in corpus if r["cohort"] == "contradicted"][:15]
    assert contradicted

    checked = 0
    for record in contradicted:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        attr = product.attributes.get("voltage_rating")
        label = record["labels"].get("voltage_rating")
        if attr is None or attr.value is None or label is None:
            continue
        if not any(s.source_type == "manufacturer_datasheet" for s in product.sources):
            continue                       # nothing authoritative to prefer
        assert values_match(attr.value, attr.unit, label["value"], label.get("unit")), (
            f"{record['sku_input']['mpn']}: took the distributor's contradicting "
            f"value {attr.value} over the datasheet's {label['value']}")
        checked += 1
    assert checked >= 5


def test_conflicting_datasheets_abstain_rather_than_vote(corpus, pipeline, schema):
    """Two manufacturer datasheets disagreeing is a conflict, not an election."""
    conflicts = [r for r in corpus if r["cohort"] == "conflict"][:15]
    assert conflicts

    saw_conflict = False
    for record in conflicts:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        attr = product.attributes.get("current_rating")
        if attr is None:
            continue
        sources = [s for s in product.sources if s.source_type == "manufacturer_datasheet"]
        if len(sources) < 2:
            continue
        saw_conflict = True
        assert attr.resolution == "abstained", (
            f"{record['sku_input']['mpn']}: published {attr.value} despite two "
            f"manufacturer datasheets disagreeing")
        assert attr.abstention_reason.code == "sources_conflict"
        assert attr.abstention_reason.resolution_hint
    assert saw_conflict, "no SKU in the sample had two conflicting datasheets"


# --------------------------------------------------------------- provenance


def test_every_published_value_has_provenance(corpus, pipeline, schema):
    """Doc 06 definition of done: every published value has provenance down to
    page and region."""
    sample = [r for r in corpus if r["cohort"] in ("normal", "contradicted")][:25]
    published = 0
    for record in sample:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        for key, attr in product.attributes.items():
            if attr.resolution != "published":
                continue
            published += 1
            assert attr.winning_candidate is not None
            evidence = attr.evidence
            assert evidence.source_id and evidence.chunk_id and evidence.span
            if evidence.source_type == "manufacturer_datasheet":
                assert evidence.page is not None, f"{key} has no page"
                assert evidence.bbox is not None, f"{key} has no region"
    assert published > 50


def test_every_abstention_carries_a_reason_and_a_hint(corpus, pipeline, schema):
    sample = corpus[:25]
    seen = 0
    for record in sample:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        if product.abstention is not None:
            assert product.abstention.detail and product.abstention.resolution_hint
            seen += 1
        for key, attr in product.attributes.items():
            if attr.resolution == "abstained":
                assert attr.abstention_reason is not None, f"{key} abstained silently"
                assert attr.abstention_reason.detail
                assert attr.abstention_reason.resolution_hint
                seen += 1
    assert seen > 10


# ------------------------------------------------------------- commerce copy


def test_description_claims_all_trace_to_published_attributes(corpus, pipeline, schema):
    """Doc 06 3.3 / risk R8: every claim in a generated description traces to a
    published attribute; untraceable spans are stripped."""
    sample = [r for r in corpus if r["cohort"] == "normal"][:15]
    checked = 0
    for record in sample:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        if product.commerce is None or not product.commerce.description:
            continue
        published = {k for k, a in product.attributes.items() if a.resolution == "published"}
        for claim in product.commerce.description_claims:
            assert claim.source_attribute is not None, (
                f"untraceable claim {claim.text_span!r} survived")
            assert claim.source_attribute in published
            assert (product.commerce.description[claim.span_start:claim.span_end]
                    == claim.text_span)
            checked += 1
    assert checked > 40


def test_title_uses_only_published_attributes(corpus, pipeline, schema):
    sample = [r for r in corpus if r["cohort"] == "normal"][:10]
    for record in sample:
        product = pipeline.run(SkuInput(**record["sku_input"]), schema)
        published = {k for k, a in product.attributes.items() if a.resolution == "published"}
        assert set(product.commerce.title_inputs) <= published
        assert "{" not in product.commerce.title, "an unfilled slot leaked into the title"


# --------------------------------------------------------------- taxonomy


def test_no_returned_category_code_is_absent_from_the_taxonomy(corpus, pipeline, schema):
    """Doc 06 3.2: across the whole test set, no returned code is absent from the
    taxonomy table."""
    from sourced.taxonomy.index import default_index

    codes = default_index().codes
    for record in label_mod.split(corpus, "test")[:40]:
        product = pipeline.run(SkuInput(**record["sku_input"]))
        if product.category is not None:
            assert product.category.code in codes


# ---------------------------------------------------------------- leakage


def test_pipeline_never_imports_the_label_module():
    """Doc 05 leakage discipline: no code path from the extraction pipeline to
    the labels."""
    import ast
    from pathlib import Path

    root = Path(config.ROOT) / "sourced"

    # Modules that legitimately sit on the label side of the wall: the
    # evaluation package, the corpus builder that writes the labels, and the
    # demo, which narrates ground truth on purpose. Everything else is the
    # inference path and must not be able to reach a label.
    label_side = {"eval", "corpus"}
    label_side_files = {"demo.py"}

    offenders = []
    for path in root.rglob("*.py"):
        if label_side & set(path.parts) or path.name in label_side_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = (getattr(node, "module", "") or "") if isinstance(
                node, ast.ImportFrom) else ""
            names = [a.name for a in getattr(node, "names", [])] if isinstance(
                node, (ast.Import, ast.ImportFrom)) else []
            if "sourced.eval" in module or any("sourced.eval" in n for n in names):
                offenders.append(str(path))
    assert not offenders, f"pipeline modules import the evaluation package: {offenders}"

    # and the wall only means something if the pipeline itself is behind it
    checked = [p for p in root.rglob("*.py")
               if not (label_side & set(p.parts)) and p.name not in label_side_files]
    assert len(checked) > 20, "the leakage check is not covering the pipeline"


def test_corpus_labels_are_not_in_the_sku_input(corpus):
    for record in corpus[:50]:
        assert set(record["sku_input"]) <= {"mpn", "manufacturer",
                                            "description_fragment", "internal_sku"}


# ------------------------------------------------------------- normalisation


@pytest.mark.parametrize("a_value,a_unit,b_value,b_unit,expected", [
    (0.5, "in", 12.7, "mm", True),
    (3.96, "mm", 0.156, "in", True),
    (85.0, "degC", 185.0, "degF", True),
    (0.9, "A", 900.0, "mA", True),
    (7.0, "A", 7.0, "V", False),
    ("brass", None, "BRASS", None, True),
    ("phosphor_bronze", None, "Phosphor Bronze", None, True),
    ("gold", None, "tin", None, False),
])
def test_tolerance_aware_comparison(a_value, a_unit, b_value, b_unit, expected):
    assert values_match(a_value, a_unit, b_value, b_unit) is expected


def test_unparseable_unit_does_not_become_a_bare_number():
    """A stated unit that does not parse must fail rather than silently
    becoming a magnitude in the canonical unit."""
    from sourced.ingest.normalize import to_canonical, to_quantity

    assert to_quantity(5, "not_a_real_unit") is None
    assert to_canonical(5, "not_a_real_unit", "mm") is None


# ------------------------------------------------------------------ store


def test_upsert_is_idempotent(tmp_path, index, schema, corpus):
    from sqlalchemy import select

    from sourced.store import models as store_models
    from sourced.store.upsert import upsert_product

    url = f"sqlite:///{(tmp_path / 'idem.db').as_posix()}"
    store_models.engine(url)
    store_models.create_all(url)

    pipe = Pipeline(index, Options(llm_tier=False))
    product = pipe.run(SkuInput(**corpus[0]["sku_input"]), schema)
    first = upsert_product(product)
    second = upsert_product(pipe.run(SkuInput(**corpus[0]["sku_input"]), schema))

    with store_models.session() as db:
        rows = list(db.execute(select(store_models.Product)).scalars())
        provenance = list(db.execute(select(store_models.Provenance)).scalars())
    assert first == second
    assert len(rows) == 1, "reprocessing duplicated the product"
    assert len(provenance) == len(product.attributes)
    store_models.engine(config.DB_URL)          # restore the default binding


def test_a_truncated_mpn_does_not_match_the_full_part(corpus, pipeline, schema):
    """A part number that has lost its last characters must not silently match
    the longer part it is a prefix of. Substring matching on fully normalised
    text made `154630200-3` match `154630200-3RT`, which is ADR-002's failure
    reached by a different route."""
    from sourced.discovery.mpn import mpn_present

    assert mpn_present("154630200-3RT", "Part Number: 154630200-3RT") is True
    assert mpn_present("154630200-3", "Part Number: 154630200-3RT") is False

    sample = [r for r in corpus if r["cohort"] == "normal"][:12]
    false_matches = []
    for record in sample:
        mpn = record["sku_input"]["mpn"]
        if len(mpn) < 5:
            continue
        truncated = SkuInput(**{**record["sku_input"], "mpn": mpn[:-2]})
        product = pipeline.run(truncated, schema)
        if product.source_status == "verified":
            false_matches.append(mpn)
    assert not false_matches, (
        f"a truncated part number verified against the full part for {false_matches}")


def test_separator_variants_of_the_same_mpn_still_match():
    """The boundary check must not break the normalisation it sits on top of."""
    from sourced.discovery.mpn import mpn_present

    for variant in ("154630200-3RT", "154630200 3RT", "154630200_3RT",
                    "154630200.3rt", "1546302003RT"):
        assert mpn_present(variant, "Ordering: 154630200-3RT vertical"), variant


# ------------------------------------------------------- fractional inches


@pytest.mark.parametrize("raw,expected", [
    ("1/4 in", 0.25),
    ("3/8 in", 0.375),
    ("1/2 in", 0.5),
    ("3/4 in", 0.75),
    ("1-1/4 in", 1.25),
    ("1-1/2 in", 1.5),
    ("2-1/2 in", 2.5),
    ("2 in", 2.0),
    ("12.7 mm", 0.5),
])
def test_fractional_inches_parse(raw, expected):
    """Doc 00 calls fractional inches the recurring failure in this domain, and
    it was: the configuration-dependent-value split ("230 V / 400 V") was
    tearing `1/4 in` into 1 and 4, so a quarter-inch fitting published as a
    one-inch fitting with a table cell as its provenance."""
    from sourced.candidates.tables import coerce

    spec = load_schema("pipe_fitting").spec("nominal_size")
    value, unit = coerce(raw, spec)
    assert unit == "inch"
    assert value == pytest.approx(expected)


def test_configuration_dependent_values_still_split():
    """The fix must not cost the feature it was protecting."""
    from sourced.candidates.tables import coerce

    spec = load_schema("electrical_connector").spec("voltage_rating")
    assert coerce("230 V / 400 V", spec) == (230.0, "V")


def test_abbreviation_soup_decomposes_without_a_document(schema):
    """Doc 00's own example. The rules tier alone, no source, no model."""
    from sourced.candidates.rules import rule_candidates

    fittings = load_schema("pipe_fitting")
    sku = SkuInput(mpn="TEST-1", description_fragment="1/2IN X 3/4IN BRS 90 ELL FIP 150#")
    found = {c.canonical_key: c.value for c in rule_candidates(sku, fittings)}
    assert found["nominal_size"] == pytest.approx(0.5)
    assert found["nominal_size_secondary"] == pytest.approx(0.75)
    assert found["body_material"] == "brass"
    assert found["form_factor"] == "elbow"
    assert found["end_connection_1"] == "female_iron_pipe"
    assert found["pressure_class"] == "class_150"
    assert found["bend_angle"] == 90


def test_ordered_end_connections(schema):
    """`MIP X FIP` is end 1 male, end 2 female, and not the other way round."""
    from sourced.candidates.rules import rule_candidates

    fittings = load_schema("pipe_fitting")
    sku = SkuInput(mpn="TEST-2", description_fragment="3/4IN SS316 TEE MIP X FIP 3000#")
    found = {c.canonical_key: c.value for c in rule_candidates(sku, fittings)}
    assert found["end_connection_1"] == "male_iron_pipe"
    assert found["end_connection_2"] == "female_iron_pipe"


def test_a_token_only_fires_for_a_schema_that_declares_the_attribute():
    """`BRS` is a contact material on a connector and a body material on a
    fitting. It must not leak the wrong key into either."""
    from sourced.candidates.rules import rule_candidates

    connector = load_schema("electrical_connector")
    fitting = load_schema("pipe_fitting")
    sku = SkuInput(mpn="T", description_fragment="BRS")
    connector_keys = {c.canonical_key for c in rule_candidates(sku, connector)}
    fitting_keys = {c.canonical_key for c in rule_candidates(sku, fitting)}
    assert connector_keys == {"contact_material"}
    assert fitting_keys == {"body_material"}


# ---------------------------------------------------------- category routing


def test_every_sku_routes_to_a_populated_schema(corpus):
    """With two categories the classifier can send a record to the wrong
    attribute set, which is worse than sending it nowhere: extraction then
    looks for attributes the part does not have."""
    from sourced.taxonomy.classify import classify, schema_for
    from sourced.taxonomy.index import default_index

    taxonomy = default_index()
    sample = corpus[::7][:80]
    wrong = []
    for record in sample:
        sku = SkuInput(**record["sku_input"])
        chosen = schema_for(classify(sku, index=taxonomy), taxonomy, sku=sku)
        if chosen != record["category"]:
            wrong.append((record["sku_input"]["mpn"], record["category"], chosen))
    accuracy = 1 - len(wrong) / len(sample)
    assert accuracy >= 0.95, f"routing accuracy {accuracy:.3f}; misrouted {wrong[:5]}"


def test_relational_lookup_resolves(schema):
    """A rule expressed over a derived value must actually evaluate."""
    from sourced.models import AttributeValue
    from sourced.validate.relational import validate_relational

    fittings = load_schema("pipe_fitting")
    attributes = {
        "pressure_class": AttributeValue(canonical_key="pressure_class",
                                         value="class_150", resolution="published"),
        "max_working_pressure": AttributeValue(canonical_key="max_working_pressure",
                                               value=900.0, unit="psi",
                                               resolution="published"),
    }
    results = validate_relational(attributes, fittings)
    checks = results.get("max_working_pressure", {})
    assert "pressure_within_class" in checks
    assert checks["pressure_within_class"].passed is False


# ------------------------------------------------- value/evidence agreement


def test_a_real_span_paired_with_a_wrong_value_is_rejected():
    """Span containment alone is weaker than ADR-006 assumes.

    Running the LLM tier for the first time produced `nominal_size_secondary =
    1 in` citing the span `"3/4 in"`. The span genuinely appears in the chunk,
    so containment passed and the value published at 0.99 confidence — on a
    fitting with no secondary size at all. The span was real; the pairing was
    invented, and only checking that the text yields the value catches it.
    """
    from sourced.validate.attribute import check_value_supported_by_span

    spec = load_schema("pipe_fitting").spec("nominal_size_secondary")
    evidence = Evidence(source_id="s", source_type="distributor_page",
                        span="3/4 in", chunk_id="c", locator="structured_field")
    fabricated = Candidate(canonical_key="nominal_size_secondary", value=1.0,
                           unit="in", raw_text="3/4 in", tier="llm",
                           evidence=evidence, producer="llm:test")
    honest = fabricated.model_copy(update={"value": 0.75, "unit": "inch"})

    assert check_value_supported_by_span(fabricated, spec).passed is False
    assert check_value_supported_by_span(honest, spec).passed is True


def test_deterministic_tiers_are_exempt_from_the_pairing_check():
    """Rules and tables compute the value from the text, so re-deriving it is a
    tautology — and `VERT` is not something a general parser resolves. Applying
    the check to them would reject correct values."""
    from sourced.validate.attribute import check_value_supported_by_span

    schema = load_schema("electrical_connector")
    evidence = Evidence(source_id="input_row", source_type="internal_record",
                        span="VERT", chunk_id="c", locator="inferred")
    rule = Candidate(canonical_key="orientation", value="vertical", raw_text="VERT",
                     tier="rule", evidence=evidence, producer="lexicon:VERT")
    assert check_value_supported_by_span(rule, schema.spec("orientation")).passed


def test_llm_provider_is_configurable_and_never_stubbed():
    """The tier must be genuinely off when no provider is configured, rather
    than quietly returning invented values."""
    from sourced.candidates import providers
    from sourced.candidates.llm import llm_candidates
    from sourced.ingest.chunks import Chunk

    schema = load_schema("electrical_connector")
    sku = SkuInput(mpn="X-1", description_fragment="CONN HEADER")
    chunk = Chunk(chunk_id="c", source_id="s", source_type="manufacturer_datasheet",
                  text="RoHS compliant", locator="prose")

    import sourced.config as cfg

    original = cfg.LLM_ENABLED
    try:
        cfg.LLM_ENABLED = False
        assert llm_candidates(sku, schema, ["rohs_compliant"], [chunk]) == []
    finally:
        cfg.LLM_ENABLED = original
    assert providers.get_provider is not None


def test_tool_payload_survives_a_content_wrapped_response():
    """Open-weight models on OpenAI-compatible endpoints often return the tool
    call as text in `content`, wrapped in `[[{...}]]`. The parser has to cope,
    because the alternative is discarding correct extractions on a formatting
    detail."""
    from sourced.candidates.providers import extract_tool_arguments

    wrapped = {"content": '[[{"name": "return_attributes", "arguments": '
                          '{"termination": {"value": "solder"}}}]]'}
    assert extract_tool_arguments(wrapped, "return_attributes") == {
        "termination": {"value": "solder"}}

    proper = {"tool_calls": [{"function": {"name": "return_attributes",
                                           "arguments": '{"a": 1}'}}]}
    assert extract_tool_arguments(proper, "return_attributes") == {"a": 1}

    assert extract_tool_arguments({"content": "no json here"},
                                  "return_attributes") is None


# ------------------------------------------------- deterministic by default


def test_ablations_inherit_the_runs_llm_setting():
    """An ablation variant must not silently switch the model tier on.

    `run_ablations` built `Options(**overrides)`, which takes the dataclass
    default `llm_tier=True`. With a provider configured that turned an
    eighteen-batch deterministic sweep into thousands of model calls and the
    evaluation simply stopped making progress — no error, no output, just a
    process sitting at 83 seconds of CPU for half an hour.
    """
    import inspect

    from sourced.eval import run as run_module

    source = inspect.getsource(run_module.run_ablations)
    assert "Options(llm_tier=use_llm" in source, (
        "run_ablations must pass the run's LLM setting into every variant")
    assert "use_llm" in inspect.signature(run_module.run_ablations).parameters


def test_the_adversarial_suite_is_deterministic():
    """The fabrication, degraded-input and re-verification experiments assert
    things about deterministic behaviour, so they must not reach a model."""
    import inspect

    from sourced.eval import run as run_module

    for name in ("injected_fabrication_test", "degraded_input_test",
                 "reverification_test"):
        source = inspect.getsource(getattr(run_module, name))
        assert "Options(llm_tier=False)" in source, f"{name} may call the model"


def test_persisted_catalogue_is_reproducible():
    """The store is built on the deterministic path so two runs agree."""
    import inspect

    from sourced.eval import report as report_module

    source = inspect.getsource(report_module.persist_catalogue)
    assert "Options(llm_tier=False)" in source


# ------------------------------------------------------- configuration load


def test_dotenv_does_not_override_a_real_environment_variable(tmp_path,
                                                              monkeypatch):
    """A container passes secrets as environment variables and ships no `.env`.

    If the file won, a stale committed value would silently beat what the
    orchestrator injected, so the real environment has to take precedence.
    """
    from sourced.config import _load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("SOURCED_TEST_KEY=from_file\n"
                        "SOURCED_TEST_OTHER=from_file\n", encoding="utf-8")

    monkeypatch.setenv("SOURCED_TEST_KEY", "from_environment")
    monkeypatch.delenv("SOURCED_TEST_OTHER", raising=False)

    _load_dotenv(env_file)

    import os

    assert os.environ["SOURCED_TEST_KEY"] == "from_environment"
    assert os.environ["SOURCED_TEST_OTHER"] == "from_file"


def test_missing_dotenv_is_not_an_error(tmp_path):
    """The container has no `.env` at all; loading must be a no-op."""
    from sourced.config import _load_dotenv

    _load_dotenv(tmp_path / "does-not-exist.env")


def test_no_provider_means_the_tier_is_off_not_broken(monkeypatch):
    """With nothing configured the pipeline must run, deterministically."""
    from sourced.candidates import providers

    monkeypatch.setattr("sourced.config.LLM_PROVIDER", "")
    monkeypatch.setattr("sourced.config.LLM_API_KEY", None)
    providers.reset_provider()
    try:
        assert providers.get_provider() is None
    finally:
        providers.reset_provider()


def test_secrets_are_not_baked_into_the_image():
    """`.env` must never be copied into the container."""
    from pathlib import Path

    from sourced import config

    dockerfile = (Path(config.ROOT) / "Dockerfile").read_text(encoding="utf-8")
    copied = [line for line in dockerfile.splitlines()
              if line.strip().upper().startswith("COPY")]
    assert not any(".env" in line for line in copied), (
        "the Dockerfile copies .env into the image")


# ------------------------------------------------------- description claims


def test_claim_offsets_address_the_span_they_carry():
    """A claim is only verifiable if its offsets select its own text.

    `_align_claims` stored a stripped sentence against unstripped match bounds,
    so `description[span_start:span_end]` came back with a leading space and
    the UI's hover highlight sat one character out. The composed path was
    correct, which is why this only surfaced when a model wrote the copy.
    """
    from sourced.commerce.description import _align_claims
    from sourced.models import AttributeValue

    published = {
        "pole_count": AttributeValue(canonical_key="pole_count", value=4,
                                     resolution="published"),
        "contact_plating": AttributeValue(canonical_key="contact_plating",
                                          value="gold", resolution="published"),
    }
    text = ("This is a 4-position header.  It uses Gold plated contacts. "
            "It ships in a box.")
    claims = _align_claims(text, published)

    assert claims, "no claims aligned"
    for claim in claims:
        assert text[claim.span_start:claim.span_end] == claim.text_span
        assert claim.text_span == claim.text_span.strip()


def test_generated_copy_is_fed_only_published_attributes():
    """ADR-013: generation sees published values and nothing else."""
    import inspect

    from sourced.commerce import description as description_module

    source = inspect.getsource(description_module.generate)
    assert "_published(product)" in source
    # the attribute block handed to the model is built from that set
    assert "attribute_lines" in source


def test_untraceable_spans_are_stripped_not_flagged():
    """A claim that cannot be traced must leave the copy, not merely be
    labelled. Shipping it with a marker would still ship the fabrication."""
    import inspect

    from sourced.commerce import description as description_module

    source = inspect.getsource(description_module.generate)
    assert "source_attribute is None" in source
    assert "text.replace(claim.text_span" in source


# --------------------------------------------- the generative surface (R8)


def _fitting_record():
    from sourced.models import AttributeValue, ProductRecord

    return ProductRecord(
        mpn="WM413-N050", mpn_normalised="WM413N050",
        manufacturer_resolved="Ward Manufacturing",
        attributes={
            "nominal_size": AttributeValue(canonical_key="nominal_size",
                                           value=0.5, unit="inch",
                                           resolution="published"),
            "pressure_class": AttributeValue(canonical_key="pressure_class",
                                             value="class_125",
                                             resolution="published"),
        })


def test_generated_copy_may_not_carry_an_unlicensed_number():
    """Risk R8, measured rather than assumed.

    Stripping untraceable claims is not sufficient: alignment works a sentence
    at a time, so a fabricated number rides along inside a sentence that also
    cites a real attribute. On a 20-description sample, 35% of generated copy
    carried a number nothing licensed.
    """
    from sourced.commerce.description import licence_violations

    product = _fitting_record()
    clean = 'The WM413-N050 is a 1/2" brass nipple rated Class 125.'
    invented = 'The WM413-N050 is a 1/2" nipple rated Class 125, with 9A current.'

    assert licence_violations(clean, product) == []
    assert licence_violations(invented, product) == ["9"]


def test_a_rewritten_part_number_is_caught():
    """The commonest generative failure here was not an invented specification
    but a mangled MPN -- `WM413-N050` written as `WM413-1050`. In a catalogue
    that is worse: a buyer ordering against it gets the wrong part."""
    from sourced.commerce.description import licence_violations

    product = _fitting_record()
    mangled = 'The WM413-1050 is a 1/2" brass nipple rated Class 125.'

    violations = licence_violations(mangled, product)
    assert violations, "a rewritten part number went undetected"
    assert "1050" in violations


def test_failing_the_licence_check_falls_back_to_composed(monkeypatch):
    """Copy that fails the gate must not ship. The deterministic composition
    does, because it cannot invent a number."""
    from sourced.commerce import description as description_module

    product = _fitting_record()
    schema = load_schema("pipe_fitting")

    class FakeProvider:
        name = "fake"

        def text_call(self, **kwargs):
            return "The WM413-1050 is rated Class 125 and good for 9000 cycles."

    monkeypatch.setattr("sourced.config.LLM_ENABLED", True)
    monkeypatch.setattr(
        "sourced.candidates.providers.get_provider", lambda *a, **k: FakeProvider())

    text, claims, generator = description_module.generate(product, schema,
                                                          use_llm=True)
    assert generator == "composed_after_failed_licence_check"
    assert "9000" not in text
    assert "WM413-1050" not in text
    assert description_module.licence_violations(text, product) == []
