# Match reference archive manifest schema

## Goal

Make `py_workflow_simple` emit `archive_manifest.json` with the same top-level schema and `job_results` value shape as the reference archive:

- top-level fields: `archive_created_at`, `current_run_attempt`, `current_run_id`, `event_name`, `job_results`, `ref`, `repository`, `sha`, `source_reused`, `source_run_id`;
- fixed CI job keys;
- every job value shaped as `{ "outputs": object, "result": string }`.

## Changes

1. Add a simple-workflow manifest builder in `py_workflow_simple/run_sweep.py` that maps actual execution state to the reference job names:
   - `setup`: success with the normalized search-space/matrix and reuse flag outputs;
   - `sweep-agentic`: actual AgentX job result (`success`/`failure`, or `skipped` for dry-run);
   - `collect-results`: actual collector result;
   - `collect-evals`: actual collector result or `skipped` for `--no-evals`;
   - `calc-success-rate`: success after stats generation;
   - `upload-changelog-metadata`: `skipped`, because this AgentX-only workflow does not upload CI changelog metadata;
   - all other reference sweep/evaluation jobs: `skipped`.
   Each value must contain only `outputs` and `result`.

2. Replace the current `make_manifest()`-based archive manifest write in `run_sweep.py` with the reference-shaped manifest. Keep the existing internal `manifest` object during execution if needed for status/calculation, but serialize only the compatibility-shaped object to `archive_manifest.json`.

3. Populate reference top-level fields from the supplied/generated identity and project:
   - numeric GitHub run ID and attempt;
   - event, ref, repository and SHA;
   - `source_reused: false` and `source_run_id` equal to the current run ID;
   - `archive_created_at` at final publication time.
   Avoid adding extra top-level keys so the serialized key set matches the reference schema exactly.

4. Make the compatibility manifest truthful: do not claim model startup, launcher, SGLang metrics upload, changelog upload, or scheduler jobs occurred. Keep raw metrics snapshots and detailed execution metadata in their existing artifact/runtime files.

5. Update focused tests to assert exact top-level key set, fixed `job_results` key set, and `{outputs,result}` shape, including successful and dry-run mappings. Run the relevant local and container test suites.
