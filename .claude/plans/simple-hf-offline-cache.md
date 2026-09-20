# Force shared Hugging Face cache offline in simple workflow

## Root cause
- The shared cache contains the exact dataset revision AIPerf requested: `8fecd2fc56694469f758f0afbbb6335ad3043740`.
- Simple workflow set the cache paths but not Hugging Face offline flags, so `datasets.load_dataset()` still issued a remote `HEAD` request before falling back to cache.
- `runners/launch_dcu-hygon.sh` already establishes the intended offline contract: `HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` alongside the separate hub/datasets cache paths.
- An isolated offline `load_dataset` check found the cached `default` configuration and its cached data location. It timed out during full dataset materialization, but did not contact the network or start any benchmark service.

## Changes
1. Add `HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1` to the explicit environment overrides passed to AIPerf and result processors.
2. Record both flags in AgentX runtime compatibility metadata and retain them in `meta_env.json`.
3. Document that the shared launcher-compatible cache is intentionally offline; a missing dataset should fail clearly rather than retry network access.
4. Add focused tests covering the offline environment mapping.
5. Run local tests, synchronize changed files with expected-SHA uploads, and run a container dry-run plus a fast environment-construction verification. Do not invoke `load_dataset` again, download data, start AIPerf, or contact the model endpoint.
