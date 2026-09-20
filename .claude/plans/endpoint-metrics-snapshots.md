# Capture endpoint Prometheus snapshots

## Goal

Add reference-compatible raw Prometheus snapshots to the AgentX-only workflow without starting, stopping, or identifying the externally managed model service.

## Implementation

1. Add a small helper in `py_workflow_simple/agentx.py` that accepts the explicit `metrics_url`, result directory, framework, and capture phase.
   - Skip only when no metrics URL is provided.
   - Request the URL with the Python standard library and bounded connection/read timeout.
   - Preserve the successful response body exactly, writing it atomically into `results/`.
   - Name snapshots `<framework>_metrics_<UTC timestamp>.prom`, normalizing the framework to a safe filename component and using a collision-resistant UTC timestamp.
   - On any HTTP/network/write failure, remove any temporary or partial file, emit a lifecycle warning, and return structured capture metadata instead of failing the benchmark.

2. In `run_agentx()`, capture the pre-benchmark snapshot after runtime metadata exists and immediately before spawning AIPerf. Wrap process streaming/waiting in `try`/`finally` so the post-benchmark snapshot is attempted after AIPerf exits, including nonzero exit or an interruption in the process-management path.

3. Extend `runtime_context.json` and the job record with endpoint-metrics provenance: explicit source URL, externally-managed-service flag, and each capture's phase/status/path/error/timestamp. The raw `.prom` content remains untouched.

4. Rely on the existing `stage_job_output()` projection: all files in `job_dir/results/` already become `artifacts/agentic_<stem>/`, so no new uploader or artifact category is needed.

5. Add focused unit tests in `py_workflow/tests/test_simple_workflow.py` for successful raw capture, safe framework-based naming, temporary-file cleanup/failure tolerance, and distinct names when captures occur close together. Mock the standard-library HTTP boundary; no real endpoint is contacted.

6. Run the focused simple-workflow tests and collector tests, then report the exact results. No benchmark, launcher, scheduler, SGLang, Mooncake, or model process will be started.

## Reference alignment

The reference SGLang script stores raw `/metrics` output before and after the benchmark. This implementation preserves that lifecycle shape for any externally supplied endpoint while avoiding a claim that this workflow manages the service. SGLang matrix rows produce `sglang_metrics_*.prom`; vLLM rows produce `vllm_metrics_*.prom`.
