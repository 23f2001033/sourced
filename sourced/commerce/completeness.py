"""Completeness scoring (doc 03 6).

Scored against the category's merchandising checklist: which required fields
are filled, which are blocking publication, and what would resolve each. A
blank field is not actionable; a named blocker is a work item.
"""
from __future__ import annotations

from sourced.models import CompletenessScore, ProductRecord
from sourced.registry import CategorySchema


def score(product: ProductRecord, schema: CategorySchema) -> CompletenessScore:
    required = schema.merchandising_required or schema.required_keys
    attributes = product.attributes

    filled = [k for k in required
              if k in attributes and attributes[k].resolution == "published"]
    missing = [k for k in required if k not in filled]

    published = sum(1 for a in attributes.values() if a.resolution == "published")
    review = sum(1 for a in attributes.values() if a.resolution == "review")
    abstained = sum(1 for a in attributes.values() if a.resolution == "abstained")

    blocking = [k for k in missing
                if schema.spec(k) is not None and schema.spec(k).criticality == "safety"]
    blocking += [k for k in missing if k not in blocking
                 and (k not in attributes or attributes[k].resolution == "abstained")]

    return CompletenessScore(
        required_total=len(required),
        required_filled=len(filled),
        published_count=published,
        review_count=review,
        abstained_count=abstained,
        missing_required=missing,
        blocking_for_publish=sorted(set(blocking)),
    )
