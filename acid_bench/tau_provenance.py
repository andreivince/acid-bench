from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

PATCHED_TAU_FILES = (
    "run.py",
    "tau_bench/run.py",
    "tau_bench/types.py",
    "tau_bench/agents/tool_calling_agent.py",
    "tau_bench/envs/__init__.py",
    "tau_bench/envs/base.py",
    "tau_bench/envs/user.py",
    "tau_bench/envs/airline/tools/update_reservation_flights.py",
    "tau_bench/envs/airline/env.py",
    "tau_bench/envs/retail/env.py",
)


def patched_tau_paths(tau_root: Path) -> list[Path]:
    return [tau_root / relative for relative in PATCHED_TAU_FILES]


def sha256_files(paths: Iterable[Path], *, relative_to: Path | None = None) -> str:
    digest = hashlib.sha256()
    for path in paths:
        label = path.relative_to(relative_to) if relative_to is not None else path
        digest.update(str(label).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def tau_tree_sha256(tau_root: Path) -> str:
    """Hash the publication-managed Tau files with stable artifact labels.

    Publication traces were produced with Tau at ``<repo>/tau-bench``.  The
    original digest therefore includes labels prefixed by ``tau-bench/``.
    Reproducing the checkout in a temporary or user-selected directory must
    not change its content identity, so this helper preserves those canonical
    labels independently of the checkout's physical location.
    """

    digest = hashlib.sha256()
    for relative in PATCHED_TAU_FILES:
        path = tau_root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        digest.update(f"tau-bench/{relative}".encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
