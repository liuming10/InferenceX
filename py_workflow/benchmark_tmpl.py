"""Local Python replacement adapter for benchmark-tmpl.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from py_workflow.benchmark import JobResult, execute
from py_workflow.manifest import Project, WorkflowError


def execute_single_node(
    entry: dict[str, Any], *, project: Project, jobs_root: Path, artifact_root: Path,
) -> JobResult:
    """Run a single-node generated entry through the established launcher."""
    if entry.get("prefill") is not None:
        raise WorkflowError("single-node adapter received a multi-node matrix entry")
    return execute(entry, project=project, jobs_root=jobs_root, artifact_root=artifact_root)
