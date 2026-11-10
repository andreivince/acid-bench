from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class StepRecord:
    step_id: int
    tool_name: str
    tool_args: Dict[str, Any]
    injected_fault: Optional[str]
    real_outcome: Any
    agent_outcome: Any
    state_before: Any
    state_after: Any
    state_delta: Any
    timestamp: str = field(default_factory=_utc_now_iso)


class TraceWriter:
    def __init__(
        self,
        path: str | Path,
        run_id: str,
        scenario_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        auto_flush: bool = False,
    ) -> None:
        self._path = Path(path)
        self._run_id = run_id
        self._scenario_id = scenario_id
        self._metadata = metadata or {}
        self._auto_flush = auto_flush
        self._steps: List[StepRecord] = []

    def append(self, record: StepRecord) -> None:
        self._steps.append(record)
        if self._auto_flush:
            self._write()

    def finalize(self) -> None:
        self._write()

    def _write(self) -> None:
        payload = {
            "run_id": self._run_id,
            "scenario_id": self._scenario_id,
            "metadata": self._metadata,
            "steps": [asdict(step) for step in self._steps],
        }
        self._atomic_write(self._path, payload)

    @staticmethod
    def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=str(path.parent),
            prefix=f".{path.name}.",
        ) as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=True)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
