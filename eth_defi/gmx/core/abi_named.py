"""Name GMX Reader tuple values from the vendored ABI.

Reader structs are Solidity tuples. Their Python decoding is positional, which
makes a newly inserted ABI field capable of silently changing every later
value. This module converts those tuples recursively using the component
names in the vendored v2.2.1 Reader ABI.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _load_reader_abi() -> tuple[dict[str, Any], ...]:
    """Load the Reader ABI shipped with this package.

    :returns: Immutable sequence of Reader ABI entries.
    """
    abi_path = Path(__file__).parents[2] / "abi" / "gmx" / "Reader.json"
    with abi_path.open(encoding="utf-8") as abi_file:
        return tuple(json.load(abi_file))


def reader_output_abi(function_name: str) -> dict[str, Any]:
    """Return the sole output ABI parameter for a Reader function.

    :param function_name: Reader function whose output is being decoded.
    :returns: Output ABI parameter, including nested component names.
    :raises KeyError: If the function is absent or has multiple outputs.
    """
    function = next((entry for entry in _load_reader_abi() if entry.get("type") == "function" and entry.get("name") == function_name), None)
    if function is None:
        raise KeyError(f"Reader ABI has no function named {function_name!r}")
    outputs = function.get("outputs", ())
    if len(outputs) != 1:
        raise KeyError(f"Reader ABI function {function_name!r} has {len(outputs)} outputs; expected one")
    return outputs[0]


def named_abi_value(value: Any, abi_parameter: dict[str, Any]) -> Any:
    """Convert decoded tuple values into dictionaries keyed by ABI names.

    Tuple arrays are traversed recursively, as are nested tuples. Primitive
    values and arrays of primitives are returned unchanged.

    :param value: Value decoded by Web3 using this ABI parameter.
    :param abi_parameter: ABI parameter describing ``value``.
    :returns: A recursively named dictionary, list or primitive value.
    :raises ValueError: If tuple components are unnamed or the tuple shape
        disagrees with the ABI.
    """
    abi_type = abi_parameter["type"]
    if abi_type.endswith("[]"):
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"Expected array for ABI type {abi_type}, got {type(value).__name__}")
        element_parameter = {**abi_parameter, "type": abi_type[:-2]}
        return [named_abi_value(item, element_parameter) for item in value]
    if abi_type != "tuple":
        return value

    components = abi_parameter.get("components", ())
    if len(value) != len(components):
        raise ValueError(f"ABI tuple has {len(components)} components but decoded value has {len(value)} fields")
    named: dict[str, Any] = {}
    for component, component_value in zip(components, value):
        name = component.get("name")
        if not name:
            error_message = "Cannot name Reader tuple field with a missing ABI component name"
            raise ValueError(error_message)
        named[name] = named_abi_value(component_value, component)
    return named


def reader_named_output(value: Any, function_name: str) -> Any:
    """Name a decoded Reader return value using the vendored ABI.

    :param value: Value returned by the corresponding Web3 contract call.
    :param function_name: Reader function used to produce ``value``.
    :returns: Recursively named output.
    """
    return named_abi_value(value, reader_output_abi(function_name))
