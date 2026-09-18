"""Local equivalent of ``.github/workflows/collect-evals.yml``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from py_workflow.manifest import WorkflowError


INPUT_DIRECTORY = ".collect-evals-input"
SUMMARY_FILE = "eval_summary.md"


def materialize(artifacts: Path, destination: Path, *, result_prefix: str = "all") -> list[Path]:
    """Build the Actions ``eval_*`` download layout from local artifacts."""
    if result_prefix != "all":
        wanted = f"eval_{result_prefix}_"
    else:
        wanted = "eval_"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    staged: list[Path] = []
    for source in sorted(artifacts.glob(f"{wanted}*")):
        if not source.is_dir():
            continue
        target = destination / source.name
        if target.exists():
            raise WorkflowError(f"duplicate local eval artifact name: {source.name}")
        shutil.copytree(source, target)
        staged.append(target)
    return staged


def collect(*, python: Path, artifacts: Path, aggregates: Path, result_prefix: str = "all") -> Path | None:
    """Summarize local eval artifacts and write ``agg_eval_<prefix>.json``.

    The collector's standard output replaces ``GITHUB_STEP_SUMMARY`` and is
    retained in the archive as Markdown so it remains inspectable offline.
    """
    input_root = aggregates / INPUT_DIRECTORY
    staged = materialize(artifacts, input_root, result_prefix=result_prefix)
    if not staged:
        return None
    completed = subprocess.run(
        [str(python), "-m", "infx.results.collect_eval_results", str(input_root), result_prefix],
        cwd=aggregates,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    summary = aggregates / SUMMARY_FILE
    summary.write_text("## Eval Summary\n\n" + completed.stdout, encoding="utf-8")
    if completed.returncode:
        raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "eval result collection failed")
    output = aggregates / f"agg_eval_{result_prefix}.json"
    if not output.is_file():
        raise WorkflowError(f"eval collector did not create {output.name}")
    reusable_output_dir = artifacts / f"eval_results_{result_prefix}"
    reusable_output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(output, reusable_output_dir / output.name)
    return output
