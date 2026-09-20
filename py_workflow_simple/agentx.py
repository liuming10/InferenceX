"""Execute AgentX/AIPerf against an already-running inference endpoint."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from py_workflow.benchmark import result_filename
from py_workflow.manifest import Project, WorkflowError


def _value(entry: dict[str, Any], key: str, default: Any = "") -> Any:
    return entry.get(key, default)


def _progress(message: str) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    print(f"[{timestamp}] [agentx] {message}", flush=True)


def _redacted_command(command: list[str]) -> str:
    redacted: list[str] = []
    redact_next = False
    for value in command:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
        elif value.lower() in {"--api-key", "--token", "--password"}:
            redacted.append(value)
            redact_next = True
        else:
            redacted.append(value)
    return shlex.join(redacted)


def write_agentx_power_timezone_offset(result_dir: Path) -> Path:
    """Record the local offset used for AIPerf's naive profile timestamps."""
    path = result_dir / "agentic_power_timezone_offset.txt"
    path.write_text(datetime.now().astimezone().strftime("%z") + "\n", encoding="utf-8")
    return path


def capture_endpoint_metrics(
    *, metrics_url: str | None, result_dir: Path, framework: str, phase: str,
) -> dict[str, Any]:
    """Save an externally managed endpoint's Prometheus response unchanged."""
    captured_at = datetime.now(timezone.utc)
    record: dict[str, Any] = {
        "phase": phase,
        "source_url": metrics_url,
        "captured_at": captured_at.isoformat(),
        "status": "skipped" if not metrics_url else "failure",
    }
    if not metrics_url:
        return record

    safe_framework = re.sub(r"[^a-z0-9_.-]+", "-", framework.lower()).strip(".-") or "server"
    timestamp = captured_at.strftime("%Y%m%dT%H%M%S%fZ")
    destination = result_dir / f"{safe_framework}_metrics_{timestamp}.prom"
    temporary: Path | None = None
    try:
        with urllib.request.urlopen(metrics_url, timeout=15) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=result_dir, prefix=f".{destination.name}.", delete=False,
            ) as output:
                temporary = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        temporary.replace(destination)
        record.update({"status": "success", "path": destination.name})
        _progress(f"captured {safe_framework} metrics ({phase}): {destination}")
    except (OSError, urllib.error.URLError, ValueError) as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        record["error"] = str(error)
        _progress(f"metrics capture skipped ({phase}): {error}")
    return record


def runtime_environment_overrides(
    hf_cache_dir: Path, dataset_configuration_timeout: int,
) -> dict[str, str]:
    """Return offline cache and AIPerf configuration environment overrides."""
    timeout = str(dataset_configuration_timeout)
    return {
        "HF_HOME": str(hf_cache_dir),
        "HF_DATASETS_CACHE": str(hf_cache_dir / "datasets"),
        "HF_HUB_CACHE": str(hf_cache_dir / "hub"),
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "AIPERF_DATASET_CONFIGURATION_TIMEOUT": timeout,
        "AIPERF_SERVICE_PROFILE_CONFIGURE_TIMEOUT": timeout,
        "AIPERF_UI_REALTIME_METRICS_ENABLED": "true",
    }


def build_runtime_context(
    entry: dict[str, Any], *, project: Project, stem: str, server_url: str,
    metrics_url: str | None, github: dict[str, Any], job_dir: Path,
    hf_cache_dir: Path, dataset_configuration_timeout: int,
) -> dict[str, Any]:
    """Construct the CI/launcher context retained without starting services."""
    prefill = entry.get("prefill") or {}
    decode = entry.get("decode") or {}
    return {
        "github": github,
        "workflow": {
            "mode": "agentx-only",
            "benchmark_launcher_skipped": True,
            "model_start_skipped": True,
            "node_scheduling_skipped": True,
            "slurm_skipped": True,
            "enroot_skipped": True,
        },
        "matrix": entry,
        "project": {
            "root": str(project.root), "head_sha": project.head_sha,
            "remote": project.remote, "dirty": project.dirty,
        },
        "launcher_parameters": {
            "IMAGE": str(_value(entry, "image")), "MODEL": str(_value(entry, "model")),
            "MODEL_PREFIX": str(_value(entry, "model-prefix")),
            "RUNNER_NAME": str(_value(entry, "runner", "simple-endpoint")),
            "FRAMEWORK": str(_value(entry, "framework", "sglang")),
            "PRECISION": str(_value(entry, "precision")), "EXP_NAME": str(_value(entry, "exp-name")),
            "RECIPE_FINGERPRINT": github.get("recipe_fingerprint") or _value(entry, "recipe-fingerprint", ""),
            "EXECUTION_ID": github.get("execution_id"),
            "TP": _value(entry, "tp", 1), "PP_SIZE": _value(entry, "pp", 1),
            "DCP_SIZE": _value(entry, "dcp-size", 1), "PCP_SIZE": _value(entry, "pcp-size", 1),
            "EP_SIZE": _value(entry, "ep", 1), "DP_ATTENTION": _value(entry, "dp-attn", False),
            "CONC": _value(entry, "conc", 0), "CONC_LIST": _value(entry, "conc-list", _value(entry, "conc", 0)),
            "GPU_COUNT": _value(entry, "gpu-count", _value(entry, "tp", 1)),
            "MAX_MODEL_LEN": _value(entry, "max-model-len"),
            "SCENARIO_SUBDIR": _value(entry, "scenario-subdir", "agentic"),
            "AIPERF_EXPERIMENTAL_FAST": _value(entry, "aiperf-experimental-fast", False),
            "RUN_EVAL": _value(entry, "run-eval", False), "EVAL_ONLY": _value(entry, "eval-only", False),
            "SPEC_DECODING": _value(entry, "spec-decoding", "none"),
            "DISAGG": _value(entry, "disagg", False), "IS_MULTINODE": False,
            "KV_OFFLOADING": _value(entry, "kv-offloading", "none"),
            "KV_OFFLOAD_BACKEND": _value(entry, "kv-offload-backend"),
            "KV_P2P_TRANSFER": _value(entry, "kv-p2p-transfer"),
            "TOTAL_CPU_DRAM_GB": _value(entry, "total-cpu-dram-gb", 0),
            "REQUIRE_POWER": _value(entry, "require-power", False),
            "PREFILL": prefill, "DECODE": decode,
            "NUM_NODES": None, "DCU_COUNT": None, "SLURM_PARTITION": None,
            "ENROOT_IMAGE": str(_value(entry, "image")),
        },
        "endpoint_metrics": {
            "source_url": metrics_url,
            "service_managed_by_workflow": False,
            "captures": [],
        },
        "agentx_parameters": {
            "RESULT_DIR": str(job_dir / "results"), "RESULT_FILENAME": stem,
            "AIPERF_SERVER_URL": server_url, "AIPERF_METRICS_URL": metrics_url,
            "SCENARIO": "inferencex-agentx-mvp", "ENDPOINT": "/v1/chat/completions",
            "ENDPOINT_TYPE": "chat", "STREAMING": True,
            "RANDOM_SEED": github["random_seed"],
            "EXECUTION_ID": github["execution_id"],
            "TRACE_IDLE_GAP_CAP_SECONDS": int(os.environ.get("AIPERF_TRACE_IDLE_GAP_CAP_SECONDS", "300")),
            "WARMUP_GRACE_PERIOD": int(os.environ.get("AGENTIC_WARMUP_GRACE_PERIOD", "1800")),
            "NO_GPU_TELEMETRY": True, "DATASET_ENTRIES": 393, "SLICE_DURATION": 1.0,
            "REALTIME_METRICS_ENABLED": True, "STATS_INTERVAL": 30,
            "HF_CACHE_DIR": str(hf_cache_dir),
            "HF_HUB_OFFLINE": True, "HF_DATASETS_OFFLINE": True,
            "DATASET_CONFIGURATION_TIMEOUT": dataset_configuration_timeout,
            "PROFILE_CONFIGURE_TIMEOUT": dataset_configuration_timeout,
        },
    }


def runtime_environment(
    entry: dict[str, Any], *, job_dir: Path, stem: str, server_url: str,
    metrics_url: str | None, github: dict[str, Any], project: Project,
) -> dict[str, str]:
    """Build the result processor environment from matrix and workflow context."""
    def text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)

    values = {
        "RESULT_DIR": job_dir / "results", "RESULT_FILENAME": stem,
        "AGENTIC_OUTPUT_DIR": job_dir, "AIPERF_SERVER_URL": server_url,
        "AIPERF_SERVER_METRICS_URLS": metrics_url or "",
        "MODEL": _value(entry, "model"), "MODEL_PREFIX": _value(entry, "model-prefix"),
        "FRAMEWORK": _value(entry, "framework", "sglang"), "PRECISION": _value(entry, "precision"),
        "TP": _value(entry, "tp", 1), "PP_SIZE": _value(entry, "pp", 1),
        "DCP_SIZE": _value(entry, "dcp-size", 1), "PCP_SIZE": _value(entry, "pcp-size", 1),
        "EP_SIZE": _value(entry, "ep", 1), "DP_ATTENTION": _value(entry, "dp-attn", False),
        "CONC": _value(entry, "conc", 0), "CONC_LIST": _value(entry, "conc-list", _value(entry, "conc", 0)),
        "GPU_COUNT": _value(entry, "gpu-count", _value(entry, "tp", 1)),
        "MAX_MODEL_LEN": _value(entry, "max-model-len"),
        "SCENARIO_SUBDIR": _value(entry, "scenario-subdir", "agentic"),
        "AIPERF_EXPERIMENTAL_FAST": _value(entry, "aiperf-experimental-fast", False),
        "MAX_MODEL_LEN": _value(entry, "max-model-len"),
        "SCENARIO_TYPE": "agentic-coding",
        "IS_MULTINODE": "false", "DISAGG": _value(entry, "disagg", False),
        "KV_OFFLOADING": _value(entry, "kv-offloading", "none"),
        "KV_OFFLOAD_BACKEND": _value(entry, "kv-offload-backend", ""),
        "SPEC_DECODING": _value(entry, "spec-decoding", "none"),
        "TOTAL_CPU_DRAM_GB": _value(entry, "total-cpu-dram-gb", 0),
        "RUNNER_TYPE": _value(entry, "runner", "simple-endpoint"),
        "RECIPE_FINGERPRINT": github.get("recipe_fingerprint") or _value(entry, "recipe-fingerprint", ""),
        "INFERENCE_X_EXECUTION_ID": github.get("execution_id"),
        "AIPERF_RANDOM_SEED": github.get("random_seed"),
        "IMAGE": _value(entry, "image", ""), "EXP_NAME": _value(entry, "exp-name", ""),
        "GITHUB_RUN_ID": github.get("run_id"), "GITHUB_RUN_ATTEMPT": github.get("run_attempt"),
        "GITHUB_EVENT_NAME": github.get("event_name"), "GITHUB_SHA": github.get("sha"),
        "GITHUB_REF": github.get("ref"), "GITHUB_REPOSITORY": github.get("repository"),
        "GITHUB_WORKSPACE": project.root,
    }
    return {key: text(value) for key, value in values.items()}


def build_command(
    entry: dict[str, Any], *, project: Project, result_dir: Path, stem: str,
    server_url: str, aiperf_cli: str, tokenizer: str | None, duration: int,
    dataset_loader: str, served_model_name: str | None, metrics_url: str | None,
    warmup_requests_per_lane: int, failed_request_threshold: float,
    random_seed: int, fast: bool,
) -> list[str]:
    if entry.get("scenario-type") != "agentic-coding":
        raise WorkflowError("py_workflow_simple currently supports only AgentX agentic-coding entries")
    if entry.get("eval-only"):
        raise WorkflowError("py_workflow_simple does not execute evaluation-only entries")
    model = served_model_name or str(_value(entry, "model"))
    tokenizer_value = tokenizer or str(_value(entry, "model"))
    concurrency = _value(entry, "conc")
    if isinstance(concurrency, list):
        concurrency = max(concurrency)
    command = [
        aiperf_cli, "profile", "--scenario", "inferencex-agentx-mvp",
        "--url", server_url, "--endpoint", "/v1/chat/completions",
        "--endpoint-type", "chat", "--streaming", "--model", model,
        "--tokenizer", tokenizer_value, "--concurrency", str(concurrency),
        "--benchmark-duration", str(1200 if fast else duration),
        "--stats-interval", "30", "--random-seed", str(random_seed),
        "--failed-request-threshold", str(failed_request_threshold),
        "--trajectory-start-min-ratio", "0.25", "--trajectory-start-max-ratio", "0.75",
        "--warmup-requests-per-lane", str(1 if fast else warmup_requests_per_lane),
        "--trace-idle-gap-cap-seconds", os.environ.get("AIPERF_TRACE_IDLE_GAP_CAP_SECONDS", "300"),
        "--warmup-grace-period", os.environ.get("AGENTIC_WARMUP_GRACE_PERIOD", "1800"),
        "--use-server-token-count", "--no-gpu-telemetry", "--tokenizer-trust-remote-code",
        "--num-dataset-entries", "393", "--slice-duration", "1.0",
        "--output-artifact-dir", str(result_dir / "aiperf_artifacts"),
    ]
    if dataset_loader:
        command.extend(["--public-dataset", dataset_loader])
    if metrics_url:
        command.extend(["--server-metrics", metrics_url])
    return command


def run_agentx(
    entry: dict[str, Any], *, project: Project, job_dir: Path, stem: str,
    server_url: str, aiperf_cli: str, tokenizer: str | None, duration: int,
    dataset_loader: str, served_model_name: str | None, metrics_url: str | None,
    warmup_requests_per_lane: int, failed_request_threshold: float, fast: bool,
    env_overrides: dict[str, str], github: dict[str, Any], hf_cache_dir: Path,
    dataset_configuration_timeout: int,
) -> tuple[list[str], subprocess.CompletedProcess[str], dict[str, str]]:
    result_dir = job_dir / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    command = build_command(
        entry, project=project, result_dir=result_dir, stem=stem,
        server_url=server_url, aiperf_cli=aiperf_cli, tokenizer=tokenizer,
        duration=duration, dataset_loader=dataset_loader,
        served_model_name=served_model_name, metrics_url=metrics_url,
        warmup_requests_per_lane=warmup_requests_per_lane,
        failed_request_threshold=failed_request_threshold,
        random_seed=int(github["random_seed"]), fast=fast,
    )
    env = dict(os.environ)
    env.update(runtime_environment(
        entry, job_dir=job_dir, stem=stem, server_url=server_url,
        metrics_url=metrics_url, github=github, project=project,
    ))
    env.update(env_overrides)
    context = build_runtime_context(
        entry, project=project, stem=stem, server_url=server_url,
        metrics_url=metrics_url, github=github, job_dir=job_dir,
        hf_cache_dir=hf_cache_dir,
        dataset_configuration_timeout=dataset_configuration_timeout,
    )
    captures = context["endpoint_metrics"]["captures"]
    context_json = json.dumps(context, indent=2, sort_keys=True, default=str) + "\n"
    (job_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
    (result_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
    safe_env = {key: value for key, value in env.items() if not any(
        marker in key.upper() for marker in ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "PROXY")
    )}
    env_json = json.dumps(safe_env, indent=2, sort_keys=True) + "\n"
    (job_dir / "meta_env.json").write_text(env_json, encoding="utf-8")
    (result_dir / "meta_env.json").write_text(env_json, encoding="utf-8")
    (result_dir / "benchmark_command.txt").write_text(
        shlex.join(command) + "\n", encoding="utf-8",
    )
    write_agentx_power_timezone_offset(result_dir)
    captures.append(capture_endpoint_metrics(
        metrics_url=metrics_url, result_dir=result_dir,
        framework=str(_value(entry, "framework", "server")), phase="before-benchmark",
    ))
    context_json = json.dumps(context, indent=2, sort_keys=True, default=str) + "\n"
    (job_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
    (result_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
    job_log = job_dir / "benchmark.log"
    artifact_log = result_dir / "benchmark.log"
    _progress(f"running AIPerf; logs={job_log}, {artifact_log}")
    _progress(f"command: {_redacted_command(command)}")
    output: list[str] = []
    returncode = 1
    try:
        with (
            job_log.open("w", encoding="utf-8") as job_log_file,
            artifact_log.open("w", encoding="utf-8") as artifact_log_file,
        ):
            process = subprocess.Popen(
                command, cwd=project.root, env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for line in process.stdout:
                output.append(line)
                job_log_file.write(line)
                job_log_file.flush()
                artifact_log_file.write(line)
                artifact_log_file.flush()
                print(f"[agentx] {line}", end="", flush=True)
            returncode = process.wait()
    finally:
        captures.append(capture_endpoint_metrics(
            metrics_url=metrics_url, result_dir=result_dir,
            framework=str(_value(entry, "framework", "server")), phase="after-benchmark",
        ))
        context_json = json.dumps(context, indent=2, sort_keys=True, default=str) + "\n"
        (job_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
        (result_dir / "runtime_context.json").write_text(context_json, encoding="utf-8")
    completed = subprocess.CompletedProcess(command, returncode, "".join(output), "")
    _progress(f"AIPerf exited with status {returncode}")
    return command, completed, env


def process_outputs(project: Project, job_dir: Path, stem: str, env: dict[str, str]) -> None:
    result_dir = job_dir / "results"
    python = str(project.python)
    commands = [
        [python, "-m", "infx.results.agentic.process_agentic_result"],
        [python, "-m", "infx.results.generate_aiperf_plots", str(result_dir)],
        [python, "-m", "infx.results.agentic.analyze_benchmark_distributions", str(result_dir / "aiperf_artifacts"), "-o", str(result_dir)],
    ]
    for command in commands:
        _progress(f"processing output: {shlex.join(command)}")
        completed = subprocess.run(command, cwd=project.root, env=env, text=True, capture_output=True)
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"command failed: {command[1]}"
            _progress(f"output processing failed: {detail}")
            raise WorkflowError(detail)
    result = job_dir / f"{stem}.json"
    if not result.is_file():
        raise WorkflowError(f"AgentX result was not generated: {result}")
    _progress(f"result processing complete: {result}")


def execute(
    entry: dict[str, Any], *, project: Project, jobs_root: Path, artifact_root: Path,
    server_url: str, aiperf_cli: str, tokenizer: str | None, duration: int,
    dataset_loader: str, served_model_name: str | None, metrics_url: str | None,
    warmup_requests_per_lane: int, failed_request_threshold: float, fast: bool,
    github: dict[str, Any], hf_cache_dir: Path, dataset_configuration_timeout: int,
) -> dict[str, Any]:
    stem = result_filename(project, entry)
    job_dir = jobs_root / stem
    job_dir.mkdir(parents=True, exist_ok=True)
    command, completed, env = run_agentx(
        entry, project=project, job_dir=job_dir, stem=stem,
        server_url=server_url, aiperf_cli=aiperf_cli, tokenizer=tokenizer,
        duration=duration, dataset_loader=dataset_loader,
        served_model_name=served_model_name, metrics_url=metrics_url,
        warmup_requests_per_lane=warmup_requests_per_lane,
        failed_request_threshold=failed_request_threshold, fast=fast,
        env_overrides=runtime_environment_overrides(
            hf_cache_dir, dataset_configuration_timeout,
        ),
        github=github, hf_cache_dir=hf_cache_dir,
        dataset_configuration_timeout=dataset_configuration_timeout,
    )
    record: dict[str, Any] = {
        "entry": entry, "result_filename": stem, "command": command,
        "status": "failure" if completed.returncode else "running",
    }
    if completed.returncode:
        record["error"] = completed.stderr.strip() or "AIPerf exited unsuccessfully"
    else:
        process_outputs(project, job_dir, stem, env)
        record["status"] = "success"
    return record
