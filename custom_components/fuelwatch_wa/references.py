"""Decode compact API references using the same registry bundled into the Worker."""

import json
from pathlib import Path
from typing import Literal

ReferenceKind = Literal["brands", "siteFeatures", "restrictions"]
# Static package data is loaded once with the integration module, never during a network refresh.
_DATA = json.loads(Path(__file__).with_name("reference_data.json").read_text(encoding="utf-8"))
_NAMES = {
    kind: {entry["code"]: entry["name"] for entry in entries} for kind, entries in _DATA.items()
}


def reference_label(value: object, kind: ReferenceKind) -> str:
    """Accept codes, expanded references and the previous string representation.

    Known expanded codes must agree with their registry name. Code 0 is reserved
    for an unmapped brand, whose original name may accompany the reference.
    Unknown numeric codes reject the snapshot instead of inventing a label.
    """
    if isinstance(value, str):
        if len(value) > 500:
            raise ValueError("Invalid reference label")
        return value.strip()
    expanded = isinstance(value, dict)
    code = value.get("code") if expanded else value
    if type(code) is not int or code not in _NAMES[kind]:
        raise ValueError("Unknown reference code")
    expected = _NAMES[kind][code]
    if not expanded:
        return expected
    name = value.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 500:
        raise ValueError("Invalid expanded reference")
    if not (kind == "brands" and code == 0) and name != expected:
        raise ValueError("Reference code and name disagree")
    return name.strip()


def reference_labels(value: object, kind: ReferenceKind) -> tuple[str, ...]:
    """Bound collection size before decoding metadata or restoring stored strings."""
    if not isinstance(value, (list, tuple)) or len(value) > 64:
        raise ValueError("Invalid reference list")
    return tuple(reference_label(item, kind) for item in value)


def brand_label(item: dict) -> str:
    """Preserve unknown brand names and existing user vendor filters across the schema upgrade."""
    value = item.get("brand", "")
    name = reference_label(value, "brands") or "Independent"
    if type(value) is int and value == 0:
        return reference_label(item.get("sourceNotes", {}).get("brand", name), "brands") or name
    return name
