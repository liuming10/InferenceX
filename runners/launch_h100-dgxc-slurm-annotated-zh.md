# `launch_h100-dgxc-slurm.sh` 小白注释版

> 对应原脚本：[`launch_h100-dgxc-slurm.sh`](launch_h100-dgxc-slurm.sh)
>
> 调用者：[`../.github/workflows/benchmark-tmpl.yml`](../.github/workflows/benchmark-tmpl.yml) 与 [`../.github/workflows/benchmark-multinode-tmpl.yml`](../.github/workflows/benchmark-multinode-tmpl.yml)
>
> 本文按“原文件节选 + 说明”解释 H100 DGXC Slurm launcher。实际运行以原脚本为准。
>
> **安全边界：** 该脚本会申请 Slurm GPU、可能导入容器镜像、运行 benchmark/Eval、删除当前工作目录内的 `outputs/`。不要把它当成普通本地脚本执行；执行到 `salloc` 或 `srtctl apply` 就会真实占用 H100 集群资源。

---

## 1. 它在 workflow 链路中的位置

```text
perf-changelog.yaml / master config
  → infx.matrix.plan 生成 matrix row
  → run-sweep.yml 调用 reusable benchmark template
  → GitHub Job 被分配到具体 self-hosted runner
  → RUNNER_NAME=b200... 或 h100-dgxc-slurm_XX
  → 依据名字前缀调用本脚本
  → 单节点：salloc + srun
  → 多节点：srtctl apply + Slurm
  → 回收 Benchmark / Eval / server logs
```

workflow 中的通用调用形式是：

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

因此：

```text
RUNNER_NAME=h100-dgxc-slurm_03
              └─────────────┘
              第一个 _ 前的前缀

实际执行：
bash ./runners/launch_h100-dgxc-slurm.sh
```

这个脚本有两个大分支：

| 条件 | 路径 | 核心工具 |
|---|---|---|
| `IS_MULTINODE=true` | 多节点部署与 Benchmark/Eval | `srtctl apply` → Slurm |
| 其他值 | 单节点 Benchmark/AgentX | `salloc` + `srun` |

---

## 2. 集群固定参数与 speculative decoding 脚本后缀

### 原文件节选：`launch_h100-dgxc-slurm.sh:1-15`

```bash
#!/usr/bin/bash
set -e

source "$(dirname "${BASH_SOURCE[0]}")/slurm_utils.sh"

# System-specific configuration for H100 DGXC Slurm cluster
SLURM_PARTITION="hpc-gpu-1"
SLURM_ACCOUNT="customer"

SPEC_SUFFIX=$([[ "$SPEC_DECODING" == "mtp" ]] && printf '_mtp' || printf '')

set -x
```

### 说明

| 项目 | 含义 |
|---|---|
| `set -e` | 任意未处理的失败命令会让脚本立即退出，避免带着错误状态继续提交/收集结果。 |
| `slurm_utils.sh` | 共享函数库，例如固定长度结果复制、Eval artifact 处理、Slurm 辅助逻辑。 |
| `SLURM_PARTITION=hpc-gpu-1` | H100 DGXC 集群的 Slurm 分区。可理解为允许该类工作负载进入的资源池。 |
| `SLURM_ACCOUNT=customer` | Slurm account；影响记账、配额和集群策略。 |
| `SPEC_SUFFIX` | 若 `SPEC_DECODING=mtp`，附加 `_mtp`，以选择带 MTP 的 benchmark recipe。 |

例如：

```text
SPEC_DECODING=mtp
EXP_NAME=dsr1_8k1k
PRECISION=fp8
FRAMEWORK=vllm

候选单节点脚本：
benchmarks/single_node/fixed_seq_len/dsr1_fp8_h100_vllm_mtp.sh
```

---

# 第一条路径：多节点 (`IS_MULTINODE=true`)

## 3. 多节点路径支持哪些模型与框架

### 原文件节选：`launch_h100-dgxc-slurm.sh:17-42`

```bash
if [[ "$IS_MULTINODE" == "true" ]]; then
    if [[ $FRAMEWORK == "dynamo-sglang" ]]; then
        if [[ $MODEL_PREFIX == "dsr1" && $PRECISION == "fp8" ]]; then
            export MODEL_PATH="/mnt/nfs/lustre/models/dsr1-fp8"
            export SRT_SLURM_MODEL_PREFIX="dsr1-fp8"
        else
            echo "Unsupported model prefix/precision for dynamo-sglang"
            exit 1
        fi
    elif [[ $FRAMEWORK == "dynamo-trt" ]]; then
        if [[ $MODEL_PREFIX == "dsr1" && $PRECISION == "fp8" ]]; then
            export MODEL_PATH="/mnt/nfs/lustre/models/dsr1-fp8"
            export SERVED_MODEL_NAME="DeepSeek-R1-0528"
            export SRT_SLURM_MODEL_PREFIX="DeepSeek-R1-0528"
        else
            echo "Unsupported model prefix/precision for dynamo-trt"
            exit 1
        fi
    else
        echo "Unsupported framework: $FRAMEWORK"
        exit 1
    fi
```

### 说明

该版本脚本的多节点路径不是一个“支持所有 H100 模型”的通用 launcher。当前逻辑只接受：

```text
MODEL_PREFIX=dsr1
PRECISION=fp8
FRAMEWORK=dynamo-sglang 或 dynamo-trt
```

否则立即失败，不会尝试猜测模型路径或 framework recipe。

这里的几个名称含义不同：

| 变量 | 示例 | 用途 |
|---|---|---|
| `MODEL_PREFIX` | `dsr1` | matrix 中的简写，用于选择分支。 |
| `MODEL_PATH` | `/mnt/nfs/lustre/models/dsr1-fp8` | H100 集群共享 Lustre 上预下载的真实权重目录。 |
| `SRT_SLURM_MODEL_PREFIX` | `dsr1-fp8` 或 `DeepSeek-R1-0528` | `srt-slurm` recipe 使用的模型别名。 |
| `SERVED_MODEL_NAME` | `DeepSeek-R1-0528` | 推理服务对 API client 暴露的模型名。 |

master YAML 可能为了可移植性保存 Hugging Face model ID；这里覆盖为本集群已有的本地路径，避免每次多节点任务重新下载模型。

---

## 4. clone `srt-slurm`、处理 AgentX 与 evaluator

### 原文件节选：`launch_h100-dgxc-slurm.sh:44-63`

```bash
SRT_REPO_DIR="srt-slurm"
if [ -d "$SRT_REPO_DIR" ]; then
    rm -rf "$SRT_REPO_DIR"
fi

if [[ "$IS_AGENTIC" == "1" ]]; then
    git clone --branch cam/sa-submission-q2-2026 --single-branch \
      https://github.com/cquil11/srt-slurm-nv.git "$SRT_REPO_DIR"
    cd "$SRT_REPO_DIR"
else
    git clone https://github.com/NVIDIA/srt-slurm.git "$SRT_REPO_DIR"
    cd "$SRT_REPO_DIR"
    git checkout sa-submission-q2-2026
fi

if [[ "${EVAL_FRAMEWORK:-lm-eval}" != "lm-eval" ]]; then
    python3 "$GITHUB_WORKSPACE/runners/patch_srt_eval_dispatch.py" "$(pwd)"
fi
```

### 说明

`srt-slurm` 是把多节点部署 recipe 转换成 Slurm 工作负载的外部工具。它负责的概念链路是：

```text
recipe
  → 生成容器与分布式进程启动配置
  → 提交 Slurm job
  → 产生 logs / results 目录
```

这里首先删除当前 workspace 下旧的临时 `srt-slurm/` clone，再 clone 新版本。

| 条件 | clone 来源 | 原因 |
|---|---|---|
| `IS_AGENTIC=1` | `cquil11/srt-slurm-nv` 的 `cam/sa-submission-q2-2026` branch | AgentX/agentic 路径使用的专用实现。 |
| 非 AgentX | `NVIDIA/srt-slurm` 的 `sa-submission-q2-2026` ref | 普通多节点固定长度部署路径。 |

如果 Eval framework 不是默认：

```text
lm-eval
```

脚本会调用 `patch_srt_eval_dispatch.py`，让 cloned srt-slurm 的 post-run Eval 分发能够调用指定的 evaluator。

`rm -rf "$SRT_REPO_DIR"` 删除的是**当前工作目录里的临时 clone**，不是模型权重目录。但它仍是删除操作，只能在受控 CI workspace 中使用。

---

## 5. 安装 `srtctl`：共享 uv 缓存，当前 clone 的 Python 环境

### 原文件节选：`launch_h100-dgxc-slurm.sh:66-84`

```bash
export UV_INSTALL_DIR="/mnt/nfs/sa-shared/.uv/bin"
export UV_CACHE_DIR="/mnt/nfs/sa-shared/.uv/cache"
export UV_PYTHON_INSTALL_DIR="/mnt/nfs/sa-shared/.uv/python"
mkdir -p "$UV_INSTALL_DIR" "$UV_CACHE_DIR" "$UV_PYTHON_INSTALL_DIR"
if ! [ -x "$UV_INSTALL_DIR/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$UV_INSTALL_DIR:$PATH"
source $UV_INSTALL_DIR/env

uv venv
source .venv/bin/activate
uv pip install -e .

if ! command -v srtctl &> /dev/null; then
    exit 1
fi
```

### 说明

此时脚本已进入 `srt-slurm/` clone。

```bash
uv venv
uv pip install -e .
```

会为当前 clone 创建 `.venv`，并将 clone 本身以 editable 方式安装，因此获得：

```text
srtctl
```

这个 `.venv` 是多节点 CI launcher 使用的环境，不是项目中用于其他诊断/开发的虚拟环境。

H100 DGXC 把 `uv` 的安装目录、wheel/download cache、Python 下载目录放在共享 NFS 路径。这样多个 runner 可复用工具和缓存，而不必每个 Job 重复下载。

最后：

```bash
command -v srtctl
```

确认提交 Slurm workload 所需的 CLI 确实可用。

---

## 6. 模型与容器镜像的就绪检查

### 原文件节选：`launch_h100-dgxc-slurm.sh:88-96`

```bash
NGINX_SQUASH_FILE="/mnt/nfs/lustre/containers/nginx_1.27.4.sqsh"

resolve_h100_srt_container "$IMAGE" "$FRAMEWORK" || exit 1
check_staged_srt_assets "$MODEL_PATH" "$SQUASH_FILE" || exit 1

export ISL="$ISL"
export OSL="$OSL"
export EVAL_ONLY="${EVAL_ONLY:-false}"
```

### 说明

多节点路径假设模型和容器已经被预置，而不是每次 job 临时下载：

```text
模型：/mnt/nfs/lustre/models/...
容器：/mnt/nfs/lustre/containers/*.sqsh
```

公共函数 `resolve_h100_srt_container` 根据：

```text
IMAGE + FRAMEWORK
```

推导需要使用的 Enroot/SquashFS 文件路径。

`check_staged_srt_assets` 再验证两件事：

```text
模型目录中是否可读取 config.json；
目标 .sqsh 容器文件是否是有效 SquashFS 文件。
```

失败会在提交 Slurm 前终止，避免申请多节点资源后才发现模型或镜像不存在。

---

## 7. 生成 `srtslurm.yaml`：集群基础设施配置

### 原文件节选：`launch_h100-dgxc-slurm.sh:98-126`

```bash
cat > srtslurm.yaml <<EOF
default_account: "${SLURM_ACCOUNT}"
default_partition: "${SLURM_PARTITION}"
default_time_limit: "6:00:00"
gpus_per_node: 8
network_interface: ""
srtctl_root: "${SRTCTL_ROOT}"
model_paths:
  "${SRT_SLURM_MODEL_PREFIX}": "${MODEL_PATH}"
containers:
  dynamo-trtllm: "${SQUASH_FILE}"
  dynamo-sglang: "${SQUASH_FILE}"
  nginx-sqsh: "${NGINX_SQUASH_FILE}"
  latest: "${SQUASH_FILE}"
  "${CONTAINER_KEY}": "${SQUASH_FILE}"
use_gpus_per_node_directive: true
use_segment_sbatch_directive: false
use_exclusive_sbatch_directive: false
EOF
```

### 说明

`srt-slurm` 还需要知道此 H100 集群的基础设施事实，因此脚本动态生成 `srtslurm.yaml`。

| 字段 | 含义 |
|---|---|
| `default_account` / `default_partition` | Slurm 的默认 account 与分区。 |
| `default_time_limit: 6:00:00` | 默认六小时时间上限。具体 recipe 仍可能定义自身约束。 |
| `gpus_per_node: 8` | 每节点按八张 H100 GPU 处理。 |
| `model_paths` | recipe 的模型 alias 如何映射到真实 Lustre 权重路径。 |
| `containers` | recipe 的容器 alias 如何映射到实际 `.sqsh` 文件。 |
| `use_gpus_per_node_directive: true` | 使用 Slurm 的 `--gpus-per-node` 资源请求方式。 |
| `use_exclusive_sbatch_directive: false` | 此路径没有强制向 Slurm 使用独占节点 directive。具体资源隔离仍受 recipe 与集群策略影响。 |

请区分：

```text
srtslurm.yaml：集群 account、partition、模型路径、容器路径等基础设施参数。
CONFIG_FILE recipe：真正的部署拓扑、推理框架参数、客户端、telemetry、Benchmark/Eval 行为。
```

---

## 8. 修改 recipe 并执行真正的 Slurm 提交

### 原文件节选：`launch_h100-dgxc-slurm.sh:131-164`

```bash
make setup ARCH=x86_64
export INFMAX_WORKSPACE="$GITHUB_WORKSPACE"

if [[ -z "$CONFIG_FILE" ]]; then
    echo "Error: CONFIG_FILE is not set."
    exit 1
fi

sed -i "s/^name:.*/name: \"${RUNNER_NAME}\"/" "$CONFIG_FILE"
sed -i '/^      watchdog-timeout:/a\      dist-timeout: 1800' "${CONFIG_FILE%%:*}"
if [[ "${EVAL_ONLY:-false}" == "true" ]]; then
    python3 "$GITHUB_WORKSPACE/runners/inject_synthetic_acceptance.py" \
        "${CONFIG_FILE%%:*}" "$FRAMEWORK" || exit 1
fi

SRTCTL_OUTPUT=$(srtctl apply -f "$CONFIG_FILE" \
  --tags "h100,${MODEL_PREFIX},${PRECISION},${ISL}x${OSL},infmax-$(date +%Y%m%d)" 2>&1)
```

### 说明

`CONFIG_FILE` 必须由 workflow/matrix 的 additional settings 提供；它为空时立即失败。

脚本会修改当前 CI workspace 中 recipe 的副本：

| 修改 | 原因 |
|---|---|
| `name: "${RUNNER_NAME}"` | Slurm Job 名采用当前 GitHub anchor runner 名，便于清理、过滤和排查。 |
| 在 `watchdog-timeout` 后插入 `dist-timeout: 1800` | 将 SGLang/Torch distributed TCPStore 的分布式初始化超时调整为 1800 秒，避免默认约 600 秒对大规模启动过短。 |
| Eval-only 时调用 `inject_synthetic_acceptance.py` | 按当前 recipe/框架的规则注入 speculative decoding 验收配置；失败则不提交。 |

这些 `sed -i` 改动是 workspace 内的运行时调整，不会自动提交到 GitHub 仓库；不过它们会改变当前 Job 使用的 recipe，因此应只在受控 CI 环境运行。

真正开始消耗集群资源的是：

```bash
srtctl apply -f "$CONFIG_FILE" ...
```

它会将 recipe 渲染为 Slurm workload，并提交模型部署、Benchmark 或 Eval。脚本从输出中提取：

```bash
JOB_ID=...
```

后续所有日志跟踪与结果回收都依赖该 Slurm Job ID。

---

## 9. 等待 Slurm Job、实时查看日志

### 原文件节选：`launch_h100-dgxc-slurm.sh:168-197`

```bash
LOGS_DIR="outputs/$JOB_ID/logs"
LOG_FILE="$LOGS_DIR/sweep_${JOB_ID}.log"

while ! ls "$LOG_FILE" &>/dev/null; do
    if ! squeue -j "$JOB_ID" --noheader 2>/dev/null | grep -q "$JOB_ID"; then
        echo "ERROR: Job $JOB_ID failed before creating log file"
        scontrol show job "$JOB_ID"
        exit 1
    fi
    sleep 5
done

(
    while squeue -j "$JOB_ID" --noheader 2>/dev/null | grep -q "$JOB_ID"; do
        sleep 10
    done
) &
POLL_PID=$!

tail -F -s 2 -n+1 "$LOG_FILE" --pid=$POLL_PID
wait $POLL_PID
```

### 说明

提交成功不代表模型已经成功加载。这里会：

```text
1. 计算 srt-slurm 应写入的日志路径。
2. 每五秒检查日志是否已经出现。
3. 如果日志未出现且 Slurm Job 已消失：打印 `scontrol show job` 诊断后失败。
4. Job 存活且日志出现后，使用 `tail -F` 持续显示日志。
5. 后台轮询 Job 是否还在 `squeue`；离开队列后停止 tail。
```

`tail -F` 比 `tail -f` 更适合 NFS 场景，因为日志可能被重新创建或发生路径替换。

---

## 10. 多节点结果、Eval 与日志回收

### 原文件节选：`launch_h100-dgxc-slurm.sh:201-245`

```bash
cp -r "$LOGS_DIR" "$GITHUB_WORKSPACE/LOGS"
tar czf "$GITHUB_WORKSPACE/multinode_server_logs.tar.gz" -C "$LOGS_DIR" .

if [[ "${EVAL_ONLY:-false}" != "true" ]]; then
    copy_fixed_sequence_results "$LOGS_DIR" "$GITHUB_WORKSPACE" "$RESULT_FILENAME"
else
    echo "EVAL_ONLY=true: Skipping benchmark result collection"
fi

if [[ "${RUN_EVAL:-false}" == "true" || "${EVAL_ONLY:-false}" == "true" ]]; then
    EVAL_DIR="$LOGS_DIR/eval_results"
    ...
    cp "$eval_file" "$GITHUB_WORKSPACE/"
fi

for i in 1 2 3 4 5; do
    rm -rf outputs 2>/dev/null && break
    sleep 10
done
find . -name '.nfs*' -delete 2>/dev/null || true
```

### 说明

| 产物 | 处理方式 | 后续用途 |
|---|---|---|
| 完整 Slurm 日志目录 | 复制为 `$GITHUB_WORKSPACE/LOGS` | workflow artifact，用于排查服务、启动和客户端问题。 |
| 服务日志归档 | `multinode_server_logs.tar.gz` | 上传为 server logs artifact。 |
| 吞吐 Benchmark JSON | `copy_fixed_sequence_results` | 复制到 workspace，供 `collect-results.yml` 聚合性能指标。 |
| Eval artifact | 复制 `eval_results/` 下文件 | 供 `collect-evals.yml` 聚合数据集质量指标。 |

### `EVAL_ONLY=true` 为什么不收集吞吐 JSON

这不是错误。该 matrix row 的语义是：

```text
部署服务
→ 跳过吞吐 Benchmark
→ 运行准确性/能力 Eval
→ 只收集 Eval 输出
```

### 为什么最后删除 `outputs/` 与 `.nfs*`

NFS 上被进程占用又删除的文件可能变成 `.nfs*` 临时文件，阻碍下一次 checkout 或 workspace 清理。脚本最多尝试五次删除 `outputs/`，每次失败等十秒。

删除范围是当前 `srt-slurm` 目录下的 outputs 与 `.nfs*` 文件，但依然属于破坏性操作；不要复制到个人项目或模型目录中执行。

---

# 第二条路径：单节点 (`IS_MULTINODE != true`)

## 11. 单节点路径的资源申请与自动取消

### 原文件节选：`launch_h100-dgxc-slurm.sh:247-262`

```bash
HF_HUB_CACHE_MOUNT="/mnt/nfs/sa-shared/gharunners/hf-hub-cache/"
AIPERF_MMAP_CACHE_HOST_PATH="/mnt/nfs/sa-shared/gharunners/ai-perf-cache"
SQUASH_FILE="/mnt/nfs/lustre/containers/$(echo "$IMAGE" | sed 's/[\/:@#]/_/g').sqsh"
LOCK_FILE="${SQUASH_FILE}.lock"

export GPU_COUNT="${GPU_COUNT:-${TP:?TP must be set}}"

salloc --partition=$SLURM_PARTITION --account=$SLURM_ACCOUNT \
  --gres=gpu:$GPU_COUNT --exclusive --time=180 --no-shell \
  --job-name="$RUNNER_NAME"
JOB_ID=$(squeue --name="$RUNNER_NAME" -u "$USER" -h -o %A | head -n1)

trap 'rc=$?; scancel "$JOB_ID" 2>/dev/null || true; exit "$rc"' EXIT
```

### 说明

单节点路径不会走 `srtctl`。它直接：

```text
salloc：申请一个临时 Slurm allocation。
srun：在该 allocation 内启动容器和 benchmark 脚本。
```

资源数量来自：

```bash
GPU_COUNT="${GPU_COUNT:-${TP:?TP must be set}}"
```

含义：

```text
若外部已经提供 GPU_COUNT，则使用它；
否则使用 TP；
若 TP 也没设置，直接报错。
```

它申请的资源是：

```text
分区：hpc-gpu-1
account：customer
GPU：GPU_COUNT 张
节点：exclusive 独占
时限：180 分钟
```

随后按 `RUNNER_NAME` 查找刚申请到的 Job ID。`trap` 很关键：无论后续成功、失败还是脚本被中断，都会尝试：

```bash
scancel "$JOB_ID"
```

防止 allocation 因脚本提前退出而继续占着 GPU。

---

## 12. 单节点容器镜像缓存：先验证，再加锁导入

### 原文件节选：`launch_h100-dgxc-slurm.sh:264-281`

```bash
srun --jobid=$JOB_ID bash -c "
    if unsquashfs -l \"$SQUASH_FILE\" > /dev/null 2>&1; then
        echo 'Squash file already exists and is valid, skipping import'
    else
        ...
        flock -w 600 9 || exit 1
        if unsquashfs -l \"$SQUASH_FILE\" > /dev/null 2>&1; then
            echo 'Squash file was imported by another job'
        else
            rm -f \"$SQUASH_FILE\"
            enroot import -o \"$SQUASH_FILE\" docker://$IMAGE
        fi
    fi
"
```

### 说明

H100 单节点任务通过 Enroot 使用 `.sqsh` 容器文件，而不是直接 `docker run`。

```text
IMAGE
  → enroot import
  → 共享 Lustre 上的 .sqsh 文件
  → srun 用该 .sqsh 启动 benchmark container
```

多个 Job 可能同时需要同一个镜像，因此逻辑是：

```text
1. 先检查已有 `.sqsh` 是否有效；有效直接读，不抢锁。
2. 不存在或无效时，打开对应 `.lock`。
3. 最多等 600 秒获取 flock。
4. 拿到锁后再次检查：可能另一个 Job 已经导入完成。
5. 仍无有效文件时，删除损坏文件并 enroot import。
```

重复检查是并发安全所必需的。否则多个 Job 可能同时写同一个 SquashFS 文件，导致镜像损坏。

---

## 13. 单节点如何选择 benchmark 脚本

### 原文件节选：`launch_h100-dgxc-slurm.sh:283-299`

```bash
BENCH_BASE="benchmarks/single_node/${SCENARIO_SUBDIR}${EXP_NAME%%_*}_${PRECISION}_h100"
BENCH_SCRIPT="${BENCH_BASE}_${FRAMEWORK}${SPEC_SUFFIX}.sh"
if [[ ! -f "$BENCH_SCRIPT" ]]; then
    BENCH_SCRIPT="${BENCH_BASE}${SPEC_SUFFIX}.sh"
fi

if [[ "$MODEL_PREFIX" == "dsv41flash" ]]; then
    CONTAINER_MOUNT_DIR=/ix
    export INFMAX_CONTAINER_WORKSPACE=/ix
    export RESULT_DIR=/ix/results
else
    CONTAINER_MOUNT_DIR=/workspace
fi
```

### 说明

单节点 Benchmark 脚本路径由多个 matrix/workflow 参数拼出：

```text
SCENARIO_SUBDIR
+ EXP_NAME 的第一个 _ 前部分
+ PRECISION
+ h100
+ FRAMEWORK
+ 可选 _mtp
```

先尝试有 framework 后缀的脚本：

```text
benchmarks/single_node/<scenario>/<experiment>_<precision>_h100_<framework>[_mtp].sh
```

若该文件不存在，回退到较老的无 framework 后缀命名：

```text
benchmarks/single_node/<scenario>/<experiment>_<precision>_h100[_mtp].sh
```

这使新/旧 recipe 能兼容。

`dsv41flash` 是特殊例外：它可能在仓库旁建立 AgentX runtime 目录，不能直接使用默认 `/workspace`，所以改用容器内 `/ix` 并同步更新结果目录变量。

---

## 14. `srun` 真正启动单节点容器 Benchmark

### 原文件节选：`launch_h100-dgxc-slurm.sh:301-309`

```bash
srun --jobid=$JOB_ID \
    --container-image=$SQUASH_FILE \
    --container-mounts=$GITHUB_WORKSPACE:$CONTAINER_MOUNT_DIR/,$HF_HUB_CACHE_MOUNT:$HF_HUB_CACHE,$AIPERF_MMAP_CACHE_HOST_PATH:/aiperf_mmap_cache \
    --no-container-mount-home \
    --container-workdir=$CONTAINER_MOUNT_DIR/ \
    --no-container-entrypoint \
    --export=ALL,PORT=8888,AIPERF_DATASET_MMAP_CACHE_DIR=/aiperf_mmap_cache \
    bash "$BENCH_SCRIPT"

scancel $JOB_ID
```

### 说明

这条 `srun` 才是单节点路径中真正执行模型服务与 benchmark 的命令。

| 参数 | 作用 |
|---|---|
| `--jobid=$JOB_ID` | 在前面 `salloc` 获得的 allocation 内执行。 |
| `--container-image=$SQUASH_FILE` | 使用 Enroot 转换好的 inference container。 |
| `--container-mounts=...` | 将 workflow checkout、HF 缓存和 AIPerf mmap cache 挂载进容器。 |
| `--no-container-mount-home` | 不自动把宿主机 home 目录带入容器，减少环境污染。 |
| `--container-workdir` | 容器启动后以 checkout 挂载目录作为工作目录。 |
| `PORT=8888` | 向 benchmark 脚本提供服务端口。 |
| `AIPERF_DATASET_MMAP_CACHE_DIR=/aiperf_mmap_cache` | 让 AIPerf 在容器内使用持久化数据集 mmap cache。 |
| `bash "$BENCH_SCRIPT"` | 执行最终选出的 fixed-seq 或 AgentX recipe。 |

脚本完成 `srun` 后调用：

```bash
scancel $JOB_ID
```

释放前面申请的 allocation；即使这一行没有运行，前述 `trap` 也会尝试清理。

---

## 15. 关键环境变量速查

### 由 workflow / matrix 传入

| 变量 | 用途 |
|---|---|
| `RUNNER_NAME` | 实际 GitHub runner 名，例如 `h100-dgxc-slurm_03`；也用作 Slurm Job 名。 |
| `IS_MULTINODE` | 决定走多节点 `srtctl` 还是单节点 `salloc/srun`。 |
| `IS_AGENTIC`、`SCENARIO_SUBDIR` | 决定 AgentX 路径、srt-slurm source 与单节点 recipe 子目录。 |
| `MODEL_PREFIX`、`MODEL`、`PRECISION` | 决定模型分支、权重路径与服务名。 |
| `FRAMEWORK` | 决定多节点支持判断、容器解析及单节点 recipe 名称。 |
| `IMAGE` | 推理容器镜像标识。 |
| `CONFIG_FILE` | 多节点必填 recipe；没有它不能执行 `srtctl apply`。 |
| `ISL`、`OSL`、`TP`、`GPU_COUNT` | 输入/输出长度与单节点 GPU 资源请求。 |
| `SPEC_DECODING` | `mtp` 时附加 `_mtp` recipe 后缀。 |
| `RUN_EVAL`、`EVAL_ONLY`、`EVAL_FRAMEWORK` | 是否执行/收集 Eval，以及使用何种 evaluator。 |
| `RESULT_FILENAME` | 多节点结果拷贝时的稳定文件身份。 |
| `GITHUB_WORKSPACE` | Actions checkout 与 artifact 暂存位置。 |

### 本脚本定义的集群事实

```text
SLURM_PARTITION=hpc-gpu-1
SLURM_ACCOUNT=customer
H100 节点默认 GPU 数：8
模型共享目录：/mnt/nfs/lustre/models/...
容器共享目录：/mnt/nfs/lustre/containers/...
uv 共享缓存：/mnt/nfs/sa-shared/.uv/...
HF / AIPerf 共享缓存：/mnt/nfs/sa-shared/gharunners/...
```

---

## 16. 新手最容易混淆的概念

### `runner.name` 不是实际 GPU compute node

```text
runner.name=h100-dgxc-slurm_03
```

表示 GitHub Actions 的控制 Job 在该 self-hosted runner 环境运行。真正用于模型推理的 GPU node，则由 Slurm 在 `hpc-gpu-1` 分区内分配。

### 同一个 launcher 有两种资源申请方式

```text
多节点：srtctl apply → srt-slurm 渲染并提交 Slurm workload。
单节点：salloc 先申请 allocation，再用 srun 启动 container benchmark。
```

### 容器 `.sqsh` 不是 Docker image 名

```text
IMAGE：镜像引用，例如 registry/repository:tag。
SQUASH_FILE：由 Enroot 转换、保存于共享存储的实际文件。
```

### Eval 不等于吞吐 Benchmark

```text
EVAL_ONLY=true：启动服务并执行质量/正确性数据集，跳过吞吐 JSON 收集。
RUN_EVAL=true：可能在 Benchmark 后执行 Eval，并收集 eval_results。
普通吞吐：从结果目录收集性能 JSON。
```

---

## 17. 关联文件

| 文件 | 关系 |
|---|---|
| [`../.github/workflows/benchmark-tmpl.yml`](../.github/workflows/benchmark-tmpl.yml) | 单节点 workflow 模板，设置 `RUNNER_NAME` 并调用此 launcher。 |
| [`../.github/workflows/benchmark-multinode-tmpl.yml`](../.github/workflows/benchmark-multinode-tmpl.yml) | 多节点模板，设置拓扑输入并调用此 launcher。 |
| [`slurm_utils.sh`](slurm_utils.sh) | 公共结果复制、镜像解析、预置资源验证和 Eval 辅助函数。 |
| [`../benchmarks/single_node/`](../benchmarks/single_node/) | 单节点 fixed-seq / AgentX benchmark recipe。 |
| [`../benchmarks/multi_node/`](../benchmarks/multi_node/) | 多节点 srt-slurm recipe。 |
| [`../configs/runners.yaml`](../configs/runners.yaml) | H100 DGXC cluster runner inventory 和硬件能力声明。 |

---

## 最简记忆版

```text
H100 DGXC launcher：

多节点：
检查只支持的模型/框架
→ 使用预置模型与容器
→ clone srt-slurm、安装 srtctl
→ 生成 srtslurm.yaml
→ srtctl apply 提交 Slurm
→ 追踪日志，收集 Benchmark/Eval/服务日志。

单节点：
申请独占 GPU allocation
→ 确保 Enroot 镜像缓存可用
→ 拼出 fixed-seq 或 AgentX recipe 脚本
→ srun 在容器中执行
→ 释放 allocation。
```
