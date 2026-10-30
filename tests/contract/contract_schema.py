"""Tiny JSON-instance -> schema inference used by the contract tests.

Node format (JSON-serialisable):
  {"type": "string"|"int"|"float"|"bool"|"null"|"any", "nullable": bool}
  {"type": "array",  "items": <node>, "nullable": bool}
  {"type": "map", "values": <node>, "nullable": bool}   (dynamic keys, e.g. order ids)
  {"type": "object", "keys": {name: <node>}, "nullable": bool}

int and float are deliberately distinct (the Rust SDK uses u64 vs f64).
"""
from __future__ import annotations


# Dict-valued fields whose KEYS are data (order ids), not contract keys.
MAP_FIELDS = {"not_canceled"}


def infer(value, field: str | None = None) -> dict:
    if isinstance(value, dict) and field in MAP_FIELDS:
        vals = {"type": "any", "nullable": False}
        for i, v in enumerate(value.values()):
            vals = infer(v) if i == 0 else merge(vals, infer(v))
        return {"type": "map", "values": vals, "nullable": False}
    if value is None:
        return {"type": "null", "nullable": True}
    if isinstance(value, bool):
        return {"type": "bool", "nullable": False}
    if isinstance(value, int):
        return {"type": "int", "nullable": False}
    if isinstance(value, float):
        return {"type": "float", "nullable": False}
    if isinstance(value, str):
        return {"type": "string", "nullable": False}
    if isinstance(value, list):
        items = {"type": "any", "nullable": False}
        for i, v in enumerate(value):
            items = infer(v) if i == 0 else merge(items, infer(v))
        return {"type": "array", "items": items, "nullable": False}
    if isinstance(value, dict):
        return {"type": "object", "keys": {k: infer(v, k) for k, v in value.items()},
                "nullable": False}
    raise TypeError(f"not a JSON value: {type(value)}")


def merge(a: dict, b: dict) -> dict:
    """Merge two schema nodes describing the same position in the payload."""
    if a["type"] == "null":
        return {**b, "nullable": True}
    if b["type"] == "null":
        return {**a, "nullable": True}
    if a["type"] == "any":
        return b
    if b["type"] == "any":
        return a
    if a["type"] != b["type"]:
        raise ValueError(f"incompatible types {a['type']} vs {b['type']}")
    nullable = a["nullable"] or b["nullable"]
    if a["type"] == "array":
        return {"type": "array", "items": merge(a["items"], b["items"]),
                "nullable": nullable}
    if a["type"] == "map":
        return {"type": "map", "values": merge(a["values"], b["values"]),
                "nullable": nullable}
    if a["type"] == "object":
        if set(a["keys"]) != set(b["keys"]):
            raise ValueError(f"key sets differ: {sorted(set(a['keys']) ^ set(b['keys']))}")
        return {"type": "object",
                "keys": {k: merge(a["keys"][k], b["keys"][k]) for k in a["keys"]},
                "nullable": nullable}
    return {"type": a["type"], "nullable": nullable}


def infer_many(values) -> dict:
    node = None
    for v in values:
        n = infer(v)
        node = n if node is None else merge(node, n)
    return node


def check(value, schema: dict, path: str = "$") -> list[str]:
    """Return a list of violations of `value` against `schema` (empty == ok)."""
    errs: list[str] = []
    if value is None:
        if not schema["nullable"]:
            errs.append(f"{path}: null but schema is not nullable")
        return errs
    t = schema["type"]
    if t == "null":
        errs.append(f"{path}: expected null, got {type(value).__name__}")
    elif t == "any":
        pass
    elif t == "bool":
        if not isinstance(value, bool):
            errs.append(f"{path}: expected bool, got {type(value).__name__}")
    elif t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            errs.append(f"{path}: expected int, got {type(value).__name__}")
    elif t == "float":
        if not isinstance(value, float):
            errs.append(f"{path}: expected float, got {type(value).__name__}")
    elif t == "string":
        if not isinstance(value, str):
            errs.append(f"{path}: expected string, got {type(value).__name__}")
    elif t == "array":
        if not isinstance(value, list):
            errs.append(f"{path}: expected array, got {type(value).__name__}")
        else:
            for i, v in enumerate(value):
                errs += check(v, schema["items"], f"{path}[{i}]")
    elif t == "map":
        if not isinstance(value, dict):
            errs.append(f"{path}: expected map, got {type(value).__name__}")
        else:
            for k, v in value.items():
                errs += check(v, schema["values"], f"{path}[{k!r}]")
    elif t == "object":
        if not isinstance(value, dict):
            errs.append(f"{path}: expected object, got {type(value).__name__}")
        else:
            exp, got = set(schema["keys"]), set(value)
            if exp != got:
                errs.append(f"{path}: key mismatch missing={sorted(exp - got)} "
                            f"extra={sorted(got - exp)}")
            for k in exp & got:
                errs += check(value[k], schema["keys"][k], f"{path}.{k}")
    return errs
