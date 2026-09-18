"""Local Python replacement adapter for benchmark-multinode-tmpl.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from py_workflow.benchmark import JobResult, execute
from py_workflow.manifest import Project, WorkflowError


def execute_multinode(
    entry: dict[str, Any], *, project: Project, jobs_root: Path, artifact_root: Path,
) -> JobResult:
    """Run a multi-node generated entry through the established launcher."""
    if entry.get("prefill") is None or entry.get("decode") is None:
        raise WorkflowError("multi-node adapter requires prefill and decode matrix settings")
    return execute(entry, project=project, jobs_root=jobs_root, artifact_root=artifact_root)
