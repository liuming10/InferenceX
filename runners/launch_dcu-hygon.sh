#!/usr/bin/env bash

set -euo pipefail

: "${IMAGE:?IMAGE must be set}"
: "${MODEL:?MODEL must be set}"
: "${EXP_NAME:?EXP_NAME must be set}"
: "${PRECISION:?PRECISION must be set}"
: "${FRAMEWORK:?FRAMEWORK must be set}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set}"

export PORT="${PORT:-8888}"
export DCU_COUNT="${DCU_COUNT:-8}"
export DCU_PARTITION="${DCU_PARTITION:-local}"
export DCU_CPUS_PER_TASK="${DCU_CPUS_PER_TASK:-128}"
export DCU_TIME_LIMIT="${DCU_TIME_LIMIT:-500}"
export SQUASH_CACHE_DIR="${SQUASH_CACHE_DIR:-/data02/lium_space/squash}"
export DFS_ROOT_DIR="${DFS_ROOT_DIR:-/stortest/lium_space/dfs_storage/102111128}"
export ENROOT_RUNTIME_PATH="${ENROOT_RUNTIME_PATH:-/data02/lium_space/tmp/enroot-${UID}/runtime}"
export DCU_BENCHMARK_SCRIPT="/workspace/benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${EXP_NAME%%_*}_${PRECISION}_dcu-hygon_${FRAMEWORK}.sh"

if [[ "$FRAMEWORK" != "sglang" ]]; then
    echo "DCU Hygon launcher only supports FRAMEWORK=sglang, got '$FRAMEWORK'" >&2
    exit 1
fi
if [[ ! -f "${DCU_BENCHMARK_SCRIPT#/workspace/}" ]]; then
    echo "Benchmark script not found: $DCU_BENCHMARK_SCRIPT" >&2
    exit 1
fi
if [[ ! -d "$MODEL" ]]; then
    echo "Model directory does not exist: $MODEL" >&2
    exit 1
fi
if [[ ! -d "$DFS_ROOT_DIR" ]]; then
    echo "Mooncake DFS root does not exist: $DFS_ROOT_DIR" >&2
    exit 1
fi

run_dcu_container() {
    set -euo pipefail

    local safe_image squash_file lock_file container_name
    safe_image=$(printf '%s' "$IMAGE" | sed 's#[/:@#]#_#g')
    squash_file="$SQUASH_CACHE_DIR/${safe_image}.sqsh"
    lock_file="${squash_file}.lock"
    container_name="inferencex-dcu-${SLURM_JOB_ID:-manual}-${SLURM_STEP_ID:-0}"

    mkdir -p "$SQUASH_CACHE_DIR"
    exec 9>"$lock_file"
    flock -w 600 9 || {
        echo "Timed out waiting for Enroot image lock: $lock_file" >&2
        exit 1
    }
    if ! unsquashfs -l "$squash_file" >/dev/null 2>&1; then
        rm -f "$squash_file"
        echo "Importing local Docker image into $squash_file"
        enroot import -o "$squash_file" "dockerd://$IMAGE"
    fi
    flock -u 9

    cleanup_container() {
        enroot remove -f "$container_name" >/dev/null 2>&1 || true
    }
    trap cleanup_container EXIT INT TERM

    for port in 50051 50052 9300 "$PORT"; do
        if ss -ltn "sport = :${port}" | grep -q LISTEN; then
            echo "Refusing to start: TCP port ${port} is already in use on $(hostname)." >&2
            exit 1
        fi
    done

    enroot remove -f "$container_name" >/dev/null 2>&1 || true
    enroot create --name "$container_name" "$squash_file"
    enroot start --root --rw \
        --mount "$GITHUB_WORKSPACE:/workspace:none:x-create=dir,bind,rw" \
        --mount "$MODEL:$MODEL:none:x-create=dir,bind,ro" \
        --mount "$DFS_ROOT_DIR:$DFS_ROOT_DIR:none:x-create=dir,bind,rw" \
        --mount '/dev/kfd:/dev/kfd:none:x-create=file,bind,rw' \
        --mount '/dev/dri:/dev/dri:none:x-create=dir,rbind,rw' \
        --mount '/dev/mkfd:/dev/mkfd:none:x-create=file,bind,rw' \
        --mount '/opt/hyhal:/opt/hyhal:none:x-create=dir,bind,ro' \
        --env "PORT=$PORT" \
        --env "MODEL=$MODEL" \
        --env "MODEL_PREFIX=${MODEL_PREFIX:-}" \
        --env "TP=${TP:-}" \
        --env "PP_SIZE=${PP_SIZE:-1}" \
        --env "PCP_SIZE=${PCP_SIZE:-1}" \
        --env "EP_SIZE=${EP_SIZE:-1}" \
        --env "DP_ATTENTION=${DP_ATTENTION:-false}" \
        --env "CONC=${CONC:-}" \
        --env "KV_OFFLOADING=${KV_OFFLOADING:-}" \
        --env "TOTAL_CPU_DRAM_GB=${TOTAL_CPU_DRAM_GB:-}" \
        --env "DURATION=${DURATION:-}" \
        --env "RESULT_DIR=${RESULT_DIR:-/workspace/results}" \
        --env "RESULT_FILENAME=${RESULT_FILENAME:-}" \
        --env "EVAL_ONLY=${EVAL_ONLY:-false}" \
        --env "RUN_EVAL=${RUN_EVAL:-false}" \
        --env "SCENARIO_TYPE=${SCENARIO_TYPE:-}" \
        --env "SCENARIO_SUBDIR=${SCENARIO_SUBDIR:-}" \
        --env "IS_AGENTIC=${IS_AGENTIC:-0}" \
        --env "AIPERF_FAILED_REQUEST_THRESHOLD=${AIPERF_FAILED_REQUEST_THRESHOLD:-0.10}" \
        --env "AIPERF_EXPERIMENTAL_FAST=${AIPERF_EXPERIMENTAL_FAST:-0}" \
        --env "MOONCAKE_DFS_ROOT_DIR=$DFS_ROOT_DIR" \
        --env "MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=$DFS_ROOT_DIR" \
        "$container_name" bash "$DCU_BENCHMARK_SCRIPT"
}

export -f run_dcu_container
srun \
    --partition="$DCU_PARTITION" \
    --gres="dcu:${DCU_COUNT}" \
    --exclusive \
    --cpus-per-task="$DCU_CPUS_PER_TASK" \
    --time="$DCU_TIME_LIMIT" \
    --job-name="${RUNNER_NAME:-dcu-hygon}" \
    --export=ALL,ENROOT_RUNTIME_PATH \
    bash -c run_dcu_container
