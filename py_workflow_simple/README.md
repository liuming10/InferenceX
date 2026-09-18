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

The generated archive uses the same `artifacts/` projections as `py_workflow`, including `bmk_agentic_<stem>`, `agentic_<stem>`, `power_audit_<stem>`, `server_logs_<stem>`, `changelog-metadata` when supplied by the matrix, and `run-stats`. The archive manifest records the endpoint-oriented execution and the supplied GitHub run identity.
