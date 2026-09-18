"""Run selected InferenceX configurations without GitHub Actions."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from py_workflow.benchmark_multinode_tmpl import execute_multinode
from py_workflow.benchmark_tmpl import execute_single_node
from py_workflow.collect_evals import collect as collect_evals
from py_workflow.collect_results import collect as collect_results
from py_workflow.manifest import (
    WorkflowError, artifact_inventory, atomic_json, make_manifest, matrix_digest,
    publish_directory, resolve_project, utc_now,
)
from py_workflow.reuse import decide_reuse, stage_reused_artifacts
from py_workflow.success_rate import calculate


MODES = (
    "sweep-enabled", "full-sweep-enabled", "full-sweep-fail-fast",
    "full-sweep-fail-fast-no-canary", "non-canary-full-sweep-enabled",
)


def parser() -> argparse.ArgumentParser:
    """Build the local executor command-line interface."""
    value = argparse.ArgumentParser(
        description=(
            "Run selected InferenceX configurations locally without GitHub Actions. "
            "The selected project's existing .venv is used for matrix generation, "
            "launchers, collection, and validation; this command never installs or "
            "modifies dependencies."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  # Run one prepared checkout. Its root .venv and subprojects must already exist.
  python -m py_workflow.run_sweep \\
    --project-dir /workspace/InferenceX \\
    --config-key dsv4flash-w4a8-dcu-hygon-sglang \\
    --archive-root /stortest/lium_space/agentX/InferenceX_result

  # Inspect the generated local matrix and archive plan without launching a benchmark.
  python -m py_workflow.run_sweep \\
    --project-dir /workspace/InferenceX \\
    --config-key dsv4flash-w4a8-dcu-hygon-sglang \\
    --no-evals --dry-run \\
    --archive-root /stortest/lium_space/agentX/InferenceX_result

  # Run selected concurrency points and reuse only a fully compatible local archive.
  python -m py_workflow.run_sweep \\
    --project-dir /workspace/InferenceX \\
    --config-key dsv4flash-w4a8-dcu-hygon-sglang \\
    --conc 1 16 32 --reuse auto \\
    --archive-root /stortest/lium_space/agentX/InferenceX_result

  # Let the executor manage a clone below <archive-root>/.worktrees.
  python -m py_workflow.run_sweep \\
    --repository https://example.invalid/InferenceX.git --ref main \\
    --config-key dsv4flash-w4a8-dcu-hygon-sglang \\
    --archive-root /stortest/lium_space/agentX/InferenceX_result

Notes:
  --no-evals cannot be combined with --evals-only or --all-evals.
  --max-parallel is currently restricted to 1 because existing launchers write
  shared project-root output paths. --compare-results requires DATABASE_URL.
""",
    )
    source = value.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--project-dir",
        type=Path,
        metavar="PATH",
        help=(
            "Use an already prepared project. Its root .venv and all subprojects "
            "must exist; the executor never clones, fetches, cleans, checks out, "
            "or updates it."
        ),
    )
    source.add_argument(
        "--repository",
        metavar="URL",
        help=(
            "Clone or update an executor-owned repository below --workspace-root "
            "(default: <archive-root>/.worktrees). The checked-out project must "
            "still provide a ready root .venv."
        ),
    )
    value.add_argument(
        "--workspace-root",
        type=Path,
        metavar="PATH",
        help="Root for executor-managed clones; defaults to <archive-root>/.worktrees.",
    )
    value.add_argument(
        "--ref",
        default="HEAD",
        metavar="REF",
        help="Git ref for --repository mode (default: HEAD).",
    )
    value.add_argument(
        "--allow-dirty-project",
        action="store_true",
        help="Allow a supplied --project-dir with uncommitted Git changes.",
    )
    value.add_argument(
        "--config-key",
        action="append",
        required=True,
        metavar="KEY_OR_GLOB",
        help="Configuration key or glob accepted by infx.matrix.generate; repeat to select more keys.",
    )
    value.add_argument(
        "--conc",
        nargs="+",
        type=int,
        metavar="N",
        help="Restrict generated benchmark jobs to these concurrency values.",
    )
    value.add_argument(
        "--mode",
        choices=MODES,
        default="sweep-enabled",
        help="Sweep policy (default: sweep-enabled, which passes --trim-conc).",
    )
    value.add_argument(
        "--labels-json",
        default="[]",
        metavar="JSON",
        help='JSON array of local labels used by the existing CI-priority policy (default: "[]").',
    )
    eval_selection = value.add_mutually_exclusive_group()
    eval_selection.add_argument(
        "--no-evals",
        action="store_true",
        help="Pass --no-evals to matrix generation; incompatible with --all-evals.",
    )
    eval_selection.add_argument(
        "--evals-only",
        action="store_true",
        help="Generate and run evaluation-only jobs instead of benchmark jobs.",
    )
    value.add_argument(
        "--all-evals",
        action="store_true",
        help="Request all evaluations from matrix generation; incompatible with --no-evals.",
    )
    value.add_argument(
        "--agentx-fast",
        action="store_true",
        help="Enable the existing fast mode for non-evaluation AgentX jobs.",
    )
    value.add_argument(
        "--archive-root",
        type=Path,
        required=True,
        metavar="PATH",
        help="Required root for dated atomic local archives and managed clones.",
    )
    value.add_argument(
        "--run-dir",
        type=Path,
        metavar="PATH",
        help="Use this exact final archive directory instead of a dated generated path.",
    )
    value.add_argument(
        "--max-parallel",
        type=int,
        default=1,
        metavar="N",
        help="Maximum concurrent jobs; only 1 is currently supported (default: 1).",
    )
    value.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate the matrix and publish a skipped-job archive without launching jobs; requires --project-dir.",
    )
    value.add_argument(
        "--reuse",
        choices=("off", "auto", "required"),
        default="off",
        help="Reuse policy for compatible local archives (default: off).",
    )
    value.add_argument(
        "--reuse-from",
        type=Path,
        metavar="ARCHIVE",
        help="Use this archive as the only reuse candidate; it must pass strict validation.",
    )
    value.add_argument(
        "--compare-results",
        action="store_true",
        help="Run the existing read-only result comparison; requires DATABASE_URL.",
    )
    return value


def options(args: argparse.Namespace) -> dict[str, Any]:
    """Return the compatibility-relevant portion of CLI input."""
    return {
        "config_keys": args.config_key, "conc": args.conc or [], "mode": args.mode,
        "all_evals": args.all_evals, "evals_only": args.evals_only,
        "no_evals": args.no_evals, "agentx_fast": args.agentx_fast,
    }


def generate(project_root: Path, python: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Use the established test-config generator instead of duplicating schemas."""
    if args.no_evals and (args.evals_only or args.all_evals):
        raise WorkflowError("--no-evals cannot be combined with --evals-only or --all-evals")
    command = [str(python), "-m", "infx.matrix.generate", "test-config", "--config-files"]
    config_files = sorted(project_root.glob("configs/*-master.yaml"))
    if not config_files:
        raise WorkflowError("no master configuration files found under configs/")
    command.extend(str(path.relative_to(project_root)) for path in config_files)
    command.extend(["--runner-config", "configs/runners.yaml", "--config-keys", *args.config_key])
    if args.conc:
        command.extend(["--conc", *(str(value) for value in args.conc)])
    if args.no_evals:
        command.append("--no-evals")
    if args.evals_only:
        command.append("--evals-only")
    if args.all_evals:
        command.append("--all-evals")
    if args.mode == "sweep-enabled":
        command.append("--trim-conc")
    completed = subprocess.run(command, cwd=project_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "matrix generation failed")
    try:
        matrix = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise WorkflowError(f"matrix generator emitted invalid JSON: {error}") from error
    if not matrix:
        raise WorkflowError("selected config keys generated an empty matrix")
    for entry in matrix:
        fingerprint = subprocess.run(
            [str(python), "-c", (
                "import json, sys; from infx.matrix.plan import recipe_fingerprint; "
                "print(recipe_fingerprint(json.load(sys.stdin)))"
            )],
            cwd=project_root, input=json.dumps(entry), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if fingerprint.returncode:
            raise WorkflowError(fingerprint.stderr.strip() or "recipe fingerprint calculation failed")
        entry["recipe-fingerprint"] = fingerprint.stdout.strip()
        if args.agentx_fast and entry.get("scenario-type") == "agentic-coding" and not entry.get("eval-only"):
            entry["agentx-fast"] = True
    try:
        labels = frozenset(json.loads(args.labels_json))
    except (json.JSONDecodeError, TypeError) as error:
        raise WorkflowError("--labels-json must be a JSON array of strings") from error
    if not all(isinstance(label, str) for label in labels):
        raise WorkflowError("--labels-json must be a JSON array of strings")
    priority_program = (
        "import json, sys; from infx.workflows.ci_priority import PriorityContext, "
        "annotate_jobs, load_policy; args=json.load(sys.stdin); "
        "print(json.dumps(annotate_jobs(args['matrix'], load_policy('configs/ci-priority.yaml'), "
        "PriorityContext(labels=frozenset(args['labels']), queue_namespace='local'))))"
    )
    priority = subprocess.run(
        [str(python), "-c", priority_program], cwd=project_root,
        input=json.dumps({"matrix": matrix, "labels": sorted(labels)}), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if priority.returncode:
        raise WorkflowError(priority.stderr.strip() or "priority annotation failed")
    return json.loads(priority.stdout)


def stage_changelog_metadata(artifact_root: Path, matrix: Any) -> None:
    """Mirror the CI changelog-metadata artifact when generator metadata exists."""
    if not isinstance(matrix, dict) or "changelog_metadata" not in matrix:
        return
    target = artifact_root / "changelog-metadata" / "changelog_metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(matrix["changelog_metadata"]), encoding="utf-8")


    """Classify a generated entry into the run-sweep job family."""
    prefix = "multi" if entry.get("prefill") is not None else "single"
    if entry.get("eval-only"):
        return f"{prefix}-{'agentic-' if entry.get('scenario-type') == 'agentic-coding' else ''}eval"
    return f"{prefix}-{'agentic' if entry.get('scenario-type') == 'agentic-coding' else 'fixed'}"


def execute_entry(entry: dict[str, Any], **kwargs: Any):
    """Dispatch a planned entry to its topology-specific local adapter."""
    return execute_multinode(entry, **kwargs) if entry.get("prefill") is not None else execute_single_node(entry, **kwargs)


def select_canary(matrix: list[dict[str, Any]], mode: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select the same lowest-concurrency fixed-sequence canary as run-sweep."""
    if mode not in {"full-sweep-enabled", "full-sweep-fail-fast"}:
        return None, matrix
    choices = [
        entry for entry in matrix
        if entry.get("prefill") is None and not entry.get("eval-only")
        and entry.get("scenario-type") != "agentic-coding"
        and (entry.get("isl"), entry.get("osl")) in {(1024, 1024), (8192, 1024)}
    ]
    if not choices:
        return None, matrix
    selected = min(choices, key=lambda entry: (entry.get("conc", 0), entry.get("exp-name", "")))
    remaining = list(matrix)
    remaining.remove(selected)
    return selected, remaining


def archive_paths(args: argparse.Namespace, sha: str) -> tuple[Path, Path]:
    """Return unique staging and final archive locations."""
    timestamp = datetime.now(timezone.utc)
    final = args.run_dir or (args.archive_root / timestamp.strftime("%F") /
                             f"local-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{sha[:12]}")
    return final.with_name(final.name + f".staging-{os.getpid()}"), final


def aggregate(python: Path, staging: Path, manifest: dict[str, Any]) -> None:
    """Run Python replacements for both reusable collector workflows."""
    artifacts = staging / "artifacts"
    aggregates = staging / "aggregates"
    aggregates.mkdir(exist_ok=True)
    collect_results(python=python, artifacts=artifacts, aggregates=aggregates)
    collect_evals(python=python, artifacts=artifacts, aggregates=aggregates)
    manifest["aggregates"] = {
        path.name: str(path.relative_to(staging))
        for path in aggregates.iterdir()
        if path.is_file()
    }


def run_sweep(args: argparse.Namespace) -> Path:
    """Execute or reuse a complete locally archived selected-config sweep."""
    if args.no_evals and (args.evals_only or args.all_evals):
        raise WorkflowError("--no-evals cannot be combined with --evals-only or --all-evals")
    if args.max_parallel != 1:
        raise WorkflowError("--max-parallel currently must be 1: existing launchers write shared project-root output paths")
    if args.dry_run and args.project_dir is None:
        raise WorkflowError(
            "--dry-run requires --project-dir with its ready .venv because matrix generation "
            "uses the selected project's installed Python environment"
        )
    if args.compare_results and not os.environ.get("DATABASE_URL"):
        raise WorkflowError("--compare-results requires DATABASE_URL")
    workspace = args.workspace_root or (args.archive_root / ".worktrees")
    project = resolve_project(
        project_dir=args.project_dir, workspace_root=workspace, repository=args.repository,
        ref=args.ref, allow_dirty=args.allow_dirty_project,
    )
    matrix = generate(project.root, project.python, args)
    run_options = options(args)
    staging, final = archive_paths(args, project.head_sha)
    if staging.exists() or final.exists():
        raise WorkflowError(f"archive path already exists: {final}")
    staging.mkdir(parents=True)
    manifest = make_manifest(project=project, run_id=final.name, matrix=matrix, options=run_options)
    atomic_json(staging / "plan.json", matrix)
    decision = decide_reuse(
        mode=args.reuse, explicit_source=args.reuse_from, archive_root=args.archive_root,
        project=project, matrix_sha256=manifest["matrix_sha256"],
        recipe_fingerprints=manifest["recipe_fingerprints"], options=run_options,
    )
    manifest["reuse"] = {"enabled": decision.enabled, "source": str(decision.source) if decision.source else None, "reason": decision.reason}
    try:
        if decision.enabled:
            assert decision.source is not None
            stage_reused_artifacts(decision.source, staging)
            source_manifest = json.loads((decision.source / "archive_manifest.json").read_text(encoding="utf-8"))
            manifest["jobs"] = source_manifest["jobs"]
        elif args.dry_run:
            manifest["jobs"] = [{"status": "skipped", "entry": entry, "reason": "dry run"} for entry in matrix]
            (staging / "artifacts").mkdir()
        else:
            artifact_root = staging / "artifacts"
            artifact_root.mkdir()
            stage_changelog_metadata(artifact_root, matrix)
            canary, remaining = select_canary(matrix, args.mode)
            failures: set[str] = set()
            if canary is not None:
                result = execute_entry(canary, project=project, jobs_root=staging / "jobs", artifact_root=artifact_root)
                result.record["family"] = "canary"
                manifest["jobs"].append(result.record)
                if result.status != "success":
                    failures.add("canary")
            for entry in remaining:
                item_family = family(entry)
                if "canary" in failures or (args.mode.startswith("full-sweep-fail-fast") and item_family in failures):
                    manifest["jobs"].append({"status": "skipped", "entry": entry, "family": item_family, "reason": "canary or family fail-fast"})
                    continue
                result = execute_entry(entry, project=project, jobs_root=staging / "jobs", artifact_root=artifact_root)
                result.record["family"] = item_family
                manifest["jobs"].append(result.record)
                if result.status != "success":
                    failures.add(item_family)
        aggregate(project.python, staging, manifest)
        if args.compare_results:
            comparison = subprocess.run(
                [str(project.python), "-m", "infx.results.compare_results", str(staging / "artifacts")],
                cwd=project.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            comparison_path = staging / "aggregates" / "comparison.txt"
            comparison_path.write_text(comparison.stdout + comparison.stderr, encoding="utf-8")
            if comparison.returncode:
                raise WorkflowError(comparison.stderr.strip() or "result comparison failed")
            manifest["comparison"] = str(comparison_path.relative_to(staging))
        manifest["artifacts"] = artifact_inventory(staging / "artifacts")
        manifest["success_rate"] = calculate(manifest["jobs"])
        stats_path = staging / "aggregates" / "run_stats.json"
        atomic_json(stats_path, manifest["success_rate"])
        manifest.setdefault("aggregates", {})[stats_path.name] = str(stats_path.relative_to(staging))
        reusable_stats = staging / "artifacts" / "run-stats"
        reusable_stats.mkdir(exist_ok=True)
        shutil.copy2(stats_path, reusable_stats / stats_path.name)
        manifest["artifacts"] = artifact_inventory(staging / "artifacts")
        if args.dry_run:
            manifest["status"] = "planned"
        else:
            manifest["status"] = "success" if all(job["status"] == "success" for job in manifest["jobs"]) else "failure"
    except Exception as error:
        manifest["status"] = "failure"
        manifest["error"] = str(error)
        raise
    finally:
        manifest["completed_at"] = utc_now()
        atomic_json(staging / "archive_manifest.json", manifest)
    if manifest["status"] not in {"success", "planned"}:
        raise WorkflowError(f"sweep failed; staging archive retained at {staging}")
    publish_directory(staging, final)
    return final


def main() -> int:
    """CLI entry point."""
    args = parser().parse_args()
    try:
        print(run_sweep(args))
    except WorkflowError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
