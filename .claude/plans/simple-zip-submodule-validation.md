# Support ZIP-extracted projects in `py_workflow_simple`

## Goal
Allow the AgentX-only workflow to run from an uploaded ZIP extraction whose runtime files and `.venv` are usable but whose Git submodule administration metadata is incomplete.

## Changes
1. Add a `validate_submodules: bool = True` parameter to `py_workflow.manifest.resolve_project()` and thread it to `supplied_project()`.
   - The default remains `True`, so existing `py_workflow` behavior remains strict and unchanged.
   - When `False`, skip only `ensure_initialized_submodules()`.
   - Continue validating that the project root is a Git work tree, reading HEAD/status/remote, enforcing `--allow-dirty-project`, locating `.venv`, and verifying `infx` and installed `aiperf` imports.

2. Change `py_workflow_simple.run_sweep.run()` to call `resolve_project(..., validate_submodules=False)`.
   - Add source-validation metadata to the simple workflow manifest so the archive declares that submodule Git metadata was intentionally not required and Python runtime imports were verified.

3. Add focused unit tests for the new resolve-project option:
   - default behavior still invokes the submodule check;
   - compatibility mode skips it while preserving other validation;
   - simple workflow explicitly requests compatibility mode.

4. Update the simple workflow README with a short ZIP-extraction note: it supports incomplete submodule Git metadata only when the bundled project files and `.venv` can import `infx` and `aiperf`; it does not initialize, download, or mutate submodules.

## Validation
Run:

```bash
python -m compileall -q py_workflow py_workflow_simple
python -m pytest py_workflow/tests
python -m py_workflow_simple.run_sweep --help
git diff --check
```
