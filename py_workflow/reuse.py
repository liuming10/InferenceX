"""Fail-closed local replacement for the GitHub reusable-sweep gate."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from py_workflow import MANIFEST_SCHEMA_VERSION
from py_workflow.manifest import Project, WorkflowError, canonical_json, verify_inventory


@dataclass(frozen=True)
class ReuseDecision:
    """The outcome of discovering a compatible local archive."""

    enabled: bool
    source: Path | None
    reason: str


def load_manifest(path: Path) -> dict[str, Any]:
    """Load one archive manifest, rejecting invalid JSON structures."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowError(f"cannot read archive manifest {path}: {error}") from error
    if not isinstance(value, dict):
        raise WorkflowError(f"archive manifest must be an object: {path}")
    return value


def candidate_archives(archive_root: Path) -> list[Path]:
    """Return published local archives newest first, excluding staging trees."""
    candidates = [
        path.parent for path in archive_root.rglob("archive_manifest.json")
        if ".staging-" not in str(path.parent)
    ] if archive_root.is_dir() else []
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def compatible(
    archive: Path, manifest: dict[str, Any], *, project: Project,
    matrix_sha256: str, recipe_fingerprints: list[str], options: dict[str, Any],
) -> str | None:
    """Return an incompatibility reason, or None for a safe reuse source."""
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        return "unsupported archive manifest schema"
    if manifest.get("status") != "success":
        return "archive did not complete successfully"
    source = manifest.get("source")
    if not isinstance(source, dict):
        return "archive has no source identity"
    if source.get("head_sha") != project.head_sha:
        return "source commit SHA differs"
    if source.get("remote", "") != project.remote:
        return "source repository differs"
    if manifest.get("matrix_sha256") != matrix_sha256:
        return "matrix digest differs"
    if sorted(manifest.get("recipe_fingerprints", [])) != sorted(recipe_fingerprints):
        return "recipe fingerprints differ"
    old_options = manifest.get("options")
    for key in ("no_evals", "evals_only", "all_evals", "agentx_fast", "mode", "config_keys", "conc"):
        if not isinstance(old_options, dict) or old_options.get(key) != options.get(key):
            return f"execution option differs: {key}"
    if options.get("evals_only") or options.get("agentx_fast"):
        return "evals-only and AgentX-fast runs are not eligible for reuse"
    artifacts = archive / "artifacts"
    inventory = manifest.get("artifacts")
    if not artifacts.is_dir() or not isinstance(inventory, list) or not inventory:
        return "archive has no complete artifact inventory"
    errors = verify_inventory(artifacts, inventory)
    if errors:
        return errors[0]
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list):
        return "archive has no job records"
    failed = [job for job in jobs if not isinstance(job, dict) or job.get("status") != "success"]
    if failed:
        return "archive contains unsuccessful job records"
    source_entries = sorted(
        canonical_json(job.get("entry"))
        for job in jobs if isinstance(job.get("entry"), dict)
    )
    requested_entries = sorted(
        canonical_json(entry) for entry in manifest.get("matrix", [])
    )
    if source_entries != requested_entries:
        return "archive job records do not exactly cover its planned matrix"
    return None


def validate_artifacts(project: Project, archive: Path) -> str | None:
    """Apply the existing artifact-level validator to the local artifact layout."""
    completed = subprocess.run(
        [str(project.python), "-m", "infx.workflows.validate_reusable_sweep_artifacts",
         "--artifacts-dir", str(archive / "artifacts")],
        cwd=project.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        return completed.stderr.strip() or completed.stdout.strip() or "artifact validation failed"
    return None


def decide_reuse(
    *, mode: str, explicit_source: Path | None, archive_root: Path, project: Project,
    matrix_sha256: str, recipe_fingerprints: list[str], options: dict[str, Any],
) -> ReuseDecision:
    """Find the first complete exact local reuse source or return a reason."""
    if mode == "off":
        return ReuseDecision(False, None, "reuse disabled")
    candidates = [explicit_source] if explicit_source else candidate_archives(archive_root)
    if not candidates:
        reason = "explicit reuse archive was not supplied" if explicit_source else "no local archives found"
        if mode == "required":
            raise WorkflowError(f"reuse required but {reason}")
        return ReuseDecision(False, None, reason)
    reasons: list[str] = []
    for archive in candidates:
        if archive is None:
            continue
        manifest_path = archive / "archive_manifest.json"
        try:
            manifest = load_manifest(manifest_path)
        except WorkflowError as error:
            reasons.append(str(error))
            continue
        reason = compatible(
            archive, manifest, project=project, matrix_sha256=matrix_sha256,
            recipe_fingerprints=recipe_fingerprints, options=options,
        )
        if reason is None:
            reason = validate_artifacts(project, archive)
        if reason is None:
            return ReuseDecision(True, archive, "exact successful local archive")
        reasons.append(f"{archive}: {reason}")
    reason = reasons[0] if reasons else "no compatible local archive"
    if mode == "required":
        raise WorkflowError(f"reuse required but no candidate qualified: {reason}")
    return ReuseDecision(False, None, reason)


def stage_reused_artifacts(source: Path, destination: Path) -> None:
    """Copy validated source artifacts into a new archive staging directory."""
    shutil.copytree(source / "artifacts", destination / "artifacts", dirs_exist_ok=False)
