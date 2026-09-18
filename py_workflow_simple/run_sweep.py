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

from py_workflow.benchmark import stage_job_output
from py_workflow.collect_evals import collect as collect_evals
from py_workflow.collect_results import collect as collect_results
from py_workflow.manifest import (
    WorkflowError, artifact_inventory, atomic_json, make_manifest,
    publish_directory, resolve_project,
)
from py_workflow.run_sweep import generate, options
from py_workflow.success_rate import calculate

from .agentx import execute


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Run AgentX/AIPerf against an already-running model; never start a model or scheduler job."
    )
    value.add_argument("--project-dir", type=Path, required=True)
    value.add_argument("--config-key", action="append", required=True)
    value.add_argument("--archive-root", type=Path, required=True)
    value.add_argument("--run-dir", type=Path)
    value.add_argument("--server-url", required=True, help="Existing model OpenAI-compatible endpoint")
    value.add_argument("--served-model-name")
    value.add_argument("--tokenizer")
    value.add_argument("--aiperf-cli", default="aiperf")
    value.add_argument("--metrics-url")
    value.add_argument("--dataset-loader", default="semianalysis_cc_traces_weka_062126_256k")
    value.add_argument("--duration", type=int, default=3600)
    value.add_argument("--warmup-requests-per-lane", type=int, default=10)
    value.add_argument("--failed-request-threshold", type=float, default=0.10)
    value.add_argument("--random-seed", type=int)
    value.add_argument("--recipe-fingerprint")
    value.add_argument("--labels-json", default="[]")
    value.add_argument("--conc", nargs="+", type=int)
    value.add_argument("--mode", default="sweep-enabled")
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
    value.add_argument("--github-ref")
    value.add_argument("--github-repository")
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
    sha = args.github_sha or project_sha
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


def github_event(args: argparse.Namespace, project_sha: str, identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "workflow_dispatch",
        "after": identity["sha"],
        "before": None,
        "number": None,
        "pull_request": None,
        "repository": {"full_name": identity["repository"]} if identity["repository"] else None,
        "ref": identity["ref"],
        "sender": None,
        "workflow_run": {
            "id": identity["run_id"],
            "run_attempt": identity["run_attempt"],
            "event": identity["event_name"],
        },
        "synthetic": identity["synthetic"],
    }


def run(args: argparse.Namespace) -> Path:
    if args.dry_run:
        args.server_url = args.server_url or "http://127.0.0.1:30001"
    elif not args.server_url:
        raise WorkflowError("--server-url is required for AgentX execution")
    if args.evals_only or args.all_evals:
        raise WorkflowError("py_workflow_simple supports AgentX benchmark entries, not evaluation-only runs")

    project = resolve_project(
        project_dir=args.project_dir, workspace_root=args.archive_root / ".worktrees",
        repository=None, ref="HEAD", allow_dirty=args.allow_dirty_project,
    )
    matrix = generate(project.root, project.python, args)
    identity = generated_run_context(args, project.head_sha)
    staging, final = archive_paths(args, project.head_sha, identity["execution_id"])
    if staging.exists() or final.exists():
        raise WorkflowError(f"archive path already exists: {final}")
    staging.mkdir(parents=True)
    manifest = make_manifest(project=project, run_id=final.name, matrix=matrix, options=options(args))
    github = {
        "run_id": identity["run_id"], "run_attempt": identity["run_attempt"],
        "event_name": identity["event_name"], "sha": identity["sha"],
        "ref": identity["ref"], "repository": identity["repository"],
        "execution_id": identity["execution_id"], "synthetic": identity["synthetic"],
        "random_seed": identity["random_seed"], "recipe_fingerprint": identity["recipe_fingerprint"],
    }
    manifest["github"] = github
    atomic_json(staging / "plan.json", matrix)
    artifact_root = staging / "artifacts"
    artifact_root.mkdir()
    manifest["jobs"] = []

    if args.dry_run:
        manifest["jobs"] = [{"status": "skipped", "entry": entry, "reason": "dry run"} for entry in matrix]
        manifest["status"] = "planned"
    else:
        for entry in matrix:
            if entry.get("scenario-type") != "agentic-coding" or entry.get("eval-only"):
                raise WorkflowError("all selected entries must be non-evaluation agentic-coding jobs")
            record = execute(
                entry, project=project, jobs_root=staging / "jobs", artifact_root=artifact_root,
                server_url=args.server_url, aiperf_cli=args.aiperf_cli, tokenizer=args.tokenizer,
                duration=args.duration, dataset_loader=args.dataset_loader,
                served_model_name=args.served_model_name, metrics_url=args.metrics_url or f"{args.server_url.rstrip('/')}/metrics",
                warmup_requests_per_lane=args.warmup_requests_per_lane,
                failed_request_threshold=args.failed_request_threshold, fast=args.agentx_fast,
                github=github,
            )
            job_dir = staging / "jobs" / record["result_filename"]
            if record["status"] == "success":
                power = subprocess.run(
                    [str(project.python), "-m", "infx.results.agentic.power_adapter",
                     "--result-dir", str(job_dir / "results"), "--agg-result", str(job_dir / f"{record['result_filename']}.json"),
                     "--expected-num-gpus", str(entry.get("tp", 1))],
                    cwd=project.root, text=True, capture_output=True,
                )
                if power.returncode:
                    record["power_error"] = power.stderr.strip() or power.stdout.strip()
            # Match the CI server-log upload shape without claiming these are
            # logs from a service started by this workflow.
            (job_dir / "results" / "server.log").write_text(
                "AgentX-only workflow: model endpoint was externally managed.\n", encoding="utf-8"
            )
            for name in ("mooncake_master.log", "mooncake_client.log"):
                (job_dir / "results" / name).write_text(
                    "AgentX-only workflow: no Mooncake process was started.\n", encoding="utf-8"
                )
            record["runtime_context"] = json.loads(
                (job_dir / "runtime_context.json").read_text(encoding="utf-8")
            )
            record["meta_env"] = "meta_env.json"
            record["artifacts"] = stage_job_output(job_dir, artifact_root, entry, record["result_filename"])
            manifest["jobs"].append(record)
        manifest["status"] = "success" if all(item["status"] == "success" for item in manifest["jobs"]) else "failure"

    aggregates = staging / "aggregates"
    aggregates.mkdir()
    collect_results(python=project.python, artifacts=artifact_root, aggregates=aggregates)
    collect_evals(python=project.python, artifacts=artifact_root, aggregates=aggregates)
    manifest["artifacts"] = artifact_inventory(artifact_root)
    manifest["success_rate"] = calculate(manifest["jobs"])
    stats_path = aggregates / "run_stats.json"
    atomic_json(stats_path, manifest["success_rate"])
    (artifact_root / "run-stats").mkdir(exist_ok=True)
    shutil.copy2(stats_path, artifact_root / "run-stats" / stats_path.name)
    manifest["artifacts"] = artifact_inventory(artifact_root)
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(staging / "archive_manifest.json", manifest)
    atomic_json(staging / "github_event.json", github_event(args, project.head_sha, identity))
    if manifest["status"] not in {"success", "planned"}:
        raise WorkflowError(f"AgentX sweep failed; staging archive retained at {staging}")
    publish_directory(staging, final)
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
