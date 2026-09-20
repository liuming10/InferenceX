# Raise AgentX dataset-configuration timeout

## Goal

Allow the AgentX-only simple workflow to finish the initial reconstruction of the cached 393-trace Weka dataset without changing the benchmark scope, launching a model, or changing scheduler behavior.

## Implementation

1. Add `--dataset-configuration-timeout` to `py_workflow_simple/run_sweep.py`, with a default of 900 seconds and positive-value validation through argparse.
2. Pass the selected timeout into `agentx.execute()` and record it in the GitHub-style context and archive manifest options so each result is reproducible.
3. Replace the Hugging Face-specific environment helper with a runtime-environment helper that retains the existing offline shared-cache variables and adds both supported AIPerf variables:
   - `AIPERF_DATASET_CONFIGURATION_TIMEOUT=<selected value>`
   - `AIPERF_SERVICE_PROFILE_CONFIGURE_TIMEOUT=<selected value>`

   Keeping the two equal satisfies AIPerf's required relationship that service profile configuration timeout is at least the dataset configuration timeout.
4. Document the 900-second default, command-line override, reason, and AIPerf mmap-cache behavior in `py_workflow_simple/README.md`.
5. Extend focused workflow tests to verify the defaults, explicit precedence, and the complete runtime environment.
6. Run the focused tests and a dry-run / syntax-level verification only. Do not launch AIPerf against the endpoint or start any model services.
7. Compute hashes after verification and synchronize only these changed workflow files, the focused test file, and the user-requested `configs/dcu-master.yaml` to their matching paths in the container. Verify remote hashes and inspect the installed AIPerf environment parsing after the copy.

## Synchronization

The local `configs/dcu-master.yaml` currently changes the active baseline from `conc-list: [4]` to `conc-list: [1]`; sync exactly that local version as requested. No unrelated container worktree modifications will be copied or altered.
