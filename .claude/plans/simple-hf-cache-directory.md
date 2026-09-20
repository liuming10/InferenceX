# Configurable persistent Hugging Face dataset cache

## Goal
Give `py_workflow_simple.run_sweep` an explicit startup parameter for the Hugging Face cache used by AIPerf public datasets, with a stable default outside the ZIP-extracted project and outside root's ephemeral home cache.

## Design
- Add `--hf-cache-dir PATH` to the simple workflow CLI.
- When omitted, resolve it to `<archive-root>/.hf-cache`, so repeated runs under the same result root reuse dataset downloads without modifying the project tree.
- Before a non-dry-run benchmark begins, create the cache root and its `datasets/` and `hub/` children.
- Pass the resolved cache root through `execute()` and `run_agentx()` as explicit environment overrides:
  - `HF_HOME=<cache-root>`
  - `HF_DATASETS_CACHE=<cache-root>/datasets`
  - `HF_HUB_CACHE=<cache-root>/hub`
- Record the resolved cache root in runtime compatibility context and metadata environment. The paths contain no credentials.
- Include it in the workflow option manifest and log the selected cache root before job execution.

## Validation
1. Add focused unit coverage for default path resolution and explicit path precedence.
2. Run local compile/test suite.
3. Sync changed simple workflow files and tests to the container with SHA-protected uploads.
4. Run a container dry run that confirms the selected default cache location in lifecycle output, without downloading a dataset or starting AIPerf.

## Non-goals
- Do not download or prefetch the dataset during dry run.
- Do not change existing root cache contents.
- Do not start models, scheduling, Slurm, Enroot, Mooncake, or SGLang.
