from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Optional

from .types import ToolCall

UNORDERED_LIST_ARG_KEYS = frozenset({"item_ids", "new_item_ids"})
DEFAULT_CONFIGURED_TOOL_ARGS_MATCH = "subset"


def tool_name(tool_call: Mapping[str, Any]) -> Optional[str]:
    for key in ("tool_name", "name", "tool"):
        if key in tool_call:
            value = tool_call[key]
            return str(value) if value is not None else None
    return None


def tool_args(tool_call: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("tool_args", "args", "arguments"):
        value = tool_call.get(key)
        if isinstance(value, dict):
            return value
    return {}


def normalize_tool_args(args: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: _normalize_arg_value(key, value)
        for key, value in args.items()
    }


def tool_args_match(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    return normalize_tool_args(expected) == normalize_tool_args(actual)


def tool_args_subset_match(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    ignore_arg_keys: list[str] | set[str] | tuple[str, ...] | None = None,
) -> bool:
    ignored = set(ignore_arg_keys or [])
    normalized_expected = {
        key: value
        for key, value in normalize_tool_args(expected).items()
        if key not in ignored
    }
    normalized_actual = {
        key: value
        for key, value in normalize_tool_args(actual).items()
        if key not in ignored
    }
    return all(
        key in normalized_actual and normalized_actual[key] == value
        for key, value in normalized_expected.items()
    )


def tool_args_match_with_options(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    match_mode: str = "exact",
    ignore_arg_keys: list[str] | set[str] | tuple[str, ...] | None = None,
    match_options: Mapping[str, Any] | Sequence[Any] | None = None,
) -> bool:
    if match_mode == "one_of":
        options = _expected_arg_options(match_options)
        return any(
            isinstance(option, Mapping)
            and tool_args_match_with_options(
                option,
                actual,
                match_mode="exact",
                ignore_arg_keys=ignore_arg_keys,
            )
            for option in options
        )
    if match_mode == "subset":
        return tool_args_subset_match(
            expected,
            actual,
            ignore_arg_keys=ignore_arg_keys,
        )
    if ignore_arg_keys:
        ignored = set(ignore_arg_keys)
        expected = {key: value for key, value in expected.items() if key not in ignored}
        actual = {key: value for key, value in actual.items() if key not in ignored}
    return tool_args_match(expected, actual)


def configured_tool_args_match(
    configured: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    expected_args = tool_args(configured)
    metadata = configured.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    return tool_args_match_with_options(
        expected_args,
        actual,
        match_mode=str(metadata.get("tool_args_match") or DEFAULT_CONFIGURED_TOOL_ARGS_MATCH),
        ignore_arg_keys=metadata.get("ignore_tool_arg_keys") or None,
        match_options=metadata,
    )


def tool_call_signature(
    tool_call: Mapping[str, Any] | ToolCall,
    *,
    ignore_arg_keys: list[str] | set[str] | tuple[str, ...] | None = None,
) -> tuple[Optional[str], str]:
    args = tool_args(tool_call)
    if ignore_arg_keys:
        ignored = set(ignore_arg_keys)
        args = {key: value for key, value in args.items() if key not in ignored}
    return (
        tool_name(tool_call),
        json.dumps(normalize_tool_args(args), sort_keys=True, ensure_ascii=True),
    )


def tool_call_matches(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> bool:
    expected_name = tool_name(expected)
    actual_name = tool_name(actual)
    if expected_name and actual_name != expected_name:
        return False
    return configured_tool_args_match(expected, tool_args(actual))


def _normalize_arg_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return normalize_tool_args(value)
    if isinstance(value, list):
        normalized = [_normalize_list_value(item) for item in value]
        if key in UNORDERED_LIST_ARG_KEYS:
            return sorted(normalized, key=_canonical_json)
        return normalized
    return value


def _normalize_list_value(value: Any) -> Any:
    if isinstance(value, dict):
        return normalize_tool_args(value)
    if isinstance(value, list):
        return [_normalize_list_value(item) for item in value]
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _expected_arg_options(
    match_options: Mapping[str, Any] | Sequence[Any] | None,
) -> list[Any]:
    if isinstance(match_options, Mapping):
        options = match_options.get("tool_arg_options") or match_options.get("options")
    else:
        options = match_options
    if isinstance(options, list):
        return options
    if isinstance(options, tuple):
        return list(options)
    return []
