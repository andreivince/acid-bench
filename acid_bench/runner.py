from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .injector import FaultInjector
from .mutations import apply_domain_mutation, apply_mutations
from .proxy import AcidProxy
from .trace import TraceWriter


@dataclass
class SimpleState:
    data: Dict[str, Any]

    def read(self) -> Dict[str, Any]:
        return deepcopy(self.data)

    def mutate(self, fault: Any, tool_call: Dict[str, Any], state_before: Any) -> None:
        if fault is not None and getattr(fault, "fault_type", None) == "crash_restart":
            if state_before is not None:
                self.data = deepcopy(state_before)
            return
        metadata = getattr(fault, "metadata", {}) if fault is not None else {}
        apply_domain_mutation(self.data, metadata, tool_call)
        apply_mutations(self.data, metadata)


def toy_executor_factory(state: SimpleState):
    def executor(tool_call: Dict[str, Any]) -> Dict[str, Any]:
        name = tool_call.get("tool_name") or tool_call.get("name") or tool_call.get("tool")
        args = tool_call.get("tool_args") or tool_call.get("args") or {}
        if name == "debit":
            amount = args.get("amount", 0)
            state.data["balance"] = state.data.get("balance", 0) - amount
            return {"status": "ok", "balance": state.data["balance"]}
        if name == "credit":
            amount = args.get("amount", 0)
            state.data["balance"] = state.data.get("balance", 0) + amount
            return {"status": "ok", "balance": state.data["balance"]}
        if name == "set":
            key = args.get("key")
            state.data[key] = args.get("value")
            return {"status": "ok"}
        if name == "inc":
            key = args.get("key")
            state.data[key] = state.data.get(key, 0) + args.get("amount", 0)
            return {"status": "ok", key: state.data[key]}
        if name == "dec":
            key = args.get("key")
            state.data[key] = state.data.get(key, 0) - args.get("amount", 0)
            return {"status": "ok", key: state.data[key]}
        return {"status": "ok"}

    return executor


def load_scenario(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def replay_scenario(
    scenario_path: str | Path,
    trace_path: str | Path,
    initial_state: Optional[Dict[str, Any]] = None,
) -> None:
    scenario = load_scenario(scenario_path)
    state = SimpleState(initial_state or {"balance": 100})
    executor = toy_executor_factory(state)

    injector = FaultInjector.from_dict({"fault_plan": scenario.get("fault_plan", [])})
    tracer = TraceWriter(
        path=trace_path,
        run_id=scenario.get("id", "unknown"),
        scenario_id=scenario.get("id", "unknown"),
        metadata={"runner": "replay"},
    )
    proxy = AcidProxy(
        executor=executor,
        injector=injector,
        tracer=tracer,
        state_reader=state.read,
        state_mutator=state.mutate,
        time_provider=lambda step_id: f"t{step_id:06d}",
    )

    tool_calls: List[Dict[str, Any]] = scenario.get("tool_calls", [])
    for call in tool_calls:
        proxy.execute(call)

    tracer.finalize()
