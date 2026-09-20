"""Artifact staging contract tests for benchmark workflow adapters."""

from __future__ import annotations

from pathlib import Path

from py_workflow.benchmark import environment, expected_results, result_marker, stage_job_output


def test_stage_agentic_with_eval_keeps_all_workflow_categories(tmp_path: Path) -> None:
    job = tmp_path / "job"
    artifact_root = tmp_path / "artifacts"
    results = job / "results" / "aiperf_artifacts"
    results.mkdir(parents=True)
    (job / "run.json").write_text("{}", encoding="utf-8")
    (job / "meta_env.json").write_text("{}", encoding="utf-8")
    (job / "results_eval.json").write_text("{}", encoding="utf-8")
    (results / "request.json").write_text("{}", encoding="utf-8")
    (results / "inputs.json").write_text("{}", encoding="utf-8")

    (job / "server.log").write_text("server", encoding="utf-8")
    (results.parent / "benchmark.log").write_text("benchmark", encoding="utf-8")
    (results.parent / "agentic_power_timezone_offset.txt").write_text("+0800\n", encoding="utf-8")

    stage_job_output(
        job, artifact_root,
        {"scenario-type": "agentic-coding", "run-eval": True}, "run",
    )

    assert (artifact_root / "bmk_agentic_run" / "run.json").is_file()
    assert (artifact_root / "agentic_run" / "aiperf_artifacts" / "request.json").is_file()
    assert not (artifact_root / "agentic_run" / "aiperf_artifacts" / "inputs.json").exists()
    assert (artifact_root / "eval_run" / "meta_env.json").is_file()
    assert (artifact_root / "eval_run" / "results_eval.json").is_file()
    assert (artifact_root / "server_logs_run" / "results" / "server.log").is_file()
    assert (artifact_root / "server_logs_run" / "results" / "benchmark.log").is_file()
    assert (artifact_root / "power_audit_run" / "results" / "agentic_power_timezone_offset.txt").is_file()
    assert not (artifact_root / "logs" / "run").exists()


def test_environment_includes_local_launcher_contract(tmp_path: Path) -> None:
    entry = {
        "exp-name": "dsv4flash", "runner": "dcu-hygon_00", "model": "/models/dsv4flash",
        "tp": 8, "pp": 2, "pcp-size": 1, "scenario-type": "agentic-coding",
        "kv-offload-backend": {"name": "hicache", "version": "1"},
        "router": {"name": "sglang-router", "version": "1"},
        "require-power": True,
    }

    env = environment(entry, project_root=tmp_path, result_dir=tmp_path / "results", result_filename="result")

    assert env["EXP_NAME"] == "dsv4flash"
    assert env["GITHUB_WORKSPACE"] == str(tmp_path)
    assert env["RUNNER_NAME"] == "dcu-hygon_00"
    assert env["GPU_COUNT"] == "16"
    assert env["KV_OFFLOAD_BACKEND"] == "hicache"
    assert env["KV_OFFLOAD_BACKEND_METADATA"] == '{"name": "hicache", "version": "1"}'
    assert env["ROUTER_METADATA"] == '{"name": "sglang-router", "version": "1"}'
    assert env["REQUIRE_POWER"] == "1"


def test_eval_only_requires_result_changed_by_current_run(tmp_path: Path) -> None:
    old = tmp_path / "results_old.json"
    old.write_text("old", encoding="utf-8")
    existing = {old: result_marker(old)}

    assert expected_results({"eval-only": True}, "unused", tmp_path, preexisting_eval_results=existing) == []

    old.write_text("new output", encoding="utf-8")
    assert expected_results({"eval-only": True}, "unused", tmp_path, preexisting_eval_results=existing) == [old]

def test_stage_fixed_keeps_aggregate_separate_from_power_input(tmp_path: Path) -> None:
    job = tmp_path / "job"
    artifact_root = tmp_path / "artifacts"
    job.mkdir()
    (job / "agg_fixed.json").write_text("{}", encoding="utf-8")
    (job / "fixed.json").write_text("{}", encoding="utf-8")

    stage_job_output(job, artifact_root, {"scenario-type": "fixed-seq-len"}, "fixed")

    assert (artifact_root / "bmk_fixed" / "agg_fixed.json").is_file()
    assert (artifact_root / "power_audit_fixed" / "fixed.json").is_file()
    assert not (artifact_root / "power_audit" / "fixed").exists()
