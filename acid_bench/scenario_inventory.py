from __future__ import annotations

import hashlib
import json
from pathlib import Path


def scenario_inventory_digest(root: Path | str) -> tuple[int, str]:
    """Return the canonical count and SHA-256 for a scenario inventory."""
    scenario_root = Path(root)
    digest = hashlib.sha256()
    count = 0
    for path in sorted(scenario_root.rglob("*.json")):
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        rel_path = path.relative_to(scenario_root).as_posix()
        digest.update(rel_path.encode("utf-8"))
        digest.update(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        count += 1
    return count, digest.hexdigest()
