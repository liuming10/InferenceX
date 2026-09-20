# Prevent multi-concurrency artifact collisions

## Findings
- A matrix derived from `conc-list: [4, 8, 16]` expands to three entries in that order.
- Each normal AgentX result stem includes `conc{entry['conc']}`, so the three expected stems differ (`..._conc4_...`, `..._conc8_...`, `..._conc16_...`).
- The simple executor creates `jobs/<stem>/results`, directs AIPerf to `jobs/<stem>/results/aiperf_artifacts`, and stages into artifact directories that embed the same stem. The normal multi-concurrency case is already isolated.
- The code lacks a preflight invariant that prevents a future matrix/configuration combination from producing duplicate stems and letting sequential runs overwrite the same job or artifact directory.

## Implementation
1. Import the shared `result_filename` helper in `py_workflow_simple/run_sweep.py`.
2. Add `validate_unique_result_stems(project, matrix)` before creating the staging archive. It computes the canonical result stem for every matrix entry and raises `WorkflowError` naming the duplicate stem and concurrency values if any repeat.
3. Persist canonical `result_filename` per planned dry-run job where useful, or keep the validation independent of manifest shape to avoid changing artifact compatibility.
4. Add a focused unit test that proves distinct concurrencies create unique canonical stems and a duplicate input is rejected. If the shared filename helper requires a project Python subprocess, use the real project virtual environment only for integration-style test input or mock the helper narrowly.
5. Synchronize the changed code and tests to the container through expected-SHA uploads. Run a no-benchmark validation using a temporary `[4,8,16]` config fed to the matrix generator, then assert the three generated stems and all job/artifact destination names are unique. Do not alter the container’s checked-in configuration.

## Guarantees
For `[4,8,16]`, execution remains serial but output paths are distinct per point:
- `jobs/<stem-for-conc>/...`
- `artifacts/bmk_agentic_<stem-for-conc>/...`
- `artifacts/agentic_<stem-for-conc>/...`
- `artifacts/power_audit_<stem-for-conc>/...`
- `artifacts/server_logs_<stem-for-conc>/...`

A duplicate canonical stem fails before any AIPerf process begins, rather than silently overwriting artifacts.
