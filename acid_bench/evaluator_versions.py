"""Scientific evaluator identity, independent of package and artifact versions.

This release ships one evaluator. ``acid_bench/assertions.py`` and
``acid_bench/assertions_v1.py`` are byte-identical; the second path is the one
named and hashed by ``configs/artifacts/research-freeze.json``.
"""

FROZEN_EVALUATOR_VERSION = "acid-bench-evaluator-v1"
CURRENT_EVALUATOR_VERSION = FROZEN_EVALUATOR_VERSION
