# Align simple workflow cache with the shared DCU Hugging Face cache

## Verified cache and launcher contract
- `/ai_data/datasets/huggingface` is visible identically on the host and target container.
- It has the expected two-cache structure:
  - `/ai_data/datasets/huggingface/hub`
  - `/ai_data/datasets/huggingface/datasets`
- The required corpus is already present in both layers:
  - `hub/datasets--semianalysisai--cc-traces-weka-062126-256k`
  - `datasets/semianalysisai___cc-traces-weka-062126-256k`
- `runners/launch_dcu-hygon.sh` follows this split explicitly: `HF_HUB_CACHE` points at the hub cache and `HF_DATASETS_CACHE` points at the datasets cache. The hub cache is intended for shared snapshot/blob reuse, while datasets must remain writable because Hugging Face creates locks.

## Changes
1. Change simple workflow's omitted `--hf-cache-dir` default to `/ai_data/datasets/huggingface`.
2. Retain `--hf-cache-dir` as an explicit root override, with `<root>/hub` and `<root>/datasets` derived exactly as in the launcher.
3. Set the AIPerf process environment to those explicit `HF_HUB_CACHE` and `HF_DATASETS_CACHE` locations, and keep `HF_HOME=<root>` only as a compatible base default for libraries that honor it.
4. Validate the cache root plus `hub/` and `datasets/` access prior to a real benchmark instead of silently creating a fresh unrelated cache. Dry runs only report the resolved paths and do not mutate cache directories.
5. Update docs, tests, runtime metadata, and lifecycle logs to show cache root plus the resolved hub/datasets paths.
6. Synchronize the changed files to the container using expected-SHA atomic uploads and run dry-run plus environment-path verification. Do not download data or run AIPerf.

## Result
The normal AgentX run will reuse the already-downloaded shared corpus rather than `/root/.cache/huggingface` or `<archive-root>/.hf-cache`.
