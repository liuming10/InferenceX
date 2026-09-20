# Restore full `conc-list` execution in simple workflow

## Verified behavior
- The simple workflow executes generated matrix entries sequentially in `py_workflow_simple/run_sweep.py`.
- The shared generator expands `conc-list: [4, 8]` to two entries (`conc=4`, `conc=8`) when no trimming is requested.
- The simple workflow currently defaults `--mode` to `sweep-enabled`; shared matrix generation translates only that mode into `--trim-conc`.
- Therefore the current default silently produces only `conc=4` for `[4, 8]`; a container dry-run proved this.
- Explicit `--mode full-sweep-enabled` produced both entries, in list order, in a second container dry-run.

## Changes
1. Change `py_workflow_simple.run_sweep` default `--mode` from `sweep-enabled` to `full-sweep-enabled`, so a declared `conc-list` is honored by default.
2. Add parser help that documents the exact semantics: the default emits every selected concurrency point; explicit `--mode sweep-enabled` remains available for the legacy trimmed single-point behavior.
3. Document the serial execution behavior and compatibility mode in `py_workflow_simple/README.md`.
4. Run local syntax/tests, sync the two changed files to the container with expected-SHA atomic uploads, and run a container dry-run with a temporary `[4, 8]` configuration copy. Verify the plan contains the ordered entries `conc=4` and `conc=8`, then restore the unchanged checked-in container configuration.

## Non-goals
- No endpoint benchmark will be started.
- No model, scheduler, Slurm, Enroot, SGLang, or Mooncake process will be started.
- No parallel execution will be introduced; the existing sequential loop remains authoritative.
