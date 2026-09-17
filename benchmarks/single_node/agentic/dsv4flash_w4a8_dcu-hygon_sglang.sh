#!/usr/bin/env bash

set -euo pipefail

source "$(dirname "$0")/../../benchmark_lib.sh"

# The DCU SGLang image's Python importlib fails with a nonempty bytecode cache
# prefix. Keep bytecode disabled by the shared library while clearing only this
# recipe's inherited prefix before importing SGLang.
unset PYTHONPYCACHEPREFIX

check_env_vars \
    MODEL \
    TP \
    CONC \
    KV_OFFLOADING \
    TOTAL_CPU_DRAM_GB \
    RESULT_DIR \
    DURATION \
    EP_SIZE \
    DP_ATTENTION \
    RESULT_FILENAME \
    PORT \
    MOONCAKE_DFS_ROOT_DIR \
    MOONCAKE_OFFLOAD_FILE_STORAGE_PATH

ulimit -c 0

export MOONCAKE_ENABLE_DFS=1
export MOONCAKE_DFS_FS_ADAPTER=posix
export MOONCAKE_DFS_ALLOCATOR_TYPE=bucket
export MOONCAKE_DFS_BUCKET_CAPACITY=$((1024 * 1024 * 256))
export MOONCAKE_DFS_MAX_BUCKET_COUNT=5120
export MOONCAKE_DFS_ALIGNMENT=4096
export MOONCAKE_DFS_SINGLE_TENANT=True
export MOONCAKE_DFS_BUCKET_META_LOG_THRESHOLD=$((4 * 1024 * 1024))
export MOONCAKE_DFS_EVICTION_ENABLED=True
export MOONCAKE_DFS_EVICTION_HIGH_WATERMARK=0.80
export MOONCAKE_DFS_EVICTION_LOW_WATERMARK=0.60
export MOONCAKE_DFS_EVICTION_CHECK_INTERVAL=2
export MOONCAKE_DFS_DEFERRED_FREE_SECONDS=10
export MOONCAKE_OFFLOAD_ENABLED=True
export MOONCAKE_OFFLOAD_STORAGE_BACKEND_DESCRIPTOR=distributed_storage_backend
export MOONCAKE_OFFLOAD_LOCAL_BUFFER_SIZE_BYTES=21474836480
export MOONCAKE_LOCAL_HOSTNAME=127.0.0.1
export MOONCAKE_MASTER=127.0.0.1:50051
export MOONCAKE_PROTOCOL=rdma
export MOONCAKE_DEVICE="${MOONCAKE_DEVICE:-shca_0,shca_1,shca_2,shca_3}"
export MC_TE_FILTERS="${MC_TE_FILTERS:-$MOONCAKE_DEVICE}"
export MC_STORE_ENABLE_SESSION_CACHE=0
export MC_STORE_ENABLE_DFS_PREFETCH=0
export AIPERF_HTTP_TCP_USER_TIMEOUT=900000
export SGLANG_TIMEOUT_KEEP_ALIVE=900
export SGLANG_ENABLE_UNIFIED_RADIX_TREE=1
export SGLANG_OPT_UNIFIED_CACHE_FREE_OUT_OF_WINDOW_SLOTS=1

mkdir -p "$RESULT_DIR"
MASTER_LOG="$RESULT_DIR/mooncake_master.log"
CLIENT_LOG="$RESULT_DIR/mooncake_client.log"
SERVER_LOG="$RESULT_DIR/server.log"
MASTER_PID=""
CLIENT_PID=""
SERVER_PID=""

wait_for_tcp_port() {
    local host="$1"
    local port="$2"
    local log="$3"
    local pid="$4"
    local deadline=$((SECONDS + 300))

    until (exec 3<>"/dev/tcp/${host}/${port}") 2>/dev/null; do
        if ! kill -0 "$pid" 2>/dev/null; then
            echo "Process died before ${host}:${port} became ready." >&2
            tail -n 200 "$log" >&2 || true
            return 1
        fi
        if (( SECONDS >= deadline )); then
            echo "Timed out waiting for ${host}:${port}." >&2
            tail -n 200 "$log" >&2 || true
            return 1
        fi
        sleep 2
    done
    exec 3>&-
    exec 3<&-
}

# 每项后台服务都用独立 session 启动，因此其 PID 也是专属进程组 ID。失败时
# 可连同服务创建的子进程一起回收，而不会按名称误杀其他作业的服务。
stop_service() {
    local pid="$1"
    local name="$2"
    local pgid

    [[ -n "$pid" ]] || return 0
    if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        return 0
    fi

    pgid=$(ps -o pgid= -p "$pid" | tr -d '[:space:]')
    if [[ "$pgid" != "$pid" ]]; then
        echo "Refusing to stop $name as a process group: PID=$pid PGID=${pgid:-unknown}" >&2
        kill "$pid" 2>/dev/null || true
    else
        echo "Stopping $name process group (PGID=$pgid)"
        kill -- "-$pgid" 2>/dev/null || true
    fi

    for _ in {1..30}; do
        kill -0 "$pid" 2>/dev/null || {
            wait "$pid" 2>/dev/null || true
            return 0
        }
        sleep 1
    done

    if [[ "$pgid" == "$pid" ]]; then
        kill -KILL -- "-$pgid" 2>/dev/null || true
    else
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
}

capture_server_metrics() {
    local snapshot="$RESULT_DIR/sglang_metrics_$(date +%Y%m%dT%H%M%S).prom"

    curl -fsS "http://127.0.0.1:$PORT/metrics" >"$snapshot" 2>/dev/null || rm -f "$snapshot"
}

cleanup() {
    local rc=$?
    trap - EXIT INT TERM
    capture_server_metrics
    stop_service "$SERVER_PID" SGLang
    stop_service "$CLIENT_PID" Mooncake-client
    stop_service "$MASTER_PID" Mooncake-master
    exit "$rc"
}
trap cleanup EXIT INT TERM

resolve_trace_source
install_agentic_deps

setsid mooncake_master \
    --logtostderr \
    --eviction_high_watermark_ratio=0.8 \
    --enable_http_metadata_server \
    --eviction_ratio=0.1 \
    --enable_offload=true >"$MASTER_LOG" 2>&1 &
MASTER_PID=$!
wait_for_tcp_port 127.0.0.1 50051 "$MASTER_LOG" "$MASTER_PID"

setsid mooncake_client \
    --host=127.0.0.1 \
    --global_segment_size=160GB \
    --local_buffer_size=4GB \
    --master_server_address=127.0.0.1:50051 \
    --metadata_server=P2PHANDSHAKE \
    --protocol="$MOONCAKE_PROTOCOL" \
    --device_names="$MOONCAKE_DEVICE" \
    --port=50052 \
    --logtostderr \
    --enable_http_server \
    --http_port=9300 \
    --enable_offload=true >"$CLIENT_LOG" 2>&1 &
CLIENT_PID=$!
wait_for_ready \
    --endpoint http://127.0.0.1:9300/health \
    --log "$CLIENT_LOG" \
    --pid "$CLIENT_PID" \
    --timeout 300 \
    --sleep-interval 2

MAX_RUNNING_REQUESTS=$((2 * CONC))
CUDA_GRAPH_MAX_BS=$MAX_RUNNING_REQUESTS
(( CUDA_GRAPH_MAX_BS > 128 )) && CUDA_GRAPH_MAX_BS=128

SGLANG_ARGS=(
    --reasoning-parser deepseek-v4
    --tool-call-parser deepseekv4
    --tp-size "$TP"
    --dist-timeout 10000
    --watchdog-timeout 3600
    --port "$PORT"
    --host 0.0.0.0
    --model-path "$MODEL"
    --model-loader-extra-config '{"enable_multithread_load":"true","num_threads":64}'
    --trust-remote-code
    --context-length "${MAX_MODEL_LEN:-131072}"
    --chunked-prefill-size 32768
    --disable-flashinfer-autotune
    --skip-server-warmup
    --cuda-graph-max-bs "$CUDA_GRAPH_MAX_BS"
    --mem-fraction-static 0.9
    --speculative-algorithm DSPARK
    --speculative-num-steps 1
    --speculative-eagle-topk 1
    --max-running-requests "$MAX_RUNNING_REQUESTS"
    --enable-metrics
    --swa-full-tokens-ratio 0.9
    --moe-runner-backend aiter
    --quantization slimquant_marlin
    --moe-a2a-backend none
    --tokenizer-worker-num 8
    --enable-unified-cache-external-linker
    --unified-cache-external-linker-backend mooncake
    --mooncake-enable-page-wise-load
    --mooncake-page-wise-load-threshold 1
    --enable-cache-report
)

if [[ "$DP_ATTENTION" == "true" ]]; then
    SGLANG_ARGS+=(--dp "$TP" --enable-dp-attention --enable-dp-lm-head)
fi
if [[ "$EP_SIZE" -gt 1 ]]; then
    SGLANG_ARGS+=(--ep-size "$EP_SIZE")
fi

write_command "$RESULT_DIR/sglang_command.txt" sglang serve "${SGLANG_ARGS[@]}"
{
    echo "=== SGLang environment at launch ==="
    env | grep -E '^(SGLANG_|MOONCAKE_|MC_)' | sort || true
    echo "===================================="
} >> "$SERVER_LOG"

setsid sglang serve "${SGLANG_ARGS[@]}" >>"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
wait_for_server_ready --port "$PORT" --server-log "$SERVER_LOG" --server-pid "$SERVER_PID"
capture_server_metrics

if [[ "${EVAL_ONLY:-false}" == "true" ]]; then
    run_eval --framework lm-eval --port "$PORT"
else
    build_replay_cmd "$RESULT_DIR"
    REPLAY_CMD+=" --server-metrics http://localhost:$PORT/metrics"
    run_agentic_replay_and_write_outputs "$RESULT_DIR"
fi
