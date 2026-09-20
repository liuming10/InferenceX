"""Safety checks for the AgentX-only local workflow."""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from py_workflow.manifest import WorkflowError
from py_workflow_simple import agentx, run_sweep


def test_hf_cache_dir_defaults_to_shared_launcher_cache() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive", "--server-url", "url",
    ])

    assert run_sweep.resolve_hf_cache_dir(args) == Path("/ai_data/datasets/huggingface")


def test_hf_cache_dir_requires_readable_hub_and_writable_datasets(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    (cache / "hub").mkdir(parents=True)
    (cache / "datasets").mkdir()

    run_sweep.validate_hf_cache_dir(cache)

    (cache / "hub").rmdir()
    with pytest.raises(WorkflowError, match="hub cache is not readable"):
        run_sweep.validate_hf_cache_dir(cache)


def test_hf_cache_dir_accepts_explicit_path() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive", "--server-url", "url",
        "--hf-cache-dir", "dataset-cache",
    ])

    assert run_sweep.resolve_hf_cache_dir(args) == Path("dataset-cache").resolve()


def test_github_ref_and_repository_default_to_reference_identity() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url",
    ])

    assert args.github_ref == "refs/heads/main"
    assert args.github_repository == "liuming10/InferenceX"


def test_github_ref_and_repository_accept_explicit_overrides() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url", "--github-ref", "refs/heads/release",
        "--github-repository", "example/benchmark-repo",
    ])

    assert args.github_ref == "refs/heads/release"
    assert args.github_repository == "example/benchmark-repo"


    common = [
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive", "--server-url", "url",
    ]

    assert run_sweep.parser().parse_args(common).dataset_configuration_timeout == 900
    assert run_sweep.parser().parse_args([
        *common, "--dataset-configuration-timeout", "1200",
    ]).dataset_configuration_timeout == 1200


def test_generated_run_context_uses_random_github_sha_when_omitted() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url",
    ])

    first = run_sweep.generated_run_context(args, "a" * 40)
    second = run_sweep.generated_run_context(args, "a" * 40)

    assert re.fullmatch(r"[0-9a-f]{40}", first["sha"])
    assert re.fullmatch(r"[0-9a-f]{40}", second["sha"])
    assert first["sha"] != second["sha"]
    assert first["sha"] != "a" * 40


def test_generated_run_context_preserves_explicit_github_sha() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url", "--github-sha", "b" * 40,
    ])

    assert run_sweep.generated_run_context(args, "a" * 40)["sha"] == "b" * 40


def test_generated_run_context_uses_random_github_run_id_when_omitted() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url",
    ])

    first = run_sweep.generated_run_context(args, "a" * 40)
    second = run_sweep.generated_run_context(args, "a" * 40)

    assert isinstance(first["run_id"], int)
    assert first["run_id"] > 0
    assert second["run_id"] > 0
    assert first["run_id"] != second["run_id"]


def test_generated_run_context_preserves_explicit_github_run_id() -> None:
    args = run_sweep.parser().parse_args([
        "--project-dir", "project", "--config-key", "demo", "--archive-root", "archive",
        "--server-url", "url", "--github-run-id", "35295109354",
    ])

    assert run_sweep.generated_run_context(args, "a" * 40)["run_id"] == 35295109354


def test_agentx_runtime_environment_is_offline_and_extends_configuration_timeout() -> None:
    cache = Path("/cache")
    assert agentx.runtime_environment_overrides(cache, 900) == {
        "HF_HOME": str(cache),
        "HF_DATASETS_CACHE": str(cache / "datasets"),
        "HF_HUB_CACHE": str(cache / "hub"),
        "HF_HUB_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "AIPERF_DATASET_CONFIGURATION_TIMEOUT": "900",
        "AIPERF_SERVICE_PROFILE_CONFIGURE_TIMEOUT": "900",
        "AIPERF_UI_REALTIME_METRICS_ENABLED": "true",
    }


def test_capture_endpoint_metrics_preserves_body_and_framework_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _size: int) -> bytes:
            if hasattr(self, "read_once"):
                return b""
            self.read_once = True
            return b"# HELP vllm_requests_total\nvalue 7\n"

    monkeypatch.setattr(agentx.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    record = agentx.capture_endpoint_metrics(
        metrics_url="http://endpoint/metrics", result_dir=tmp_path,
        framework="vLLM", phase="before-benchmark",
    )

    assert record["status"] == "success"
    output = tmp_path / record["path"]
    assert output.name.startswith("vllm_metrics_")
    assert output.read_bytes() == b"# HELP vllm_requests_total\nvalue 7\n"


def test_capture_endpoint_metrics_failure_does_not_leave_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise OSError("endpoint unavailable")

    monkeypatch.setattr(agentx.urllib.request, "urlopen", fail)
    record = agentx.capture_endpoint_metrics(
        metrics_url="http://endpoint/metrics", result_dir=tmp_path,
        framework="sglang", phase="after-benchmark",
    )

    assert record["status"] == "failure"
    assert list(tmp_path.iterdir()) == []


def test_capture_endpoint_metrics_skips_missing_url(tmp_path: Path) -> None:
    record = agentx.capture_endpoint_metrics(
        metrics_url=None, result_dir=tmp_path,
        framework="sglang", phase="before-benchmark",
    )

    assert record["status"] == "skipped"
    assert list(tmp_path.iterdir()) == []

def test_reference_archive_manifest_matches_reference_schema() -> None:
    identity = {
        "run_id": 35311504420, "run_attempt": 1, "event_name": "workflow_dispatch",
        "ref": "refs/heads/main", "repository": "liuming10/InferenceX",
        "sha": "0123456789abcdef0123456789abcdef01234567",
    }
    manifest = run_sweep.reference_archive_manifest(
        identity=identity, project=object(), matrix=[{"runner": "r", "framework": "sglang"}],
        jobs=[{"status": "success"}], status="success", dry_run=False,
        results_collected=True, evals_collected=False, success_rate_calculated=True,
        changelog_metadata_uploaded=True,
    )

    assert set(manifest) == {
        "archive_created_at", "current_run_attempt", "current_run_id", "event_name",
        "job_results", "ref", "repository", "sha", "source_reused", "source_run_id",
    }
    assert set(manifest["job_results"]) == set(run_sweep.REFERENCE_JOB_NAMES)
    assert all(set(value) == {"outputs", "result"} for value in manifest["job_results"].values())
    assert manifest["job_results"]["sweep-agentic"]["result"] == "success"
    assert manifest["job_results"]["collect-evals"]["result"] == "skipped"
    assert manifest["job_results"]["upload-changelog-metadata"]["result"] == "success"


def test_stage_reference_placeholder_files_are_empty(tmp_path: Path) -> None:
    run_sweep.write_reference_placeholder_files(tmp_path)

    assert {
        path.name: path.read_bytes()
        for path in tmp_path.iterdir()
    } == {
        "server.log": b"",
        "mooncake_master.log": b"",
        "mooncake_client.log": b"",
        "sglang_command.txt": b"",
    }


def test_power_timezone_offset_matches_reference_format(tmp_path: Path) -> None:
    path = agentx.write_agentx_power_timezone_offset(tmp_path)

    assert path.name == "agentic_power_timezone_offset.txt"
    assert re.fullmatch(r"[+-]\d{4}\n", path.read_text(encoding="utf-8"))


def test_stage_changelog_metadata_writes_ci_empty_payload(tmp_path: Path) -> None:
    path = run_sweep.stage_changelog_metadata(tmp_path)

    assert path == tmp_path / "changelog-metadata" / "changelog_metadata.json"
    assert path.read_text(encoding="utf-8") == "{}\n"


def test_copy_github_event_preserves_supplied_webhook_bytes(tmp_path: Path) -> None:
    source = tmp_path / "event.json"
    source.write_bytes(b'{"action":"synchronize"}\n')
    staging = tmp_path / "staging"
    staging.mkdir()

    run_sweep.copy_github_event(source, staging)

    assert (staging / "github_event.json").read_bytes() == source.read_bytes()


def test_copy_github_event_omits_unsupplied_webhook(tmp_path: Path) -> None:
    run_sweep.copy_github_event(None, tmp_path)

    assert not (tmp_path / "github_event.json").exists()


def test_unique_result_stems_accept_distinct_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_sweep, "result_filename", lambda _project, entry: f"result-conc{entry['conc']}")

    run_sweep.validate_unique_result_stems(object(), [{"conc": 4}, {"conc": 8}, {"conc": 16}])


def test_unique_result_stems_reject_colliding_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_sweep, "result_filename", lambda _project, _entry: "same-result")

    with pytest.raises(WorkflowError, match=r"same-result \(concurrency=\[4, 8\]\)"):
        run_sweep.validate_unique_result_stems(object(), [{"conc": 4}, {"conc": 8}])
