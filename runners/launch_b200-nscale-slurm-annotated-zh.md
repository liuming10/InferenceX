# `launch_b200-nscale-slurm.sh` 小白注释版

> 对应原脚本：[`launch_b200-nscale-slurm.sh`](launch_b200-nscale-slurm.sh)
>
> 调用者：[`../.github/workflows/benchmark-multinode-tmpl.yml`](../.github/workflows/benchmark-multinode-tmpl.yml)
>
> 本文采用“原文件节选 + 说明”的方式解读。节选用于说明关键控制点；实际运行时应以原脚本为准。
>
> **安全边界：** 该脚本会下载容器镜像、修改当前工作区内的 recipe 副本、提交 Slurm 作业、清理当前目录中的 `outputs/`。不要为了“试一下”而手工执行到 `srtctl apply`；那会真实申请 B200 集群资源并启动模型服务。

---

## 1. 它处于整条链路的什么位置

```text
perf-changelog.yaml / master config
  → infx.matrix.plan 生成 matrix row
  → run-sweep.yml 调用 benchmark-multinode-tmpl.yml
  → GitHub 将 Job 派给具体 self-hosted runner
  → 本脚本 launch_b200-nscale-slurm.sh
  → srtctl apply
  → Slurm 分配实际 B200 compute nodes
  → 部署推理服务、跑 Benchmark 或 Eval、回收 artifacts
```

这个脚本不是“模型启动命令”，也不是普通 Python benchmark 脚本。它是 **B200 Nscale 集群专用的多节点 Slurm launcher**，负责把 workflow 传入的模型、框架、拓扑和 recipe 转换为可提交给 Slurm 的工作负载。

GitHub workflow 通过实体 self-hosted runner 名选择它：

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

例如：

```text
RUNNER_NAME=b200-nscale-slurm_03
              └────────────────┘
              去掉第一个 _ 及其后的编号

实际执行：
bash ./runners/launch_b200-nscale-slurm.sh
```

参见 `benchmark-multinode-tmpl.yml:410-433`。

---

## 2. 文件职责、集群固定参数与缓存目录

### 原文件节选：`launch_b200-nscale-slurm.sh:3-30`

```bash
# Standalone launcher for the B200 nscale Slurm cluster.
#
# Self-contained because Nscale has its own Slurm and storage layout.

SLURM_PARTITION="batch_1"
SLURM_ACCOUNT="benchmark"

# Node-local NVMe, not a shared filesystem.
NSCALE_MODEL_ROOT="/scratch/models"
SQUASH_DIR="/data/home/sa-shared/containers"
AIPERF_MMAP_CACHE_HOST_PATH="/data/home/sa-shared/gharunners/aiperf-cache"
HF_HUB_CACHE_HOST_PATH="/data/home/sa-shared/gharunners/hf-hub-cache"
SQUASH_LOCK_TIMEOUT=3600

source "$(dirname "${BASH_SOURCE[0]}")/slurm_utils.sh"
```

### 说明

| 变量 | 值 | 小白理解 |
|---|---|---|
| `SLURM_PARTITION` | `batch_1` | Slurm 提交任务使用的分区。可理解为集群中可提交 benchmark 的资源池。 |
| `SLURM_ACCOUNT` | `benchmark` | Slurm 记账/配额账户。它影响资源使用如何归属和被策略限制。 |
| `NSCALE_MODEL_ROOT` | `/scratch/models` | **每个 compute node 本地 NVMe** 的预置模型根目录。不是 GitHub workspace，也不是共享 home。大型模型从本地 NVMe 读取会更快。 |
| `SQUASH_DIR` | 共享容器缓存目录 | 存放 Enroot 导入后的 `.sqsh` 容器镜像，避免每次任务重新拉取和转换。 |
| `AIPERF_MMAP_CACHE_HOST_PATH` | 共享 AIPerf 缓存 | AgentX trace 数据集 mmap cache 的宿主机路径。 |
| `HF_HUB_CACHE_HOST_PATH` | 共享 Hugging Face 缓存 | AgentX trace 数据集等 Hugging Face 内容的持久缓存。 |
| `SQUASH_LOCK_TIMEOUT` | `3600` 秒 | 争抢同一个镜像缓存锁时最多等待一小时。 |

`slurm_utils.sh` 提供公共函数，例如：

```text
stream_slurm_job_log
  等待 Slurm 日志出现并持续输出。

inject_synthetic_acceptance
  根据显式 opt-in，向 recipe 注入 speculative decoding 的验收配置。

copy_eval_artifacts / bundle_server_logs / collect_agentic_power_results
  收集 Eval、服务日志和 AgentX 功耗相关产物。
```

---

## 3. 为什么有些任务会回退到兼容 launcher

### 原文件节选：`launch_b200-nscale-slurm.sh:36-68`

```bash
run_compat_launcher() {
    exec bash "$(dirname "${BASH_SOURCE[0]}")/launch_b200-nscale-compat.sh"
}

if [[ "$IS_MULTINODE" != "true" ]]; then
    run_compat_launcher
fi

if [[ "$FRAMEWORK" == "tilert" && "${IS_AGENTIC:-0}" != "1" ]]; then
    run_compat_launcher
fi

# 只识别若干模型前缀和精度组合；其余回退。
if [[ $MODEL_PREFIX == "dsv4" && $PRECISION == "fp4" ]]; then
    ...
elif [[ $MODEL_PREFIX == "kimik2.6" && $PRECISION == "fp4" ]]; then
    ...
elif [[ $MODEL_PREFIX == "kimik3" && $PRECISION == "fp4" ]]; then
    ...
elif [[ $MODEL_PREFIX == "glm5.1" && $PRECISION == "fp8" && $FRAMEWORK == "tilert" ]]; then
    ...
else
    run_compat_launcher
fi
```

### 说明

这个脚本不是所有 B200 workload 的总入口。它只处理特定的、已适配 srt-slurm 的多节点组合。

| 条件 | 实际行为 |
|---|---|
| `IS_MULTINODE != true` | 立即 `exec` 到 `launch_b200-nscale-compat.sh`；当前脚本后面的代码不再执行。 |
| TileRT 但不是 AgentX | 回退到兼容 launcher。 |
| 不属于脚本支持的模型/精度/框架组合 | 回退到兼容 launcher。 |
| 受支持的多节点组合 | 继续走下面的 srt-slurm / Slurm 提交流程。 |

脚本当前重点支持的模型前缀包括：

```text
dsv4 + fp4
kimik2.6 + fp4
kimik3 + fp4
glm5.1 + fp8 + tilert
```

框架也有约束：主要是 `dynamo-vllm`，受限制的 `dynamo-sglang`，以及 `glm5.1/fp8/tilert/mtp` 的组合。

`exec` 很重要：它不是“再调用一次然后返回”，而是用兼容 launcher **替换当前 shell 进程**。因此回退后，本脚本不会重复执行容器准备、`srtctl apply` 或清理步骤。

---

## 4. 模型短名、服务名和真实权重路径

### 原文件节选：`launch_b200-nscale-slurm.sh:48-61,101`

```bash
if [[ $MODEL_PREFIX == "dsv4" && $PRECISION == "fp4" ]]; then
    export MODEL_PATH="${MODEL_PATH:-$NSCALE_MODEL_ROOT/DeepSeek-V4-Pro}"
    export SRT_SLURM_MODEL_PREFIX="deepseek-v4-pro"
elif [[ $MODEL_PREFIX == "kimik2.6" && $PRECISION == "fp4" ]]; then
    export MODEL_PATH="${MODEL_PATH:-$NSCALE_MODEL_ROOT/Kimi-K2.6-NVFP4}"
    export SRT_SLURM_MODEL_PREFIX="kimi-k2.6-nvfp4"
elif [[ $MODEL_PREFIX == "kimik3" && $PRECISION == "fp4" ]]; then
    export MODEL_PATH="${MODEL_PATH:-$NSCALE_MODEL_ROOT/Kimi-K3}"
    export SRT_SLURM_MODEL_PREFIX="kimik3"
elif [[ $MODEL_PREFIX == "glm5.1" && $PRECISION == "fp8" && $FRAMEWORK == "tilert" ]]; then
    export SRT_SLURM_MODEL_PREFIX="glm5.1-fp8"
fi

export SERVED_MODEL_NAME=$MODEL
```

### 说明

不要把下面几个名字混为一谈：

| 名称 | 例子 | 用途 |
|---|---|---|
| `MODEL_PREFIX` | `dsv4`、`kimik3`、`glm5.1` | matrix/config 的简短身份，用于分支判断和结果命名。 |
| `MODEL` / `SERVED_MODEL_NAME` | 由 workflow/matrix 传入 | 推理服务向客户端暴露的模型名。 |
| `MODEL_PATH` | `/scratch/models/Kimi-K3` | compute node 上真实的模型权重目录。 |
| `SRT_SLURM_MODEL_PREFIX` | `kimik3`、`deepseek-v4-pro` | `srt-slurm` 配置和 recipe 使用的 model alias。 |

这一段的 `${MODEL_PATH:-默认值}` 表示：

```text
如果上游已经设置 MODEL_PATH，就尊重上游值；
否则使用本 B200 集群预置模型目录中的默认位置。
```

`glm5.1` 分支只在此处设置 `SRT_SLURM_MODEL_PREFIX`，没有在这里直接赋默认 `MODEL_PATH`。因此 GLM 的实际权重路径需要由上游环境或相应 recipe 正确提供；不能假设所有模型都由这一小段脚本设置路径。

---

## 5. DCGM 功耗采集：先从 recipe 判断，再强制保护

### 原文件节选：`launch_b200-nscale-slurm.sh:70-99`

```bash
USES_DCGM_POWER=0
USES_AGENTX_POWER=0
_POWER_CONFIG_FILE="${CONFIG_FILE:-}"
if [[ "${EVAL_ONLY:-false}" == "true" && -n "${EVAL_CONFIG_FILE:-}" ]]; then
    _POWER_CONFIG_FILE="$EVAL_CONFIG_FILE"
fi

# 从选定 recipe 的 telemetry 段识别：
# provider: dcgm-power
# enabled: true
if ... awk ... "$_RECIPE_SRC"; then
    USES_DCGM_POWER=1
fi

if [[ "$USES_DCGM_POWER" == "1" && "$IS_AGENTIC" == "1" &&
    "$MODEL_PREFIX" == "kimik3" && "$PRECISION" == "fp4" && "$FRAMEWORK" == "dynamo-vllm" ]]; then
    USES_AGENTX_POWER=1
elif [[ "$USES_DCGM_POWER" == "1" && (...) ]]; then
    echo "Error: B200 nscale dcgm-power requires a supported fixed-sequence lane or Kimi-K3 AgentX vLLM" >&2
    exit 1
fi
```

### 说明

是否采集功耗，不是通过“看起来像 B200”就默认开启。脚本会先查看当前选择的 recipe 是否写明：

```yaml
telemetry:
  provider: dcgm-power
  enabled: true
```

只有 recipe 明确启用时，才会设置：

```bash
USES_DCGM_POWER=1
```

随后它再次检查：当前模型、精度、场景、框架是否是**已支持且可信的功耗测量组合**。不支持则直接失败，而不是悄悄跑完一个缺失或不可信的功耗结果。

对 AgentX 来说，当前专用功耗路径是：

```text
Kimi K3 + FP4 + dynamo-vLLM + IS_AGENTIC=1
```

这段保护的目的不是限制普通 benchmark，而是避免将不兼容的 telemetry 配方标成成功。

---

## 6. 获取并固定 `srt-slurm` 版本，再复制 InferenceX recipes

### 原文件节选：`launch_b200-nscale-slurm.sh:103-173`

```bash
SRT_REPO_DIR="srt-slurm"
rm -rf "$SRT_REPO_DIR"

git clone ... "$SRT_REPO_DIR" || exit 1
cd "$SRT_REPO_DIR" || exit 1
git checkout <固定的 commit 或 ref> || exit 1
test "$(git rev-parse HEAD)" = "<预期 commit>" || exit 1

cp -rT "$GITHUB_WORKSPACE/benchmarks/multi_node/srt-slurm-recipes/..." \
    recipes/... || exit 1

if [[ "${EVAL_FRAMEWORK:-lm-eval}" != "lm-eval" ]]; then
    python3 "$GITHUB_WORKSPACE/runners/patch_srt_eval_dispatch.py" "$(pwd)" || exit 1
fi
```

### 说明

`srt-slurm` 是外部工具仓库。它的作用可以概括为：

```text
读取 recipe
  → 生成 Slurm / 容器 / 分布式启动命令
  → 提交并追踪多节点任务
```

这段工作分为四步：

1. 在当前 runner workspace 中删除旧的临时 `srt-slurm/` clone。
2. clone 与当前模型/框架/功耗路径匹配的 `srt-slurm` 来源。
3. checkout 到固定 commit，并验证 `HEAD` 确实等于期望 commit。
4. 将 InferenceX 仓库中的 recipe 复制到该 clone 预期的 `recipes/...` 路径。

固定 commit 的意义是可复现性：

```text
同一个 InferenceX matrix row
+ 同一份 recipes
+ 同一个 srt-slurm commit
= 不会因上游 main 分支后来变化而生成不同的启动命令。
```

`rm -rf "$SRT_REPO_DIR"` 的范围是**当前工作目录中的 `srt-slurm` 临时 clone**，不是模型目录，也不是整个 Slurm 集群数据。但因为它是删除操作，仍只能在 CI 管理的工作目录中运行。

当 evaluator 不是默认 `lm-eval` 时，`patch_srt_eval_dispatch.py` 会修补 clone 中的 Eval 分发方式，让指定的 evaluator 能通过该 srt-slurm 运行路径启动。

---

## 7. 安装 `srtctl`：这是 CI 专用的独立 Python 环境

### 原文件节选：`launch_b200-nscale-slurm.sh:176-187`

```bash
echo "Installing srtctl..."
export UV_INSTALL_DIR="$GITHUB_WORKSPACE/.local/bin"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$UV_INSTALL_DIR:$PATH"
uv venv "$GITHUB_WORKSPACE/.venv"
source "$GITHUB_WORKSPACE/.venv/bin/activate"
uv pip install -e .

if ! command -v srtctl &> /dev/null; then
    echo "Error: Failed to install srtctl" >&2
    exit 1
fi
```

### 说明

这里的当前目录已经是刚 clone 的 `srt-slurm/`。因此：

```bash
uv pip install -e .
```

安装的是该 `srt-slurm` clone 所提供的 `srtctl` CLI。

这个 `.venv` 是 workflow 在 `$GITHUB_WORKSPACE` 下临时创建的 **CI launcher 虚拟环境**。它不能与远程调试 AgentX 时使用的 Python 虚拟环境混为一谈。

最后的检查：

```bash
command -v srtctl
```

确保下一步真正提交任务的 CLI 已经可执行；失败就终止，避免执行一个不存在的命令。

---

## 8. 容器为什么要转成 Enroot `.sqsh`，以及为什么要加锁

### 原文件节选：`launch_b200-nscale-slurm.sh:189-268`

```bash
SQUASH_FILE="$SQUASH_DIR/$(echo "$IMAGE" | sed 's/[\/:@#]/_/g').sqsh"

import_squash() {
    local squash_file="$1"
    local image_ref="$2"
    ...
    (
        flock -w "$SQUASH_LOCK_TIMEOUT" 9 || exit 1
        if unsquashfs -l "$squash_file" > /dev/null 2>&1; then
            echo "Squash file already exists and is valid, skipping import"
        else
            rm -f "$squash_file"
            enroot import -o "$squash_file" "$enroot_uri"
            unsquashfs -l "$squash_file" > /dev/null || exit 1
            chmod a+r "$squash_file" || true
        fi
    ) 9>"$lock_file"
}

import_squash "$SQUASH_FILE" "$IMAGE"
import_squash "$NGINX_SQUASH_FILE" "$NGINX_IMAGE"
```

### 说明

多节点 Slurm 环境常使用 Enroot，而不是在每个 compute node 上直接执行 `docker run`。Enroot 可以将 OCI/Docker image 转换为 SquashFS 文件：

```text
容器 image
  → enroot import
  → .sqsh 文件
  → Slurm worker 使用该文件启动容器
```

脚本会处理至少两类镜像：

```text
IMAGE        推理服务框架镜像，例如 dynamo-vLLM / dynamo-SGLang。
NGINX_IMAGE  服务代理所需镜像。
```

如果是 TileRT，还会要求并额外导入：

```bash
PREFILL_IMAGE
```

如果启用 DCGM 功耗采集，还会导入 `dcgm-exporter` 镜像。

### 为什么要 `flock`

多个 self-hosted runner 可能同时需要同一个镜像。没有锁时可能出现：

```text
runner A 正在写 image.sqsh
runner B 也开始写同一个 image.sqsh
  → 文件损坏、空间浪费、任务不稳定
```

所以每个 image cache key 有独占锁：

```bash
flock -w "$SQUASH_LOCK_TIMEOUT" 9
```

逻辑是：

```text
已有有效 .sqsh：直接复用。
没有或文件损坏：删除该文件，再导入并验证。
其他 runner 正在导入：等待锁，最多等待 3600 秒。
```

这一步可能下载大镜像、占大量磁盘和网络资源，因此不适合手工“验证一下 launcher”。

---

## 9. AgentX 的两个持久化缓存如何挂进容器

### 原文件节选：`launch_b200-nscale-slurm.sh:270-292`

```bash
DEFAULT_MOUNTS_BLOCK=""
if [[ "$IS_AGENTIC" == "1" ]]; then
    mkdir -p "$AIPERF_MMAP_CACHE_HOST_PATH" "$HF_HUB_CACHE_HOST_PATH"
    chmod 777 "$AIPERF_MMAP_CACHE_HOST_PATH" "$HF_HUB_CACHE_HOST_PATH" 2>/dev/null || true
    DEFAULT_MOUNTS_BLOCK="default_mounts:
  ${AIPERF_MMAP_CACHE_HOST_PATH}: /aiperf_mmap_cache
  ${HF_HUB_CACHE_HOST_PATH}: /hf_hub_cache"
fi
```

### 说明

当 matrix row 是：

```text
scenario-type=agentic-coding
```

workflow 会令：

```text
IS_AGENTIC=1
```

于是本段执行，将宿主机的长期缓存挂载进每个 AgentX worker container：

| 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|
| `.../aiperf-cache` | `/aiperf_mmap_cache` | AIPerf trace dataset 的 mmap 缓存。 |
| `.../hf-hub-cache` | `/hf_hub_cache` | Hugging Face dataset / 文件缓存。 |

这能避免每一次 AgentX benchmark 都重新下载或重新构建大体积数据集缓存。

TileRT 还会添加：

```text
GitHub workspace → /infmax-workspace
TileRT shared weight cache → 同路径容器挂载
```

`chmod 777` 是为了受控共享 CI 存储中可能使用不同 UID 的 runner/container 进程能够读写缓存。它是特定共享 runner 场景下的兼容措施，不应不加评估地复制到一般生产服务器。

---

## 10. `srtslurm.yaml`：把集群事实告诉 `srtctl`

### 原文件节选：`launch_b200-nscale-slurm.sh:294-328`

```bash
SRTCTL_ROOT="${GITHUB_WORKSPACE}/${SRT_REPO_DIR}"
cat > srtslurm.yaml <<EOF
default_account: "${SLURM_ACCOUNT}"
default_partition: "${SLURM_PARTITION}"
default_time_limit: "4:00:00"
gpus_per_node: 8
network_interface: ""
srtctl_root: "${SRTCTL_ROOT}"
model_paths:
  "${SRT_SLURM_MODEL_PREFIX}": "${MODEL_PATH}"
containers:
  dynamo-vllm: "${SQUASH_FILE}"
  dynamo-sglang: "${SQUASH_FILE}"
  "${IMAGE}": "${SQUASH_FILE}"
  nginx-sqsh: "${NGINX_SQUASH_FILE}"
${TILERT_CONTAINER_BLOCK}
use_exclusive_sbatch_directive: true
${DEFAULT_MOUNTS_BLOCK}
EOF
```

### 说明

这会在当前 `srt-slurm` 工作目录中生成一个临时配置文件。它回答的是：

```text
向哪个 Slurm account / partition 投递？
每个节点按多少 GPU 计算？
recipe 中的模型别名实际对应哪个权重目录？
容器别名对应哪个 .sqsh 镜像文件？
worker container 应该挂载哪些共享目录？
```

`use_exclusive_sbatch_directive: true` 表示 srt-slurm 生成的 Slurm 请求采用节点独占策略。

请区分两种配置：

| 文件/来源 | 决定什么 |
|---|---|
| 本段临时生成的 `srtslurm.yaml` | 集群基础设施参数：account、partition、GPU、模型路径、容器、挂载。 |
| `CONFIG_FILE` 指向的 recipe | 模型部署拓扑、Prefill/Decode 角色、推理框架参数、benchmark/client、telemetry 等。 |

---

## 11. 选择 recipe、改写运行副本，并到达真正的提交边界

### 原文件节选：`launch_b200-nscale-slurm.sh:339-389`

```bash
if [[ "${EVAL_ONLY:-false}" == "true" && -n "${EVAL_CONFIG_FILE:-}" ]]; then
    CONFIG_FILE="$EVAL_CONFIG_FILE"
    echo "EVAL_ONLY=true: selecting real-verification recipe $CONFIG_FILE"
fi

if [[ -z "$CONFIG_FILE" ]]; then
    echo "Error: CONFIG_FILE is not set. The srt-slurm path requires a CONFIG_FILE in additional-settings." >&2
    exit 1
fi

CONFIG_PATH="${CONFIG_FILE%%:*}"

sed -i "s/^name:.*/name: \"${RUNNER_NAME}\"/" "$CONFIG_PATH"
sed -i 's/^  max_attempts: [0-9]*/  max_attempts: 720/' "$CONFIG_PATH"
inject_synthetic_acceptance "$CONFIG_PATH" "$FRAMEWORK" || exit 1

if [[ $MODEL_PREFIX == "kimik2.6" ]] ||
   [[ $MODEL_PREFIX == "kimik3" ]] ||
   [[ $MODEL_PREFIX == "dsv4" ]]; then
    SRTCTL_PREFLIGHT_ARGS+=(--no-preflight)
fi

SRTCTL_OUTPUT=$(srtctl apply -f "$CONFIG_FILE" \
  "${SRTCTL_PREFLIGHT_ARGS[@]}" \
  --tags "b200,${MODEL_PREFIX},${PRECISION},${ISL}x${OSL},infmax-$(date +%Y%m%d)" 2>&1)
```

### 说明

这是整个 launcher 最关键的边界。

### 11.1 `CONFIG_FILE` 从哪里来

`CONFIG_FILE` 不是此脚本随意猜测出来的；它应由 matrix recipe 的 additional settings / workflow 环境传入。它为空会立即失败。

Eval-only 行如果额外提供：

```text
EVAL_CONFIG_FILE
```

则可选择专门用于真实验证的 recipe，而不是吞吐行使用的 recipe。

### 11.2 为什么会用 `sed -i`

脚本会修改当前 checkout/复制进 workspace 的 recipe 文件：

```bash
name: <runner name>
max_attempts: 720
```

含义是：

```text
name：让 Slurm job 名与当前 GitHub anchor runner 对应，便于清理和追踪。
max_attempts=720：按每次 10 秒计算，将模型健康检查等待上限扩展至约 7200 秒（2 小时），给超大模型加载留出时间。
```

这不是修改 Git 仓库历史，也不是改远程 GitHub 内容；但它会修改当前 CI workspace 内的文件。因此脚本应只在临时/受控的 runner workspace 中运行。

### 11.3 为什么某些模型有 `--no-preflight`

这些模型权重存放在 compute node 本地的 `/scratch/models`，而不是 GitHub runner/login node 可验证的位置。`--no-preflight` 避免 srtctl 在错误的位置先检查模型文件并误判失败。

### 11.4 真正消耗集群资源的是哪一行

就是：

```bash
srtctl apply -f "$CONFIG_FILE" ...
```

它会生成并提交 Slurm workload，导致：

```text
申请节点和 GPU
启动容器
加载模型
启动推理服务
运行 Benchmark 或 Eval
```

因此，任何“只验证脚本语法”的操作都不应执行到这一行。

脚本之后从输出中解析：

```bash
JOB_ID=...
```

获取 Slurm job ID，供后续日志等待和结果收集使用。

---

## 12. Slurm 日志如何等待和实时显示

### 原文件节选：`launch_b200-nscale-slurm.sh:393-402`

```bash
LOGS_DIR="outputs/$JOB_ID/logs"
LOG_FILE="$LOGS_DIR/sweep_${JOB_ID}.log"

SRT_JOB_RC=0
stream_slurm_job_log "$JOB_ID" "$LOG_FILE" || SRT_JOB_RC=$?
if [[ "$SRT_JOB_RC" != "0" && "$USES_AGENTX_POWER" != "1" ]]; then
    exit "$SRT_JOB_RC"
fi
```

### 公共函数原文节选：`slurm_utils.sh:48-70`

```bash
while [[ ! -f "$log_file" ]]; do
    if ! slurm_job_is_active "$job_id"; then
        echo "ERROR: job $job_id failed before creating $log_file" >&2
        scontrol show job "$job_id" || true
        return 1
    fi
    sleep 5
done

... tail -F -s 2 -n+1 "$log_file" --pid="$poll_pid"
```

### 说明

提交 `srtctl apply` 不代表模型已经启动成功。脚本随后：

```text
1. 等待预期日志文件出现。
2. 如果日志还未出现，但 Slurm job 已消失：输出 scontrol 诊断并失败。
3. 如果日志出现：实时 tail 日志。
4. 一直等待到 job 离开 Slurm 队列。
```

普通路径中，Slurm job 失败会直接让 GitHub workflow 失败。

AgentX 功耗路径有额外处理：它会先尽量收集功耗审计和服务日志，再依据功耗校验结果决定失败，避免失败时丢失排查证据。

---

## 13. 结果、Eval 和服务日志如何回到 GitHub workspace

### 原文件节选：`launch_b200-nscale-slurm.sh:406-479`

```bash
cp -r "$LOGS_DIR" "$GITHUB_WORKSPACE/LOGS"
bundle_server_logs "$LOGS_DIR" "$GITHUB_WORKSPACE/multinode_server_logs.tar.gz"

if [[ "${EVAL_ONLY:-false}" != "true" ]]; then
    RESULT_SUBDIRS=$(find "$LOGS_DIR" -maxdepth 1 -type d -name "*isl*osl*")
    ...
    RESULT_FILES=$(find "$result_subdir" -name "results_concurrency_*.json")
    ...
    cp "$result_file" "$WORKSPACE_RESULT_FILE"
else
    echo "EVAL_ONLY=true: Skipping benchmark result collection"
fi

if [[ "${RUN_EVAL:-false}" == "true" || "${EVAL_ONLY:-false}" == "true" ]]; then
    copy_eval_artifacts "$LOGS_DIR/eval_results" "$GITHUB_WORKSPACE"
fi
```

### 说明

任务结束后，脚本会将 Slurm 输出中有用的内容回收到 GitHub Actions workspace；之后 benchmark 模板再把这些文件上传为 GitHub artifacts。

| 类型 | 收集方式 | 目的 |
|---|---|---|
| Slurm / 服务日志 | 复制 `LOGS_DIR`，并打包服务日志 | 排查模型加载、服务启动、客户端失败等问题。 |
| 固定长度吞吐 JSON | 查找 `results_concurrency_*.json` 并复制到 workspace | 后续 `collect-results.yml` 聚合吞吐/时延结果。 |
| AgentX 功耗结果 | 专用 `collect_agentic_power_results` 路径 | 审计 AgentX 的功耗数据及其生产者版本。 |
| Eval JSON | `copy_eval_artifacts` | 供 `collect-evals.yml` 聚合质量评估结果。 |

`EVAL_ONLY=true` 时不收集吞吐 benchmark JSON 是**预期行为**：该行只部署服务并运行 Eval，没有运行 AIPerf/固定长度吞吐 benchmark。

`RUN_EVAL=true` 且非 `EVAL_ONLY` 的场景则可能既有 benchmark 结果，又有 post-run Eval 结果。

---

## 14. 结尾清理：删除范围与原因

### 原文件节选：`launch_b200-nscale-slurm.sh:481-489`

```bash
# Clean up srt-slurm outputs to prevent NFS silly-rename lock files from
# blocking the next job's checkout on this runner.
echo "Cleaning up srt-slurm outputs..."
for i in 1 2 3 4 5; do
    rm -rf outputs 2>/dev/null && break
    echo "Retry $i/5: Waiting for NFS locks to release..."
    sleep 10
done
find . -name '.nfs*' -delete 2>/dev/null || true
```

### 说明

本段清除的是当前 `srt-slurm` 工作目录中生成的：

```text
outputs/
.nfs* 临时文件
```

NFS 在文件仍被进程占用时可能留下 `.nfs*` 占位文件；这些文件可能阻塞下一个 self-hosted runner job 的 checkout 或 workspace 清理。因此脚本最多重试五次，每次等十秒。

虽然目标范围相对明确，但它仍然是删除操作。不要将这段复制到个人目录、模型目录或不明目录中执行。

---

## 15. 环境变量来源速查表

### 主要由 workflow / matrix 传入

| 变量 | 用途 |
|---|---|
| `RUNNER_NAME` | 实际 GitHub self-hosted runner 名，如 `b200-nscale-slurm_03`。用于 Slurm job 命名与 launcher 选择。 |
| `MODEL_PREFIX`、`MODEL`、`PRECISION` | 选择模型路径、srt-slurm 分支、容器/recipe 和服务模型名。 |
| `FRAMEWORK` | 选择 Dynamo-vLLM、Dynamo-SGLang、TileRT 等实现路径。 |
| `IS_MULTINODE` | 决定是否应进入此多节点 launcher。 |
| `IS_AGENTIC` | 决定是否挂载 AgentX 缓存、采用 agentic recipe 和相关功耗路径。 |
| `ISL`、`OSL`、`CONC_LIST` | 输入长度、输出长度、并发点；用于 tags、recipe 和结果处理。 |
| `IMAGE`、`PREFILL_IMAGE` | 推理容器镜像；TileRT 还需要独立 prefill image。 |
| `CONFIG_FILE` | 必填。指向 srt-slurm recipe/配置。 |
| `EVAL_CONFIG_FILE` | 可选。Eval-only 时可替换 `CONFIG_FILE`。 |
| `EVAL_ONLY`、`RUN_EVAL`、`EVAL_FRAMEWORK` | 决定是否跳过吞吐、是否收集 Eval，以及 evaluator 分发方式。 |
| `RESULT_FILENAME` | workflow 用于确保 artifact 和结果文件不会冲突的稳定身份。 |
| `SPEC_DECODING` | 约束支持的 speculative decoding 路径，并参与 recipe 行为。 |
| `GITHUB_WORKSPACE` | 当前 GitHub Actions checkout/workspace，保存 clone、配置、结果与 artifacts。 |

### 主要由本脚本定义

```text
SLURM_PARTITION
SLURM_ACCOUNT
NSCALE_MODEL_ROOT
SQUASH_DIR
AIPERF_MMAP_CACHE_HOST_PATH
HF_HUB_CACHE_HOST_PATH
SRT_SLURM_MODEL_PREFIX
MODEL_PATH（仅部分支持的模型分支设置默认值）
```

---

## 16. 新手最容易混淆的四件事

### 1. GitHub runner 不是实际 GPU 节点

```text
runner.name=b200-nscale-slurm_03
```

表示 GitHub workflow 的 shell 在这个 anchor runner 环境执行。实际加载模型和使用 GPU 的 compute node 仍由 Slurm 选择。

### 2. `cluster:b200-nscale` 不是一个主机名

它是 GitHub Actions 的永久集群标签。动态调度器会从该集群标签对应的实体 runners 中选择 anchor，并为多节点工作负载预留容量。

### 3. `srtctl apply` 才是“真的开始跑”

此前的 clone、镜像转换、配置生成都在准备；`srtctl apply` 才会提交 Slurm 作业并实际占用集群资源。

### 4. Eval 与吞吐 Benchmark 是不同工作负载

```text
EVAL_ONLY=true：部署服务并运行质量/正确性数据集，不收集吞吐 JSON。
RUN_EVAL=true：可在 benchmark 后运行 Eval，并收集 eval artifacts。
普通吞吐：收集 results_concurrency_*.json，供性能聚合。
AgentX replay：由 agentic recipe/AIPerf 路径执行，重点是轨迹回放性能与稳定性。
```

---

## 17. 关联文件

| 文件 | 关系 |
|---|---|
| [`../.github/workflows/benchmark-multinode-tmpl.yml`](../.github/workflows/benchmark-multinode-tmpl.yml) | 设置 `RUNNER_NAME`、输入环境变量，并调用本 launcher。 |
| [`slurm_utils.sh`](slurm_utils.sh) | 本 launcher 引用的公共 Slurm 日志、结果收集和注入函数。 |
| [`launch_b200-nscale-compat.sh`](launch_b200-nscale-compat.sh) | 当前脚本不支持的组合所使用的回退 launcher。 |
| [`../benchmarks/multi_node/srt-slurm-recipes/`](../benchmarks/multi_node/srt-slurm-recipes/) | 被复制进 srt-slurm clone 的部署/benchmark recipe 来源。 |
| [`../configs/runners.yaml`](../configs/runners.yaml) | 定义 `cluster:b200-nscale` 的 runner inventory 和硬件信息。 |

---

## 最简记忆版

```text
这个脚本的工作不是直接运行模型命令。

它做的是：
选对 srt-slurm 版本
→ 准备 Enroot 容器缓存
→ 给 AgentX 挂载数据集缓存
→ 生成 srtslurm.yaml
→ 选择并微调 recipe
→ srtctl apply 提交 Slurm
→ 等待日志
→ 回收 Benchmark / Eval / 服务日志
→ 清理本次临时输出。
```
