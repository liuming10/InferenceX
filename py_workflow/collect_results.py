"""Local equivalent of ``.github/workflows/collect-results.yml``.

The Actions workflow downloads every artifact whose name begins with ``bmk_``
and invokes the established result collector.  Local benchmark adapters retain
those artifact categories below one archive, so this module materializes the
same download layout before delegating collection to the selected project's
virtual environment.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from py_workflow.manifest import WorkflowError


FIXED_PREFIX = "bmk_"
AGENTIC_PREFIX = "bmk_agentic_"
AGGREGATE_CATEGORY = "results_bmk"
INPUT_DIRECTORY = ".collect-results-input"


def _copy_tree(source: Path, destination: Path) -> None:
    """Copy one artifact directory without silently merging producers."""
    if destination.exists():
        raise WorkflowError(f"duplicate local result artifact name: {destination.name}")
    shutil.copytree(source, destination)


def materialize(artifacts: Path, destination: Path, *, result_prefix: str = "bmk") -> list[Path]:
    """Build the ``download-artifact``-equivalent input tree.

    ``bmk_<stem>`` and ``bmk_agentic_<stem>`` preserve their Actions artifact
    names. Raw ``agentic_*`` output is deliberately excluded, matching the
    workflow's ``bmk_*`` pattern. ``results_bmk`` is reserved for the collected
    aggregate uploaded by the local replacement and is never collection input.
    """
    if result_prefix != "bmk":
        raise WorkflowError(f"unsupported local result prefix: {result_prefix!r}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    staged: list[Path] = []

    for source in sorted(artifacts.glob(f"{FIXED_PREFIX}*")):
        if source.is_dir() and not source.name.startswith(AGENTIC_PREFIX):
            target = destination / source.name
            _copy_tree(source, target)
            staged.append(target)

    for source in sorted(artifacts.glob(f"{AGENTIC_PREFIX}*")):
        if source.is_dir():
            target = destination / source.name
            _copy_tree(source, target)
            staged.append(target)
    return staged


def collect(*, python: Path, artifacts: Path, aggregates: Path, result_prefix: str = "bmk") -> Path | None:
    """Collect fixed and AgentX aggregate JSON into ``agg_<prefix>.json``."""
    input_root = aggregates / INPUT_DIRECTORY
    staged = materialize(artifacts, input_root, result_prefix=result_prefix)
    if not staged:
        return None
    completed = subprocess.run(
        [str(python), "-m", "infx.results.collect_results", str(input_root), result_prefix],
        cwd=aggregates,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "result collection failed")
    output = aggregates / f"agg_{result_prefix}.json"
    if not output.is_file():
        raise WorkflowError(f"result collector did not create {output.name}")
    reusable_output_dir = artifacts / AGGREGATE_CATEGORY
    reusable_output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output, reusable_output_dir / output.name)
    return output
