#!/usr/bin/env bash
#
# 在 Slurm 分配的资源中，以 Enroot 容器启动一个 Hygon DCU 单机 benchmark。
# benchmark-tmpl.yml 根据 runner 标签选择本启动器：
# dcu-hygon_00 -> launch_dcu-hygon.sh。
#
# 执行链路：校验输入 -> srun 分配资源 -> 锁定/导入共享 .sqsh 镜像
# -> 创建/启动 Enroot -> 执行所选 benchmark 脚本 -> 删除容器。
#
# 宿主机前置条件：
# - github-runner 可申请 local Slurm 分区和 DCU_COUNT 张 DCU。
# - github-runner 通过 squash-cache 用户组可写入 SQUASH_CACHE_DIR。
# - ENROOT_RUNTIME_PATH 为 github-runner 私有且可写的目录。
# - 仅当所需 .sqsh 镜像缓存不存在时，才需要 Docker socket 访问权限。
# - 宿主机存在 MODEL、DFS_ROOT_DIR、DCU 设备节点和 /opt/hyhal。

set -euo pipefail

# 必填的 workflow 输入参数。
# IMAGE：宿主机 Docker daemon 中已有的镜像；将被缓存为 .sqsh。
# MODEL：checkpoint 的绝对路径；在容器中以相同路径只读挂载。
# EXP_NAME：实验名；其前缀用于选择 benchmark shell 脚本。
# PRECISION：脚本名中的精度后缀，例如 w4a8。
# FRAMEWORK：推理框架；本启动器仅支持 sglang。
# GITHUB_WORKSPACE：已 checkout 的仓库；以读写方式挂载到 /workspace。
: "${IMAGE:?IMAGE must be set}"
: "${MODEL:?MODEL must be set}"
: "${EXP_NAME:?EXP_NAME must be set}"
: "${PRECISION:?PRECISION must be set}"
: "${FRAMEWORK:?FRAMEWORK must be set}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set}"

# 可选的服务与 Slurm 控制参数。workflow 提供的值会覆盖这些默认值。
# PORT 为 SGLang 服务端口；DCU_TIME_LIMIT 的单位为分钟，对应 srun --time。
export PORT="${PORT:-30001}"
export DCU_COUNT="${DCU_COUNT:-8}"
export DCU_PARTITION="${DCU_PARTITION:-local}"
export DCU_CPUS_PER_TASK="${DCU_CPUS_PER_TASK:-128}"
export DCU_TIME_LIMIT="${DCU_TIME_LIMIT:-500}"

# 持久化 Enroot 镜像缓存。每个镜像都有同名 .lock 文件，防止并发 workflow
# 同时向同一个 .sqsh 文件导入镜像。
export SQUASH_CACHE_DIR="${SQUASH_CACHE_DIR:-/data02/lium_space/squash}"

# 共享的 Mooncake DFS/offload 存储。它以相同绝对路径读写 bind mount 到容器，
# 使 Mooncake 配置可直接在容器内使用。
export DFS_ROOT_DIR="${DFS_ROOT_DIR:-/stortest/lium_space/dfs_storage/102111128}"

# 宿主机已预下载的 Hugging Face 缓存根目录。须挂载完整根目录而不是仅挂载某个
# 数据集目录，以保留 hub/ 快照、blobs 和 datasets/ 元数据之间的引用关系。
export HF_HUB_CACHE_HOST_PATH="${HF_HUB_CACHE_HOST_PATH:-/ai_data/datasets/huggingface}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/hf_hub_cache}"

# 宿主机侧的 Enroot 运行时状态目录。非 root runner 无法使用 /run/enroot，
# 因此在 /data02 上使用按 UID 隔离的私有目录。该目录不是容器挂载点。
export ENROOT_RUNTIME_PATH="${ENROOT_RUNTIME_PATH:-/data02/lium_space/tmp/enroot-${UID}/runtime}"

# AgentX 会传入 SCENARIO_SUBDIR=agentic/；其他场景保留历史默认值
# fixed_seq_len/。${EXP_NAME%%_*} 提取 EXP_NAME 中的模型前缀。
export DCU_BENCHMARK_SCRIPT="/workspace/benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${EXP_NAME%%_*}_${PRECISION}_dcu-hygon_${FRAMEWORK}.sh"

# 在 srun 前创建运行时目录。下方 srun --export 会将此精确路径传入 Slurm
# 任务，避免 Enroot 回退使用无权限的 /run。
install -d -m 700 "$ENROOT_RUNTIME_PATH"

if [[ "$FRAMEWORK" != "sglang" ]]; then
    echo "DCU Hygon launcher only supports FRAMEWORK=sglang, got '$FRAMEWORK'" >&2
    exit 1
fi

# DCU_BENCHMARK_SCRIPT 是容器路径。去掉 /workspace 后可映射为宿主机 checkout
# 路径，用于在启动前快速检查脚本是否存在。
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
if [[ ! -d "$HF_HUB_CACHE_HOST_PATH" || ! -r "$HF_HUB_CACHE_HOST_PATH" || ! -x "$HF_HUB_CACHE_HOST_PATH" ]]; then
    echo "Hugging Face cache is not readable: $HF_HUB_CACHE_HOST_PATH" >&2
    exit 1
fi

# srun 会启动新的 Bash 进程。导出该函数，使新进程可在申请到的 DCU 资源中
# 执行镜像缓存与容器生命周期逻辑。
run_dcu_container() {
    set -euo pipefail

    local safe_image squash_file lock_file container_name

    # 将 registry/image:tag 转换为共享 SquashFS 缓存中的安全文件名。
    safe_image=$(printf '%s' "$IMAGE" | sed 's#[/:@#]#_#g')
    squash_file="$SQUASH_CACHE_DIR/${safe_image}.sqsh"
    lock_file="${squash_file}.lock"

    # 容器名包含 Slurm ID，避免多个资源分配共享同一容器状态。
    container_name="inferencex-dcu-${SLURM_JOB_ID:-manual}-${SLURM_STEP_ID:-0}"

    mkdir -p "$SQUASH_CACHE_DIR"

    # 文件描述符 9 持有镜像专属的排他锁。等待最多十分钟，避免中断的导入任务
    # 无限阻塞后续 workflow。
    exec 9>"$lock_file"
    flock -w 600 9 || {
        echo "Timed out waiting for Enroot image lock: $lock_file" >&2
        exit 1
    }

    # 复用有效的缓存镜像。若文件缺失或无效，在持锁期间从本地 Docker daemon 导入，
    # 再作为共享缓存供后续任务使用。
    if ! unsquashfs -l "$squash_file" >/dev/null 2>&1; then
        rm -f "$squash_file"
        echo "Importing local Docker image into $squash_file"
        enroot import -o "$squash_file" "dockerd://$IMAGE"
    fi
    flock -u 9

    # 无论成功、报错、中断或被终止，都清理 Enroot 容器元数据。
    cleanup_container() {
        enroot remove -f "$container_name" >/dev/null 2>&1 || true
    }
    trap cleanup_container EXIT INT TERM

    # Mooncake 使用 50051、50052、9300；PORT 为 SGLang 端口。该部署为单主机，
    # 因此任一所需端口已被监听时均拒绝启动。
    for port in 50051 50052 9300 "$PORT"; do
        if ss -ltn "sport = :${port}" | grep -q LISTEN; then
            echo "Refusing to start: TCP port ${port} is already in use on $(hostname)." >&2
            exit 1
        fi
    done

    # 删除此前中断运行遗留的元数据，再由已校验的共享 SquashFS 镜像创建新的
    # 可写 Enroot 容器。
    enroot remove -f "$container_name" >/dev/null 2>&1 || true
    enroot create --name "$container_name" "$squash_file"

    # Bind mount：
    # - workspace：脚本、生成的配置、日志和 benchmark 结果。
    # - model：以原始绝对路径只读挂载 checkpoint。
    # - DFS root：以原始路径读写挂载 Mooncake 后端存储。
    # - Hugging Face cache：以只读方式映射到 /hf_hub_cache，供 AgentX 复用本地 trace。
    # - DCU 设备与 /opt/hyhal：Hygon 驱动接口及用户态运行时。
    #
    # 下方环境变量按用途划分：端点/模型、并行参数、AgentX 负载与结果、
    # 场景/评测设置、AIPerf 失败策略，以及 Mooncake DFS 路径。
    enroot start --root --rw \
        --mount "$GITHUB_WORKSPACE:/workspace:none:x-create=dir,bind,rw" \
        --mount "$MODEL:$MODEL:none:x-create=dir,bind,ro" \
        --mount "$DFS_ROOT_DIR:$DFS_ROOT_DIR:none:x-create=dir,bind,rw" \
        --mount "$HF_HUB_CACHE_HOST_PATH:$HF_HUB_CACHE:none:x-create=dir,bind,ro" \
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
        --env "HF_HUB_CACHE=$HF_HUB_CACHE" \
        --env "MOONCAKE_DFS_ROOT_DIR=$DFS_ROOT_DIR" \
        --env "MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=$DFS_ROOT_DIR" \
        "$container_name" bash "$DCU_BENCHMARK_SCRIPT"
}

export -f run_dcu_container

# 提交一个独占的单节点 Slurm step。--export=ALL 保留所有 workflow 变量；
# 显式列出 ENROOT_RUNTIME_PATH 以增强清晰度并确保跨 Slurm 环境传递。
srun \
    --partition="$DCU_PARTITION" \
    --gres="dcu:${DCU_COUNT}" \
    --exclusive \
    --cpus-per-task="$DCU_CPUS_PER_TASK" \
    --time="$DCU_TIME_LIMIT" \
    --job-name="${RUNNER_NAME:-dcu-hygon}" \
    --export=ALL,ENROOT_RUNTIME_PATH \
    bash -c run_dcu_container
