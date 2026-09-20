# Local Python Sweep Executor

[中文](./README_zh.md)

`py_workflow` runs selected InferenceX configurations locally without GitHub Actions. It retains the existing matrix generator, runner launchers, Enroot/Slurm behavior, result processors, AgentX validation, aggregation, dated archival, and a fail-closed local reuse gate.

## Source selection

Exactly one source mode is required.

### Use an already downloaded project

```bash
<project>/.venv/bin/python -m py_workflow.run_sweep \
  --project-dir <project> \
  --config-key dsv41flash-fp4-mi355x-vllm-agentic-dspark \
  --archive-root /stortest/lium_space/agentX/InferenceX_result
```

`--project-dir` is read-only with respect to Git and the virtual environment:

- The project, every subproject/submodule, and `<project>/.venv` must already exist.
- The executor does not clone, fetch, clean, checkout, reset, initialize submodules, install packages, or modify `.venv`.
- A dirty project is rejected unless `--allow-dirty-project` is explicit.
- The project `.venv` is the only Python environment used for matrix generation, AIPerf, validation, and collection.

### Use an executor-managed clone

```bash
python -m py_workflow.run_sweep \
  --repository https://example.invalid/InferenceX.git \
  --ref main \
  --config-key dsv41flash-fp4-mi355x-vllm-agentic-dspark \
  --archive-root /stortest/lium_space/agentX/InferenceX_result
```

The managed-clone mode can recursively clone/fetch/update only its own copy below `<archive-root>/.worktrees`. Its project must still provide a ready `.venv`; the executor never installs dependencies.

## Configuration and modes

`--config-key` is required and repeatable. It accepts the same exact keys and glob patterns as `infx.matrix.generate test-config`; optional `--conc` restricts the generated concurrency points. No GitHub event, path filter, PR label, or changelog diff is required.

`--mode sweep-enabled` is the default and corresponds to the former `sweep-enabled` minimum-concurrency sweep. Full modes preserve canary and fail-fast selection. `--no-evals` maps directly to the workflow input `no-evals: true` and passes `--no-evals` to the existing matrix generator; it is mutually exclusive with `--evals-only` and cannot be combined with `--all-evals`. Current `--max-parallel` must remain `1`: existing launchers write some outputs in the project root, so concurrent jobs would corrupt one another.

## Local reuse gate

Use `--reuse auto`, `--reuse required`, or `--reuse-from <archive>`. A source is accepted only if it is a successful versioned local archive with all of the following exact matches:

- repository identity and resolved Git SHA;
- generated matrix digest and recipe fingerprint set;
- compatibility-relevant execution options;
- complete artifact inventory with unchanged file sizes and SHA-256 checksums;
- all successful job records and the existing reusable-artifact validator.

The gate rejects old GitHub Actions archives because they do not contain this integrity contract. It also rejects automatic reuse of `--evals-only` or `--agentx-fast` sources. A reuse creates a new dated archive whose manifest records the validated source.

## Archives and optional comparison

Successful runs publish atomically under the supplied archive root. The `artifacts/` subtree mirrors the GitHub Actions artifact names: `bmk_<stem>/`, `bmk_agentic_<stem>/`, `agentic_<stem>/`, `server_logs_<stem>/` (or the `eval_` variant), `gpu_metrics_<stem>/` when telemetry files exist, `power_audit_<stem>/`, `eval_<stem>/`, and `changelog-metadata/` when the matrix provides changelog metadata. AgentX raw files retain the workflow exclusions for `inputs.json` and `profile_export_raw.jsonl`. Local bookkeeping remains outside `artifacts/`: `plan.json`, `archive_manifest.json`, `aggregates/`, and `run_stats.json`. Failed runs remain in a `.staging-<pid>` directory and are never reusable.

`--compare-results` is off by default and requires `DATABASE_URL`; it is intended only for the existing read-only result comparison. No GitHub API, Actions artifact transfer, PR comment, GitHub secret, or hosted runner is used.
