#!/usr/bin/env bash
#
# 在 Slurm 中以“一节点一 Enroot 容器”的方式启动 Hygon DCU SGLang
# Prefill/Decode disaggregated serving。此脚本由 launch_dcu-hygon.sh 在
# IS_MULTINODE=true 时调用；它不使用 srtctl，也不会清理其他作业的进程或容器。
#
# 拓扑契约（由 benchmark-multinode-tmpl.yml 提供）：
#   Slurm 节点数由 PREFILL/DECODE worker 数和 TP 在 launcher 内部计算，
#   不依赖 benchmark-multinode-tmpl.yml 新增的环境变量。
# 提供 :8000 服务，rank 0 同时在 :30000 运行 SGLang router 和 AIPerf。

set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
DCUS_PER_NODE="${DCU_COUNT:-8}"
DCU_PARTITION="${DCU_PARTITION:-local}"
DCU_CPUS_PER_TASK="${DCU_CPUS_PER_TASK:-128}"
DCU_TIME_LIMIT="${DCU_TIME_LIMIT:-500}"
SQUASH_CACHE_DIR="${SQUASH_CACHE_DIR:-/data02/lium_space/squash}"
DFS_ROOT_DIR="${DFS_ROOT_DIR:-/stortest/lium_space/dfs_storage/102111128}"
RDMA_DEVICE_NAMES="${RDMA_DEVICE_NAMES:-shca_0,shca_1,shca_2,shca_3}"
MOONCAKE_DEVICE="${MOONCAKE_DEVICE:-$RDMA_DEVICE_NAMES}"
RDMA_DEVICES_HOST_PATH="${RDMA_DEVICES_HOST_PATH:-/dev/infiniband}"
RDMA_SYSFS_HOST_PATH="${RDMA_SYSFS_HOST_PATH:-/sys/class/infiniband}"
HF_CACHE_ROOT_HOST_PATH="${HF_CACHE_ROOT_HOST_PATH:-/stortest/lium_space/agentX/actions-runner/huggingface}"
HF_HUB_CACHE_HOST_PATH="${HF_HUB_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/hub}"
HF_DATASETS_CACHE_HOST_PATH="${HF_DATASETS_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/datasets}"
HF_HUB_CACHE="${HF_HUB_CACHE:-/mnt/hf_hub_cache}"
HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/mnt/hf_datasets_cache}"
ENROOT_RUNTIME_PATH="${ENROOT_RUNTIME_PATH:-/data02/lium_space/tmp/enroot-${UID}/runtime}"

require_var() {
    local name="$1"
    [[ -n "${!name:-}" ]] || { echo "Required variable is unset: $name" >&2; exit 1; }
}

is_positive_integer() { [[ "$1" =~ ^[1-9][0-9]*$ ]]; }

validate_topology() {
    local role worker_count tp pp dcp pcp ep dp nodes_per_worker expected

    [[ "${IS_MULTINODE:-false}" == "true" ]] || {
        echo "launch_multi_dcu-hygon.sh requires IS_MULTINODE=true" >&2
        exit 1
    }
    [[ "${FRAMEWORK:-}" == "sglang" || "${FRAMEWORK:-}" == "sglang-disagg" ]] || {
        echo "DCU multi-node launcher supports FRAMEWORK=sglang or sglang-disagg, got '${FRAMEWORK:-}'" >&2
        exit 1
    }
    [[ "${DISAGG:-false}" == "true" ]] || {
        echo "DCU multi-node launcher currently supports only DISAGG=true." >&2
        exit 1
    }
    [[ "${IS_AGENTIC:-0}" == "1" || "${IS_AGENTIC:-}" == "true" ]] || {
        echo "DCU multi-node launcher currently supports only AgentX (IS_AGENTIC=1)." >&2
        exit 1
    }
    [[ -n "${KV_P2P_TRANSFER:-}" ]] || {
        echo "KV_P2P_TRANSFER must name the SGLang disaggregation transfer backend." >&2
        exit 1
    }
    is_positive_integer "$DCUS_PER_NODE" || { echo "DCU_COUNT must be a positive integer" >&2; exit 1; }

    for role in PREFILL DECODE; do
        worker_count="${role}_NUM_WORKERS"
        tp="${role}_TP"
        pp="${role}_PP_SIZE"
        dcp="${role}_DCP_SIZE"
        pcp="${role}_PCP_SIZE"
        ep="${role}_EP"
        dp="${role}_DP_ATTN"
        for name in "$worker_count" "$tp" "$pp" "$dcp" "$pcp" "$ep" "$dp"; do require_var "$name"; done
        is_positive_integer "${!worker_count}" || { echo "$worker_count must be positive" >&2; exit 1; }
        is_positive_integer "${!tp}" || { echo "$tp must be positive" >&2; exit 1; }
        is_positive_integer "${!ep}" || { echo "$ep must be positive" >&2; exit 1; }
        [[ "${!pp}" == "1" && "${!dcp}" == "1" && "${!pcp}" == "1" ]] || {
            echo "$role PP/DCP/PCP multi-node topology is not implemented; each must be 1." >&2
            exit 1
        }
        (( ${!tp} % DCUS_PER_NODE == 0 )) || {
            echo "$role TP=${!tp} must be divisible by DCU_COUNT=$DCUS_PER_NODE; partial-node placement is not supported." >&2
            exit 1
        }
        nodes_per_worker=$(( ${!tp} / DCUS_PER_NODE ))
        expected=$(( ${!worker_count} * nodes_per_worker ))
        printf '%s topology: workers=%s TP=%s nodes/worker=%s nodes=%s\n' \
            "$role" "${!worker_count}" "${!tp}" "$nodes_per_worker" "$expected"
    done

    NODE_COUNT=$(( PREFILL_NUM_WORKERS * PREFILL_TP / DCUS_PER_NODE + DECODE_NUM_WORKERS * DECODE_TP / DCUS_PER_NODE ))
    export NODE_COUNT
    printf 'Derived Slurm node count: %s (from existing role topology)\n' "$NODE_COUNT"
}

host_preflight() {
    local path
    for path in "$MODEL" "$DFS_ROOT_DIR" "$RDMA_DEVICES_HOST_PATH" "$RDMA_SYSFS_HOST_PATH" \
        "$HF_HUB_CACHE_HOST_PATH" "$HF_DATASETS_CACHE_HOST_PATH"; do
        [[ -d "$path" ]] || { echo "Required host directory does not exist: $path" >&2; exit 1; }
    done
    [[ -r "$HF_HUB_CACHE_HOST_PATH" && -x "$HF_HUB_CACHE_HOST_PATH" ]] || {
        echo "Hugging Face hub cache is not readable: $HF_HUB_CACHE_HOST_PATH" >&2; exit 1;
    }
    [[ -r "$HF_DATASETS_CACHE_HOST_PATH" && -w "$HF_DATASETS_CACHE_HOST_PATH" && -x "$HF_DATASETS_CACHE_HOST_PATH" ]] || {
        echo "Hugging Face datasets cache is not readable/writable: $HF_DATASETS_CACHE_HOST_PATH" >&2; exit 1;
    }
}

run_container_task() {
    set -euo pipefail
    local safe_image squash_file lock_file container_name task_rank
    task_rank="${SLURM_PROCID:?SLURM_PROCID must be set for a multi-node task}"
    safe_image=$(printf '%s' "$IMAGE" | tr '/:@' '_')
    squash_file="$SQUASH_CACHE_DIR/${safe_image}.sqsh"
    lock_file="${squash_file}.lock"
    container_name="inferencex-dcu-${SLURM_JOB_ID:?}-${task_rank}"

    mkdir -p "$SQUASH_CACHE_DIR" "$ENROOT_RUNTIME_PATH"
    exec 9>"$lock_file"
    flock -w 600 9 || { echo "Timed out waiting for Enroot image lock: $lock_file" >&2; exit 1; }
    if ! unsquashfs -l "$squash_file" >/dev/null 2>&1; then
        rm -f "$squash_file"
        echo "[$(hostname)] Importing local Docker image into $squash_file"
        enroot import -o "$squash_file" "dockerd://$IMAGE"
    fi
    flock -u 9

    cleanup_container() { enroot remove -f "$container_name" >/dev/null 2>&1 || true; }
    trap cleanup_container EXIT INT TERM
    enroot remove -f "$container_name" >/dev/null 2>&1 || true
    enroot create --name "$container_name" "$squash_file"

    enroot start --root --rw \
        --mount "$GITHUB_WORKSPACE:/workspace:none:x-create=dir,bind,rw" \
        --mount "$MODEL:$MODEL:none:x-create=dir,bind,ro" \
        --mount "$DFS_ROOT_DIR:$DFS_ROOT_DIR:none:x-create=dir,bind,rw" \
        --mount "$RDMA_DEVICES_HOST_PATH:$RDMA_DEVICES_HOST_PATH:none:x-create=dir,rbind,rw" \
        --mount "$RDMA_SYSFS_HOST_PATH:$RDMA_SYSFS_HOST_PATH:none:x-create=dir,rbind,ro" \
        --mount "$HF_HUB_CACHE_HOST_PATH:$HF_HUB_CACHE:none:x-create=dir,bind,ro" \
        --mount "$HF_DATASETS_CACHE_HOST_PATH:$HF_DATASETS_CACHE:none:x-create=dir,bind,rw" \
        --mount '/dev/kfd:/dev/kfd:none:x-create=file,bind,rw' \
        --mount '/dev/dri:/dev/dri:none:x-create=dir,rbind,rw' \
        --mount '/dev/mkfd:/dev/mkfd:none:x-create=file,bind,rw' \
        --mount '/opt/hyhal:/opt/hyhal:none:x-create=dir,bind,ro' \
        --env DCU_MULTI_NODE_CONTAINER=1 \
        --env "SLURM_PROCID=$SLURM_PROCID" \
        --env "SLURM_JOB_ID=$SLURM_JOB_ID" \
        --env "IS_MULTINODE=true" --env "DISAGG=true" \
        --env "IMAGE=$IMAGE" --env "MODEL=$MODEL" --env "MODEL_PREFIX=${MODEL_PREFIX:-}" --env "EXP_NAME=$EXP_NAME" \
        --env "FRAMEWORK=$FRAMEWORK" --env "PRECISION=$PRECISION" --env "SCENARIO_TYPE=${SCENARIO_TYPE:-}" \
        --env "SCENARIO_SUBDIR=${SCENARIO_SUBDIR:-agentic/}" --env "IS_AGENTIC=${IS_AGENTIC:-0}" \
        --env "ISL=${ISL:-}" --env "OSL=${OSL:-}" --env "SPEC_DECODING=${SPEC_DECODING:-}" \
        --env "ROUTER_METADATA=${ROUTER_METADATA:-}" --env "KV_OFFLOAD_BACKEND=${KV_OFFLOAD_BACKEND:-}" \
        --env "KV_OFFLOAD_BACKEND_METADATA=${KV_OFFLOAD_BACKEND_METADATA:-}" --env "CONC_LIST=${CONC_LIST:-}" \
        --env "RUNNER_NAME=${RUNNER_NAME:-dcu-hygon}" --env "RUNNER_TYPE=${RUNNER_TYPE:-}" \
        --env "EVAL_FRAMEWORK=${EVAL_FRAMEWORK:-lm-eval}" --env "EVAL_SUITE=${EVAL_SUITE:-}" \
        --env "EVAL_CONC=${EVAL_CONC:-}" --env "EVAL_LIMIT=${EVAL_LIMIT:-}" --env "HF_TOKEN=${HF_TOKEN:-}" \
        --env "NODE_COUNT=$NODE_COUNT" --env "NODE_IPS=$NODE_IPS" \
        --env "PREFILL_NUM_WORKERS=$PREFILL_NUM_WORKERS" --env "PREFILL_TP=$PREFILL_TP" \
        --env "PREFILL_EP=$PREFILL_EP" --env "PREFILL_DP_ATTN=$PREFILL_DP_ATTN" \
        --env "DECODE_NUM_WORKERS=$DECODE_NUM_WORKERS" --env "DECODE_TP=$DECODE_TP" \
        --env "DECODE_EP=$DECODE_EP" --env "DECODE_DP_ATTN=$DECODE_DP_ATTN" \
        --env "DCU_COUNT=$DCUS_PER_NODE" --env "KV_P2P_TRANSFER=$KV_P2P_TRANSFER" \
        --env "RESULT_DIR=$RESULT_DIR" --env "RESULT_FILENAME=${RESULT_FILENAME:-}" \
        --env "CONC=$CONC" --env "DURATION=$DURATION" --env "MAX_MODEL_LEN=${MAX_MODEL_LEN:-1048576}" \
        --env "KV_OFFLOADING=${KV_OFFLOADING:-none}" --env "TOTAL_CPU_DRAM_GB=${TOTAL_CPU_DRAM_GB:-}" \
        --env "IS_AGENTIC=${IS_AGENTIC:-0}" --env "EVAL_ONLY=${EVAL_ONLY:-false}" --env "RUN_EVAL=${RUN_EVAL:-false}" \
        --env "AIPERF_EXPERIMENTAL_FAST=${AIPERF_EXPERIMENTAL_FAST:-0}" \
        --env "AIPERF_FAILED_REQUEST_THRESHOLD=${AIPERF_FAILED_REQUEST_THRESHOLD:-0.10}" \
        --env "HF_HUB_CACHE=$HF_HUB_CACHE" --env "HF_DATASETS_CACHE=$HF_DATASETS_CACHE" \
        --env HF_HUB_OFFLINE=1 --env HF_DATASETS_OFFLINE=1 \
        --env "DFS_ROOT_DIR=$DFS_ROOT_DIR" --env "MOONCAKE_DFS_ROOT_DIR=$DFS_ROOT_DIR" \
        --env "MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=$DFS_ROOT_DIR" --env "MOONCAKE_DEVICE=$MOONCAKE_DEVICE" \
        --env "RDMA_DEVICE_NAMES=$RDMA_DEVICE_NAMES" \
        "$container_name" bash /workspace/runners/launch_dcu_multi_hygon.sh --container-task
}

wait_for_tcp() {
    local host="$1" port="$2" pid="$3" log="$4" deadline=$((SECONDS + ${5:-900}))
    until (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; do
        if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
            echo "Service died before ${host}:${port} became ready." >&2
            tail -n 160 "$log" >&2 || true
            return 1
        fi
        (( SECONDS < deadline )) || { echo "Timed out waiting for ${host}:${port}" >&2; tail -n 160 "$log" >&2 || true; return 1; }
        sleep 3
    done
    exec 3>&-; exec 3<&-
}

container_task() {
    set -euo pipefail
    local rank="$SLURM_PROCID" node_ip role role_rank worker_rank rank_in_worker nodes_per_worker head_index
    local server_pid="" master_pid="" client_pid="" router_pid="" server_log router_log finished_marker
    local -a ips prefill_urls decode_urls sglang_args
    IFS=',' read -r -a ips <<< "$NODE_IPS"
    node_ip="${ips[$rank]:?Missing IP for task rank $rank}"
    nodes_per_worker=$((PREFILL_TP / DCU_COUNT))
    if (( rank < PREFILL_NUM_WORKERS * nodes_per_worker )); then
        role=prefill; role_rank=$rank; worker_rank=$((role_rank / nodes_per_worker)); rank_in_worker=$((role_rank % nodes_per_worker))
        head_index=$((worker_rank * nodes_per_worker))
    else
        role=decode; role_rank=$((rank - PREFILL_NUM_WORKERS * nodes_per_worker)); nodes_per_worker=$((DECODE_TP / DCU_COUNT))
        worker_rank=$((role_rank / nodes_per_worker)); rank_in_worker=$((role_rank % nodes_per_worker))
        head_index=$((PREFILL_NUM_WORKERS * PREFILL_TP / DCU_COUNT + worker_rank * nodes_per_worker))
    fi

    RESULT_DIR="${RESULT_DIR:-/workspace/results}"
    mkdir -p "$RESULT_DIR/multinode/${SLURM_JOB_ID}"
    local log_dir="$RESULT_DIR/multinode/${SLURM_JOB_ID}"
    finished_marker="$log_dir/finished"
    rm -f "$finished_marker" 2>/dev/null || true
    server_log="$log_dir/${role}-${rank}-$(hostname).log"
    router_log="$log_dir/router-$(hostname).log"

    stop_group() {
        local pid="$1"
        [[ -n "$pid" ]] || return 0
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    }
    cleanup() {
        local rc=$?
        trap - EXIT INT TERM
        if [[ "$rank" -eq 0 ]]; then touch "$finished_marker"; fi
        stop_group "$router_pid"; stop_group "$server_pid"; stop_group "$client_pid"; stop_group "$master_pid"
        exit "$rc"
    }
    trap cleanup EXIT INT TERM

    # Preserve the DCU image tuning used by the single-node recipe.
    export NCCL_MIN_NCHANNELS="${NCCL_MIN_NCHANNELS:-16}" NCCL_MAX_NCHANNELS="${NCCL_MAX_NCHANNELS:-16}"
    export SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE="${SGLANG_DSV4_HCU_INT8_INDEX_K_CACHE:-1}"
    export SGLANG_ROCM_USE_AITER_MOE="${SGLANG_ROCM_USE_AITER_MOE:-1}"
    export SGLANG_W4A8_TPMOE_BACKEND="${SGLANG_W4A8_TPMOE_BACKEND:-aiter}"
    export MOONCAKE_PROTOCOL="${MOONCAKE_PROTOCOL:-rdma}" MOONCAKE_MASTER="${ips[0]}:50051"
    export MOONCAKE_LOCAL_HOSTNAME="$node_ip" MC_TE_FILTERS="$MOONCAKE_DEVICE"
    export MOONCAKE_ENABLE_DFS=1 MOONCAKE_DFS_FS_ADAPTER=posix MOONCAKE_DFS_ROOT_DIR="$DFS_ROOT_DIR"
    export MOONCAKE_OFFLOAD_FILE_STORAGE_PATH="$DFS_ROOT_DIR" MOONCAKE_OFFLOAD_ENABLED=True

    if [[ "$rank" -eq 0 ]]; then
        setsid mooncake_master --logtostderr --enable_http_metadata_server --enable_offload=true >"$log_dir/mooncake_master.log" 2>&1 &
        master_pid=$!
        wait_for_tcp 127.0.0.1 50051 "$master_pid" "$log_dir/mooncake_master.log"
    else
        wait_for_tcp "${ips[0]}" 50051 "" "$server_log"
    fi

    setsid mooncake_client --host=0.0.0.0 --global_segment_size=160GB --local_buffer_size=4GB \
        --master_server_address="${ips[0]}:50051" --metadata_server=P2PHANDSHAKE \
        --protocol="$MOONCAKE_PROTOCOL" --device_names="$MOONCAKE_DEVICE" --port=50052 \
        --logtostderr --enable_http_server --http_port=9300 --enable_offload=true >"$log_dir/mooncake_client-${rank}.log" 2>&1 &
    client_pid=$!
    wait_for_tcp 127.0.0.1 9300 "$client_pid" "$log_dir/mooncake_client-${rank}.log"

    sglang_args=(
        --reasoning-parser deepseek-v4 --tool-call-parser deepseekv4
        --tp-size "$([[ "$role" == prefill ]] && echo "$PREFILL_TP" || echo "$DECODE_TP")"
        --disaggregation-mode "$role" --disaggregation-transfer-backend "$KV_P2P_TRANSFER"
        --host 0.0.0.0 --port 8000 --model-path "$MODEL" --trust-remote-code
        --model-loader-extra-config '{"enable_multithread_load":"true","num_threads":64}'
        --context-length "$MAX_MODEL_LEN" --chunked-prefill-size 32768 --skip-server-warmup
        --mem-fraction-static 0.9 --enable-metrics --moe-runner-backend aiter
        --quantization slimquant_marlin --moe-a2a-backend none --tokenizer-worker-num 8
        --enable-unified-cache-external-linker --unified-cache-external-linker-backend mooncake
        --mooncake-enable-page-wise-load --mooncake-page-wise-load-threshold 1 --enable-cache-report
        --dist-timeout 10000 --watchdog-timeout 3600
    )
    if [[ "$role" == prefill ]]; then
        if (( PREFILL_EP > 1 )); then sglang_args+=(--ep-size "$PREFILL_EP"); fi
        if [[ "$PREFILL_DP_ATTN" == true ]]; then sglang_args+=(--dp "$PREFILL_TP" --enable-dp-attention --enable-dp-lm-head); fi
    else
        if (( DECODE_EP > 1 )); then sglang_args+=(--ep-size "$DECODE_EP"); fi
        if [[ "$DECODE_DP_ATTN" == true ]]; then sglang_args+=(--dp "$DECODE_TP" --enable-dp-attention --enable-dp-lm-head); fi
    fi
    if (( nodes_per_worker > 1 )); then
        sglang_args+=(--dist-init-addr "${ips[$head_index]}:29500" --nnodes "$nodes_per_worker" --node-rank "$rank_in_worker")
    fi

    printf '%q ' sglang serve "${sglang_args[@]}" >"$log_dir/${role}-${rank}-command.txt"; printf '\n' >>"$log_dir/${role}-${rank}-command.txt"
    setsid sglang serve "${sglang_args[@]}" >>"$server_log" 2>&1 &
    server_pid=$!

    # A worker head service represents each worker in the router; check every
    # task's listener so a non-head TP rank cannot fail silently.
    wait_for_tcp 127.0.0.1 8000 "$server_pid" "$server_log" 1800

    if [[ "$rank" -ne 0 ]]; then
        while kill -0 "$server_pid" 2>/dev/null && [[ ! -e "$finished_marker" ]]; do sleep 5; done
        [[ -e "$finished_marker" ]] || { echo "Local SGLang process exited unexpectedly." >&2; exit 1; }
        return 0
    fi

    for ((worker_rank = 0; worker_rank < PREFILL_NUM_WORKERS; worker_rank++)); do
        head_index=$((worker_rank * (PREFILL_TP / DCU_COUNT)))
        prefill_urls+=(--prefill "http://${ips[$head_index]}:8000")
    done
    local decode_base=$((PREFILL_NUM_WORKERS * PREFILL_TP / DCU_COUNT))
    for ((worker_rank = 0; worker_rank < DECODE_NUM_WORKERS; worker_rank++)); do
        head_index=$((decode_base + worker_rank * (DECODE_TP / DCU_COUNT)))
        decode_urls+=(--decode "http://${ips[$head_index]}:8000")
    done

    # All node tasks publish an :8000 listener before rank 0 proceeds. A bounded
    # TCP loop makes failed remote ranks fail the entire srun step rather than
    # allowing the router/AIPerf phase to hang indefinitely.
    local deadline=$((SECONDS + 1800)) remote
    for remote in "${ips[@]}"; do
        until (exec 3<>"/dev/tcp/${remote}/8000") 2>/dev/null; do
            (( SECONDS < deadline )) || { echo "Timed out waiting for worker: ${remote}:8000" >&2; exit 1; }
            kill -0 "$server_pid" 2>/dev/null || { tail -n 160 "$server_log" >&2 || true; exit 1; }
            sleep 3
        done
        exec 3>&-; exec 3<&-
    done

    setsid python -m sglang_router.launch_router --pd-disaggregation --port 30000 \
        "${prefill_urls[@]}" "${decode_urls[@]}" >"$router_log" 2>&1 &
    router_pid=$!
    wait_for_tcp 127.0.0.1 30000 "$router_pid" "$router_log" 900

    local canary_deadline=$((SECONDS + 900)) code
    until code=$(curl --silent --output "$log_dir/router-canary.out" --write-out '%{http_code}' \
        --max-time 120 -X POST http://127.0.0.1:30000/v1/chat/completions \
        -H 'Content-Type: application/json' \
        -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":1,\"temperature\":0}") && [[ "$code" == 200 ]]; do
        (( SECONDS < canary_deadline )) || { echo "Router never completed a canary request." >&2; cat "$log_dir/router-canary.out" >&2 || true; exit 1; }
        kill -0 "$server_pid" 2>/dev/null && kill -0 "$router_pid" 2>/dev/null || { tail -n 160 "$server_log" "$router_log" >&2 || true; exit 1; }
        sleep 5
    done

    export PORT=30000 AIPERF_SERVER_URL=http://127.0.0.1:30000
    export AIPERF_SERVER_METRICS_URLS="$(printf 'http://%s:8000/metrics,' "${ips[@]}")"
    AIPERF_SERVER_METRICS_URLS="${AIPERF_SERVER_METRICS_URLS%,}"
    source /workspace/benchmarks/benchmark_lib.sh
    unset PYTHONPYCACHEPREFIX
    resolve_trace_source
    install_agentic_deps
    if [[ "${EVAL_ONLY:-false}" == true ]]; then
        run_eval --framework "${EVAL_FRAMEWORK:-lm-eval}" --port "$PORT"
    else
        build_replay_cmd "$RESULT_DIR"
        run_agentic_replay_and_write_outputs "$RESULT_DIR"
    fi
}

submit_job() {
    require_var IMAGE; require_var MODEL; require_var EXP_NAME; require_var FRAMEWORK; require_var PRECISION; require_var GITHUB_WORKSPACE
    require_var CONC; require_var DURATION; require_var RESULT_DIR
    validate_topology
    host_preflight
    install -d -m 700 "$ENROOT_RUNTIME_PATH"

    local nodes=() node_ips=() node ip
    while IFS= read -r node; do nodes+=("$node"); done < <(scontrol show hostnames "${SLURM_JOB_NODELIST:-}" 2>/dev/null || true)
    if (( ${#nodes[@]} > 0 )); then
        echo "Refusing nested allocation: this launcher must run from the GitHub runner, not inside an existing Slurm allocation." >&2
        exit 1
    fi

    local log_dir="$RESULT_DIR/multinode-submit"
    mkdir -p "$log_dir"
    echo "Submitting DCU multi-node job: nodes=$NODE_COUNT partition=$DCU_PARTITION"
    sbatch --parsable --exclusive --nodes="$NODE_COUNT" --ntasks="$NODE_COUNT" --ntasks-per-node=1 \
        --gres="dcu:${DCUS_PER_NODE}" --cpus-per-task="$DCU_CPUS_PER_TASK" --partition="$DCU_PARTITION" \
        --time="$DCU_TIME_LIMIT" --job-name="${RUNNER_NAME:-dcu-hygon}" \
        --output="$log_dir/slurm-%j.out" --error="$log_dir/slurm-%j.err" \
        --export=ALL,ENROOT_RUNTIME_PATH="$ENROOT_RUNTIME_PATH" "$SCRIPT_PATH" --allocated-job
}

allocated_job() {
    validate_topology
    local -a nodes ips
    local node ip
    while IFS= read -r node; do nodes+=("$node"); done < <(scontrol show hostnames "$SLURM_JOB_NODELIST")
    [[ "${#nodes[@]}" -eq "$NODE_COUNT" ]] || { echo "Slurm allocated ${#nodes[@]} nodes, expected $NODE_COUNT" >&2; exit 1; }
    for node in "${nodes[@]}"; do
        ip=$(srun --nodes=1 --ntasks=1 --nodelist="$node" bash -lc "hostname -I | awk '{print \\$1}'")
        [[ -n "$ip" ]] || { echo "Unable to resolve an IP for $node" >&2; exit 1; }
        ips+=("$ip")
    done
    export NODE_IPS="$(IFS=,; printf '%s' "${ips[*]}")"
    export SLURM_NODELIST="$(IFS=,; printf '%s' "${nodes[*]}")"
    echo "Allocated nodes: ${nodes[*]}"
    echo "Node IPs: $NODE_IPS"

    # Validate shared paths and DCU/RDMA visibility on every allocated node
    # before importing the image or starting any SGLang process.
    srun --nodes="$NODE_COUNT" --ntasks="$NODE_COUNT" --ntasks-per-node=1 \
        --kill-on-bad-exit=1 --unbuffered --export=ALL,ENROOT_RUNTIME_PATH \
        bash -lc '
            set -euo pipefail
            for path in "$MODEL" "$DFS_ROOT_DIR" "$RDMA_DEVICES_HOST_PATH" "$RDMA_SYSFS_HOST_PATH" "$HF_HUB_CACHE_HOST_PATH" "$HF_DATASETS_CACHE_HOST_PATH"; do
                [[ -d "$path" ]] || { echo "[$(hostname)] missing host directory: $path" >&2; exit 1; }
            done
            [[ -r "$DFS_ROOT_DIR" && -x "$DFS_ROOT_DIR" ]] || { echo "[$(hostname)] DFS root is not searchable" >&2; exit 1; }
            [[ -r "$HF_HUB_CACHE_HOST_PATH" && -x "$HF_HUB_CACHE_HOST_PATH" ]] || { echo "[$(hostname)] HF hub cache is not readable" >&2; exit 1; }
            [[ -r "$HF_DATASETS_CACHE_HOST_PATH" && -w "$HF_DATASETS_CACHE_HOST_PATH" && -x "$HF_DATASETS_CACHE_HOST_PATH" ]] || { echo "[$(hostname)] HF datasets cache is not writable" >&2; exit 1; }
            IFS=, read -r -a rnic_names <<< "$RDMA_DEVICE_NAMES"
            for rnic in "${rnic_names[@]}"; do
                [[ -d "$RDMA_SYSFS_HOST_PATH/$rnic" ]] || { echo "[$(hostname)] missing RDMA HCA: $rnic" >&2; exit 1; }
            done
            compgen -G "$RDMA_DEVICES_HOST_PATH/*" >/dev/null || { echo "[$(hostname)] no RDMA device nodes" >&2; exit 1; }
            for dev in /dev/kfd /dev/mkfd; do [[ -e "$dev" ]] || { echo "[$(hostname)] missing DCU device: $dev" >&2; exit 1; }; done
            [[ -d /dev/dri && -d /opt/hyhal ]] || { echo "[$(hostname)] missing DCU runtime mount" >&2; exit 1; }
            echo "[$(hostname)] DCU multi-node preflight passed"
        '

    srun --nodes="$NODE_COUNT" --ntasks="$NODE_COUNT" --ntasks-per-node=1 --kill-on-bad-exit=1 \
        --signal=TERM@30 --unbuffered --export=ALL,ENROOT_RUNTIME_PATH \
        bash "$SCRIPT_PATH" --host-task
}

case "${1:-}" in
    --container-task) container_task ;;
    --host-task) run_container_task ;;
    --allocated-job) allocated_job ;;
    '') submit_job ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac
