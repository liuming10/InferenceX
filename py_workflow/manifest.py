"""Manifest, source-resolution, and artifact-integrity primitives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from py_workflow import MANIFEST_SCHEMA_VERSION


class WorkflowError(RuntimeError):
    """Raise when the local workflow cannot safely continue."""


@dataclass(frozen=True)
class Project:
    """A resolved source tree and its pre-existing Python interpreter."""

    root: Path
    python: Path
    source_mode: str
    head_sha: str
    remote: str
    dirty: bool


def canonical_json(value: Any) -> str:
    """Return a stable JSON representation suitable for hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_bytes(value: bytes) -> str:
    """Hash bytes using SHA-256."""
    return hashlib.sha256(value).hexdigest()


def matrix_digest(matrix: Any) -> str:
    """Return the stable SHA-256 identity of a normalized matrix."""
    return sha256_bytes(canonical_json(matrix).encode("utf-8"))


def file_sha256(path: Path) -> str:
    """Hash one regular file without loading it entirely into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Execute a local command with captured text diagnostics."""
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=check)


def git_output(root: Path, *args: str) -> str:
    """Read a Git value from a source tree."""
    completed = run(["git", *args], cwd=root)
    return completed.stdout.strip()


def venv_python(root: Path) -> Path:
    """Return the already-installed project virtual-environment interpreter."""
    candidate = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not candidate.is_file():
        raise WorkflowError(
            f"--project-dir requires its preinstalled virtual environment at {candidate}; "
            "the local workflow never creates or modifies .venv"
        )
    return candidate


def verify_python(project_root: Path, python: Path) -> None:
    """Ensure the selected project environment imports project and AIPerf modules."""
    probe = (
        "import pathlib, sys; "
        f"root=pathlib.Path({str(project_root)!r}); "
        "sys.path.insert(0, str(root)); "
        "import infx; import aiperf"
    )
    completed = subprocess.run(
        [str(python), "-c", probe], cwd=project_root, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise WorkflowError(
            f"project virtual environment cannot import InferenceX and AIPerf: {message}"
        )


def ensure_initialized_submodules(root: Path) -> None:
    """Reject a supplied project whose declared submodules are absent."""
    modules = root / ".gitmodules"
    if not modules.is_file():
        return
    completed = run(["git", "submodule", "status", "--recursive"], cwd=root)
    missing = [line for line in completed.stdout.splitlines() if line.startswith("-")]
    if missing:
        raise WorkflowError(
            "--project-dir assumes subprojects are already present; missing submodule(s): "
            + ", ".join(line[1:].strip() for line in missing)
        )


def supplied_project(
    path: Path, *, allow_dirty: bool, validate_submodules: bool = True,
) -> Project:
    """Resolve a user-supplied immutable project tree."""
    root = path.resolve()
    if not root.is_dir():
        raise WorkflowError(f"--project-dir does not exist: {root}")
    try:
        if git_output(root, "rev-parse", "--is-inside-work-tree") != "true":
            raise WorkflowError(f"--project-dir is not a Git working tree: {root}")
        head_sha = git_output(root, "rev-parse", "HEAD")
        status = git_output(root, "status", "--porcelain")
    except subprocess.CalledProcessError as error:
        raise WorkflowError(error.stderr.strip() or f"cannot inspect Git project {root}") from error
    remote_result = run(
        ["git", "config", "--get", "remote.origin.url"], cwd=root, check=False,
    )
    remote = remote_result.stdout.strip()
    dirty = bool(status)
    if dirty and not allow_dirty:
        raise WorkflowError(
            "--project-dir has tracked or untracked changes; pass --allow-dirty-project "
            "only when intentionally benchmarking that exact modified tree"
        )
    if validate_submodules:
        ensure_initialized_submodules(root)
    python = venv_python(root)
    verify_python(root, python)
    return Project(root, python, "project-dir", head_sha, remote, dirty)


def managed_project(
    workspace_root: Path, repository: str, ref: str,
) -> Project:
    """Create or update the executor-owned source checkout and its submodules."""
    identity = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:16]
    root = workspace_root / identity
    workspace_root.mkdir(parents=True, exist_ok=True)
    if not root.exists():
        run(["git", "clone", "--recurse-submodules", repository, str(root)], cwd=workspace_root)
    if not (root / ".git").exists():
        raise WorkflowError(f"managed source path is not a Git work tree: {root}")
    run(["git", "fetch", "--tags", "--prune", "origin"], cwd=root)
    run(["git", "checkout", "--detach", ref], cwd=root)
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=root)
    python = venv_python(root)
    verify_python(root, python)
    return Project(
        root=root,
        python=python,
        source_mode="managed-clone",
        head_sha=git_output(root, "rev-parse", "HEAD"),
        remote=git_output(root, "config", "--get", "remote.origin.url"),
        dirty=False,
    )


def resolve_project(
    *, project_dir: Path | None, workspace_root: Path, repository: str | None,
    ref: str, allow_dirty: bool, validate_submodules: bool = False,
) -> Project:
    """Resolve a supplied project or managed checkout with optional Git submodule checks."""
    if project_dir is not None:
        if repository is not None:
            raise WorkflowError("--project-dir and --repository cannot be used together")
        return supplied_project(
            project_dir, allow_dirty=allow_dirty, validate_submodules=validate_submodules,
        )
    if not repository:
        raise WorkflowError("supply --project-dir or --repository")
    return managed_project(workspace_root, repository, ref)


def artifact_inventory(root: Path) -> list[dict[str, Any]]:
    """Return checksummed metadata for every regular artifact beneath root."""
    inventory = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            inventory.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            })
    return inventory


def verify_inventory(root: Path, inventory: list[dict[str, Any]]) -> list[str]:
    """Return integrity errors for a recorded artifact inventory."""
    errors: list[str] = []
    expected_paths = set()
    for entry in inventory:
        relative = entry.get("path")
        if not isinstance(relative, str):
            errors.append("inventory contains a non-string path")
            continue
        expected_paths.add(relative)
        path = root / relative
        if not path.is_file():
            errors.append(f"missing artifact: {relative}")
            continue
        if path.stat().st_size != entry.get("size"):
            errors.append(f"artifact size changed: {relative}")
            continue
        if file_sha256(path) != entry.get("sha256"):
            errors.append(f"artifact checksum changed: {relative}")
    actual_paths = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    }
    extras = actual_paths - expected_paths
    if extras:
        errors.append("unrecorded artifact(s): " + ", ".join(sorted(extras)[:10]))
    return errors


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, value: Any) -> None:
    """Write a JSON document atomically in its final directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def make_manifest(*, project: Project, run_id: str, matrix: Any, options: dict[str, Any]) -> dict[str, Any]:
    """Create the initial versioned manifest for one local execution."""
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "status": "running",
        "run_id": run_id,
        "created_at": utc_now(),
        "completed_at": None,
        "source": {
            "mode": project.source_mode,
            "project_root": str(project.root),
            "remote": project.remote,
            "head_sha": project.head_sha,
            "dirty": project.dirty,
        },
        "options": options,
        "matrix": matrix,
        "matrix_sha256": matrix_digest(matrix),
        "recipe_fingerprints": sorted(
            str(entry["recipe-fingerprint"])
            for entry in flatten_matrix(matrix)
            if entry.get("recipe-fingerprint")
        ),
        "jobs": [],
        "artifacts": [],
        "aggregates": {},
        "reuse": {"enabled": False, "source": None, "reason": "not requested"},
    }


def flatten_matrix(value: Any) -> list[dict[str, Any]]:
    """Return all matrix job dictionaries from nested collection structures."""
    if isinstance(value, list):
        return [item for entry in value for item in flatten_matrix(entry)]
    if isinstance(value, dict):
        if "runner" in value and "framework" in value:
            return [value]
        return [item for entry in value.values() for item in flatten_matrix(entry)]
    return []


def publish_directory(staging: Path, final: Path) -> None:
    """Atomically publish a completed archive without overwriting prior output."""
    if final.exists():
        raise WorkflowError(f"archive destination already exists: {final}")
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(final)
