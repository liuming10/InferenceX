# Preserve AIPerf console log in final artifact

## Goal
Ensure the live AIPerf console output remains available both in the per-job staging directory and in the `results/` projection that is copied into the final `agentic_<stem>` artifact.

## Findings
- `run_agentx()` currently streams merged standard output/error into `jobs/<stem>/benchmark.log`.
- The AgentX artifact staging logic copies only files below `jobs/<stem>/results/` into `artifacts/agentic_<stem>/`.
- Consequently, on successful runs the root-level `benchmark.log` does not enter the final AgentX artifact.

## Implementation
1. In `py_workflow_simple/agentx.py`, define both `job_dir / "benchmark.log"` and `result_dir / "benchmark.log"` as log targets after the result directory exists.
2. During the existing streaming `Popen` loop, write and flush every merged AIPerf output line to both targets before printing it live.
3. Update the lifecycle message to show both retained log locations, while preserving the familiar root-log path for failure diagnostics.
4. Add or update focused tests if an appropriate simple-workflow test location exists; otherwise validate syntax and execute the established container dry-run after synchronization.
5. Synchronize only the changed file to `/stortest/lium_space/agentX/InferenceX_lm/py_workflow_simple/agentx.py` through the existing SHA-protected remote-update process, then run a non-benchmark dry run to verify no model, scheduler, or benchmark process is started.

## Resulting paths
- During any run: `jobs/<stem>/benchmark.log`.
- After successful artifact staging: `artifacts/agentic_<stem>/benchmark.log`.
- AIPerf’s own structured log remains `results/aiperf_artifacts/logs/aiperf.log`, projected to `artifacts/agentic_<stem>/aiperf_artifacts/logs/aiperf.log`.
