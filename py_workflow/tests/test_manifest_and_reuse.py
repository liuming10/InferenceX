from __future__ import annotations

import json
from pathlib import Path

import pytest

import py_workflow.manifest as manifest_module
from py_workflow.benchmark_multinode_tmpl import execute_multinode
from py_workflow.benchmark_tmpl import execute_single_node
from py_workflow.manifest import (
    Project, WorkflowError, artifact_inventory, atomic_json, make_manifest,
    matrix_digest, resolve_project, verify_inventory,
)
from py_workflow.reuse import decide_reuse
from py_workflow.run_sweep import options, parser
from py_workflow.success_rate import calculate


def project(tmp_path: Path, sha: str = "a" * 40) -> Project:
    return Project(tmp_path, tmp_path / "python", "project-dir", sha, "example/repo", False)


def successful_archive(tmp_path: Path, current: Project, matrix: list[dict]) -> Path:
    archive = tmp_path / "archive"
    artifacts = archive / "artifacts" / "results_bmk"
    artifacts.mkdir(parents=True)
    (artifacts / "result.json").write_text("{}", encoding="utf-8")
    manifest = make_manifest(project=current, run_id="source", matrix=matrix, options={
        "config_keys": ["demo"], "conc": [], "mode": "sweep-enabled", "all_evals": False,
        "evals_only": False, "no_evals": False, "agentx_fast": False,
    })
    manifest["status"] = "success"
    manifest["jobs"] = [{"status": "success", "entry": matrix[0]}]
    manifest["artifacts"] = artifact_inventory(archive / "artifacts")
    atomic_json(archive / "archive_manifest.json", manifest)
    return archive


def test_resolve_project_skips_submodule_validation_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(manifest_module, "git_output", lambda _root, *args: {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("rev-parse", "HEAD"): "a" * 40,
        ("status", "--porcelain"): "",
    }[args])
    monkeypatch.setattr(manifest_module, "run", lambda *_args, **_kwargs: type("Result", (), {"stdout": ""})())
    monkeypatch.setattr(manifest_module, "venv_python", lambda root: root / ".venv/bin/python")
    monkeypatch.setattr(manifest_module, "verify_python", lambda *_args: None)
    monkeypatch.setattr(manifest_module, "ensure_initialized_submodules", lambda _root: calls.append("checked"))

    resolve_project(
        project_dir=project_dir, workspace_root=tmp_path / "worktrees", repository=None,
        ref="HEAD", allow_dirty=False, validate_submodules=False,
    )

    assert calls == []


def test_resolve_project_skips_submodule_validation_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(manifest_module, "git_output", lambda _root, *args: {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("rev-parse", "HEAD"): "a" * 40,
        ("status", "--porcelain"): "",
    }[args])
    monkeypatch.setattr(manifest_module, "run", lambda *_args, **_kwargs: type("Result", (), {"stdout": ""})())
    monkeypatch.setattr(manifest_module, "venv_python", lambda root: root / ".venv/bin/python")
    monkeypatch.setattr(manifest_module, "verify_python", lambda *_args: None)
    monkeypatch.setattr(manifest_module, "ensure_initialized_submodules", lambda _root: calls.append("checked"))

    resolve_project(
        project_dir=project_dir, workspace_root=tmp_path / "worktrees", repository=None,
        ref="HEAD", allow_dirty=False, validate_submodules=False,
    )

    assert calls == []


def test_resolve_project_can_validate_submodules_explicitly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    calls: list[str] = []

    monkeypatch.setattr(manifest_module, "git_output", lambda _root, *args: {
        ("rev-parse", "--is-inside-work-tree"): "true",
        ("rev-parse", "HEAD"): "a" * 40,
        ("status", "--porcelain"): "",
    }[args])
    monkeypatch.setattr(manifest_module, "run", lambda *_args, **_kwargs: type("Result", (), {"stdout": ""})())
    monkeypatch.setattr(manifest_module, "venv_python", lambda root: root / ".venv/bin/python")
    monkeypatch.setattr(manifest_module, "verify_python", lambda *_args: None)
    monkeypatch.setattr(manifest_module, "ensure_initialized_submodules", lambda _root: calls.append("checked"))

    resolve_project(
        project_dir=project_dir, workspace_root=tmp_path / "worktrees", repository=None,
        ref="HEAD", allow_dirty=False, validate_submodules=True,
    )

    assert calls == ["checked"]


def test_matrix_digest_is_order_stable_for_mapping_keys() -> None:
    assert matrix_digest({"a": 1, "b": [2]}) == matrix_digest({"b": [2], "a": 1})


def test_inventory_detects_tampering(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = artifacts / "result.json"
    target.write_text("before", encoding="utf-8")
    inventory = artifact_inventory(artifacts)
    target.write_text("after", encoding="utf-8")
    assert verify_inventory(artifacts, inventory) == ["artifact size changed: result.json"]


def test_reuse_accepts_only_exact_complete_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = project(tmp_path / "project")
    matrix = [{"runner": "demo", "framework": "sglang", "recipe-fingerprint": "f" * 64}]
    source = successful_archive(tmp_path, current, matrix)
    monkeypatch.setattr("py_workflow.reuse.validate_artifacts", lambda *_: None)
    decision = decide_reuse(
        mode="required", explicit_source=source, archive_root=tmp_path, project=current,
        matrix_sha256=matrix_digest(matrix), recipe_fingerprints=["f" * 64], options={
            "config_keys": ["demo"], "conc": [], "mode": "sweep-enabled", "all_evals": False,
            "evals_only": False, "no_evals": False, "agentx_fast": False,
        },
    )
    assert decision.enabled is True
    assert decision.source == source


def test_reuse_rejects_other_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_project = project(tmp_path / "project", "a" * 40)
    current = project(tmp_path / "current", "b" * 40)
    matrix = [{"runner": "demo", "framework": "sglang", "recipe-fingerprint": "f" * 64}]
    source = successful_archive(tmp_path, source_project, matrix)
    monkeypatch.setattr("py_workflow.reuse.validate_artifacts", lambda *_: None)
    with pytest.raises(WorkflowError, match="source commit SHA differs"):
        decide_reuse(
            mode="required", explicit_source=source, archive_root=tmp_path, project=current,
            matrix_sha256=matrix_digest(matrix), recipe_fingerprints=["f" * 64], options={
                "config_keys": ["demo"], "conc": [], "mode": "sweep-enabled", "all_evals": False,
                "evals_only": False, "no_evals": False, "agentx_fast": False,
            },
        )


def test_reuse_rejects_partial_job_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = project(tmp_path / "project")
    matrix = [
        {"runner": "demo", "framework": "sglang", "recipe-fingerprint": "f" * 64},
        {"runner": "demo", "framework": "sglang", "recipe-fingerprint": "e" * 64},
    ]
    source = successful_archive(tmp_path, current, matrix)
    manifest_path = source / "archive_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["jobs"] = manifest["jobs"][:1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr("py_workflow.reuse.validate_artifacts", lambda *_: None)
    with pytest.raises(WorkflowError, match="do not exactly cover"):
        decide_reuse(
            mode="required", explicit_source=source, archive_root=tmp_path, project=current,
            matrix_sha256=matrix_digest(matrix), recipe_fingerprints=["e" * 64, "f" * 64], options={
                "config_keys": ["demo"], "conc": [], "mode": "sweep-enabled", "all_evals": False,
                "evals_only": False, "no_evals": False, "agentx_fast": False,
            },
        )


def test_sweep_enabled_is_default_and_no_evals_is_recorded() -> None:
    args = parser().parse_args([
        "--project-dir", ".", "--config-key", "demo", "--archive-root", "archive", "--no-evals",
    ])
    assert args.mode == "sweep-enabled"
    assert options(args)["no_evals"] is True


def test_topology_adapters_reject_wrong_matrix_shape(tmp_path: Path) -> None:
    current = project(tmp_path)
    with pytest.raises(WorkflowError, match="single-node adapter"):
        execute_single_node(
            {"prefill": {}, "decode": {}}, project=current, jobs_root=tmp_path / "jobs",
            artifact_root=tmp_path / "artifacts",
        )
    with pytest.raises(WorkflowError, match="multi-node adapter"):
        execute_multinode(
            {"runner": "demo"}, project=current, jobs_root=tmp_path / "jobs",
            artifact_root=tmp_path / "artifacts",
        )


def test_success_rate_ignores_skipped_jobs() -> None:
    assert calculate([
        {"status": "success", "entry": {"runner": "dcu"}},
        {"status": "failure", "entry": {"runner": "dcu"}},
        {"status": "skipped", "entry": {"runner": "dcu"}},
    ]) == {"dcu": {"n_success": 1, "total": 2}}
