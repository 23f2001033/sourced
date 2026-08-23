"""Canonical attribute registry (doc 02).

One schema per category. Attributes carry their criticality, which sets the
publish threshold.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from sourced import config
from sourced.models import Criticality


class AttributeSpec(BaseModel):
    key: str
    type: str                       # quantity | enum | bool | text
    dimension: str | None = None
    canonical_unit: str | None = None
    values: list[str] | None = None
    plausible_range: tuple[float, float] | None = None
    criticality: Criticality = "functional"
    required: bool = False
    aliases: list[str] = Field(default_factory=list)


class RelationalRule(BaseModel):
    id: str
    expr: str
    when: str | None = None
    severity: str = "warn"
    message: str = ""
    lookup: str | None = None
    tolerance_factor: float | None = None


class Lookup(BaseModel):
    """A value derived from one attribute through a table, exposed to the
    relational rules by name."""

    from_key: str = Field(alias="from")
    table: dict[str, float]

    model_config = {"populate_by_name": True}


class CategorySchema(BaseModel):
    category: str
    label: str = ""
    taxonomy_codes: dict[str, str] = Field(default_factory=dict)
    title_template: str = ""
    lookups: dict[str, Lookup] = Field(default_factory=dict)
    attributes: list[AttributeSpec] = Field(default_factory=list)
    relational_rules: list[RelationalRule] = Field(default_factory=list)
    merchandising_required: list[str] = Field(default_factory=list)

    @functools.cached_property
    def by_key(self) -> dict[str, AttributeSpec]:
        return {a.key: a for a in self.attributes}

    def spec(self, key: str) -> AttributeSpec | None:
        return self.by_key.get(key)

    @property
    def required_keys(self) -> list[str]:
        return [a.key for a in self.attributes if a.required]

    @property
    def keys(self) -> list[str]:
        return [a.key for a in self.attributes]

    model_config = {"ignored_types": (functools.cached_property,)}


@functools.lru_cache(maxsize=None)
def load_schema(category: str, schema_dir: str | None = None) -> CategorySchema:
    path = Path(schema_dir or config.SCHEMAS) / f"{category}.yaml"
    return CategorySchema(**yaml.safe_load(path.read_text(encoding="utf-8")))


@functools.lru_cache(maxsize=None)
def all_schemas(schema_dir: str | None = None) -> dict[str, CategorySchema]:
    """Every populated category schema.

    `schemas/` also holds the lexicon, the alias table and the taxonomy, which
    are not category schemas. A top-level `category:` key is what distinguishes
    one, so that is the test rather than the filename.
    """
    d = Path(schema_dir or config.SCHEMAS)
    out = {}
    for p in sorted(d.glob("*.yaml")):
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or "category" not in raw:
            continue
        s = CategorySchema(**raw)
        out[s.category] = s
    return out
