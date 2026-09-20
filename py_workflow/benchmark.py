"""Single-node and multi-node adapters for existing InferenceX launchers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from py_workflow.manifest import Project, WorkflowError


@dataclass(frozen=True)
class JobResult:
    """Outcome and staged artifacts for one planned benchmark entry."""

    status: str
    record: dict[str, Any]


def as_env(value: Any) -> str:
    """Serialize workflow values to the launcher environment convention."""
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return ""
    return str(value)


def environment(
    entry: dict[str, Any], *, project_root: Path, result_dir: Path, result_filename: str,
) -> dict[str, str]:
    """Translate one generated row to the established launcher environment."""
    env = dict(os.environ)
    scenario_type = entry.get("scenario-type", "fixed-seq-len")
    kv_offload_backend = entry.get("kv-offload-backend")
    router = entry.get("router")
    mapping = {
        "EXP_NAME": entry.get("exp-name"), "GITHUB_WORKSPACE": str(project_root),
        "RUNNER_NAME": entry.get("runner"), "MODEL": entry.get("model"),
        "MODEL_PREFIX": entry.get("model-prefix"), "IMAGE": entry.get("image"),
        "FRAMEWORK": entry.get("framework"), "PRECISION": entry.get("precision"),
        "TP": entry.get("tp"), "PP_SIZE": entry.get("pp", 1),
        "DCP_SIZE": entry.get("dcp-size", 1), "PCP_SIZE": entry.get("pcp-size", 1),
        "EP_SIZE": entry.get("ep", 1), "DP_ATTENTION": entry.get("dp-attn", False),
        "GPU_COUNT": int(entry.get("tp", 1)) * int(entry.get("pp", 1)) * int(entry.get("pcp-size", 1)),
        "CONC": entry.get("conc"),
        "CONC_LIST": " ".join(str(item) for item in entry.get("conc-list", [entry.get("conc", "")])),
        "ISL": entry.get("isl"), "OSL": entry.get("osl"),
        "MAX_MODEL_LEN": entry.get("max-model-len"),
        "SPEC_DECODING": entry.get("spec-decoding", "none"),
        "DISAGG": entry.get("disagg", False), "RUN_EVAL": entry.get("run-eval", False),
        "EVAL_ONLY": entry.get("eval-only", False),
        "EVAL_FRAMEWORK": entry.get("eval-framework", "lm-eval"),
        "EVAL_SUITE": entry.get("eval-suite", ""), "EVAL_CONC": entry.get("eval-conc", ""),
        "EVAL_LIMIT": entry.get("eval-limit", ""),
        "SWEBENCH_GEN_MODE": entry.get("swebench-gen-mode", ""),
        "SCENARIO_TYPE": scenario_type,
        "SCENARIO_SUBDIR": "agentic/" if scenario_type == "agentic-coding" else "fixed_seq_len/",
        "IS_AGENTIC": 1 if scenario_type == "agentic-coding" else 0,
        "KV_OFFLOADING": entry.get("kv-offloading", "none"),
        "KV_OFFLOAD_BACKEND": kv_offload_backend.get("name", "")
        if isinstance(kv_offload_backend, dict) else (kv_offload_backend or ""),
        "KV_OFFLOAD_BACKEND_METADATA": json.dumps(kv_offload_backend) if isinstance(kv_offload_backend, dict) else "",
        "ROUTER_METADATA": json.dumps(router) if isinstance(router, dict) else (router or ""),
        "KV_P2P_TRANSFER": entry.get("kv-p2p-transfer", ""),
        "TOTAL_CPU_DRAM_GB": entry.get("total-cpu-dram-gb", 0),
        "DURATION": entry.get("duration"), "RECIPE_FINGERPRINT": entry.get("recipe-fingerprint", ""),
        "REQUIRE_POWER": 1 if entry.get("require-power") and not entry.get("eval-only") else 0,
        "POWER_PRODUCER_SHA": entry.get("power-producer-sha", ""),
        "RUNNER_TYPE": entry.get("runner"), "RESULT_DIR": str(result_dir),
        "RESULT_FILENAME": result_filename, "AIPERF_FAILED_REQUEST_THRESHOLD": "0.10",
        "AIPERF_EXPERIMENTAL_FAST": "1" if entry.get("agentx-fast") else "0",
    }
    if entry.get("prefill") is not None:
        prefill, decode = entry["prefill"], entry["decode"]
        mapping.update({
            "IS_MULTINODE": "true",
            "PREFILL_NUM_WORKERS": prefill.get("num-worker"), "PREFILL_TP": prefill.get("tp"),
            "PREFILL_PP_SIZE": prefill.get("pp", 1), "PREFILL_DCP_SIZE": prefill.get("dcp-size", 1),
            "PREFILL_PCP_SIZE": prefill.get("pcp-size", 1), "PREFILL_EP": prefill.get("ep", 1),
            "PREFILL_DP_ATTN": prefill.get("dp-attn", False), "DECODE_NUM_WORKERS": decode.get("num-worker"),
            "DECODE_TP": decode.get("tp"), "DECODE_PP_SIZE": decode.get("pp", 1),
            "DECODE_DCP_SIZE": decode.get("dcp-size", 1), "DECODE_PCP_SIZE": decode.get("pcp-size", 1),
            "DECODE_EP": decode.get("ep", 1), "DECODE_DP_ATTN": decode.get("dp-attn", False),
        })
        for settings in (prefill.get("additional-settings", []), decode.get("additional-settings", [])):
            for item in settings:
                key, separator, value = str(item).partition("=")
                if not separator or not key:
                    raise WorkflowError(f"invalid multi-node additional setting: {item!r}")
                mapping[key] = value
    env.update({key: as_env(value) for key, value in mapping.items()})
    return env


def result_filename(project: Project, entry: dict[str, Any]) -> str:
    """Reproduce the template's stable filename identity."""
    if entry.get("prefill") is None:
        base = (
            f"{entry['exp-name']}_{entry['precision']}_{entry['framework']}_"
            f"tp{entry['tp']}-pp{entry.get('pp', 1)}-dcp{entry.get('dcp-size', 1)}-"
            f"pcp{entry.get('pcp-size', 1)}-ep{entry.get('ep', 1)}-"
            f"dpa{entry.get('dp-attn', False)}_disagg-{entry.get('disagg', False)}_"
            f"spec-{entry.get('spec-decoding', 'none')}_conc{entry['conc']}_{entry['runner']}"
        )
    else:
        prefill, decode = entry["prefill"], entry["decode"]
        concs = entry.get("conc-list", [entry["conc"]])
        base = (
            f"{entry['exp-name']}_{entry['precision']}_{entry['framework']}_"
            f"prefill-tp{prefill['tp']}-pp{prefill.get('pp', 1)}-dcp{prefill.get('dcp-size', 1)}-"
            f"pcp{prefill.get('pcp-size', 1)}-ep{prefill.get('ep', 1)}-dp{prefill.get('dp-attn', False)}-"
            f"nw{prefill.get('num-worker')}_decode-tp{decode['tp']}-pp{decode.get('pp', 1)}-"
            f"dcp{decode.get('dcp-size', 1)}-pcp{decode.get('pcp-size', 1)}-ep{decode.get('ep', 1)}-"
            f"dp{decode.get('dp-attn', False)}-nw{decode.get('num-worker')}_"
            f"disagg-{entry.get('disagg', False)}_spec-{entry.get('spec-decoding', 'none')}_"
            f"conc{'x'.join(map(str, concs))}_{entry['runner']}"
        )
    command = [
        str(project.python), "-c", (
            "import os; from infx.results.result_filename import result_stem; "
            "print(result_stem(os.environ['RESULT_FILENAME_BASE'], os.environ.get('RECIPE_FINGERPRINT', '')))"
        ),
    ]
    completed = subprocess.run(
        command, cwd=project.root, env={**os.environ, "RESULT_FILENAME_BASE": base,
        "RECIPE_FINGERPRINT": str(entry.get("recipe-fingerprint", ""))},
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise WorkflowError(completed.stderr.strip() or "result filename calculation failed")
    return completed.stdout.strip()


def result_marker(path: Path) -> tuple[int, int]:
    """Return enough file metadata to distinguish output from a prior local run."""
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size


def expected_results(
    entry: dict[str, Any], stem: str, cwd: Path,
    *, preexisting_eval_results: dict[Path, tuple[int, int]] | None = None,
) -> list[Path]:
    """Return output paths required by template-equivalent validation."""
    if entry.get("eval-only"):
        results = sorted(cwd.glob("results*.json"))
        if preexisting_eval_results is None:
            return results
        return [
            path for path in results
            if preexisting_eval_results.get(path) != result_marker(path)
        ]
    if entry.get("scenario-type") == "agentic-coding":
        if entry.get("prefill") is not None:
            return sorted(cwd.glob(f"{stem}_conc*.json"))
        return [cwd / f"{stem}.json"]
    if entry.get("prefill") is not None:
        return sorted(cwd.glob(f"{stem}_*.json"))
    return [cwd / f"{stem}.json"]


def validate(
    entry: dict[str, Any], *, project: Project, job_dir: Path, stem: str, env: dict[str, str],
    preexisting_eval_results: dict[Path, tuple[int, int]] | None = None,
) -> list[Path]:
    """Perform the workflow's post-launch result checks and processors."""
    paths = expected_results(
        entry, stem, job_dir, preexisting_eval_results=preexisting_eval_results,
    )
    if not paths:
        raise WorkflowError(f"no result files found for {stem}")
    if entry.get("eval-only"):
        command = [str(project.python), "utils/evals/validate_scores.py"]
    elif entry.get("scenario-type") == "agentic-coding":
        if entry.get("prefill") is None:
            command = [str(project.python), "-m", "infx.results.agentic.validate_agentic_result",
                       str(job_dir / "results" / "aiperf_artifacts"), "--failed-request-threshold", "0.10"]
        else:
            wanted = len(entry.get("conc-list", [entry["conc"]]))
            if len(paths) != wanted:
                raise WorkflowError(f"expected {wanted} AgentX results, found {len(paths)}")
            for path in paths:
                import json
                data = json.loads(path.read_text(encoding="utf-8"))
                if not data.get("num_requests_successful"):
                    raise WorkflowError(f"AgentX result has zero successful requests: {path.name}")
            return paths
    else:
        command = [str(project.python), "utils/process_result.py"]
        if entry.get("prefill") is not None:
            command.append("--all")
    completed = subprocess.run(command, cwd=job_dir, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode:
        raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "result validation failed")
    return paths


def stage_job_output(job_dir: Path, artifact_root: Path, entry: dict[str, Any], stem: str) -> list[str]:
    """Stage files using the same directory layout as Actions artifacts."""
    included: list[str] = []

    def copy_file(source: Path, target: Path, *, relative: Path | None = None) -> None:
        destination = target / (relative if relative is not None else source.name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        included.append(destination.relative_to(artifact_root).as_posix())

    def copy_matches(target: Path, patterns: list[str], *, relative_to_results: bool = False) -> None:
        matches: list[Path] = []
        for pattern in patterns:
            matches.extend(path for path in job_dir.glob(pattern) if path.is_file())
        for source in sorted(set(matches)):
            relative = (
                source.relative_to(job_dir / "results")
                if relative_to_results and source.is_relative_to(job_dir / "results")
                else None
            )
            copy_file(source, target, relative=relative)

    def copy_workflow_paths(target: Path, patterns: list[str]) -> None:
        """Preserve the relative paths named by an upload-artifact block."""
        matches: list[Path] = []
        for pattern in patterns:
            matches.extend(path for path in job_dir.glob(pattern) if path.is_file())
        for source in sorted(set(matches)):
            copy_file(source, target, relative=source.relative_to(job_dir))

    def copy_results_projection(target: Path, patterns: list[str]) -> None:
        """Copy workflow paths below the artifact's observed ``results/`` root."""
        results_target = target / "results"
        matches: list[Path] = []
        for pattern in patterns:
            matches.extend(path for path in job_dir.glob(pattern) if path.is_file())
        for source in sorted(set(matches)):
            if source.is_relative_to(job_dir / "results"):
                copy_file(source, results_target, relative=source.relative_to(job_dir / "results"))
            else:
                copy_file(source, results_target)

    agentic = entry.get("scenario-type") == "agentic-coding"
    eval_requested = bool(entry.get("eval-only") or entry.get("run-eval"))
    if not entry.get("eval-only"):
        if agentic:
            aggregate = artifact_root / f"bmk_agentic_{stem}"
            copy_matches(aggregate, [f"{stem}.json", f"{stem}_conc*.json"])
            raw = artifact_root / f"agentic_{stem}"
            results = job_dir / "results"
            if results.is_dir():
                for source in sorted(results.rglob("*")):
                    if source.is_file() and source.name not in {"inputs.json", "profile_export_raw.jsonl"}:
                        copy_file(source, raw, relative=source.relative_to(results))
        else:
            copy_matches(artifact_root / f"bmk_{stem}", [f"agg_{stem}.json", f"agg_{stem}_*.json"])

    if eval_requested:
        copy_matches(artifact_root / f"eval_{stem}", [
            "meta_env.json", "results*.json", "*_report.json", "*_results.jsonl",
            "*_artifacts.tar.gz", "sample*.jsonl", "agent_preds.json", "predictions.jsonl",
        ])

    # The Actions server-log artifact materializes both root server.log and
    # results/*.log under a common results/ directory after download.
    server_logs = artifact_root / f"{'eval_server_logs_' if entry.get('eval-only') else 'server_logs_'}{stem}"
    log_sources = [*job_dir.glob("server.log"), *job_dir.glob("results/*.log"), *job_dir.glob("results/*_config.json")]
    for source in sorted(path for path in set(log_sources) if path.is_file()):
        copy_file(source, server_logs / "results", relative=source.relative_to(job_dir / "results") if source.parent == job_dir / "results" else None)

    gpu_target = artifact_root / f"{'eval_gpu_metrics_' if entry.get('eval-only') else 'gpu_metrics_'}{stem}"
    copy_workflow_paths(gpu_target, [
        "gpu_metrics.csv", "gpu_metrics*_context.json", "gpu_metrics_energy_start.csv",
        "gpu_metrics_energy_end.csv", "gpu_metrics_identity.json", "gpu_metrics_identity.csv",
        "results/gpu_metrics*.csv", "results/gpu_metrics*_context.json",
        "results/gpu_metrics_identity.json",
    ])

    if not entry.get("eval-only"):
        power = artifact_root / f"power_audit_{stem}"
        copy_matches(power, [
            f"{stem}.json", f"{stem}_*.json", f"agg_{stem}.json", f"agg_{stem}_*.json",
            f"power_validation_{stem}_*.json", f"result_processing_{stem}.json",
            "gpu_metrics.csv", "gpu_metrics*_context.json", "gpu_metrics_energy_start.csv",
            "gpu_metrics_energy_end.csv", "gpu_metrics_identity.json", "gpu_metrics_identity.csv",
            "multinode_server_logs.tar.gz",
        ])
        copy_workflow_paths(power, [
            "results/gpu_metrics*.csv", "results/gpu_metrics*_context.json",
            "results/gpu_metrics_identity.json", "results/agentic_power_window.json",
            "results/agentic_power_timezone_offset.txt", "results/power_validation.json",
        ])
    return included


def execute(entry: dict[str, Any], *, project: Project, jobs_root: Path, artifact_root: Path) -> JobResult:
    """Run one existing launcher in an isolated local job directory."""
    import sys
    stem = result_filename(project, entry)
    job_dir = project.root
    jobs_root.mkdir(parents=True, exist_ok=True)
    env = environment(
        entry, project_root=project.root, result_dir=project.root / "results", result_filename=stem,
    )
    launcher = project.root / "runners" / f"launch_{str(entry['runner']).split('_', 1)[0]}.sh"
    preexisting_eval_results = (
        {path: result_marker(path) for path in job_dir.glob("results*.json")}
        if entry.get("eval-only") else None
    )
    started = time.monotonic()
    command = ["bash", str(launcher)]
    completed = subprocess.run(command, cwd=job_dir, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (job_dir / "launcher.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (job_dir / "launcher.stderr.log").write_text(completed.stderr, encoding="utf-8")
    record = {"entry": entry, "result_filename": stem, "command": command, "duration_seconds": time.monotonic() - started}
    try:
        if completed.returncode:
            raise WorkflowError(f"launcher exited {completed.returncode}: {completed.stderr.strip()}")
        outputs = validate(
            entry, project=project, job_dir=job_dir, stem=stem, env=env,
            preexisting_eval_results=preexisting_eval_results,
        )
        record.update(status="success", outputs=[str(path.relative_to(job_dir)) for path in outputs])
    except WorkflowError as error:
        record.update(status="failure", error=str(error))
    record["artifacts"] = stage_job_output(job_dir, artifact_root, entry, stem)
    return JobResult(record["status"], record)
