"""Run AgentX against an already-running model endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from py_workflow.benchmark import result_filename, stage_job_output
from py_workflow.collect_evals import collect as collect_evals
from py_workflow.collect_results import collect as collect_results
from py_workflow.manifest import (
    WorkflowError, artifact_inventory, atomic_json, make_manifest,
    publish_directory, resolve_project,
)
from py_workflow.run_sweep import generate, options
from py_workflow.success_rate import calculate

from .agentx import execute


def progress(message: str) -> None:
    """Print one concise, timestamped workflow lifecycle message."""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{timestamp}] [simple-workflow] {message}", flush=True)


def positive_seconds(value: str) -> int:
    """Parse a positive timeout in seconds."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1 second")
    return parsed


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run AgentX/AIPerf against an already-running model; never start a model or scheduler job."
    )
    value.add_argument("--project-dir", type=Path, required=True)
    value.add_argument("--config-key", action="append", required=True)
    value.add_argument("--archive-root", type=Path, required=True)
    value.add_argument(
        "--hf-cache-dir", type=Path,
        help="Hugging Face cache root; defaults to /ai_data/datasets/huggingface",
    )
    value.add_argument("--run-dir", type=Path)
    value.add_argument("--server-url", required=True, help="Existing model OpenAI-compatible endpoint")
    value.add_argument("--served-model-name")
    value.add_argument("--tokenizer")
    value.add_argument("--aiperf-cli", help="AIPerf executable; defaults to the project virtual environment")
    value.add_argument("--metrics-url")
    value.add_argument("--dataset-loader", default="semianalysis_cc_traces_weka_062126_256k")
    value.add_argument(
        "--dataset-configuration-timeout", type=positive_seconds, default=900,
        help="Seconds allowed for AIPerf cached-dataset configuration (default: 900)",
    )
    value.add_argument("--duration", type=int, default=3600)
    value.add_argument("--warmup-requests-per-lane", type=int, default=10)
    value.add_argument("--failed-request-threshold", type=float, default=0.10)
    value.add_argument("--random-seed", type=int)
    value.add_argument("--recipe-fingerprint")
    value.add_argument("--labels-json", default="[]")
    value.add_argument("--conc", nargs="+", type=int)
    value.add_argument(
        "--mode", default="full-sweep-enabled",
        help=(
            "Matrix sweep policy (default: full-sweep-enabled, which runs every "
            "declared conc-list point serially; sweep-enabled retains legacy --trim-conc behavior)."
        ),
    )
    value.add_argument("--no-evals", action="store_true")
    value.add_argument("--evals-only", action="store_true")
    value.add_argument("--all-evals", action="store_true")
    value.add_argument("--agentx-fast", action="store_true")
    value.add_argument("--dry-run", action="store_true")
    value.add_argument("--allow-dirty-project", action="store_true")
    value.add_argument("--github-run-id", type=int)
    value.add_argument("--github-run-attempt", type=int, default=1)
    value.add_argument("--github-event-name", default="workflow_dispatch")
    value.add_argument("--github-sha")
    value.add_argument("--github-ref", default="refs/heads/main")
    value.add_argument("--github-repository", default="liuming10/InferenceX")
    value.add_argument(
        "--github-event-path", type=Path,
        help="Original GitHub webhook payload to preserve as github_event.json",
    )
    return value


def archive_paths(args: argparse.Namespace, sha: str, execution_id: str) -> tuple[Path, Path]:
    final = args.run_dir
    if final is None:
        timestamp = datetime.now(timezone.utc)
        final = args.archive_root / timestamp.strftime("%F") / f"simple-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}-{execution_id}-{sha[:12]}"
    return final.with_name(final.name + f".staging-{os.getpid()}"), final


def generated_run_context(args: argparse.Namespace, project_sha: str) -> dict[str, Any]:
    """Resolve explicit identity first and generate unique values for omissions."""
    execution_id = uuid.uuid4().hex
    random_seed = args.random_seed if args.random_seed is not None else 42
    run_id = args.github_run_id if args.github_run_id is not None else int(secrets.randbelow(9_000_000_000) + 1_000_000_000)
    sha = args.github_sha or secrets.token_hex(20)
    recipe = args.recipe_fingerprint or hashlib.sha256(
        f"{sha}:{execution_id}:{random_seed}".encode()
    ).hexdigest()
    return {
        "execution_id": execution_id, "run_id": run_id,
        "run_attempt": args.github_run_attempt, "event_name": args.github_event_name,
        "sha": sha, "ref": args.github_ref, "repository": args.github_repository,
        "random_seed": random_seed, "recipe_fingerprint": recipe,
        "synthetic": args.github_run_id is None,
    }


def stage_changelog_metadata(artifact_root: Path, metadata: Any = None) -> Path:
    """Write the same changelog artifact payload as upload-changelog-metadata."""
    target = artifact_root / "changelog-metadata" / "changelog_metadata.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(target, metadata if metadata is not None else {})
    return target


def copy_github_event(source: Path | None, staging: Path) -> None:
    """Preserve a genuine GitHub webhook payload exactly as CI archive does."""
    if source is None:
        return
    if not source.is_file() or not os.access(source, os.R_OK):
        raise WorkflowError(f"GitHub event payload is not a readable file: {source}")
    shutil.copyfile(source, staging / "github_event.json")


DEFAULT_HF_CACHE_DIR = Path("/ai_data/datasets/huggingface")


def resolve_hf_cache_dir(args: argparse.Namespace) -> Path:
    """Return the launcher-compatible Hugging Face cache root for public datasets."""
    return args.hf_cache_dir.resolve() if args.hf_cache_dir else DEFAULT_HF_CACHE_DIR


def validate_hf_cache_dir(cache_dir: Path) -> None:
    """Require the shared hub and writable datasets cache expected by AgentX."""
    hub = cache_dir / "hub"
    datasets = cache_dir / "datasets"
    if not hub.is_dir() or not os.access(hub, os.R_OK | os.X_OK):
        raise WorkflowError(f"Hugging Face hub cache is not readable: {hub}")
    if not datasets.is_dir() or not os.access(datasets, os.R_OK | os.W_OK | os.X_OK):
        raise WorkflowError(f"Hugging Face datasets cache is not readable/writable: {datasets}")


REFERENCE_JOB_NAMES = (
    "calc-success-rate",
    "canary-sweep",
    "collect-evals",
    "collect-results",
    "setup",
    "sweep-agentic",
    "sweep-agentic-evals",
    "sweep-evals",
    "sweep-multi-node-1k1k",
    "sweep-multi-node-8k1k",
    "sweep-multi-node-agentic",
    "sweep-multi-node-agentic-evals",
    "sweep-multi-node-evals",
    "sweep-single-node-1k1k",
    "sweep-single-node-8k1k",
    "upload-changelog-metadata",
)


def validate_unique_result_stems(project: Project, matrix: list[dict[str, Any]]) -> None:
    """Reject matrix rows that would write to the same job and artifact paths."""
    by_stem: dict[str, list[dict[str, Any]]] = {}
    for entry in matrix:
        by_stem.setdefault(result_filename(project, entry), []).append(entry)
    duplicates = {
        stem: entries for stem, entries in by_stem.items()
        if len(entries) > 1
    }
    if duplicates:
        details = "; ".join(
            f"{stem} (concurrency={[entry.get('conc') for entry in entries]})"
            for stem, entries in sorted(duplicates.items())
        )
        raise WorkflowError(f"matrix creates colliding AgentX result artifacts: {details}")


def _reference_job(result: str, outputs: dict[str, str] | None = None) -> dict[str, Any]:
    """Return one job result using the reference archive's exact value shape."""
    return {"outputs": outputs or {}, "result": result}


def write_reference_placeholder_files(result_dir: Path) -> None:
    """Create empty files expected by the reference artifact layout."""
    for name in ("server.log", "mooncake_master.log", "mooncake_client.log", "sglang_command.txt"):
        (result_dir / name).write_bytes(b"")


def reference_archive_manifest(
    *, identity: dict[str, Any], project: Project, matrix: list[dict[str, Any]],
    jobs: list[dict[str, Any]], status: str, dry_run: bool,
    results_collected: bool, evals_collected: bool, success_rate_calculated: bool,
    changelog_metadata_uploaded: bool,
) -> dict[str, Any]:
    """Build the reference GitHub workflow archive manifest shape."""
    agentic_result = "skipped" if dry_run else (
        "success" if status == "success" else "failure"
    )
    collection_result = "success" if results_collected else "failure"
    eval_result = "success" if evals_collected else "skipped"
    jobs_result = {
        name: _reference_job("skipped") for name in REFERENCE_JOB_NAMES
    }
    jobs_result.update({
        "setup": _reference_job("success", {
            "reuse-enabled": "false",
            "search-space-config": json.dumps(
                {"single_node": {"agentic": matrix}, "multi_node": {},
                 "evals": [], "agentic_evals": [], "multinode_evals": [],
                 "multinode_agentic_evals": [], "changelog_metadata": {}},
                sort_keys=True,
            ),
        }),
        "sweep-agentic": _reference_job(agentic_result),
        "collect-results": _reference_job(collection_result),
        "collect-evals": _reference_job(eval_result),
        "upload-changelog-metadata": _reference_job(
            "success" if changelog_metadata_uploaded else "failure"
        ),
        "calc-success-rate": _reference_job(
            "success" if success_rate_calculated else "failure"
        ),
    })
    return {
        "archive_created_at": datetime.now(timezone.utc).isoformat(),
        "current_run_attempt": identity["run_attempt"],
        "current_run_id": identity["run_id"],
        "event_name": identity["event_name"],
        "job_results": jobs_result,
        "ref": identity["ref"],
        "repository": identity["repository"],
        "sha": identity["sha"],
        "source_reused": False,
        "source_run_id": identity["run_id"],
    }


def run(args: argparse.Namespace) -> Path:
    if args.dry_run:
        args.server_url = args.server_url or "http://127.0.0.1:30001"
    elif not args.server_url:
        raise WorkflowError("--server-url is required for AgentX execution")
    if args.evals_only or args.all_evals:
        raise WorkflowError("py_workflow_simple supports AgentX benchmark entries, not evaluation-only runs")
    if args.github_event_path is not None and (
        not args.github_event_path.is_file() or not os.access(args.github_event_path, os.R_OK)
    ):
        raise WorkflowError(f"GitHub event payload is not a readable file: {args.github_event_path}")

    progress(f"validating project: {args.project_dir}")
    project = resolve_project(
        project_dir=args.project_dir, workspace_root=args.archive_root / ".worktrees",
        repository=None, ref="HEAD", allow_dirty=args.allow_dirty_project,
        validate_submodules=False,
    )
    matrix = generate(project.root, project.python, args)
    validate_unique_result_stems(project, matrix)
    progress(
        f"project HEAD={project.head_sha[:12]} dirty={project.dirty}; "
        f"generated {len(matrix)} AgentX job(s)"
    )
    aiperf_cli = args.aiperf_cli or str(project.python.with_name("aiperf"))
    hf_cache_dir = resolve_hf_cache_dir(args)
    if not args.dry_run:
        validate_hf_cache_dir(hf_cache_dir)
    if not Path(aiperf_cli).is_file() and args.aiperf_cli is None:
        raise WorkflowError(f"AIPerf executable is missing from the project virtual environment: {aiperf_cli}")
    identity = generated_run_context(args, project.head_sha)
    progress(f"AIPerf executable: {aiperf_cli}")
    progress(
        f"Hugging Face cache: root={hf_cache_dir} hub={hf_cache_dir / 'hub'} "
        f"datasets={hf_cache_dir / 'datasets'}"
    )
    progress(
        f"execution_id={identity['execution_id']} github_run_id={identity['run_id']} "
        f"attempt={identity['run_attempt']} random_seed={identity['random_seed']}"
    )
    staging, final = archive_paths(args, project.head_sha, identity["execution_id"])
    if staging.exists() or final.exists():
        raise WorkflowError(f"archive path already exists: {final}")
    staging.mkdir(parents=True)
    progress(f"staging archive: {staging}")
    manifest_options = options(args)
    manifest_options["hf_cache_dir"] = str(hf_cache_dir)
    manifest_options["dataset_configuration_timeout"] = args.dataset_configuration_timeout
    manifest = make_manifest(project=project, run_id=final.name, matrix=matrix, options=manifest_options)
    github = {
        "run_id": identity["run_id"], "run_attempt": identity["run_attempt"],
        "event_name": identity["event_name"], "sha": identity["sha"],
        "ref": identity["ref"], "repository": identity["repository"],
        "execution_id": identity["execution_id"], "synthetic": identity["synthetic"],
        "random_seed": identity["random_seed"], "recipe_fingerprint": identity["recipe_fingerprint"],
        "hf_cache_dir": str(hf_cache_dir),
        "dataset_configuration_timeout": args.dataset_configuration_timeout,
    }
    manifest["github"] = github
    manifest["source"]["submodule_validation"] = {
        "mode": "runtime-imports-only",
        "git_metadata_required": False,
        "verified_imports": ["infx", "aiperf"],
    }
    atomic_json(staging / "plan.json", matrix)
    artifact_root = staging / "artifacts"
    artifact_root.mkdir()
    manifest["jobs"] = []

    if args.dry_run:
        progress("dry-run: writing plan without invoking AIPerf")
        manifest["jobs"] = [{"status": "skipped", "entry": entry, "reason": "dry run"} for entry in matrix]
        manifest["status"] = "planned"
    else:
        for index, entry in enumerate(matrix, start=1):
            if entry.get("scenario-type") != "agentic-coding" or entry.get("eval-only"):
                raise WorkflowError("all selected entries must be non-evaluation agentic-coding jobs")
            progress(
                f"job {index}/{len(matrix)}: model={entry.get('model')} "
                f"concurrency={entry.get('conc')} runner={entry.get('runner')}"
            )
            record = execute(
                entry, project=project, jobs_root=staging / "jobs", artifact_root=artifact_root,
                server_url=args.server_url, aiperf_cli=aiperf_cli, tokenizer=args.tokenizer,
                duration=args.duration, dataset_loader=args.dataset_loader,
                served_model_name=args.served_model_name, metrics_url=args.metrics_url or f"{args.server_url.rstrip('/')}/metrics",
                warmup_requests_per_lane=args.warmup_requests_per_lane,
                failed_request_threshold=args.failed_request_threshold, fast=args.agentx_fast,
                github=github, hf_cache_dir=hf_cache_dir,
                dataset_configuration_timeout=args.dataset_configuration_timeout,
            )
            job_dir = staging / "jobs" / record["result_filename"]
            progress(f"job {index}/{len(matrix)} AIPerf status={record['status']}; log={job_dir / 'benchmark.log'}")
            if record["status"] == "success":
                progress(f"job {index}/{len(matrix)}: validating power window")
                power_command = [
                    str(project.python), "-m", "infx.results.agentic.power_adapter",
                    "--result-dir", str(job_dir / "results"),
                    "--agg-result", str(job_dir / f"{record['result_filename']}.json"),
                    "--expected-num-gpus", str(
                        int(entry.get("tp", 1))
                        * int(entry.get("pp", 1))
                        * int(entry.get("pcp-size", 1))
                    ),
                ]
                if entry.get("require-power"):
                    power_command.append("--require-power")
                power = subprocess.run(
                    power_command,
                    cwd=project.root, text=True, capture_output=True,
                )
                if power.returncode:
                    record["power_error"] = power.stderr.strip() or power.stdout.strip()
                    progress(f"job {index}/{len(matrix)} power validation warning: {record['power_error']}")
                    if entry.get("require-power"):
                        record["status"] = "failure"
                        record["error"] = f"AgentX power validation failed: {record['power_error']}"
                else:
                    progress(f"job {index}/{len(matrix)} power validation complete")
            write_reference_placeholder_files(job_dir / "results")
            record["runtime_context"] = json.loads(
                (job_dir / "runtime_context.json").read_text(encoding="utf-8")
            )
            record["meta_env"] = "meta_env.json"
            record["artifacts"] = stage_job_output(job_dir, artifact_root, entry, record["result_filename"])
            progress(f"job {index}/{len(matrix)} staged {len(record['artifacts'])} artifact file(s)")
            manifest["jobs"].append(record)
        manifest["status"] = "success" if all(item["status"] == "success" for item in manifest["jobs"]) else "failure"

    aggregates = staging / "aggregates"
    aggregates.mkdir()
    progress("collecting result and evaluation summaries")
    results_summary = collect_results(
        python=project.python, artifacts=artifact_root, aggregates=aggregates,
        project_root=project.root,
    )
    evals_summary = collect_evals(
        python=project.python, artifacts=artifact_root, aggregates=aggregates,
        project_root=project.root,
    )
    changelog_metadata = next(
        (entry["changelog_metadata"] for entry in matrix if "changelog_metadata" in entry),
        {},
    )
    changelog_path = stage_changelog_metadata(artifact_root, changelog_metadata)
    manifest["artifacts"] = artifact_inventory(artifact_root)
    manifest["success_rate"] = calculate(manifest["jobs"])
    stats_path = aggregates / "run_stats.json"
    atomic_json(stats_path, manifest["success_rate"])
    (artifact_root / "run-stats").mkdir(exist_ok=True)
    shutil.copy2(stats_path, artifact_root / "run-stats" / stats_path.name)
    manifest["artifacts"] = artifact_inventory(artifact_root)
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    compatibility_manifest = reference_archive_manifest(
        identity=identity, project=project, matrix=matrix,
        jobs=manifest["jobs"], status=manifest["status"], dry_run=args.dry_run,
        results_collected=results_summary is not None,
        evals_collected=evals_summary is not None,
        success_rate_calculated=True,
        changelog_metadata_uploaded=changelog_path.is_file(),
    )
    atomic_json(staging / "archive_manifest.json", compatibility_manifest)
    copy_github_event(args.github_event_path, staging)
    if manifest["status"] not in {"success", "planned"}:
        progress(f"workflow failed; retained staging archive: {staging}")
        raise WorkflowError(f"AgentX sweep failed; staging archive retained at {staging}")
    publish_directory(staging, final)
    progress(f"workflow {manifest['status']}; published archive: {final}")
    return final


def main() -> int:
    args = parser().parse_args()
    try:
        print(run(args))
    except (WorkflowError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
