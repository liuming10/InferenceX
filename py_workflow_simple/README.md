# py_workflow_simple

`py_workflow_simple` runs the AgentX/AIPerf part of the InferenceX workflow against a model that is already running. It does **not** invoke `runners/launch_*.sh`, Slurm, Enroot, Mooncake, or SGLang startup.

The entry point mirrors the local workflow:

```bash
python -m py_workflow_simple.run_sweep \
  --project-dir /stortest/lium_space/agentX/InferenceX \
  --config-key dsv4flash-w4a8-dcu-hygon-sglang \
  --archive-root /stortest/lium_space/agentX/InferenceX_result \
  --server-url http://127.0.0.1:30001 \
  --metrics-url http://127.0.0.1:30001/metrics \
  --no-evals \
  --github-run-id 35311504416 \
  --github-run-attempt 1 \
  --github-event-name workflow_dispatch \
  --github-repository liuming10/InferenceX
```

Use `--dry-run` to generate the matrix and archive structure without calling AIPerf:

```bash
python -m py_workflow_simple.run_sweep \
  --project-dir /stortest/lium_space/agentX/InferenceX \
  --config-key dsv4flash-w4a8-dcu-hygon-sglang \
  --archive-root /stortest/lium_space/agentX/InferenceX_result \
  --server-url http://127.0.0.1:30001 \
  --no-evals --dry-run
```

The generated archive uses the same `artifacts/` projections as `py_workflow`, including `bmk_agentic_<stem>`, `agentic_<stem>`, `power_audit_<stem>`, `server_logs_<stem>`, `changelog-metadata`, and `run-stats`. `benchmark_command.txt` is written under each job's `results/` directory immediately before the actual AIPerf invocation, so it is projected to `agentic_<stem>` by the same `results/**` rule as CI. The workflow also records `agentic_power_timezone_offset.txt` before AIPerf and uses `power_adapter` to derive `agentic_power_window.json` and `power_validation.json` from genuine profile artifacts; these are included in the matching power-audit bundle. The archive manifest records the endpoint-oriented execution and the supplied GitHub run identity. When `--metrics-url` is available, the workflow also saves the endpoint's raw Prometheus response before and after AIPerf under `artifacts/agentic_<stem>/`: `sglang_metrics_<timestamp>.prom` for SGLang matrix entries and `vllm_metrics_<timestamp>.prom` for vLLM entries. These snapshots come from the externally managed endpoint; this workflow never starts or stops the model service. AIPerf's continuous metric exports remain under `aiperf_artifacts/server_metrics_export.{csv,json}`.

The CI archive copies its webhook payload byte-for-byte. Supply that original payload with `--github-event-path PATH` when one exists; the simple workflow then copies it unchanged to `github_event.json`. Without an original payload, it deliberately omits `github_event.json` rather than fabricate a GitHub event. Similarly, `sglang_command.txt`, SGLang/Mooncake logs, and GPU-monitor telemetry are absent: their reference producers are model-service or telemetry processes that the AgentX-only workflow does not start.

The generated `archive_manifest.json` uses the same compatibility schema as the reference GitHub archive: its top level contains `archive_created_at`, `current_run_attempt`, `current_run_id`, `event_name`, `job_results`, `ref`, `repository`, `sha`, `source_reused`, and `source_run_id`; each fixed CI job entry contains only `outputs` and `result`. AgentX-specific command, matrix, runtime, artifact checksums, and raw metrics details remain in `plan.json`, job runtime files, and the artifact inventory rather than adding non-reference keys to this manifest.

Pass `--hf-cache-dir PATH` to choose the shared Hugging Face cache root used by AIPerf `--public-dataset` loaders. If omitted, the workflow uses the same cache layout as `runners/launch_dcu-hygon.sh`:

```text
/ai_data/datasets/huggingface
```

The cache root must already contain readable `hub/` and readable/writable `datasets/` directories. The workflow sets `HF_HOME`, `HF_HUB_CACHE=<cache-root>/hub`, `HF_DATASETS_CACHE=<cache-root>/datasets`, `HF_HUB_OFFLINE=1`, and `HF_DATASETS_OFFLINE=1` for AIPerf. This reuses the shared corpus cache without remote Hugging Face validation retries. A missing cached dataset fails locally rather than falling back to a network download.

The first reconstruction of the 393-trace Weka dataset on the shared filesystem can exceed AIPerf's 300-second dataset-configuration default. The workflow therefore sets `AIPERF_DATASET_CONFIGURATION_TIMEOUT=900` and its required paired value `AIPERF_SERVICE_PROFILE_CONFIGURE_TIMEOUT=900`. Override both through `--dataset-configuration-timeout SECONDS`; the selected value is written to the archive manifest and job runtime context. A successful first run also allows AIPerf's default mmap cache to reuse byte-identical trace reconstruction on later runs.


Each value in a selected `conc-list` becomes an independent AIPerf job. The simple workflow runs those jobs serially, in the declared list order, so `conc-list: [4, 8]` runs `--concurrency 4` to completion before it starts `--concurrency 8`. The default `--mode full-sweep-enabled` preserves every declared point. Use `--mode sweep-enabled` only when the legacy trimmed single-point behavior is intended.

## ZIP-extracted project copies

`py_workflow_simple` supports a project copied to the execution container from a ZIP archive when Git submodule administration metadata is incomplete. It does not run `git submodule`, initialize submodules, download sources, or mutate the project. It still validates the project Git identity, enforces `--allow-dirty-project` when applicable, and requires the selected `.venv` to import both `infx` and `aiperf`. The archive manifest records this as `runtime-imports-only` submodule validation.
