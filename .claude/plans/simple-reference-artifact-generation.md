# Align AgentX-only artifact generation with the real sweep workflow

## Scope

Align the simple endpoint-only workflow with the actual generation and upload chain:

`run-sweep.yml` → `benchmark-tmpl.yml` → `benchmarks/benchmark_lib.sh` → `power_adapter`.

The implementation preserves the explicit constraint that the simple workflow never starts SGLang, Mooncake, a GPU telemetry monitor, or a scheduler job. It must not fabricate artifacts that require those processes.

## Changes

1. **Write the actual AIPerf replay command at the CI location.**
   - In `py_workflow_simple/agentx.py`, write the already-constructed and redaction-safe command to `job_dir/results/benchmark_command.txt` immediately before AIPerf starts.
   - This mirrors `run_agentic_replay_and_write_outputs`, which writes `$REPLAY_CMD` to `$RESULT_DIR/benchmark_command.txt` before invoking it.
   - Remove the redundant job-root copy so `stage_job_output()` projects the file to `artifacts/agentic_<stem>/benchmark_command.txt` via the real `results/**` upload rule.

2. **Reproduce the truthful single-node AgentX power lifecycle inputs.**
   - Before AIPerf starts, write `results/agentic_power_timezone_offset.txt` using the local UTC offset in `±HHMM` form, as the reference shell function does before replay when AgentX power handling is enabled.
   - Keep the existing post-processing `power_adapter` invocation, but pass the reference GPU shape (`tp * pp * pcp-size`) and propagate `--require-power` when the selected row requires it.
   - This lets the existing adapter generate the real `agentic_power_window.json` and `power_validation.json` from the AIPerf profile artifacts. It does not create `gpu_metrics.csv`, because no GPU monitor was started.
   - Extend `py_workflow/benchmark.py::stage_job_output()` so the `power_audit_<stem>` projection includes `results/agentic_power_timezone_offset.txt`, matching the exact `benchmark-tmpl.yml` upload list.

3. **Follow the archive workflow’s event provenance rule.**
   - Add an optional `--github-event-path` input to the simple runner.
   - If supplied, validate it is a regular readable file and copy its bytes unchanged to `github_event.json`, reproducing the reference workflow’s `cp "$GITHUB_EVENT_PATH" ...` behavior.
   - If no genuine webhook payload is supplied, do not create `github_event.json`; remove the current synthetic event-schema generator rather than presenting local identity values as an actual GitHub event.
   - Retain GitHub identity values in `archive_manifest.json` and runtime context, where they are already explicit inputs rather than webhook claims.

4. **Mirror the real changelog artifact execution condition.**
   - Generate `artifacts/changelog-metadata/changelog_metadata.json` after setup for every simple run, because `upload-changelog-metadata` runs whenever CI setup succeeds and its script writes `matrix.get('changelog_metadata', {})`.
   - Use actual generated metadata when available; otherwise write the same empty JSON object that the CI script produces for an empty metadata field.
   - Mark `upload-changelog-metadata` successful in the reference-shaped archive manifest once this artifact is staged.

5. **Remove invented service logs.**
   - Delete the simple-runner code that writes placeholder `server.log`, `mooncake_master.log`, and `mooncake_client.log`.
   - `benchmark.log` remains a genuine AIPerf log and continues to be projected by the CI-equivalent raw-results and server-log staging rules.
   - Intentionally leave `sglang_command.txt`, GPU monitor files, Mooncake logs, and service-start logs absent because their reference producers did not run in this workflow.

6. **Test and document the contract.**
   - Add focused tests for command placement, timezone format, optional raw GitHub event copying/omission, empty changelog artifact generation, manifest job status, expected GPU count/strict power argument, and the new power-audit timezone staging path.
   - Update `py_workflow_simple/README.md` to distinguish CI-compatible artifacts generated from real simple execution from intentionally absent model-service lifecycle artifacts.
   - Run the focused simple-workflow and staging tests, then synchronize the changed files to the container and run the same authoritative container test set. A full benchmark is only needed after code validation if artifact output needs an end-to-end confirmation.
