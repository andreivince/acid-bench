#!/usr/bin/env python3
"""Build the deterministic ACID-Bench research artifact ZIP from an explicit whitelist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acid_bench.artifact_verifier import verify_controlled_artifact  # noqa: E402, I001


BUILD_SCHEMA = "acid.controlled_artifact_build.v1"
PACKAGE_SCHEMA = "acid.controlled_artifact_package.v1"
DEFAULT_OUTPUT = ROOT / "dist" / "acid-bench-1.0.0-artifact.zip"
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SKIP_PARTS = {".DS_Store", "__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
FORBIDDEN_TOP_LEVEL = {
    ".git",
    ".venv",
    "acid_traces",
    "local_audit",
    "results",
    "tau-bench",
    "traces",
}


EXACT_FILES = (
    "README.md",
    "KNOWN_ISSUES.md",
    "ERRATA.md",
    "CANARY.txt",
    "LICENSE",
    "LICENSE-DATA",
    "THIRD_PARTY_NOTICES.md",
    "CITATION.cff",
    "pyproject.toml",
    "requirements-publication.txt",
    "data/corrected_labels.csv",
    "configs/artifacts/publication-runs.json",
    "configs/artifacts/research-freeze.json",
    "configs/artifacts/trace-audit-samples.json",
    "configs/task_overlays/acid-tau-clarifications-v1.json",
    "artifacts/row-evidence/validation/manifest.json",
    "artifacts/row-evidence/validation/rows.jsonl",
    "artifacts/row-evidence/validation/validation-results.tex",
    "artifacts/row-evidence/validation/invalid-warning-taxonomy.json",
    "artifacts/row-evidence/validation/invalid-warning-taxonomy.csv",
    "artifacts/trace-audit/samples/manifest.json",
    "artifacts/evaluator-v1/evaluate_acid_traces.py.txt",
    "docs/a01-walkthrough.md",
    "docs/architecture.md",
    "docs/benchmark-card.md",
    "docs/controlled-artifact.md",
    "docs/evaluator.md",
    "docs/reproducibility.md",
    "docs/scenario-admission-audit.md",
    "scripts/bootstrap_tau_bench.py",
    "scripts/build_invalid_warning_taxonomy.py",
    "scripts/build_corrected_labels.py",
    "scripts/check_release_boundary.py",
    "scripts/evaluate_acid_traces.py",
    "scripts/export_publication_tables.py",
    "scripts/package_controlled_artifact.py",
    "scripts/recompute_validation_table_from_row_evidence.py",
    "scripts/replay_trace_audit_pack.py",
    "scripts/run_tau_suite.py",
    "vendor/tau-bench-publication.bundle",
    "vendor/tau-bench-publication-working-tree.patch",
)

INCLUDE_DIRS = (
    ("acid_bench", {".py", ".json"}),
    ("scenarios", {".json"}),
    ("artifacts/trace-audit/samples/expected", {".json"}),
    ("artifacts/trace-audit/samples/traces", {".json"}),
)



class PackageBuildError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(relative_dir: str, suffixes: set[str]) -> Iterable[str]:
    base = ROOT / relative_dir
    if not base.is_dir():
        raise PackageBuildError(f"required artifact directory is missing: {relative_dir}")
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        if path.suffix in suffixes:
            yield relative.as_posix()


def _referenced_model_configs() -> set[str]:
    rows_path = ROOT / "artifacts" / "row-evidence" / "validation" / "rows.jsonl"
    configs: set[str] = set()
    for line_number, line in enumerate(rows_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        config = row.get("model_config_path")
        if not isinstance(config, str) or not config.startswith("configs/models/"):
            raise PackageBuildError(
                f"validation row {line_number} has an invalid model_config_path: {config!r}"
            )
        configs.add(config)
    return configs


def selected_files() -> list[str]:
    selected = set(EXACT_FILES)
    selected.update(_referenced_model_configs())
    for relative_dir, suffixes in INCLUDE_DIRS:
        selected.update(_iter_files(relative_dir, suffixes))
    missing = [relative for relative in sorted(selected) if not (ROOT / relative).is_file()]
    if missing:
        raise PackageBuildError("required artifact files are missing: " + ", ".join(missing))
    return sorted(selected)


def _copy_bytes(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise PackageBuildError(f"symbolic links are not allowed in the artifact: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as source_handle, destination.open("wb") as target_handle:
        shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)


def _write_manifest(stage: Path, files: list[str]) -> dict[str, Any]:
    freeze = json.loads(
        (stage / "configs" / "artifacts" / "research-freeze.json").read_text(
            encoding="utf-8"
        )
    )
    entries = []
    for relative in files:
        path = stage / relative
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "artifact_version": "1",
        "freeze_id": freeze["freeze_id"],
        "distribution": "controlled_external",
        "scientific_scope": "frozen",
        "deterministic_archive": True,
        "file_count": len(entries) + 1,  # entries plus ARTIFACT_MANIFEST.json itself
        "verification_command": "python3 -m acid_bench verify-artifact",
        "files": entries,
    }
    (stage / "ARTIFACT_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _audit_stage(stage: Path, manifest: dict[str, Any]) -> None:
    expected = {"ARTIFACT_MANIFEST.json", *(entry["path"] for entry in manifest["files"])}
    actual = {
        path.relative_to(stage).as_posix()
        for path in stage.rglob("*")
        if path.is_file()
        and not any(part in SKIP_PARTS for part in path.relative_to(stage).parts)
        and path.suffix not in SKIP_SUFFIXES
    }
    issues: list[str] = []
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            issues.append("missing staged files: " + ", ".join(missing))
        if unexpected:
            issues.append("unexpected staged files: " + ", ".join(unexpected))
    mac_home_marker = b"/" + b"Users" + b"/"
    windows_home_marker = b"C:" + b"\\" + b"Users" + b"\\"
    for relative in sorted(actual):
        path = Path(relative)
        if path.parts and path.parts[0] in FORBIDDEN_TOP_LEVEL:
            issues.append(f"forbidden top-level path: {relative}")
        if any(part == ".env" or part.startswith(".env.") for part in path.parts):
            issues.append(f"environment file included: {relative}")
        payload = (stage / relative).read_bytes()
        if mac_home_marker in payload or windows_home_marker in payload:
            issues.append(f"host-local absolute path included: {relative}")
    if issues:
        raise PackageBuildError("; ".join(issues))


def _create_deterministic_zip(
    stage: Path, output: Path, manifest: dict[str, Any]
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        archive_paths = sorted(
            {"ARTIFACT_MANIFEST.json", *(entry["path"] for entry in manifest["files"])}
        )
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for relative in archive_paths:
                path = stage / relative
                info = zipfile.ZipInfo(relative, date_time=ZIP_TIMESTAMP)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, path.read_bytes())
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def _verify_extracted(stage: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "acid_bench", "verify-artifact"],
        cwd=stage,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise PackageBuildError(
            "staged artifact verifier did not emit one JSON object: "
            f"stdout={completed.stdout!r}; stderr={completed.stderr!r}"
        ) from exc
    if completed.returncode != 0 or payload.get("ok") is not True:
        raise PackageBuildError(
            "staged artifact verification failed: "
            f"returncode={completed.returncode}; errors={payload.get('errors')}"
        )
    return payload


def build_package(output: Path) -> dict[str, Any]:
    source_verification = verify_controlled_artifact(root=ROOT)
    if source_verification.get("ok") is not True:
        raise PackageBuildError(
            "source artifact verification failed: "
            + "; ".join(source_verification.get("errors") or ["unknown verification error"])
        )
    files = selected_files()
    with tempfile.TemporaryDirectory(prefix="acid-controlled-artifact-") as tmpdir:
        stage = Path(tmpdir) / "artifact"
        stage.mkdir()
        for relative in files:
            _copy_bytes(ROOT / relative, stage / relative)
        manifest = _write_manifest(stage, files)
        _audit_stage(stage, manifest)
        staged_verification = _verify_extracted(stage)
        _create_deterministic_zip(stage, output.resolve(), manifest)
    return {
        "schema": BUILD_SCHEMA,
        "ok": True,
        "output": str(output.resolve()),
        "sha256": sha256_file(output.resolve()),
        "size_bytes": output.resolve().stat().st_size,
        "file_count": manifest["file_count"],
        "freeze_id": manifest["freeze_id"],
        "verification_summary": staged_verification["summary"],
        "errors": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a deterministic, controlled ACID-Bench research artifact ZIP."
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    try:
        result = build_package(Path(args.output))
    except Exception as exc:
        result = {
            "schema": BUILD_SCHEMA,
            "ok": False,
            "output": str(Path(args.output).resolve()),
            "errors": [str(exc)],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
