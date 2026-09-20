"""Tests for local reusable collector workflow replacements."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from py_workflow.collect_evals import collect as collect_evals
from py_workflow.collect_results import collect as collect_results


def test_collect_results_includes_fixed_and_agentx_artifacts(
    tmp_path: Path, monkeypatch
) -> None:
    artifacts = tmp_path / "artifacts"
    fixed = artifacts / "bmk_fixed"
    agentic = artifacts / "bmk_agentic_agentx"
    fixed.mkdir(parents=True)
    agentic.mkdir()
    (fixed / "fixed.json").write_text('{"kind": "fixed"}', encoding="utf-8")
    (agentic / "agentx.json").write_text('{"kind": "agentx"}', encoding="utf-8")
    aggregates = tmp_path / "aggregates"
    project_root = tmp_path / "project"
    project_root.mkdir()
    observed: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        observed["input"] = Path(command[-2])
        observed["cwd"] = Path(kwargs["cwd"])
        observed["pythonpath"] = Path(kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0])
        payload = []
        for path in observed["input"].rglob("*.json"):
            payload.append(json.loads(path.read_text(encoding="utf-8")))
        (Path(kwargs["cwd"]) / "agg_bmk.json").write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("py_workflow.collect_results.subprocess.run", fake_run)
    output = collect_results(
        python=tmp_path / "python", artifacts=artifacts, aggregates=aggregates,
        project_root=project_root,
    )

    assert output == aggregates / "agg_bmk.json"
    assert observed["cwd"] == aggregates
    assert observed["pythonpath"] == project_root
    assert sorted(path.name for path in observed["input"].iterdir()) == [
        "bmk_agentic_agentx", "bmk_fixed",
    ]
    assert {item["kind"] for item in json.loads(output.read_text(encoding="utf-8"))} == {
        "fixed", "agentx",
    }
    assert (artifacts / "results_bmk" / "agg_bmk.json").is_file()


def test_collect_results_collects_agentx_only(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    agentic = artifacts / "bmk_agentic_agentx"
    agentic.mkdir(parents=True)
    (agentic / "agentx.json").write_text("{}", encoding="utf-8")
    aggregates = tmp_path / "aggregates"

    def fake_run(command, **kwargs):
        assert Path(command[-2]).joinpath("bmk_agentic_agentx", "agentx.json").is_file()
        (Path(kwargs["cwd"]) / "agg_bmk.json").write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("py_workflow.collect_results.subprocess.run", fake_run)
    assert collect_results(python=tmp_path / "python", artifacts=artifacts, aggregates=aggregates)


def test_collect_evals_materializes_all_eval_artifacts(tmp_path: Path, monkeypatch) -> None:
    artifacts = tmp_path / "artifacts"
    first = artifacts / "eval_one"
    second = artifacts / "eval_two"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "meta_env.json").write_text("{}", encoding="utf-8")
    (second / "meta_env.json").write_text("{}", encoding="utf-8")
    aggregates = tmp_path / "aggregates"
    observed: dict[str, Path] = {}

    def fake_run(command, **kwargs):
        observed["input"] = Path(command[-2])
        (Path(kwargs["cwd"]) / "agg_eval_all.json").write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "eval table\n", "")

    monkeypatch.setattr("py_workflow.collect_evals.subprocess.run", fake_run)
    output = collect_evals(
        python=tmp_path / "python", artifacts=artifacts, aggregates=aggregates,
    )

    assert output == aggregates / "agg_eval_all.json"
    assert sorted(path.name for path in observed["input"].iterdir()) == ["eval_one", "eval_two"]
    assert (artifacts / "eval_results_all" / "agg_eval_all.json").is_file()
    assert (aggregates / "eval_summary.md").read_text(encoding="utf-8") == "## Eval Summary\n\neval table\n"
