"""A small JSON-Schema checker for tool arguments: the subset our tools use (type, required, properties, additionalProperties, enum, bounds, items)."""
from __future__ import annotations


class Invalid(Exception):
    pass


def _type_ok(value, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def validate(value, schema: dict, path: str = "arguments", depth: int = 0) -> None:
    if depth > 8:
        raise Invalid(f"{path}: nested too deeply")
    expected = schema.get("type")
    if expected:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(value, t) for t in allowed):
            raise Invalid(f"{path}: expected {' or '.join(allowed)}")
    if "enum" in schema and value not in schema["enum"]:
        raise Invalid(f"{path}: must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        if len(value) > schema.get("maxLength", 100_000):
            raise Invalid(f"{path}: longer than {schema.get('maxLength', 100_000)} characters")
        if len(value) < schema.get("minLength", 0):
            raise Invalid(f"{path}: shorter than {schema['minLength']} characters")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise Invalid(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise Invalid(f"{path}: must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", 1000):
            raise Invalid(f"{path}: more than {schema.get('maxItems', 1000)} items")
        item = schema.get("items")
        if item:
            for i, element in enumerate(value):
                validate(element, item, f"{path}[{i}]", depth + 1)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise Invalid(f"{path}.{name}: is required")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    raise Invalid(f"{path}.{name}: is not a known field")
        for name, sub in props.items():
            if name in value:
                validate(value[name], sub, f"{path}.{name}", depth + 1)
