"""Level 2 — cross-attribute validation (doc 03 4).

Catches a class of error that per-attribute checks structurally cannot: two
individually plausible values that cannot both be true.

Rules are declared per category in YAML and evaluated in a restricted
environment — no builtins, no attribute access, only the resolved attribute
values of this record.
"""
from __future__ import annotations

import ast

from sourced.models import AttributeValue, CheckResult
from sourced.registry import CategorySchema, RelationalRule

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare, ast.Name,
    ast.Load, ast.Constant, ast.List, ast.Tuple, ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub,
)


def _safe_eval(expr: str, env: dict) -> bool | None:
    """Evaluate a rule expression. Returns None when a referenced attribute is
    missing — an unevaluable rule is not a failed rule."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None
        if isinstance(node, ast.Name) and node.id not in env:
            return None
    try:
        return bool(eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, env))
    except Exception:
        return None


def _env(attributes: dict[str, AttributeValue],
         schema: CategorySchema | None = None) -> dict:
    env = {k: v.value for k, v in attributes.items()
           if v.value is not None and v.resolution != "abstained"}
    for name, lookup in (schema.lookups if schema else {}).items():
        source = env.get(lookup.from_key)
        if source is None:
            continue
        value = lookup.table.get(str(source))
        if value is not None:
            env[name] = value
    return env


def _message(rule: RelationalRule, env: dict) -> str:
    text = rule.message or rule.id
    for key, value in env.items():
        text = text.replace("{" + key + "}", str(value))
    return text


def validate_relational(attributes: dict[str, AttributeValue],
                        schema: CategorySchema) -> dict[str, dict[str, CheckResult]]:
    """Returns {canonical_key: {rule_id: CheckResult}} for every attribute the
    rule references, so a failure attaches to both sides of the relation."""
    env = _env(attributes, schema)
    results: dict[str, dict[str, CheckResult]] = {k: {} for k in attributes}

    for rule in schema.relational_rules:
        if rule.when is not None and _safe_eval(rule.when, env) is not True:
            continue
        outcome = _safe_eval(rule.expr, env)
        if outcome is None:
            continue
        referenced = [k for k in env if k in attributes
                      and (k in rule.expr or (rule.when and k in rule.when))]
        if not referenced:
            # a rule expressed purely over derived values still has to attach
            # somewhere; the attribute the lookup came from is the honest anchor
            referenced = [lookup.from_key
                          for name, lookup in (schema.lookups or {}).items()
                          if name in rule.expr and lookup.from_key in attributes]
        for key in referenced:
            results.setdefault(key, {})[rule.id] = CheckResult(
                passed=outcome, level="relational",
                detail=None if outcome else f"[{rule.severity}] {_message(rule, env)}")
    return results


def rule_severity(schema: CategorySchema, rule_id: str) -> str:
    for rule in schema.relational_rules:
        if rule.id == rule_id:
            return rule.severity
    return "warn"
