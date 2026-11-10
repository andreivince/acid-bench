from __future__ import annotations

import math
from typing import Optional

CONFIDENCE_Z_95 = 1.959963984540054


def rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return numerator / denominator


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = CONFIDENCE_Z_95,
) -> tuple[Optional[float], Optional[float]]:
    if total <= 0:
        return None, None
    if successes < 0 or successes > total:
        raise ValueError("successes must be between 0 and total")

    p = successes / total
    denominator = 1 + (z * z / total)
    center = p + (z * z / (2 * total))
    margin = z * math.sqrt((p * (1 - p) / total) + (z * z / (4 * total * total)))
    return (
        max(0.0, (center - margin) / denominator),
        min(1.0, (center + margin) / denominator),
    )


def binary_rate_summary(successes: int, total: int) -> dict[str, Optional[float] | int]:
    ci_low, ci_high = wilson_interval(successes, total)
    return {
        "successes": successes,
        "total": total,
        "rate": rate(successes, total),
        "ci_low": ci_low,
        "ci_high": ci_high,
    }
