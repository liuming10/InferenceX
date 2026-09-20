# Add runtime progress logging to `py_workflow_simple`

## Goal
Make AgentX-only runs observable in the terminal without leaking environment secrets or duplicating opaque implementation detail.

## Changes
1. Add a small timestamped console-status helper in `py_workflow_simple.run_sweep`.
   - Log project identity/dirty state, resolved AIPerf executable, matrix job count, execution/run identity, staging location, dry-run completion, each job start/result, power processing, collection, publish, and retained staging archive on failure.
   - Do not print environment dumps, metadata snapshots, tokenizers beyond an existing command representation, or secrets.

2. Change `py_workflow_simple.agentx.run_agentx` from `subprocess.run(capture_output=True)` to a merged-stream `Popen` loop.
   - Stream AIPerf stdout/stderr live to the terminal, prefixed as AgentX output.
   - Write exactly the same stream to `benchmark.log`.
   - Preserve the return value shape expected by the caller.

3. Log post-processing commands and success/failure state from `process_outputs` without streaming unrelated tool output unless an error occurs.

4. Add focused tests for status formatting/streaming with mocked subprocess behavior, and retain the existing workflow test suite.

## Validation
Run compilation, the `py_workflow` tests, CLI help, a container dry-run, and `git diff --check`.
