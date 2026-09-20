# Fix source-tree result collector imports

## Goal

Ensure post-benchmark result and evaluation collectors can import `infx` when the project is a ZIP/extracted source tree whose `.venv` does not contain an installed `infx` distribution.

## Implementation

1. Add an optional `project_root` argument to `py_workflow.collect_results.collect()` and `py_workflow.collect_evals.collect()`.
2. Use `project_root` as the subprocess working directory when supplied; retain the existing `aggregates` fallback for callers that rely on current behavior.
3. Pass `project.root` from `py_workflow_simple.run_sweep.run()` for both collectors.
4. Add regression assertions to collector tests that verify the subprocess cwd is the project root, plus a simple-workflow test or call-site check as appropriate.
5. Run focused collector/simple-workflow tests and compile checks. Do not rerun AIPerf.
6. Synchronize the changed collector modules, simple workflow caller, and tests to the container using the existing safe SHA-verified upload process, then run the focused tests in the container.
