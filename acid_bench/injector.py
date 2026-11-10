from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .tool_matching import (
    DEFAULT_CONFIGURED_TOOL_ARGS_MATCH,
    tool_args,
    tool_args_match_with_options,
    tool_call_signature,
    tool_name,
)
from .types import FaultInstruction, ToolCall

SUPPORTED_FAULTS = {"ghost_success", "hard_fail", "stale_read", "crash_restart"}


class FaultInjector:
    def __init__(self, faults: Iterable[FaultInstruction]) -> None:
        self._by_step: Dict[int, List[FaultInstruction]] = defaultdict(list)
        self._any_step: List[FaultInstruction] = []
        self._used: set[tuple[int, int]] = set()
        self._tool_counts: Dict[str, int] = defaultdict(int)
        self._eligible_counts: Dict[tuple[int, int], int] = defaultdict(int)
        self._tool_history: List[ToolCall] = []
        for fault in faults:
            if fault.step_index <= 0:
                self._any_step.append(fault)
            else:
                self._by_step[fault.step_index].append(fault)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "FaultInjector":
        faults_raw = data.get("faults") or data.get("fault_plan") or []
        faults = [FaultInstruction(**fault) for fault in faults_raw]
        return FaultInjector(faults)

    @staticmethod
    def from_file(path: str | Path) -> "FaultInjector":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return FaultInjector.from_dict(data)

    def match(self, step_index: int, tool_call: ToolCall) -> Optional[FaultInstruction]:
        current_tool_name = tool_name(tool_call)
        if current_tool_name:
            self._tool_counts[current_tool_name] += 1

        def record_current_call() -> None:
            self._tool_history.append(
                {"tool_name": current_tool_name, "tool_args": tool_args(tool_call)}
            )

        def requires_prior_tool_calls(fault: FaultInstruction) -> bool:
            metadata = getattr(fault, "metadata", None) or {}
            required = metadata.get("require_tool_calls_before")
            if required is None:
                return True
            if not isinstance(required, list):
                return True
            required_calls = [
                call
                for call in required
                if isinstance(call, dict) and isinstance(call.get("tool_name"), str)
            ]
            required_counts = Counter(tool_call_signature(call) for call in required_calls)
            history_counts = Counter(
                tool_call_signature(call) for call in self._tool_history
            )
            return all(
                history_counts.get(signature, 0) >= count
                for signature, count in required_counts.items()
            )

        def matches_tool_args(fault: FaultInstruction, actual_args: dict[str, Any]) -> bool:
            if fault.tool_args is None:
                return True
            metadata = getattr(fault, "metadata", None) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            return tool_args_match_with_options(
                fault.tool_args,
                actual_args,
                match_mode=str(metadata.get("tool_args_match") or DEFAULT_CONFIGURED_TOOL_ARGS_MATCH),
                ignore_arg_keys=metadata.get("ignore_tool_arg_keys") or None,
                match_options=metadata,
            )

        def requires_prior_tools(fault: FaultInstruction) -> bool:
            metadata = getattr(fault, "metadata", None) or {}
            required = metadata.get("require_tool_before")
            if required is None:
                return True
            if isinstance(required, str):
                required_tools = [required]
            elif isinstance(required, list):
                required_tools = [tool for tool in required if isinstance(tool, str)]
            else:
                return True
            for tool, required_count in Counter(required_tools).items():
                count = self._tool_counts.get(tool, 0)
                if tool == current_tool_name:
                    count -= 1
                if count < required_count:
                    return False
            return True

        def matches_occurrence(key: tuple[int, int], fault: FaultInstruction) -> bool:
            if fault.occurrence is None:
                return True
            self._eligible_counts[key] += 1
            return self._eligible_counts[key] == fault.occurrence

        candidates = self._by_step.get(step_index, [])
        for idx, fault in enumerate(candidates):
            key = (step_index, idx)
            if key in self._used:
                continue
            if fault.tool_name and current_tool_name and fault.tool_name != current_tool_name:
                continue
            if not matches_tool_args(fault, tool_args(tool_call)):
                continue
            if not requires_prior_tool_calls(fault):
                continue
            if not requires_prior_tools(fault):
                continue
            if fault.fault_type not in SUPPORTED_FAULTS:
                continue
            if not matches_occurrence(key, fault):
                continue
            self._used.add(key)
            record_current_call()
            return fault
        for idx, fault in enumerate(self._any_step):
            key = (-1, idx)
            if key in self._used:
                continue
            if fault.tool_name and current_tool_name and fault.tool_name != current_tool_name:
                continue
            if not matches_tool_args(fault, tool_args(tool_call)):
                continue
            if not requires_prior_tool_calls(fault):
                continue
            if not requires_prior_tools(fault):
                continue
            if fault.fault_type not in SUPPORTED_FAULTS:
                continue
            if not matches_occurrence(key, fault):
                continue
            self._used.add(key)
            record_current_call()
            return fault
        record_current_call()
        return None

    def reset(self) -> None:
        self._used.clear()
        self._tool_counts.clear()
        self._eligible_counts.clear()
        self._tool_history.clear()
