"""Local result aggregation and hardware success-rate reporting."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def calculate(job_records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count local non-skipped jobs by generated runner identity."""
    result: dict[str, dict[str, int]] = defaultdict(lambda: {"n_success": 0, "total": 0})
    for job in job_records:
        status = job.get("status")
        if status == "skipped":
            continue
        entry = job.get("entry", {})
        runner = str(entry.get("runner", "unknown"))
        result[runner]["total"] += 1
        if status == "success":
            result[runner]["n_success"] += 1
    return dict(sorted(result.items()))
