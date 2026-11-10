from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

from .provider_contracts import provider_contract_metadata
from .task_overlays import load_overlay_set
from .tau_provenance import patched_tau_paths as patched_tau_paths
from .tau_provenance import sha256_files as sha256_files
from .tau_provenance import tau_tree_sha256
from .validator import load_scenario

PROVENANCE_FIELDS = (
    "tau_commit",
    "tau_patch_sha256",
    "tau_tree_sha256",
    "acid_bench_commit",
    "acid_bench_diff_sha256",
)
STANDALONE_ARTIFACT_PROVENANCE_VERSION = 1
BASE_TAU_NO_OVERLAY_CONDITION_ID = "base_tau_no_overlay"
DEFAULT_TASK_OVERLAY_POLICY = "require_task_overlay_set"
BASE_TAU_NO_OVERLAY_POLICY = BASE_TAU_NO_OVERLAY_CONDITION_ID


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_models(path: Path | None) -> list[dict[str, Any]] | None:
    if path is None:
        return None
    models = load_json(path)
    if not isinstance(models, list):
        raise ValueError(f"models config must be a JSON array: {path}")
    return models


def iter_scenarios(scenario_dir: Path) -> Iterator[tuple[Path, dict[str, Any]]]:
    for path in sorted(scenario_dir.rglob("*.json")):
        yield path, load_scenario(path)


def iter_json_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*.json")):
        yield path


def model_slug(model: dict[str, Any]) -> str:
    return str(model["name"]).replace("/", "_")


def model_identity_fields(model: dict[str, Any]) -> dict[str, Any]:
    provider_contract = provider_contract_metadata(model)
    return {
        "model": model.get("name", "unknown"),
        "provider": model.get("provider", "unknown"),
        "user_model": model.get("user_model", model.get("name", "unknown")),
        "user_provider": model.get("user_provider", model.get("provider", "unknown")),
        "agent_strategy": model.get("agent_strategy", "tool-calling"),
        "model_owner": model.get("model_owner") or provider_contract.get("model_owner"),
        "provider_contract": provider_contract,
    }


def metadata_for_model(model: dict[str, Any] | None) -> dict[str, Any]:
    if model is None:
        return {
            "model": "unknown",
            "provider": "unknown",
            "agent_strategy": "unknown",
            "publication_ready": False,
            "publication_warning": "pass --models-config for publication-grade missing-run accounting",
        }
    return {
        **model_identity_fields(model),
        "model_config": model,
    }


def task_overlay_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    task_overlay = metadata.get("task_overlay")
    return task_overlay if isinstance(task_overlay, dict) else {}


def prompt_condition(metadata: dict[str, Any], *, prefer_embedded: bool = False) -> str:
    value = metadata.get("prompt_condition")
    if prefer_embedded and isinstance(value, str) and value:
        return value

    parts = []
    task_prompt_condition = metadata.get("task_prompt_condition") or metadata.get(
        "condition_id"
    )
    if (
        isinstance(task_prompt_condition, str)
        and task_prompt_condition == BASE_TAU_NO_OVERLAY_CONDITION_ID
    ):
        parts.append(f"task_prompt:{BASE_TAU_NO_OVERLAY_CONDITION_ID}")

    task_overlay_set_id = task_overlay_metadata(metadata).get("overlay_set_id")
    if isinstance(task_overlay_set_id, str) and task_overlay_set_id:
        parts.append(f"task_overlay:{task_overlay_set_id}")
    else:
        task_overlay_set = metadata.get("task_overlay_set")
        if isinstance(task_overlay_set, dict):
            task_overlay_set_id = task_overlay_set.get("overlay_set_id")
            if isinstance(task_overlay_set_id, str) and task_overlay_set_id:
                parts.append(f"task_overlay:{task_overlay_set_id}")

    user_strategy = metadata.get("user_strategy")
    if isinstance(user_strategy, str) and user_strategy == "scripted":
        parts.append("user_mode:scripted")

    policy_profile = metadata.get("acid_user_policy_profile")
    stop_policy = metadata.get("acid_user_stop_policy")
    if isinstance(policy_profile, str) and policy_profile:
        parts.append(f"user_policy_profile:{policy_profile}")
    elif isinstance(stop_policy, str) and stop_policy:
        parts.append(f"user_stop_policy:{stop_policy}")
    return "+".join(parts) if parts else "baseline"


def is_base_tau_no_overlay_condition(metadata: dict[str, Any]) -> bool:
    value = metadata.get("prompt_condition")
    if isinstance(value, str) and value:
        return f"task_prompt:{BASE_TAU_NO_OVERLAY_CONDITION_ID}" in value.split("+")
    if metadata.get("condition_id") == BASE_TAU_NO_OVERLAY_CONDITION_ID:
        return True
    if metadata.get("task_prompt_condition") == BASE_TAU_NO_OVERLAY_CONDITION_ID:
        return True
    return False


def metadata_overlay_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    task_overlay = task_overlay_metadata(metadata)
    task_overlay_set = metadata.get("task_overlay_set")
    if not isinstance(task_overlay_set, dict):
        task_overlay_set = {}
    applied_ids = task_overlay.get("applied_task_overlay_ids")
    if not isinstance(applied_ids, list) or not all(
        isinstance(value, str) for value in applied_ids
    ):
        applied_ids = []
    return {
        "prompt_condition": prompt_condition(metadata, prefer_embedded=True),
        "task_overlay_set_id": task_overlay.get("overlay_set_id")
        or task_overlay_set.get("overlay_set_id"),
        "task_overlay_set_sha256": task_overlay.get("overlay_set_sha256")
        or task_overlay_set.get("overlay_set_sha256"),
        "applied_task_overlay_ids": applied_ids,
        "acid_user_stop_policy": metadata.get("acid_user_stop_policy"),
        "acid_user_policy_profile": metadata.get("acid_user_policy_profile"),
    }


def standalone_result_identity_fields(
    metadata: dict[str, Any],
    trace: dict[str, Any] | None = None,
    trace_path: Path | None = None,
) -> dict[str, Any]:
    trace = trace if isinstance(trace, dict) else {}
    model_config = metadata.get("model_config")
    if not isinstance(model_config, dict):
        model_config = {}
    provider_contract = metadata.get("provider_contract")
    if not isinstance(provider_contract, dict):
        provider_contract = {}

    trace_run_id = trace.get("run_id")
    if not isinstance(trace_run_id, str) or not trace_run_id:
        trace_run_id = trace_path.stem if trace_path is not None else "missing"

    publication_run_id = (
        metadata.get("publication_run_id")
        or trace.get("publication_run_id")
        or metadata.get("run_id")
        or trace_run_id
    )
    return {
        "run_id": trace_run_id,
        "publication_run_id": publication_run_id,
        "model": metadata.get("model") or model_config.get("name") or "unknown",
        "provider": metadata.get("provider") or model_config.get("provider") or "unknown",
        "user_model": metadata.get("user_model")
        or model_config.get("user_model")
        or model_config.get("name")
        or "unknown",
        "user_provider": metadata.get("user_provider")
        or model_config.get("user_provider")
        or model_config.get("provider")
        or "unknown",
        "agent_strategy": metadata.get("agent_strategy")
        or model_config.get("agent_strategy")
        or "unknown",
        "model_owner": metadata.get("model_owner")
        or provider_contract.get("model_owner")
        or model_config.get("model_owner"),
        "task_id": metadata.get("task_id"),
    }


def standalone_result_provenance_fields(
    metadata: dict[str, Any],
    trace_path: Path | None = None,
) -> dict[str, Any]:
    provenance = {
        "schema_version": STANDALONE_ARTIFACT_PROVENANCE_VERSION,
        "trace_path": str(trace_path) if trace_path is not None else None,
    }
    for field in PROVENANCE_FIELDS:
        provenance[field] = metadata.get(field)
    return {
        **{field: metadata.get(field) for field in PROVENANCE_FIELDS},
        "artifact_provenance": provenance,
    }


def build_run_metadata(
    model: dict[str, Any],
    task_overlay_set: Path | None = None,
    root: Path | None = None,
    tau_root: Path | None = None,
    publication_run_id: str | None = None,
    python_bin: str | None = None,
    user_strategy: str | None = None,
    condition_id: str | None = None,
    task_overlay_policy: str | None = None,
    scenario_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or repository_root()
    tau_root = tau_root or root / "tau-bench"
    tau_diff = _git_value(tau_root, ["diff", "HEAD", "--"]) or ""
    acid_diff = _git_value(root, ["diff", "HEAD", "--"]) or ""
    model_config = deepcopy(model)
    if user_strategy == "scripted":
        model_config["user_model"] = "scripted-user"
        model_config["user_provider"] = "local"
        model_config["user_provider_options"] = {}
    metadata = {
        **model_identity_fields(model),
        "model_config": model_config,
        "provider_options": model.get("provider_options", {}),
        "user_provider_options": (
            {} if user_strategy == "scripted" else model.get("user_provider_options", {})
        ),
        "strict_tools": bool(model.get("strict_tools", False)),
        "tau_commit": _git_value(tau_root, ["rev-parse", "HEAD"]),
        "tau_patch_sha256": _sha256_text(tau_diff) if tau_diff else None,
        "tau_tree_sha256": tau_tree_sha256(tau_root),
        "tau_has_uncommitted_changes": bool(
            (_git_value(tau_root, ["status", "--porcelain"]) or "").strip()
        ),
        "acid_bench_commit": _git_value(root, ["rev-parse", "HEAD"]),
        "acid_bench_diff_sha256": _sha256_text(acid_diff),
        "acid_bench_has_uncommitted_changes": bool(
            (_git_value(root, ["status", "--porcelain"]) or "").strip()
        ),
    }
    if model.get("metadata"):
        metadata.update(model["metadata"])
    if user_strategy == "scripted":
        metadata["user_model"] = "scripted-user"
        metadata["user_provider"] = "local"
    publication_environment = _runtime_publication_environment(
        model_config.get("metadata", {}).get("publication_environment"),
        python_bin=python_bin,
    )
    metadata["publication_environment"] = publication_environment
    model_config.setdefault("metadata", {})["publication_environment"] = publication_environment
    if publication_run_id:
        metadata["publication_run_id"] = publication_run_id
    if condition_id:
        metadata["condition_id"] = condition_id
        if condition_id == BASE_TAU_NO_OVERLAY_CONDITION_ID:
            metadata["task_prompt_condition"] = BASE_TAU_NO_OVERLAY_CONDITION_ID
    if task_overlay_policy:
        metadata["task_overlay_policy"] = task_overlay_policy
    if scenario_set:
        metadata["scenario_set"] = scenario_set
    if task_overlay_set is not None:
        overlay_set = load_overlay_set(task_overlay_set)
        metadata["task_overlay_set"] = {
            "path": str(task_overlay_set),
            "overlay_set_id": overlay_set.overlay_set_id,
            "overlay_set_sha256": overlay_set.sha256,
        }
    if user_strategy:
        metadata["user_strategy"] = user_strategy
    metadata["prompt_condition"] = prompt_condition(metadata)
    return metadata


def _runtime_publication_environment(
    base: Any,
    *,
    python_bin: str | None = None,
) -> dict[str, Any]:
    environment = dict(base) if isinstance(base, dict) else {}
    python_executable = python_bin or sys.executable
    environment["python"] = _python_version_for_executable(python_executable)
    environment["python_executable"] = python_executable
    return environment


def _python_version_for_executable(python_bin: str) -> str:
    try:
        raw_version = subprocess.check_output(
            [python_bin, "--version"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return platform.python_version()
    prefix = "Python "
    return raw_version.removeprefix(prefix).strip() or platform.python_version()


def _git_value(cwd: Path, args: list[str]) -> str | None:
    if not (cwd / ".git").exists():
        return None
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
