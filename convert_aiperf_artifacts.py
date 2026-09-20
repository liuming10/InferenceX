#!/usr/bin/env python3
"""Convert standalone AIPerf artifacts into a CI-shaped AgentX archive.

The converter reads only the supplied ``aiperf_artifacts`` directory. Files
that CI normally captures from shell, SGLang, Mooncake, or GitHub Actions are
created as explicitly marked synthetic placeholders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SOURCE = Path("/stortest/lium_space/agentX/results_01/aiperf_artifacts")
DEFAULT_ARCHIVE_ROOT = Path("/stortest/lium_space/agentX/InferenceX_result/2026-09-18")
REFERENCE_RUN = DEFAULT_ARCHIVE_ROOT / "run-35311504416-attempt-1"
REQUIRED_SOURCE_FILES = (
    "profile_export_aiperf.json",
    "profile_export.jsonl",
    "server_metrics_export.json",
)
SYNTHETIC_PREFIX = (
    "SYNTHETIC ARTIFACT: this file was created from standalone AIPerf output.\n"
    "The original runtime stream was not available in aiperf_artifacts.\n"
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="AIPerf artifact directory")
    value.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    value.add_argument("--output", type=Path, help="New archive directory; must not already contain files")
    value.add_argument("--stem", default="standalone-aiperf", help="Artifact filename stem")
    value.add_argument("--timezone-offset", default="+0800", help="UTC offset for naive AIPerf profile times")
    value.add_argument("--expected-gpus", type=int, default=8, help="Expected accelerator count for validation")
    value.add_argument("--model", default="", help="Optional model path for the AgentX result JSON")
    value.add_argument("--framework", default="sglang")
    value.add_argument("--precision", default="unknown")
    value.add_argument("--tp", type=int, default=8)
    value.add_argument("--dry-run", action="store_true")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_inventory(source: Path) -> list[dict[str, Any]]:
    return [
        {"path": path.relative_to(source).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(source.rglob("*")) if path.is_file()
    ]


def validate_source(source: Path) -> None:
    if not source.is_dir():
        raise ValueError(f"AIPerf artifact directory does not exist: {source}")
    missing = [name for name in REQUIRED_SOURCE_FILES if not (source / name).is_file()]
    if missing:
        raise ValueError(f"AIPerf artifact directory is missing required files: {', '.join(missing)}")


def default_output(root: Path, stem: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / f"local-aiperf-conversion-{timestamp}-{stem}"


def ensure_safe_output(output: Path) -> None:
    if output.resolve() == REFERENCE_RUN.resolve():
        raise ValueError(f"refusing to overwrite CI reference archive: {REFERENCE_RUN}")
    if output.exists():
        raise ValueError(f"output directory already exists: {output}")


def synthetic_text(kind: str) -> str:
    return f"{SYNTHETIC_PREFIX}Unavailable CI source: {kind}.\n"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_tree(source: Path, destination: Path) -> None:
    for path in sorted(source.rglob("*")):
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def run_project_module(project: Path, module: str, args: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", module, *args], cwd=project, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"{module} failed: {detail}")


def make_result(
    project: Path, source: Path, result_dir: Path, stem: str, args: argparse.Namespace,
) -> Path:
    copy_tree(source, result_dir / "aiperf_artifacts")
    env = dict(os.environ)
    env.update({
        "RESULT_FILENAME": stem,
        "RESULT_DIR": str(result_dir),
        "AGENTIC_OUTPUT_DIR": str(result_dir),
        "MODEL": args.model,
        "FRAMEWORK": args.framework,
        "PRECISION": args.precision,
        "TP": str(args.tp),
        "PP_SIZE": "1",
        "DCP_SIZE": "1",
        "PCP_SIZE": "1",
        "EP_SIZE": "1",
        "DP_ATTENTION": "false",
        "CONC": "",
        "SCENARIO_TYPE": "agentic-coding",
        "IS_MULTINODE": "false",
        "KV_OFFLOADING": "none",
    })
    run_project_module(project, "infx.results.agentic.process_agentic_result", [], env)
    result = result_dir / f"{stem}.json"
    if not result.is_file():
        raise RuntimeError("AgentX result processor did not produce a result JSON")
    return result


def add_synthetic_agentic_files(agentic: Path, source: Path) -> list[Path]:
    created: list[Path] = []
    aiperf_log = source / "logs" / "aiperf.log"
    benchmark_log = agentic / "benchmark.log"
    if aiperf_log.is_file():
        shutil.copy2(aiperf_log, benchmark_log)
    else:
        benchmark_log.write_text(synthetic_text("aiperf_artifacts/logs/aiperf.log"), encoding="utf-8")
    created.append(benchmark_log)
    for name, origin in (
        ("server.log", "SGLang stdout/stderr"),
        ("mooncake_master.log", "Mooncake master stdout/stderr"),
        ("mooncake_client.log", "Mooncake client stdout/stderr"),
        ("benchmark_command.txt", "benchmark shell command"),
        ("sglang_command.txt", "SGLang launch command"),
        ("sglang_metrics_synthetic.prom", "SGLang /metrics response"),
    ):
        path = agentic / name
        path.write_text(synthetic_text(origin), encoding="utf-8")
        created.append(path)
    return created


def convert(args: argparse.Namespace) -> Path:
    project = Path(__file__).resolve().parent
    source = args.source.resolve()
    validate_source(source)
    output = (args.output or default_output(args.archive_root, args.stem)).resolve()
    ensure_safe_output(output)

    if args.dry_run:
        print(json.dumps({"source": str(source), "output": str(output), "stem": args.stem}, indent=2))
        return output

    artifacts = output / "artifacts"
    agentic = artifacts / f"agentic_{args.stem}"
    bmk = artifacts / f"bmk_agentic_{args.stem}"
    power = artifacts / f"power_audit_{args.stem}"
    server_logs = artifacts / f"server_logs_{args.stem}"
    changelog = artifacts / "changelog-metadata"
    for directory in (
        agentic / "aiperf_artifacts" / "logs", bmk, power / "results",
        server_logs / "results", changelog,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="infx-aiperf-convert-") as temp:
        temp_result = Path(temp) / "results"
        result = make_result(project, source, temp_result, args.stem, args)
        copy_tree(source, agentic / "aiperf_artifacts")

        run_project_module(project, "infx.results.generate_aiperf_plots", [str(agentic)], dict(os.environ))
        run_project_module(
            project, "infx.results.agentic.analyze_benchmark_distributions",
            [str(agentic / "aiperf_artifacts"), "-o", str(agentic)], dict(os.environ),
        )

        timezone_file = agentic / "agentic_power_timezone_offset.txt"
        timezone_file.write_text(args.timezone_offset + "\n", encoding="utf-8")
        power_env = dict(os.environ)
        run_project_module(
            project, "infx.results.agentic.power_adapter",
            ["--result-dir", str(agentic), "--agg-result", str(result),
             "--expected-num-gpus", str(args.expected_gpus)], power_env,
        )
        shutil.copy2(result, bmk / result.name)
        shutil.copy2(result, power / result.name)

    for name in ("agentic_power_window.json", "agentic_power_timezone_offset.txt", "power_validation.json"):
        path = agentic / name
        if path.is_file():
            shutil.copy2(path, power / "results" / name)

    synthetic_files = add_synthetic_agentic_files(agentic, source)
    for source_log in (agentic / "benchmark.log", agentic / "server.log", agentic / "mooncake_master.log", agentic / "mooncake_client.log"):
        shutil.copy2(source_log, server_logs / "results" / source_log.name)

    write_json(changelog / "changelog_metadata.json", {
        "base_ref": None,
        "head_ref": None,
        "entries": [],
        "synthetic": True,
        "provenance": "No Git changelog or GitHub Actions sweep matrix is available in standalone AIPerf artifacts.",
    })
    inventory = source_inventory(source)
    created_at = datetime.now(timezone.utc).isoformat()
    write_json(output / "archive_manifest.json", {
        "archive_created_at": created_at,
        "current_run_attempt": None,
        "current_run_id": None,
        "event_name": "synthetic_aiperf_conversion",
        "job_results": {
            "setup": {"result": "synthetic", "outputs": {}},
            "sweep-agentic": {"result": "synthetic", "outputs": {}},
            "collect-results": {"result": "synthetic", "outputs": {}},
            "upload-changelog-metadata": {"result": "synthetic", "outputs": {}},
        },
        "ref": None,
        "repository": None,
        "sha": None,
        "source_reused": False,
        "source_run_id": None,
        "synthetic": True,
        "provenance": "Created by convert_aiperf_artifacts.py from standalone AIPerf output; no GitHub Actions run occurred.",
        "source": str(source),
        "source_inventory": inventory,
        "artifact_stem": args.stem,
        "timezone_offset": args.timezone_offset,
        "synthetic_files": [path.relative_to(output).as_posix() for path in synthetic_files],
        "unavailable_ci_categories": ["gpu_metrics_<stem>"],
    })
    write_json(output / "github_event.json", {
        "action": "synthetic_aiperf_conversion",
        "after": None,
        "before": None,
        "number": None,
        "pull_request": None,
        "repository": None,
        "sender": None,
        "synthetic": True,
        "provenance": "No GitHub webhook event is available in standalone AIPerf artifacts.",
        "created_at": created_at,
    })
    return output


def main() -> int:
    args = parser().parse_args()
    try:
        output = convert(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if not args.dry_run:
        print(f"Created CI-shaped archive: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
