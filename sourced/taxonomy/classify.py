"""Category classification (doc 03 1.2, ADR-008).

Retrieve top-k real codes, constrain the model to choose among them, then
hard-validate that the returned code exists. The validation is what makes an
invented code structurally impossible rather than merely unlikely, and it runs
whether the choice came from the model or from the retriever.

With the LLM tier disabled the retriever's own top-1 is taken. That is stated
in the assignment's `method` field rather than hidden, because a retriever-only
choice and a model-adjudicated choice are not the same evidence.
"""
from __future__ import annotations

import re

from sourced.models import CategoryAssignment, SkuInput
from sourced.taxonomy.index import TaxonomyIndex, default_index

SYSTEM_PROMPT = """You assign an industrial product to exactly one category from a supplied \
candidate list.

Choose the code whose definition best describes the part. You may only return a
code that appears in the candidate list. If none of the candidates fits, return
the code "NONE"."""


def expand_abbreviations(text: str) -> str:
    """Turn abbreviation soup into words the taxonomy actually contains.

    A taxonomy leaf says "elbow"; a distributor row says `ELL`. Retrieval over
    the raw fragment therefore matches nothing, and the classifier routes a
    pipe fitting to a connector. The lexicon already holds the mapping, so this
    reuses it as a domain synonym dictionary.

    Unlike the rules tier, expansion is deliberately *not* filtered by schema:
    the whole point is that the category is not yet known. Nothing here becomes
    a candidate value -- it only widens the text used for retrieval.

    Each expansion carries the attribute it fills as well as its value: `R/A`
    alone becomes "right angle", which matches a pipe elbow's definition as
    readily as a connector's orientation, whereas "orientation right angle" is
    less confusable. Which *schema* the row belongs to is decided separately
    and by counting, in `decode_keys` -- text similarity is the wrong
    instrument for a decision that can be made exactly.
    """
    from sourced.candidates.rules import _lexicon

    upper = (text or "").upper()
    expansions: list[str] = []
    for abbr, entry in _lexicon()["tokens"].items():
        if not re.search(rf"(?<![A-Z0-9/]){re.escape(abbr)}(?![A-Z0-9/])", upper):
            continue
        for reading in (entry if isinstance(entry, list) else [entry]):
            value = reading.get("value")
            if isinstance(value, str):
                key = reading.get("key", "")
                expansions.append(f"{key.replace('_', ' ')} {value.replace('_', ' ')}")
    return " ".join(dict.fromkeys(expansions))


def decode_keys(text: str) -> list[str]:
    """Which attribute keys the fragment's abbreviations decode to.

    `ELL` decodes to form_factor, `SMD` to mounting_type. Those keys exist in
    exactly one populated schema each, so counting them says which attribute
    set the row belongs to -- exactly, and with an auditable count, rather than
    by hoping cosine similarity lands on the right leaf.
    """
    from sourced.candidates.rules import _lexicon

    upper = (text or "").upper()
    keys: list[str] = []
    for abbr, entry in _lexicon()["tokens"].items():
        if not re.search(rf"(?<![A-Z0-9/]){re.escape(abbr)}(?![A-Z0-9/])", upper):
            continue
        for reading in (entry if isinstance(entry, list) else [entry]):
            key = reading.get("key")
            if key:
                keys.append(key)
    return keys


def schema_votes(text: str) -> dict[str, int]:
    """Populated schemas ranked by how many decoded keys they declare.

    A key declared by every schema discriminates nothing and is not counted.
    """
    from sourced.registry import all_schemas

    schemas = all_schemas()
    votes = {name: 0 for name in schemas}
    for key in decode_keys(text):
        owners = [name for name, schema in schemas.items() if schema.spec(key)]
        if len(owners) == len(schemas):
            continue                       # shared by all: no signal
        for name in owners:
            votes[name] += 1
    return votes


def product_text(sku: SkuInput, attributes: dict | None = None) -> str:
    fragment = sku.description_fragment or ""
    bits = [fragment, expand_abbreviations(fragment), sku.manufacturer or "", sku.mpn]
    for key, value in (attributes or {}).items():
        bits.append(f"{key.replace('_', ' ')} {value}")
    return " ".join(b for b in bits if b)


def _llm_choose(text: str, candidates) -> tuple[str | None, str]:
    """Constrain the choice to retrieved codes (ADR-008).

    The enum in the tool schema plus the hard existence check in `classify`
    means a code that does not exist cannot survive, whatever the model emits.
    """
    from sourced.candidates.providers import get_provider

    provider = get_provider()
    if provider is None:
        return None, "retrieval_top1"

    options = "\n".join(f"- {leaf.code}: {leaf.title}. {leaf.definition}"
                         for leaf, _ in candidates)
    codes = [leaf.code for leaf, _ in candidates]
    result = provider.structured_call(
        system=SYSTEM_PROMPT,
        user=f"PRODUCT:\n{text}\n\nCANDIDATES:\n{options}",
        tool_schema={"type": "object",
                     "properties": {"code": {"type": "string",
                                             "enum": codes + ["NONE"]},
                                    "reason": {"type": "string"}},
                     "required": ["code"]},
        tool_name="choose_category",
        temperature=0.0, max_tokens=400)
    if not result:
        return None, "retrieve_then_constrain_unparsed"
    return str(result.get("code") or ""), "retrieve_then_constrain"


def classify(sku: SkuInput, attributes: dict | None = None, k: int = 20,
             index: TaxonomyIndex | None = None,
             use_llm: bool = False) -> CategoryAssignment | None:
    index = index or default_index()
    text = product_text(sku, attributes)
    candidates = index.search(text, k)
    if not candidates:
        return None

    code, method = _llm_choose(text, candidates) if use_llm else (None, "retrieval_top1")
    if code == "NONE":
        return None
    if code is None:
        code = candidates[0][0].code

    # HARD VALIDATION — structurally prevents invented codes
    allowed = {leaf.code for leaf, _ in candidates}
    if code not in allowed or code not in index.codes:
        return None

    leaf = index.by_code(code)
    if leaf is None:
        return None

    scores = [s for _, s in candidates]
    margin = (scores[0] - scores[1]) if len(scores) > 1 else scores[0]
    return CategoryAssignment(
        taxonomy=leaf.taxonomy,                     # type: ignore[arg-type]
        code=leaf.code,
        label=leaf.title,
        candidates_considered=[(c.code, round(s, 4)) for c, s in candidates[:10]],
        # an observable separation between the top candidate and the runner-up,
        # not the model's opinion of itself (ADR-005)
        confidence=round(min(1.0, max(0.0, scores[0])) * (0.5 + 0.5 * min(1.0, margin * 5)), 4),
        method=method,
    )


def refine_category(assignment: CategoryAssignment | None, schema_name: str | None,
                    attributes: dict, sku: SkuInput,
                    index: TaxonomyIndex | None = None) -> CategoryAssignment | None:
    """Sharpen the taxonomy leaf once the attributes are known.

    Doc 03 1.2 passes `attributes` to the classifier; this is that second pass.
    Stage 1 has to choose an attribute schema before anything is extracted, and
    from a truncated row like `310501-038X ... CR` there is not enough text to
    tell a cross from a nipple. After extraction there is: `form_factor` says
    so outright.

    The **schema is not revisited** -- changing it here would mean the record
    was extracted against one attribute set and filed under another. Only the
    leaf moves, and only among leaves that map to the schema already used.
    """
    if assignment is None or schema_name is None:
        return assignment
    index = index or default_index()

    values = [str(v) for v in attributes.values() if isinstance(v, str)]
    if not values:
        return assignment

    text = " ".join([sku.description_fragment or ""]
                    + [v.replace("_", " ") for v in values])
    candidates = [(leaf, score) for leaf, score in index.search(text, 25)
                  if leaf.maps_to_category == schema_name]
    if not candidates:
        return assignment

    best = candidates[0][0]
    # prefer a leaf that names the resolved form outright
    for leaf, _score in candidates:
        haystack = f"{leaf.title} {' '.join(leaf.path)}".lower()
        if any(v.replace("_", " ") in haystack for v in values):
            best = leaf
            break

    if best.code != assignment.code:
        assignment.code = best.code
        assignment.label = best.title
        assignment.method = f"{assignment.method}+refined_after_extraction"
    return assignment


def decoded_values_of(text: str) -> list[str]:
    """The controlled values the row's abbreviations decode to."""
    from sourced.candidates.rules import _lexicon

    upper = (text or "").upper()
    values: list[str] = []
    for abbr, entry in _lexicon()["tokens"].items():
        if not re.search(rf"(?<![A-Z0-9/]){re.escape(abbr)}(?![A-Z0-9/])", upper):
            continue
        for reading in (entry if isinstance(entry, list) else [entry]):
            value = reading.get("value")
            if isinstance(value, str):
                values.append(value)
    return values


def _reconcile_leaf(assignment: CategoryAssignment, schema_name: str,
                    index: TaxonomyIndex,
                    decoded_values: list[str] | None = None) -> None:
    """Move the taxonomy code to the best retrieved leaf that maps to the schema
    actually chosen.

    Without this the record can say one thing and be extracted as another: a
    pipe bushing routed correctly to the fitting schema by decoded-key votes,
    while its stated category read `Battery` because that is what similarity
    returned. A record whose category contradicts its own attribute set is
    worse than one with no category, so the two are kept consistent and the
    override is recorded in `method`.
    """
    mapped = [index.by_code(code) for code, _ in assignment.candidates_considered]
    mapped = [leaf for leaf in mapped
              if leaf and leaf.maps_to_category == schema_name]
    if not mapped:
        assignment.method = f"{assignment.method}+leaf_unreconciled"
        return

    # prefer the leaf that names what the row decoded to. A row decoding to
    # form_factor=bushing should not be filed as a nipple just because the
    # nipple leaf ranked higher on text similarity.
    if decoded_values:
        for leaf in mapped:
            text = f"{leaf.title} {' '.join(leaf.path)}".lower()
            if any(value.replace("_", " ") in text for value in decoded_values):
                assignment.code = leaf.code
                assignment.label = leaf.title
                assignment.method = f"{assignment.method}+leaf_by_decoded_value"
                return

    leaf = mapped[0]
    assignment.code = leaf.code
    assignment.label = leaf.title
    assignment.method = f"{assignment.method}+leaf_reconciled"
    return



def schema_for(assignment: CategoryAssignment | None, index: TaxonomyIndex | None = None,
               sku: SkuInput | None = None) -> str | None:
    """The attribute schema to extract against.

    The taxonomy answer and the extraction schema are two different questions,
    and only one of them has to be answered by similarity. Most leaves have no
    populated attribute schema -- the registry holds two, the taxonomy holds
    seventy-three -- so the leaf mapping alone is not enough.

    Decided in this order, with the reason recorded in `method`:

      1. decoded-key votes, when one schema clearly wins. `ELL` decodes to
         form_factor and `SMD` to mounting_type; each key belongs to one
         schema, so this is a count rather than a guess.
      2. the assigned leaf's own mapping, if that schema is populated.
      3. the highest-ranked retrieved leaf whose mapping is populated.

    Returning None is a real outcome: the category was identified but no
    attribute set exists for it, so there is nothing to extract against.
    """
    if assignment is None:
        return None
    index = index or default_index()
    from sourced.registry import all_schemas

    populated = set(all_schemas())

    if sku is not None and sku.description_fragment:
        votes = schema_votes(sku.description_fragment)
        ranked = sorted(votes.items(), key=lambda kv: -kv[1])
        if ranked and ranked[0][1] > 0 and (
                len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
            winner = ranked[0][0]
            leaf = index.by_code(assignment.code)
            if not (leaf and leaf.maps_to_category == winner):
                assignment.method = f"{assignment.method}+schema_by_decoded_keys"
                _reconcile_leaf(assignment, winner, index,
                                decoded_values_of(sku.description_fragment))
            return winner

    leaf = index.by_code(assignment.code)
    if leaf and leaf.maps_to_category in populated:
        return leaf.maps_to_category

    for code, _score in assignment.candidates_considered:
        candidate = index.by_code(code)
        if candidate and candidate.maps_to_category in populated:
            assignment.method = f"{assignment.method}+schema_fallback:{code}"
            return candidate.maps_to_category
    return None
