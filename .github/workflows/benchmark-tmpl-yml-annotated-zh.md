# `benchmark-tmpl.yml` 原文与逐 Step 中文注释（单节点 Benchmark / AgentX 模板）

> 源文件：[`.github/workflows/benchmark-tmpl.yml`](.github/workflows/benchmark-tmpl.yml)。
>
> 调用方：[`run-sweep.yml`](run-sweep-yml-annotated-zh.md) 的 canary、单节点固定长度、`sweep-agentic`、单节点 Eval 等 Job。
>
> **作用**：把 `run-sweep.yml` 的一条单节点矩阵配置映射为环境变量，选择 self-hosted runner，执行对应 launcher，验证/处理结果并上传 artifact。
>
> **高影响提醒**：本模板包含 Docker、Slurm 和持久 self-hosted workspace 的清理操作。它只应在项目专用 CI runner 中由授权 workflow 使用，不能复制到有现存服务或用户容器的共享主机。

## 1. 这个模板处于哪一层

```text
run-sweep.yml 的 matrix.config
  → benchmark-tmpl.yml 的 workflow_call inputs
  → env 环境变量
  → bash ./runners/launch_${RUNNER_NAME%%_*}.sh
  → runner launcher 分配 Slurm / 容器
  → benchmarks/single_node/<scenario>/<recipe>.sh
  → 固定长度 Benchmark 或 AgentX AIPerf / Eval
  → artifact 上传
```

它是 reusable workflow：

```yaml
on:
  workflow_call:
```

因此不能直接由 push/PR 自动触发；必须由调用方 `uses: ./.github/workflows/benchmark-tmpl.yml` 调用。

## 2. 输入原文：哪些字段从 matrix 传进来

### 2.1 资源、镜像和模型身份

```yaml
inputs:
  runner: { required: true, type: string }
  priority: { required: true, type: string }
  queue-token: { required: true, type: string }
  skip-queue-pr: { required: false, type: string, default: '' }
  image: { required: true, type: string }
  model: { required: true, type: string }
  model-prefix: { required: true, type: string }
  precision: { required: true, type: string }
  framework: { required: true, type: string }
  exp-name: { required: true, type: string }
  recipe-fingerprint: { required: false, type: string, default: '' }
```

| 输入 | 含义 |
|---|---|
| `runner` | 配置层的 runner 类型/标签，供 `runs-on` 选择专用机器。 |
| `priority` / `queue-token` | `ci_priority` 生成的调度元数据。优先级越高通常越早执行；token 区分单次排队请求。 |
| `skip-queue-pr` | 标注允许的 `skip_queue` PR 号；不等价于任意绕过权限。 |
| `image` | 用于容器导入/启动的镜像。 |
| `model` / `model-prefix` | 完整模型标识及用于命名/筛选的模型族短名。 |
| `precision` | 例如 fp8、fp4。 |
| `framework` | 例如 sglang、vllm、dynamo-sglang。 |
| `exp-name` | 配方实验名；launcher 用其前缀拼 benchmark 脚本名。 |
| `recipe-fingerprint` | 对生成配方的稳定哈希身份，避免只用并发或显示名识别结果。 |

### 2.2 单节点并行、长度与并发

```yaml
  isl: { required: true, type: string }
  osl: { required: true, type: string }
  tp: { required: true, type: string }
  pp: { required: false, type: string, default: '1' }
  dcp-size: { required: false, type: string, default: '1' }
  pcp-size: { required: false, type: string, default: '1' }
  ep: { required: true, type: string }
  dp-attn: { required: true, type: boolean }
  max-model-len: { required: true, type: string }
  conc: { required: true, type: string }
  spec-decoding: { required: true, type: string }
  disagg: { required: true, type: string }
```

| 缩写 | 含义 |
|---|---|
| `isl` / `osl` | 固定长度场景的输入/输出序列长度。AgentX 不使用固定长度，调用方传 `'0'`。 |
| `tp` | Tensor Parallel（张量并行）度。 |
| `pp` | Pipeline Parallel（流水并行）度。 |
| `dcp-size` / `pcp-size` | 配方定义的上下文/流水相关并行维度。 |
| `ep` | Expert Parallel（专家并行）度。 |
| `dp-attn` | Attention 相关数据并行标记。 |
| `max-model-len` | 模型最大上下文长度。AgentX 调用方传 `'0'`，由轨迹和服务配置决定。 |
| `conc` | 当前单节点 matrix 点的并发。每个值通常是独立 Job。 |
| `spec-decoding` | 投机解码方法，如 `none`、`mtp`。 |
| `disagg` | Prefill/Decode 是否解耦；单节点路径通常按其 recipe 处理。 |

### 2.3 Eval 输入

```yaml
  run-eval: { type: boolean, required: true, default: false }
  eval-only: { type: boolean, required: false, default: false }
  eval-framework: { type: string, default: "lm-eval" }
  eval-suite: { type: string, default: "" }
  eval-limit: { type: string, default: "" }
  swebench-gen-mode: { type: string, default: "" }
```

- `run-eval=true`：服务运行后也执行 Eval；
- `eval-only=true`：跳过吞吐 Benchmark 结果要求，只做 Eval；
- `eval-framework` / `eval-suite`：选择评估实现和数据集/套件；
- `eval-limit`：空值/`full` 是完整 split；数字通常是前 N 条 smoke slice；
- `swebench-gen-mode`：SWE-bench 生成模式，空值默认 Agentic，`single-shot` 是明确调试逃生开关。

### 2.4 AgentX 输入

```yaml
  scenario-type: { type: string, default: 'fixed-seq-len' }
  kv-offloading: { type: string }
  kv-offload-backend: { type: string }
  kv-offload-backend-metadata: { type: string }
  router: { type: string }
  kv-p2p-transfer: { type: string }
  total-cpu-dram-gb: { type: string, default: '0' }
  duration: { type: string, default: '3600' }
  agentx-fast: { type: boolean, default: false }
```

AgentX 调用方会设定 `scenario-type: agentic-coding`，并提供 KV offload、DRAM 容量、持续时间等字段。`agentx-fast=true` 的语义是 AgentX profile 20 分钟、每 lane 额外 warmup 1 次；它不是跳过轨迹 replay。

## 3. 环境变量映射原文与解释

### 3.1 普通字段

```yaml
env:
  HF_TOKEN: ${{ secrets.INFERENCEX_OFFICIAL_RO_HF_TOKEN }}
  HF_HUB_CACHE: '/mnt/hf_hub_cache/'
  EXP_NAME: ${{ inputs.exp-name }}
  MODEL: ${{ inputs.model }}
  ISL: ${{ inputs.isl }}
  OSL: ${{ inputs.osl }}
  IMAGE: ${{ inputs.image }}
  FRAMEWORK: ${{ inputs.framework }}
  PRECISION: ${{ inputs.precision }}
  TP: ${{ inputs.tp }}
  PP_SIZE: ${{ inputs.pp }}
  EP_SIZE: ${{ inputs.ep }}
  CONC: ${{ inputs.conc }}
```

这里把 GitHub `inputs` 转成 launcher 和 shell recipe 可读取的环境变量。`HF_TOKEN` 是只读 Hugging Face secret；不应记录或打印其值。

### 3.2 AgentX 分流原文

```yaml
  SCENARIO_TYPE: ${{ inputs.scenario-type }}
  SCENARIO_SUBDIR: ${{ inputs.scenario-type == 'agentic-coding' && 'agentic/' || 'fixed_seq_len/' }}
  IS_AGENTIC: ${{ inputs.scenario-type == 'agentic-coding' && '1' || '0' }}
  KV_OFFLOADING: ${{ inputs.kv-offloading }}
  TOTAL_CPU_DRAM_GB: ${{ inputs.total-cpu-dram-gb }}
  DURATION: ${{ inputs.duration }}
  AIPERF_EXPERIMENTAL_FAST: ${{ inputs.agentx-fast && '1' || '0' }}
  AIPERF_FAILED_REQUEST_THRESHOLD: '0.10'
  RESULT_DIR: /workspace/results
```

| 环境变量 | 下游效果 |
|---|---|
| `SCENARIO_SUBDIR` | runner launcher 选择 `benchmarks/single_node/agentic/` 或 `fixed_seq_len/`。 |
| `IS_AGENTIC` | 通知 launcher/recipe 使用 AgentX 分支。 |
| `KV_OFFLOADING` / `TOTAL_CPU_DRAM_GB` | Agentic recipe 决定是否配置 HiCache 等 KV offload。 |
| `DURATION` | AIPerf AgentX profile 目标时长。 |
| `AIPERF_EXPERIMENTAL_FAST` | `build_replay_cmd()` 将 3600 秒改为 1200 秒、额外 warmup 每 lane 由 10 改为 1。 |
| `AIPERF_FAILED_REQUEST_THRESHOLD` | AgentX artifact 验证所允许失败请求比例，固定为 10%。 |
| `RESULT_DIR` | 容器中结果和 AIPerf artifact 的输出位置。 |

## 4. Job：`benchmark` 的 runner 选择

### 原文（缩略）

```yaml
jobs:
  benchmark:
    runs-on: ${{ fromJSON(
      vars.PRIORITY_SCHEDULER_ENABLED == 'true' && ...
      format('["self-hosted", runner, ci-job-priority-token, ci-attempt-N]') ||
      format('[runner]')
    ) }}
    timeout-minutes: 500
```

- `runs-on` 动态生成 runner 标签数组；启用优先级/节点调度器时加入 `self-hosted`、节点数、优先级、尝试次数等标签。
- 未启用时退化为 `[inputs.runner]`。
- `timeout-minutes: 500` 是整个单节点 Job 的 GitHub Actions 时限，包括 cleanup、checkout、容器准备、Benchmark、上传；不是 AIPerf 的 profile duration。

### 原文：Job 显示名

```yaml
name: >-
  p${{ inputs.priority }} | ${{ inputs.model-prefix }} ${{ inputs.precision }}
  ... TP${{ inputs.tp }} ... c${{ inputs.conc }}
```

通过名称呈现优先级、模型、精度、runner、框架、TP/PP/DCP/PCP/EP、投机解码、KV offload、并发、是否 Eval-only，方便在 Actions 矩阵视图中识别实例。

## 5. 每个 Step 的原文与作用

### Step 1：`Resource cleanup (pre-run)`

```yaml
- name: Resource cleanup (pre-run)
  run: |
    if [[ "${{ inputs.runner }}" == "cluster:mi355x-amds" && -d "$GITHUB_WORKSPACE" ]]; then
      sudo chown -R "$(id -u):$(id -g)" "$GITHUB_WORKSPACE"
    fi
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      docker ps -aq | xargs -r docker rm -f
      docker network prune -f
      while [ -n "$(docker ps -aq)" ]; do sleep 5; done
    fi
    if command -v squeue >/dev/null 2>&1; then
      scancel --name="${{ runner.name }}" || true
      while [ -n "$(squeue --name='${{ runner.name }}' ...)" ]; do sleep 5; done
    fi
```

- AMD runner 可能因中断容器留下 root-owned 文件，先修复 workspace 所有者；
- 若 Docker 可用，删除该 runner 上的所有容器并清理网络；
- 若 Slurm 可用，取消同 runner 名称的残留 Job；
- 这是为专用、串行 CI runner 写的恢复步骤，**不安全于共享 Docker/Slurm 主机**。

### Step 2：`Repair stale git state (pre-run)`

```yaml
- name: Repair stale git state (pre-run)
  run: |
    find "${repo_dir}/.git" -name index.lock -type f -delete || true
    ...
    if ! git --git-dir="${gitdir}" rev-parse --verify 'HEAD^{commit}'; then
      rm -rf "${gitdir}" "${repo_dir:?}/${rel}"
    fi
```

修复持久 self-hosted workspace 中因中断 Run 遗留的 Git lock 或损坏 submodule 元数据，使 checkout 能重新拉取。这是 runner 工作区恢复逻辑，不是普通项目清理命令。

### Step 3：Checkout

```yaml
- uses: actions/checkout@...
  with:
    token: ${{ secrets.REPO_PAT }}
    fetch-depth: 0
    ref: ${{ inputs.ref || github.sha }}
    clean: true
    submodules: true
```

- 优先 checkout 调用者的 `inputs.ref`，否则当前 SHA；
- `clean: true` 清理工作树；
- `submodules: true` 拉取 `utils/aiperf` 等子模块；
- 因此前置的 ownership/Git 修复至关重要。

### Step 4：`Launch job script`

```yaml
- name: Launch job script
  env:
    RUNNER_NAME: ${{ runner.name }}
    RUNNER_TYPE: ${{ inputs.runner }}
    RESULT_FILENAME_BASE: ...
    GITHUB_STEP_SUMMARY: ''
  run: |
    RESULT_FILENAME=$(python3 utils/result_filename.py || sha256 fallback)
    export GPU_COUNT=$((TP * PP_SIZE * PCP_SIZE))
    bash ./runners/launch_${RUNNER_NAME%%_*}.sh
    ...验证结果文件...
    if [ "${{ inputs.scenario-type }}" = "agentic-coding" ]; then
      python3 -m utils.agentic.validation.validate_agentic_result \
        results/aiperf_artifacts --failed-request-threshold "$AIPERF_FAILED_REQUEST_THRESHOLD"
    fi
```

这是模板中真正转入硬件执行链路的 Step：

1. 生成稳定 `RESULT_FILENAME`；旧 commit 无 helper 时用 base + fingerprint 的 SHA-256 回退；
2. 单节点 GPU 数按 `TP × PP_SIZE × PCP_SIZE` 计算；
3. Bash `${RUNNER_NAME%%_*}` 去掉第一个 `_` 之后部分，例如 `mi355x-amds_03` 得到 `mi355x-amds`；
4. 调用 `runners/launch_mi355x-amds.sh` 等 launcher；
5. Eval-only 需要 `results*.json`；普通固定长度需要 `$RESULT_FILENAME.json`；
6. AgentX 还必须通过 `validate_agentic_result`：验证 AIPerf artifact 与 10% 失败阈值。

#### launcher 的典型 AMD AgentX 选择路径

`launch_mi355x-amds.sh` 单节点分支会：

```bash
SCRIPT_BASE="${EXP_NAME%%_*}_${PRECISION}_mi355x"
SCRIPT_FW="benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${SCRIPT_BASE}_${FRAMEWORK}${SPEC_SUFFIX}.sh"
srun ... bash "$BENCHMARK_SCRIPT"
```

对 Qwen3.5 FP4 SGLang MTP AgentX：

```text
SCENARIO_SUBDIR=agentic/
EXP_NAME 前缀=qwen3.5
PRECISION=fp4
FRAMEWORK=sglang
SPEC_SUFFIX=_mtp
→ benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
```

该 recipe 会启动 SGLang、调用 `build_replay_cmd`，再运行：

```text
aiperf profile --scenario inferencex-agentx-mvp
```

### Step 5：`Process result`

```yaml
- name: Process result
  if: ${{ always() && env.RESULT_FILENAME != '' && !inputs.eval-only &&
           inputs.scenario-type != 'agentic-coding' }}
  run: python3 utils/process_result.py
```

只处理非 Eval-only、非 AgentX 的固定长度结果。它将原始 fixed-seq JSON 转换为聚合结果。AgentX 使用自己的已聚合 result JSON 和专用验证，故刻意绕过此 Step。

### Step 6：`Upload result`

```yaml
- name: Upload result
  if: ${{ success() && ... && inputs.scenario-type != 'agentic-coding' }}
  uses: actions/upload-artifact@...
  with:
    name: bmk_${{ env.RESULT_FILENAME }}
    path: agg_${{ env.RESULT_FILENAME }}.json
```

固定长度成功结果上传为 `bmk_*` artifact，供 `collect-results.yml` 下载与聚合。

### Step 7：`Upload agentic aggregated result`

```yaml
- name: Upload agentic aggregated result
  if: ${{ always() && inputs.scenario-type == 'agentic-coding' }}
  with:
    name: bmk_agentic_${{ env.RESULT_FILENAME }}
    path: ${{ env.RESULT_FILENAME }}.json
```

AgentX 不走固定长度聚合器，上传 recipe/AIPerf 输出的 Agentic 聚合 JSON。`always()` 便于失败时保留诊断性输出，但上传是否成功仍取决于文件是否存在。

### Step 8：`Upload agentic raw results`

```yaml
- name: Upload agentic raw results
  if: ${{ always() && inputs.scenario-type == 'agentic-coding' }}
  with:
    name: agentic_${{ env.RESULT_FILENAME }}
    path: |
      results/**
      !results/aiperf_artifacts/inputs.json
      !results/aiperf_artifacts/profile_export_raw.jsonl
```

上传 AgentX 原始结果和日志，但排除可能很大或包含请求内容的 `inputs.json`、`profile_export_raw.jsonl`。这有利于调试且控制 artifact 体积/敏感数据暴露范围。

### Step 9：`Upload server logs`

```yaml
- name: Upload server logs
  if: always()
  with:
    name: ${{ inputs.eval-only && 'eval_server_logs_' || 'server_logs_' }}${{ env.RESULT_FILENAME }}
    path: |
      server.log
      results/*.log
      results/*_config.json
```

无论成功或失败上传服务日志和配置，方便定位启动、HTTP、模型/配置问题。固定长度服务日志常在根目录，AgentX 常写在 `results/`。

### Step 10：`Upload GPU metrics`

```yaml
- name: Upload GPU metrics
  if: always()
  with:
    name: ...gpu_metrics_...
    path: gpu_metrics.csv ... results/gpu_metrics*.csv
```

上传 GPU 功耗、上下文、能耗采样和身份文件。InferenceX 页面“Measured Power/Energy”类指标依赖这类输入；缺失并不一定使吞吐 Benchmark 失败，具体要求取决于 `require-power` 和处理器逻辑。

### Step 11：`Upload power audit bundle`

```yaml
- name: Upload power audit bundle
  if: ${{ always() && !inputs.eval-only }}
  with:
    name: power_audit_${{ env.RESULT_FILENAME }}
    path: ... power_validation_*.json ... agentic_power_window.json ...
```

保存可审计的原始/聚合结果、功耗样本和验证信息。Eval-only 不上传，因为它不是吞吐/功耗性能结果。

### Step 12：`Upload eval results (if any)`

```yaml
- name: Upload eval results (if any)
  if: ${{ always() && (env.RUN_EVAL == 'true' || inputs.eval-only) }}
  with:
    name: eval_${{ env.RESULT_FILENAME }}_${{ env.EVAL_FRAMEWORK }}_...
    path: results*.json ... predictions.jsonl ... *.traj*
    if-no-files-found: ${{ inputs.eval-only && 'error' || 'ignore' }}
```

Eval-only 缺结果是错误；“性能 Job 附带 Eval”缺结果则按 `ignore` 处理。artifact 名以 `eval_` 开头，正是 [`collect-evals.yml`](collect-evals-yml-annotated-zh.md) 的下载契约。

### Step 13：`Verify eval scores`

```yaml
- name: Verify eval scores
  if: ${{ (success() || failure()) && inputs.eval-only }}
  run: python3 utils/evals/validate_scores.py
```

即使前面 Step 已失败，也尽力验证 Eval score 文件。其目的在于让“产生了文件”不被误当成“评分有效”。

### Step 14：`Cleanup eval outputs (post-upload)`

```yaml
- name: Cleanup eval outputs (post-upload)
  if: ${{ always() && (env.RUN_EVAL == 'true' || inputs.eval-only) }}
  run: rm -f meta_env.json results*.json ...
```

清理本 Job 工作区中已上传的 Eval 临时文件，避免 self-hosted workspace 污染下一 Job。它发生在上传后。

### Step 15：`Resource cleanup (post-run)`

```yaml
- name: Resource cleanup (post-run)
  if: always()
  run: *resource-cleanup
```

`*resource-cleanup` 是 YAML anchor，复用 Step 1 的 Docker/Slurm/workspace 清理脚本。`always()` 保证 Benchmark 失败、被取消或上传失败后仍尽力释放专用资源。

## 6. 固定长度与 AgentX 的处理差异

| 环节 | 固定长度 | AgentX / `agentic-coding` |
|---|---|---|
| 输入长度 | `isl` / `osl` | 调用方传 `0`；真实多轮 trace 决定请求。 |
| recipe 目录 | `fixed_seq_len/` | `agentic/`。 |
| 结果处理 | `utils/process_result.py` | AIPerf 输出 + `validate_agentic_result`。 |
| 聚合 artifact | `bmk_<id>` / `agg_<id>.json` | `bmk_agentic_<id>` / `<id>.json`。 |
| 原始 artifact | 通常不走 AgentX raw 路径 | `agentic_<id>`，排除原始输入和 raw profile export。 |
| fast 模式 | 无此专用开关 | `agentx-fast → AIPERF_EXPERIMENTAL_FAST=1`。 |

## 7. 不应在共享环境复现的动作

本模板及 launcher 可能执行：`docker rm -f`、`docker network prune`、`scancel`、`salloc`、`srun`、`enroot import`、工作区删除/修复。它们是项目受控 runner 的运维机制，而不是本地调试模板。若要研究 AgentX，先在不影响现有服务的环境中只分析命令和 artifact；未经明确授权不要执行该链路。
