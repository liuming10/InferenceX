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

# 多节点工作流仍按 runner 名称调用本入口；显式转交给独立的
# Slurm/Enroot/SGLang 多节点实现，避免把多节点变量误送入单机 recipe。
if [[ "${IS_MULTINODE:-false}" == "true" ]]; then
    exec bash "$(dirname "${BASH_SOURCE[0]}")/launch_multi_dcu-hygon.sh" "$@"
fi

# DCU master 配置通过 workflow 的 environment 传入本脚本；本脚本不直接解析
# configs/dcu-master.yaml。下面按“已接入单机启动链路”和“当前单机未使用”列出
# 参数，便于核对 master 配置 -> matrix -> workflow -> launcher 的传递关系。
#
# workflow 传入并由本 launcher 可取得的参数（srun --export=ALL）：
#   顶层：IMAGE MODEL MODEL_PREFIX RUNNER_NAME PRECISION FRAMEWORK
#   单机拓扑：TP PP_SIZE DCP_SIZE PCP_SIZE EP_SIZE DP_ATTENTION
#   负载：CONC SPEC_DECODING KV_OFFLOADING KV_OFFLOAD_BACKEND
#   场景：SCENARIO_TYPE SCENARIO_SUBDIR IS_AGENTIC DURATION MAX_MODEL_LEN
#   结果/评测：RESULT_DIR RESULT_FILENAME EVAL_ONLY RUN_EVAL EVAL_LIMIT
#   其他：REQUIRE_POWER KV_OFFLOAD_BACKEND_METADATA ROUTER_METADATA KV_P2P_TRANSFER
#           TOTAL_CPU_DRAM_GB DISAGG
#
# 当前单机 dcu-hygon + sglang 链路实际传入 benchmark recipe 的参数：
#   MODEL MODEL_PREFIX TP PP_SIZE PCP_SIZE EP_SIZE DP_ATTENTION CONC
#   KV_OFFLOADING TOTAL_CPU_DRAM_GB DURATION RESULT_DIR RESULT_FILENAME
#   EVAL_ONLY RUN_EVAL SCENARIO_TYPE SCENARIO_SUBDIR IS_AGENTIC
#   AIPERF_FAILED_REQUEST_THRESHOLD AIPERF_EXPERIMENTAL_FAST
#
# 当前 launcher/recipe 不使用的参数（保留为注释，不能据此启用多节点能力）：
#   DCP_SIZE        -> 当前单机 recipe 没有对应 SGLang 参数
#   SPEC_DECODING   -> 当前 recipe 未实现 speculative decoding
#   MAX_MODEL_LEN   -> 当前值由 recipe 默认 context length 管理，未从 launcher 转发
#   KV_OFFLOAD_BACKEND / KV_OFFLOAD_BACKEND_METADATA
#                   -> 当前 recipe 未将 backend metadata 转成运行参数
#   ROUTER_METADATA -> 单机启动器没有 router 进程
#   KV_P2P_TRANSFER -> 仅多节点 disagg 链路使用
#   DRAM_UTILIZATION-> 当前单机 recipe 未实现 DRAM KV offload
#   DISAGG          -> 单机 launcher 不启动 disaggregated topology
#   PREFILL/DECODE/WORKER/NUM_NODES/HARDWARE/ADDITIONAL_SETTINGS
#                   -> 多节点 topology，不能用于本单机 launcher
#
# benchmark-multinode-tmpl.yml 导出的环境变量；IS_MULTINODE=true 时由本入口
# 转交给 launch_dcu_multi_hygon.sh，下面的多节点变量不会送入单机 recipe：
#   任务标记：
#     IS_MULTINODE=true
#   基础配置：
#     EXP_NAME RECIPE_FINGERPRINT IMAGE MODEL MODEL_PREFIX FRAMEWORK PRECISION
#     ISL OSL MAX_MODEL_LEN DISAGG SCENARIO_TYPE SCENARIO_SUBDIR IS_AGENTIC
#   并发与运行控制：
#     CONC CONC_LIST DURATION SPEC_DECODING
#   Prefill 角色：
#     PREFILL_HARDWARE PREFILL_NUM_WORKERS PREFILL_TP PREFILL_PP_SIZE
#     PREFILL_DCP_SIZE PREFILL_PCP_SIZE PREFILL_EP PREFILL_DP_ATTN
#   Decode 角色：
#     DECODE_HARDWARE DECODE_NUM_WORKERS DECODE_TP DECODE_PP_SIZE
#     DECODE_DCP_SIZE DECODE_PCP_SIZE DECODE_EP DECODE_DP_ATTN
#   组件与 KV 设置：
#     KV_OFFLOADING KV_OFFLOAD_BACKEND KV_OFFLOAD_BACKEND_METADATA
#     ROUTER_METADATA KV_P2P_TRANSFER TOTAL_CPU_DRAM_GB
#   评测与产物：
#     RUN_EVAL EVAL_ONLY EVAL_FRAMEWORK EVAL_SUITE EVAL_CONC EVAL_LIMIT
#     SWEBENCH_GEN_MODE REQUIRE_POWER POWER_PRODUCER_SHA RESULT_FILENAME
#   其他 workflow 环境：
#     HF_TOKEN PYTHONDONTWRITEBYTECODE PYTHONPYCACHEPREFIX SWEBENCH_USE_MODAL
#     MODAL_TOKEN_ID MODAL_TOKEN_SECRET
#
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

# Mooncake RDMA transport 使用的 HCA。容器须同时看到 verbs 设备节点和 HCA
# sysfs 条目，才能将 shca 名称解析为可用 RNIC。
export RDMA_DEVICE_NAMES="${RDMA_DEVICE_NAMES:-shca_0,shca_1,shca_2,shca_3}"
export MOONCAKE_DEVICE="${MOONCAKE_DEVICE:-$RDMA_DEVICE_NAMES}"
export RDMA_DEVICES_HOST_PATH="${RDMA_DEVICES_HOST_PATH:-/dev/infiniband}"
export RDMA_SYSFS_HOST_PATH="${RDMA_SYSFS_HOST_PATH:-/sys/class/infiniband}"

# Hugging Face 将 hub/（snapshot 与 blobs）和 datasets/（Arrow 数据与索引）作为
# 两套独立缓存。模型 hub 缓存保持只读；datasets 在命中缓存时仍会创建 FileLock，
# 因此必须读写挂载，否则 AIPerf 会在 DatasetBuilder 初始化前失败。
export HF_CACHE_ROOT_HOST_PATH="${HF_CACHE_ROOT_HOST_PATH:-/stortest/lium_space/agentX/actions-runner/huggingface}"
export HF_HUB_CACHE_HOST_PATH="${HF_HUB_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/hub}"
export HF_DATASETS_CACHE_HOST_PATH="${HF_DATASETS_CACHE_HOST_PATH:-$HF_CACHE_ROOT_HOST_PATH/datasets}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-/mnt/hf_hub_cache}"
export HF_DATASETS_CACHE="${HF_DATASETS_CACHE:-/mnt/hf_datasets_cache}"

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
for access in r x; do
    if ! test "-$access" "$DFS_ROOT_DIR"; then
        echo "Mooncake DFS root is not host-${access}-accessible: $DFS_ROOT_DIR" >&2
        exit 1
    fi
done
if [[ ! -d "$RDMA_DEVICES_HOST_PATH" || ! -r "$RDMA_DEVICES_HOST_PATH" || ! -x "$RDMA_DEVICES_HOST_PATH" ]]; then
    echo "RDMA device directory is not accessible: $RDMA_DEVICES_HOST_PATH" >&2
    exit 1
fi
if [[ ! -d "$RDMA_SYSFS_HOST_PATH" || ! -r "$RDMA_SYSFS_HOST_PATH" || ! -x "$RDMA_SYSFS_HOST_PATH" ]]; then
    echo "RDMA sysfs directory is not accessible: $RDMA_SYSFS_HOST_PATH" >&2
    exit 1
fi
IFS=',' read -r -a rdma_devices <<< "$RDMA_DEVICE_NAMES"
for rdma_device in "${rdma_devices[@]}"; do
    if [[ ! -d "$RDMA_SYSFS_HOST_PATH/$rdma_device" ]]; then
        echo "Required RDMA HCA is unavailable: $RDMA_SYSFS_HOST_PATH/$rdma_device" >&2
        exit 1
    fi
done
if [[ ! -d "$HF_HUB_CACHE_HOST_PATH" || ! -r "$HF_HUB_CACHE_HOST_PATH" || ! -x "$HF_HUB_CACHE_HOST_PATH" ]]; then
    echo "Hugging Face hub cache is not readable: $HF_HUB_CACHE_HOST_PATH" >&2
    exit 1
fi
if [[ ! -d "$HF_DATASETS_CACHE_HOST_PATH" || ! -r "$HF_DATASETS_CACHE_HOST_PATH" || ! -w "$HF_DATASETS_CACHE_HOST_PATH" || ! -x "$HF_DATASETS_CACHE_HOST_PATH" ]]; then
    echo "Hugging Face datasets cache is not readable/writable: $HF_DATASETS_CACHE_HOST_PATH" >&2
    exit 1
fi

# srun 会启动新的 Bash 进程。导出该函数，使新进程可在申请到的 DCU 资源中
# 执行镜像缓存与容器生命周期逻辑。
run_dcu_container() {
    set -euo pipefail

    local safe_image squash_file lock_file container_name

    # Enroot 通过 SHELLOPTS 把调用者的 Bash 选项传给其 runtime shell。launcher
    # 启用了 nounset，但 Enroot 生成的 environment 文件允许含延迟变量展开的值；
    # 在中断清理时以 nounset 加载该文件会使可选变量变成致命错误。Enroot 自身不
    # 需要继承 launcher 的 shell 选项或导出的函数，因此只在 Enroot 子进程中移除它们。
    run_enroot() {
        env -u SHELLOPTS -u BASHOPTS -u BASH_ENV \
            -u 'BASH_FUNC_run_dcu_container%%' enroot "$@"
    }

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
        run_enroot import -o "$squash_file" "dockerd://$IMAGE"
    fi
    flock -u 9

    # 无论成功、报错、中断或被终止，都清理 Enroot 容器元数据。
    cleanup_container() {
        run_enroot remove -f "$container_name" >/dev/null 2>&1 || true
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
    run_enroot remove -f "$container_name" >/dev/null 2>&1 || true
    run_enroot create --name "$container_name" "$squash_file"

    # 在实际容器身份中确认 Mooncake 所需的 RDMA 发现路径和 DFS 权限。DFS
    # probe 只会创建、读取并删除一个由本次容器 ID 唯一限定的临时目录与文件。
    run_enroot start --root --rw \
        --mount "$DFS_ROOT_DIR:$DFS_ROOT_DIR:none:x-create=dir,bind,rw" \
        --mount "$RDMA_DEVICES_HOST_PATH:$RDMA_DEVICES_HOST_PATH:none:x-create=dir,rbind,rw" \
        --mount "$RDMA_SYSFS_HOST_PATH:$RDMA_SYSFS_HOST_PATH:none:x-create=dir,rbind,ro" \
        --env "DFS_ROOT_DIR=$DFS_ROOT_DIR" \
        --env "RDMA_DEVICE_NAMES=$RDMA_DEVICE_NAMES" \
        --env "RDMA_DEVICES_HOST_PATH=$RDMA_DEVICES_HOST_PATH" \
        --env "RDMA_SYSFS_HOST_PATH=$RDMA_SYSFS_HOST_PATH" \
        --env "DFS_PROBE_NAME=.inferencex-dfs-probe-${container_name}" \
        "$container_name" bash -c '
            set -euo pipefail
            for access in r x; do
                if ! test "-$access" "$DFS_ROOT_DIR"; then
                    echo "Mooncake DFS root is not container-${access}-accessible: $DFS_ROOT_DIR" >&2
                    exit 1
                fi
            done
            IFS="," read -r -a rdma_devices <<< "$RDMA_DEVICE_NAMES"
            for rdma_device in "${rdma_devices[@]}"; do
                if [[ ! -d "$RDMA_SYSFS_HOST_PATH/$rdma_device" ]]; then
                    echo "Required RDMA HCA is not visible in the container: $RDMA_SYSFS_HOST_PATH/$rdma_device" >&2
                    exit 1
                fi
            done
            if ! compgen -G "$RDMA_DEVICES_HOST_PATH/*" >/dev/null; then
                echo "RDMA device nodes are not visible in the container: $RDMA_DEVICES_HOST_PATH" >&2
                exit 1
            fi
            dfs_probe="$DFS_ROOT_DIR/$DFS_PROBE_NAME"
            cleanup_dfs_probe() {
                rm -rf -- "$dfs_probe"
            }
            trap cleanup_dfs_probe EXIT INT TERM
            mkdir "$dfs_probe"
            printf "inferencex-dfs-probe\\n" > "$dfs_probe/write-test"
            test -r "$dfs_probe/write-test"
            test -s "$dfs_probe/write-test"
            cleanup_dfs_probe
            trap - EXIT INT TERM
            printf "Mooncake preflight passed: DFS root is container-readable/writable/searchable; RDMA HCAs: %s\\n" "$RDMA_DEVICE_NAMES"
        '

    # Bind mount：
    # - workspace：脚本、生成的配置、日志和 benchmark 结果。
    # - model：以原始绝对路径只读挂载 checkpoint。
    # - DFS root：以原始路径读写挂载 Mooncake 后端存储。
    # - Hugging Face hub/datasets cache：分别以只读方式映射，供 AgentX 离线复用 trace。
    # - DCU 设备与 /opt/hyhal：Hygon 驱动接口及用户态运行时。
    #
    # 下方环境变量按用途划分：端点/模型、并行参数、AgentX 负载与结果、
    # 场景/评测设置、AIPerf 失败策略，以及 Mooncake DFS 路径。
    run_enroot start --root --rw \
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
        --env "HF_DATASETS_CACHE=$HF_DATASETS_CACHE" \
        --env "HF_HUB_OFFLINE=1" \
        --env "HF_DATASETS_OFFLINE=1" \
        --env "MOONCAKE_DFS_ROOT_DIR=$DFS_ROOT_DIR" \
        --env "MOONCAKE_OFFLOAD_FILE_STORAGE_PATH=$DFS_ROOT_DIR" \
        --env "MOONCAKE_DEVICE=$MOONCAKE_DEVICE" \
        --env "MC_TE_FILTERS=$MOONCAKE_DEVICE" \
        "$container_name" bash "$DCU_BENCHMARK_SCRIPT"
}

# export -f run_dcu_container

# 提交一个独占的单节点 Slurm step。不要使用 export -f：导出的函数会以
# BASH_FUNC_run_dcu_container%% 进入 Enroot 的 environment hook；该 hook 在
# 中断清理时重新展开函数体，可能因 container_name 是局部变量而触发 nounset。
# 直接把函数定义作为 bash 命令传给 srun，既保留 Slurm 子 shell 的执行能力，
# 又不把函数定义放进 Enroot 的环境变量。

srun \
    --partition="$DCU_PARTITION" \
    --gres="dcu:${DCU_COUNT}" \
    --exclusive \
    --cpus-per-task="$DCU_CPUS_PER_TASK" \
    --time="$DCU_TIME_LIMIT" \
    --job-name="${RUNNER_NAME:-dcu-hygon}" \
    --export=ALL,ENROOT_RUNTIME_PATH \
    bash -c "$(declare -f run_dcu_container); run_dcu_container"
