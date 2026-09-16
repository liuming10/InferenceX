# 从 `run-sweep.yml` 看懂 InferenceX 自动性能扫描（Linux / 初学者指南）

> 本文以 [`.github/workflows/run-sweep.yml`](../../.github/workflows/run-sweep.yml) 为唯一入口，说明一次 InferenceX PR 性能扫描从“提交配置”到“结果展示或生产入库”的完整链路。
>
> **适用边界**：本文解释 Linux 上的 GitHub Actions 与专用 GPU Runner 如何协同；它不是在个人电脑或共享服务器上直接执行 Benchmark 的操作手册。目标 Ref 的工作流、配置和脚本才是最终契约，不能用本文或旧 Run 的命令替代源码核对。

---

## 1. 先用一句话理解它

`run-sweep.yml` 是 InferenceX 的**自动性能扫描编排器**：当一个面向 `main` 的 PR 改动了 `perf-changelog.yaml`，或者 `main` 上的该文件发生变化时，它会：

```text
GitHub 事件
  → 检查 changelog 是否合格、是否允许运行
  → 根据 base/head 的 changelog 差异规划测试矩阵
  → （必要时）先跑固定长度 canary
  → 将矩阵拆成许多独立的 GPU Benchmark Job
  → 收集每个 Job 上传的 artifact
  → 在 PR 上生成非正式结果入口，或在 main 合并后触发生产入库
```

它本身**不运行模型、不下载模型、不运行 AIPerf，也不直接占用 GPU**。它是“调度与分流层”。真正申请专用 GPU/Slurm 资源、启动容器和推理服务的步骤，在它调用的 reusable workflow 与 runner launcher 中。

---

## 2. 先区分三个容易混淆的阶段

### 2.1 Workflow Run 被创建，不等于 GPU Benchmark 已开始

符合下面事件过滤条件时，GitHub 会创建一个名为 **Run Sweep** 的 workflow run：

```yaml
on:
  push:
    branches: [main]
    paths: ["perf-changelog.yaml"]
  pull_request:
    branches: [main]
    types: [synchronize, labeled, unlabeled]
    paths: ["perf-changelog.yaml"]
```

这只说明 GitHub 开始执行 YAML 中的检查 Job。此时很可能只运行 `ubuntu-latest` 上的 Python 校验，完全没有 GPU。

### 2.2 GPU Benchmark 被授权，才会进入矩阵 Job

`setup` Job 会要求下列条件同时成立：

1. 是同一仓库的 PR（避免不受信任 Fork 获得受保护的 GPU/secret）；
2. `perf-changelog.yaml` 校验成功；
3. PR 没有 `[skip-sweep]` 标记；
4. 没有被复用逻辑标记为跳过；
5. PR 有一个允许 GPU 扫描的主标签。

主标签为：

- `sweep-enabled`
- `full-sweep-enabled`
- `non-canary-full-sweep-enabled`
- `full-sweep-fail-fast`
- `full-sweep-fail-fast-no-canary`

没有这些标签时，workflow 可以存在、检查也可以成功，但 GPU Benchmark 不会被派发。

### 2.3 Benchmark 结束，不等于已发布为正式数据

- **PR Run**：结果可被汇总、比较，并由机器人评论提供 `unofficialRun=<runId>` 的可视化链接；这是非正式预览。
- **合并到 `main` 后的 push Run**：成功的结果才可能通过 repository dispatch 发送给 `SemiAnalysisAI/InferenceX-app`，进入生产入库链路。

因此，PR 上“看到结果”与生产网站“正式入库”是两件不同的事。

---

## 3. 基础词汇表

| 词汇 | 面向初学者的含义 |
| --- | --- |
| **workflow** | GitHub Actions 的自动化流程文件，例如 `run-sweep.yml`。 |
| **job** | workflow 中可独立调度的一段工作，例如校验、矩阵规划、单节点压测。 |
| **step** | Job 内顺序执行的一个动作，例如 checkout、运行 Python 模块、上传 artifact。 |
| **runner** | 真正执行 Job 的机器。`ubuntu-latest` 是 GitHub 托管机；`self-hosted` 是项目维护的专用机器，通常连接 GPU 集群。 |
| **self-hosted runner** | 项目自己的 GitHub runner。它只负责接收 Job；随后脚本可能再用 Slurm 申请 GPU。 |
| **matrix（矩阵）** | 一组配置行。每一行是一个可独立运行的 Benchmark，例如相同模型但并发分别为 20、24、28。 |
| **sweep（扫描）** | 对多组配置/并发/拓扑批量运行 Benchmark，而不是单次命令。 |
| **artifact** | Job 上传给 GitHub 的结果文件包。后续汇总 Job 从 artifact 下载结果。 |
| **reusable workflow** | 通过 `uses: ./.github/workflows/xxx.yml` 被另一个 workflow 调用的模板 workflow。 |
| **canary（金丝雀）** | 在大扫描之前选少量低成本固定长度测试，尽早发现明显故障。不是 AgentX 的抽样。 |
| **base/head** | PR 的比较两端：base 是目标分支基线（通常 `main`），head 是 PR 当前提交。 |
| **changelog** | 这里特指 `perf-changelog.yaml`，记录“哪些性能配方发生变化，应该测什么”。 |
| **ingest（入库）** | 将已验证结果通知结果服务，由其写入生产数据系统。 |
| **eval** | 正确性/能力评估；与吞吐、TTFT 等性能 Benchmark 是相邻但不同的任务。 |

---

## 4. GitHub Actions YAML 语法速读

### 4.1 `on`：什么事件会创建 Run

`on` 是触发器。此工作流关注两类事件：

- `pull_request`：PR 更新（`synchronize`），或 PR 标签被添加/移除（`labeled`/`unlabeled`）；
- `push`：`main` 上推送了包含 `perf-changelog.yaml` 变化的提交。

`branches: [main]` 表示 PR 目标分支或 push 分支必须是 `main`。`paths` 是路径过滤：没有 changelog 变化时，这个 workflow 不会因为普通代码变更而创建 Run。

### 4.2 `${{ ... }}`：GitHub 表达式

`${{ ... }}` 在 GitHub 执行工作流前或执行期间被求值。例如：

```yaml
run-name: Run Sweep - ${{ github.event.pull_request.title || github.event.head_commit.message }}
```

PR 事件用 PR 标题命名 Run；push 事件则用提交消息。`||` 是“左边没有值时使用右边”的回退。

### 4.3 常见 context

| context | 来自哪里 | 典型用途 |
| --- | --- | --- |
| `github` | 当前 GitHub 事件 | `github.event_name`、PR 标签、PR 编号、SHA、仓库名。 |
| `needs` | 已完成的前置 Job | 读取 `setup.outputs.search-space-config` 等输出。 |
| `matrix` | 当前矩阵的一行 | 读取 `matrix.config.model`、`matrix.config.conc` 等字段。 |
| `inputs` | reusable workflow 调用者传入 | 模板 workflow 内读取模型、runner、并发等。 |
| `vars` | 仓库/组织配置变量 | 例如是否启用优先级调度器。不是 secret。 |
| `secrets` | GitHub 加密机密 | HF token、PAT、数据库连接信息等；不应输出到日志。 |

### 4.4 `needs`、`if`、`strategy.matrix`

- `needs: [setup, canary-select]`：本 Job 必须等这些 Job 完成。
- `if:`：条件不满足时 Job 显示为 `skipped`，不是失败。
- `strategy.matrix`：对数组中的每个对象创建一个 Job 实例。

AgentX 单节点部分的核心形态是：

```yaml
strategy:
  matrix:
    config: ${{ fromJson(needs.setup.outputs.search-space-config).single_node['agentic'] }}
```

`setup` 先输出 JSON 字符串；`fromJson(...)` 将它还原为对象；`['agentic']` 取出 AgentX 配置行；GitHub 为每行创建一个 `benchmark-tmpl.yml` 调用。

### 4.5 `permissions`

`permissions` 是 GitHub token 在该 Job 内具备的最小权限。例如模板 workflow 声明：

```yaml
permissions:
  contents: read
```

表示它只需要读代码。涉及 PR 评论、artifact 或跨仓库 dispatch 的 Job 会在各自位置拥有不同且更精确的权限/secret。

### 4.6 `concurrency` 与 `cancel-in-progress`

`run-sweep.yml` 按 PR 编号或 commit SHA 建立并发组：

```yaml
concurrency:
  group: sweep-...
  cancel-in-progress: true
```

含义是：同一 PR 的普通更新到来时，旧扫描可被取消，以免旧提交继续占用稀缺 GPU。对于会改变执行语义的标签事件，group 中还混入 `github.run_id`，使它们可并存，不被错误取消。

---

## 5. `perf-changelog.yaml`：为什么它是入口的核心

它不是“发布说明”，也不会直接启动模型。它是**性能配方变更清单 + 审计记录 + 测试规划输入**。

典型条目说明：

```yaml
config-keys:
  - qwen3.5-fp4-mi355x-sglang-agentic-mtp
scenario-type: agentic-coding
description: Update HiCache defaults
pr-link: https://github.com/SemiAnalysisAI/InferenceX/pull/3118
```

`config-keys` 指向 master config 内的配方 key；`scenario-type` 告诉规划器生成固定长度还是 AgentX 矩阵；描述和 PR 链接让后续结果具有可审计来源。

`check-changelog` Job 运行：

```bash
python -m infx.workflows.validate_perf_changelog
```

它会严格校验 YAML、字节格式、重复 key 和 changelog schema。通过校验只说明“清单合法”，并不说明模型能启动或性能正常。

---

## 6. 从入口开始逐 Job 阅读 `run-sweep.yml`

下面的顺序按实际依赖关系组织；同一层中一些 Job 可以并行。

### 6.1 `check-changelog`：先验证，先做安全门控

它主要回答四个问题：

1. 这是不是同仓库 PR？外部 Fork 不应自动获得受保护 GPU/secret。
2. 是否存在彼此冲突的 sweep 标签？
3. PR 标题/描述是否要求 `[skip-sweep]`？
4. `perf-changelog.yaml` 是否能通过 `validate_perf_changelog`？

它输出供后续 Job 使用的 gate 状态，例如 changelog 是否有效、是否请求跳过。这里执行的是 GitHub 托管的普通检查环境，而不是 GPU Benchmark。

### 6.2 `reuse-sweep-gate`：能否复用已有扫描

它运行：

```bash
python3 -m infx.workflows.reuse \
  --repo "${{ github.repository }}" \
  --commit-sha "${{ github.event.pull_request.head.sha }}" \
  --event-name "${{ github.event_name }}" \
  --event-action "${{ github.event.action }}" \
  --pr-number "${{ github.event.pull_request.number }}" \
  --ref "${{ github.ref }}" \
  --workflow-id "run-sweep.yml"
```

其目的不是生成矩阵，而是查找是否存在可被授权复用的结果。复用能避免同一配方/提交重复占用 GPU。后续 `setup` 和 AgentX 入库路径会根据它的输出判断是新跑、跳过，还是复用已有 artifact。

### 6.3 `setup`：把 changelog 差异变成可运行矩阵

这是编排层最关键的 Job。它选择比较两端：

- PR：`BASE_REF=origin/<github.base_ref>`，`HEAD_REF=<PR head SHA>`；
- main push：使用事件中的 before/after SHA。

接着执行：

```bash
python -m infx.matrix.plan \
  --changelog-file "$GITHUB_WORKSPACE/perf-changelog.yaml" \
  --base-ref "$BASE_REF" \
  --head-ref "$HEAD_REF"
```

`infx.matrix.plan` 是 PR 感知的规划器。它会读取 base/head 中的 changelog 差异，确定新增或变更的 config key、场景类型与 eval 策略，然后在 Python 进程内调用 `infx.matrix.generate` 的生成逻辑。

可把它理解为：

```text
perf-changelog diff
  → 选择要测的 config key / scenario
  → 读取 configs/*-master.yaml + configs/runners.yaml
  → 展开 search-space、conc-list、拓扑
  → 验证每一行
  → 输出按用途分桶的 JSON
```

常见输出桶包括：

```text
single_node: 1k1k、8k1k、agentic
multi_node: 1k1k、8k1k、agentic
 evals / agentic_evals / multinode_evals / multinode_agentic_evals
changelog_metadata
```

标签会改变 planner 参数：

| 标签 | 给规划/调度带来的效果 |
| --- | --- |
| `sweep-enabled` | 加 `--trim-conc`，通常每个部署形状只保留最低并发点。 |
| `all-evals` | 加 `--all-evals`，扩大 Eval 选择。 |
| `evals-only` | 加 `--evals-only`，跳过性能吞吐 Benchmark，只做 Eval。 |

注意：`infx.matrix.generate` 不是独立服务，也不是需要额外安装的产品。它是仓库中的 Python 模块；`python -m infx.matrix.generate` 运行其 `main()`。而 PR 自动链路通常由 `infx.matrix.plan` 在进程内调用生成函数，而非简单 shell 出一个 generator CLI。

`setup` 也可能运行：

```bash
python -m infx.workflows.ci_priority ...
```

它为 GPU 队列生成优先级和一次性 queue token。某些部署中还可受 `vars.PRIORITY_SCHEDULER_ENABLED` 控制，使用受限的自动分类辅助信息；这不改变 Benchmark 配方本身。

### 6.4 `canary-select`：在大扫描前选择少量固定长度探针

只在 `full-sweep-enabled` 或 `full-sweep-fail-fast` 时启用。它从：

```text
single_node['1k1k'] + single_node['8k1k']
```

中挑选每个相关部署形状的最低并发候选，输出 canary matrix。

**AgentX 不在这里面。** `single_node['agentic']` 不参与 canary 选择。因此不能把“canary 成功”解释成“AgentX 轨迹回放已经验证成功”。

### 6.5 `canary-sweep`：实际运行固定长度 canary

它对 canary matrix 调用：

```text
.github/workflows/benchmark-tmpl.yml
```

canary 成功或被跳过后，正常 sweep 才能继续。若 canary 失败，普通 full sweep 由依赖条件阻止，从而避免已知坏镜像/配方继续消耗大量 GPU。

### 6.6 固定长度性能 Job

这些 Job 都是矩阵风扇出，并分别覆盖不同拓扑：

| Job | 数据桶 | 作用 |
| --- | --- | --- |
| `sweep-single-node-1k1k` | `single_node['1k1k']` | 单节点固定序列长度性能测试。 |
| `sweep-single-node-8k1k` | `single_node['8k1k']` | 单节点较长上下文固定序列长度性能测试。 |
| `sweep-multi-node-1k1k` | `multi_node['1k1k']` | 多节点固定长度测试。 |
| `sweep-multi-node-8k1k` | `multi_node['8k1k']` | 多节点长上下文固定长度测试。 |

单节点调用 `benchmark-tmpl.yml`；多节点调用 `benchmark-multinode-tmpl.yml`。两者都不是直接写在 `run-sweep.yml` 内的 shell 命令，而是 reusable workflow 调用。

### 6.7 `sweep-agentic`：单节点 AgentX 主路径

它的关键条件是：

```yaml
needs.setup.result == 'success'
needs.setup.outputs.reuse-enabled != 'true'
(needs.canary-sweep.result == 'success' || needs.canary-sweep.result == 'skipped')
toJson(fromJson(needs.setup.outputs.search-space-config).single_node['agentic']) != 'null'
```

含义是：规划成功、无需复用、固定长度 canary 没有阻止后续流程，且规划器确实给出单节点 AgentX 条目时，才展开 AgentX Job。

它为每条 agentic matrix 行调用 `benchmark-tmpl.yml`，并显式传入：

```yaml
isl: '0'
osl: '0'
max-model-len: '0'
disagg: 'false'
run-eval: false
scenario-type: agentic-coding
agentx-fast: ${{ contains(github.event.pull_request.labels.*.name, 'agentx-fast') }}
```

其中 `isl`、`osl` 为零不是“输入输出为零”，而是表示 AgentX 不使用固定序列长度模型。AgentX 由真实多轮轨迹决定每一轮消息与输出行为。

### 6.8 `sweep-multi-node-agentic`：多节点 AgentX 路径

逻辑与单节点 AgentX 类似，但使用：

```text
.github/workflows/benchmark-multinode-tmpl.yml
```

并将 prefill/decode worker、各阶段 TP/EP、节点数等多节点拓扑字段交给模板。是否存在该 Job 的实例由规划器的 multi-node agentic 桶决定。

### 6.9 Eval Job：性能之外的正确性/能力检查

工作流还有以下分支：

```text
sweep-evals
sweep-agentic-evals
sweep-multi-node-evals
sweep-multi-node-agentic-evals
```

它们分别消费固定长度、单节点 AgentX、多节点固定长度、多节点 AgentX 的 eval 桶。它们可能在已有服务配方上执行 `lm-eval`、SWE-bench 或其他支持的评估框架；这不是 AIPerf 吞吐量结果的重复计算。

### 6.10 收集、元数据、成功率与比较 Job

| Job | 做什么 | 重要边界 |
| --- | --- | --- |
| `collect-results` | 调用 `collect-results.yml` 下载 `bmk_*` artifact 并运行 `infx.results.collect_results`。 | 当前条件重点检查固定长度 Job；纯 AgentX PR 可能跳过这一传统聚合路径。 |
| `collect-evals` | 调用 `collect-evals.yml` 下载 `eval_*` artifact，运行 `infx.results.collect_eval_results`，生成聚合 Eval JSON 和摘要。 | 只要相关 Eval Job 未全部 skipped，便可执行。 |
| `upload-changelog-metadata` | 写入/上传 `changelog_metadata.json` 和 `sweep_manifest.json`。 | 让结果能追溯到配置和本次 sweep。 |
| `calc-success-rate` | 运行 `python -m infx.workflows.calc_success_rate`。 | 计算本次工作流的成功率统计。 |
| `compare-results` | PR 上运行 `python -m infx.results.compare_results results/`，写入 `$GITHUB_STEP_SUMMARY`。 | 依赖可用的传统聚合结果与只读结果数据源。 |

### 6.11 PR 展示与 main 入库 Job

- `comment-unofficial-run-visualizer`：在 PR 中评论非正式可视化链接，例如：

  ```text
  https://inferencex.semianalysis.com/inference?unofficialRun=<runId>
  https://inferencex.semianalysis.com/evaluation?unofficialRun=<runId>
  ```

- `trigger-ingest`：仅针对 `main` push、且没有 AgentX 结果桶时，向 `SemiAnalysisAI/InferenceX-app` 发 repository dispatch：

  ```json
  { "event_type": "ingest-results" }
  ```

- `trigger-agentic-ingest`：仅针对 `main` push、且 AgentX 成功完成或被授权复用时，发送：

  ```json
  {
    "event_type": "ingest-agentic-results",
    "database-target": "production"
  }
  ```

repository dispatch 是 GitHub 的跨仓库事件通知；它不是在当前 Runner 上执行 AgentX，也不是给任何外部人员开放的公共 HTTP 调用。

---

## 7. reusable workflow 如何把矩阵变成真实执行环境

### 7.1 `benchmark-tmpl.yml`：单节点通用模板

它通过 `workflow_call` 接受模型、镜像、精度、框架、TP/EP、并发等 input，并将其映射为 shell 环境变量：

```yaml
EXP_NAME: ${{ inputs.exp-name }}
MODEL: ${{ inputs.model }}
FRAMEWORK: ${{ inputs.framework }}
TP: ${{ inputs.tp }}
EP_SIZE: ${{ inputs.ep }}
CONC: ${{ inputs.conc }}
SCENARIO_TYPE: ${{ inputs.scenario-type }}
SCENARIO_SUBDIR: ${{ inputs.scenario-type == 'agentic-coding' && 'agentic/' || 'fixed_seq_len/' }}
IS_AGENTIC: ${{ inputs.scenario-type == 'agentic-coding' && '1' || '0' }}
DURATION: ${{ inputs.duration }}
AIPERF_EXPERIMENTAL_FAST: ${{ inputs.agentx-fast && '1' || '0' }}
```

这段映射非常关键：`agentic-coding` 不只是一个显示标签；它决定后续 launcher 从 `benchmarks/single_node/agentic/` 选择脚本，并让 AIPerf 路径走 AgentX 重放。

模板的 `runs-on` 会选择匹配的 self-hosted runner 标签，并附带优先级/队列元数据。它不意味着该机器上的任意用户可以复用 GPU；资源由项目的 CI 队列与后续 Slurm 控制。

模板中“Launch job script”最终执行：

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

若 `RUNNER_NAME=mi355x-amds_03`，Bash 的 `${RUNNER_NAME%%_*}` 会去掉第一个下划线及之后内容，结果为：

```bash
bash ./runners/launch_mi355x-amds.sh
```

### 7.2 `benchmark-multinode-tmpl.yml`：多节点模板

它与单节点模板概念相同，但额外接受：

- `node-count`；
- prefill/decode 的 worker 数、TP/PP/EP；
- prefill/decode 硬件描述；
- 多节点通信与并发列表。

这些字段不能可靠地塞进单节点 launcher；所以多节点有独立模板与 launcher 分支。

### 7.3 `collect-results.yml` 与 `collect-evals.yml`

`collect-results.yml` 下载匹配的 benchmark artifact，并运行：

```bash
python3 -m infx.results.collect_results results/ <prefix>
```

`collect-evals.yml` 则下载 `eval_*` artifact，并运行：

```bash
python -m infx.results.collect_eval_results eval_results/ <prefix>
```

之后再把聚合 JSON 上传成新的 artifact。收集器只处理已上传文件；它不重新启动模型或重新跑 Benchmark。

---

## 8. 以 AMD AgentX 配方为例，脚本到底怎么被选中

以历史 PR #3118 中的 key 为例：

```text
qwen3.5-fp4-mi355x-sglang-agentic-mtp
```

配置和执行路径如下：

```text
perf-changelog.yaml
  → infx.matrix.plan
  → configs/amd-master.yaml 的该 config key
  → agentic-coding matrix 行
  → run-sweep.yml 的 sweep-agentic
  → benchmark-tmpl.yml
  → runners/launch_mi355x-amds.sh
  → benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
  → benchmarks/benchmark_lib.sh
  → AIPerf: aiperf profile --scenario inferencex-agentx-mvp
```

`launch_mi355x-amds.sh` 会从环境变量构造候选脚本名。对于：

```text
EXP_NAME 前缀 = qwen3.5
PRECISION = fp4
FRAMEWORK = sglang
SPEC_DECODING = mtp
SCENARIO_SUBDIR = agentic/
```

它选择：

```text
benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
```

这个脚本负责：

1. 检查 `MODEL`、`TP`、`CONC`、`EP_SIZE`、`KV_OFFLOADING`、`TOTAL_CPU_DRAM_GB`、`RESULT_DIR`、`DURATION` 等环境变量；
2. 准备/下载模型；
3. 固定 AgentX 数据集 loader，解析 trace source，安装 AgentX 依赖；
4. 按 TP/EP/MTP/HiCache 参数启动 SGLang；
5. 调用 `build_replay_cmd "$RESULT_DIR"`；
6. 增加 `--apply-chat-template` 后调用 `run_agentic_replay_and_write_outputs`；
7. 写入命令、日志、AIPerf artifact 与规范化结果 JSON。

而 `benchmark_lib.sh` 中的 `build_replay_cmd()` 才组装真实 AIPerf 命令。官方 AgentX 路径包含 streaming、`inferencex-agentx-mvp` scenario、轨迹起点比例、warmup、失败请求阈值、trace idle cap、服务端 token 计数等选项。

`agentx-fast` 会设置：

```bash
AIPERF_EXPERIMENTAL_FAST=1
```

在该函数中将典型 AgentX profile 从 3600 秒缩短为 1200 秒，并把每 lane 的额外 warmup 请求从 10 减为 1；它不是“跳过 AgentX”，也不是把所有数据集轨迹替换为 synthetic request。

---

## 9. PR 标签的实际作用

### 9.1 主执行模式标签

| 标签 | 语义 |
| --- | --- |
| `sweep-enabled` | 运行经过并发裁剪的小扫描，常用于先验证一个部署形状。 |
| `full-sweep-enabled` | 运行完整扫描，并先执行固定长度 canary。 |
| `non-canary-full-sweep-enabled` | 完整扫描但不要求 canary。 |
| `full-sweep-fail-fast` | 完整扫描，包含 canary；矩阵中有一个 Job 失败时尽早取消尚未完成的同组 Job。 |
| `full-sweep-fail-fast-no-canary` | fail-fast 完整扫描，但不运行 canary。 |

这些是互斥语义，`check-changelog` 会阻止不合理的冲突组合。

### 9.2 修饰标签

| 标签 | 作用 |
| --- | --- |
| `agentx-fast` | 仅影响 AgentX：profile 20 分钟、每 lane 额外 warmup 1 次。 |
| `all-evals` | 请求全部适用 Eval。 |
| `evals-only` | 只运行 Eval，不运行常规性能 sweep。 |
| `skip_queue` | 传递队列跳过请求，但仍受项目授权与 runner 调度机制约束。 |
| `ci-patchwork` / `engine-patch` | 与 CI/补丁工作流的策略和优先级相关。 |
| `ci-patchwork-waived` / `ci-checklist-complete` | 作为流程状态/豁免信号，可能影响授权或优先级策略。 |

标签只是 workflow 输入和 gate 信号；标签不会把本地容器变成 GitHub runner，也不会让用户在共享宿主机直接运行专用脚本。

---

## 10. PR #3118 作为完整 AgentX 例子

PR #3118 的主题是 AMD Qwen3.5 AgentX 相关镜像与 HiCache 默认设置更新。其历史配方包含：

```text
qwen3.5-fp4-mi355x-sglang-agentic-mtp
```

其 `agentic-coding` search-space 中包含如下形状（历史快照）：

```text
TP=4, EP=1, KV=none: conc [1, 4, 8, 12, 16]
TP=2, EP=1, KV=none: conc [1, 4, 8, 12, 16, 20]
TP=2, EP=1, KV=dram + HiCache: conc [20, 24, 28, 32, 36, 40]
```

完整扫描会产生 17 个 AgentX matrix 点。以最后一组为例，每个并发值是独立的 matrix Job；不是一台服务在同一个进程内依次切换并发。

历史 PR 改动的 HiCache 默认值是：

```text
direct / page_first_direct
→ kernel / page_first
```

这些默认值只在 `KV_OFFLOADING=dram` 且后端要求 HiCache 的分支中生效；它们不作用于 `kv-offloading: none` 的配置。

> **历史快照警告**：PR #3118 的配置、镜像和 workflow 是该 PR 时间点的事实。当前 `main` 可能已有后续修改；复现或解释当前行为时必须读取目标 commit 的 `configs/amd-master.yaml`、`run-sweep.yml` 和脚本，而不能假设历史值仍未变化。

另有专门的历史链路文档：[`pr-3118-agentx-mi355x-hicache-chain-zh.md`](./pr-3118-agentx-mi355x-hicache-chain-zh.md)，用于深入查看该 PR 的差异和参数来源；本文侧重如何读懂自动工作流。

---

## 11. 哪些地方会真正影响基础设施

`run-sweep.yml` 中的 Python 规划、JSON 分桶、artifact 汇总大多是可撤销的 CI 编排动作；但以下链路会影响真实计算资源：

```text
benchmark-tmpl.yml / benchmark-multinode-tmpl.yml
  → self-hosted runner
  → runners/launch_*.sh
  → Slurm salloc / srun
  → 容器镜像准备、Docker 操作、挂载缓存
  → 推理服务器启动
  → AIPerf / Eval 负载
  → 清理 Docker / Slurm 资源
```

例如 `launch_mi355x-amds.sh` 会申请 Slurm 独占资源、运行容器，并在其职责范围内执行清理。不能因为看到了脚本就把它用于带有现有 vLLM 服务、用户容器或共享缓存的机器。

**安全操作原则：**

1. 只通过项目授权的 GitHub Actions、专用 self-hosted runner 和 Slurm 分区运行此链路；
2. 未经明确授权，不在共享宿主机执行 launcher、Docker cleanup 或 GPU Benchmark；
3. 不为了验证 workflow 权限而随意 dispatch 工作流；
4. 不重启已有推理服务来“模拟” CI；
5. 不把 token、代理凭据、数据库 URL 或其他 secret 写进 Markdown、日志、Issue 或 PR。

---

## 12. 如何排查“为什么没有跑 AgentX”

按从外到内的顺序检查：

1. **Run 是否创建？**确认 PR 目标是 `main`，且该次变更包含 `perf-changelog.yaml`。
2. **`check-changelog` 是否成功？**检查 YAML 格式、schema、冲突标签、`[skip-sweep]`。
3. **是否有主 sweep 标签？**没有 `sweep-enabled` 等标签时，不会派发 GPU Job。
4. **`reuse-sweep-gate` 是否选择复用？**复用时可能看不到新的 benchmark Job。
5. **`setup` 是否生成 `single_node['agentic']` 或 multi-node agentic 桶？**若没有，说明 changelog/config/scenario 组合没有产出 AgentX 行。
6. **canary 是否阻塞？**full sweep 的固定长度 canary 失败可阻止后续 AgentX；但 canary 成功不代表 AgentX 已验证。
7. **`sweep-agentic` 是否为 skipped？**查看其 `if` 的每一项，尤其 `reuse-enabled`、bucket 是否为 null、canary 结果。
8. **矩阵实例是否失败？**此时才沿 reusable template → launcher → recipe → AIPerf 日志逐层排查。

不要把“workflow 未创建”“Job skipped”“GPU Job 排队”“模型服务失败”“artifact 验证失败”混为同一种问题；它们分别属于事件过滤、授权 gate、资源调度、运行时和结果处理。

---

## 13. 最终心智模型

```text
开发者修改性能配方，并把意图写进 perf-changelog.yaml
  ↓
PR / main 事件满足 paths 与 branches 过滤
  ↓
run-sweep.yml 创建 Run
  ↓
check-changelog + reuse-sweep-gate
  ↓
setup：比较 base/head，infx.matrix.plan 生成并分桶矩阵
  ↓
可选 fixed-seq canary
  ↓
单节点/多节点 fixed-seq、AgentX、Eval 的 reusable workflow matrix
  ↓
self-hosted runner → launcher → Slurm/container → benchmark recipe
  ↓
推理服务 + AIPerf 或 Eval 生成 artifact
  ↓
collect / metadata / success rate / compare
  ↓
PR：非正式可视化评论
main：repository dispatch 到 InferenceX-app，正式入库
```

如果只记住一件事：**`run-sweep.yml` 的工作是根据 changelog 和标签规划、授权、扇出和收集；AgentX 的真实运行发生在它派发的 reusable benchmark template、runner launcher、AgentX recipe 和 AIPerf 这条下游链路中。**

---

## 14. 关联源码索引

- 自动扫描入口： [`.github/workflows/run-sweep.yml`](../../.github/workflows/run-sweep.yml)
- changelog 校验：[`infx/workflows/validate_perf_changelog.py`](../../infx/workflows/validate_perf_changelog.py)
- PR-aware planner：[`infx/matrix/plan.py`](../../infx/matrix/plan.py)
- 矩阵生成器：[`infx/matrix/generate.py`](../../infx/matrix/generate.py)
- 单节点模板： [`.github/workflows/benchmark-tmpl.yml`](../../.github/workflows/benchmark-tmpl.yml)
- 多节点模板： [`.github/workflows/benchmark-multinode-tmpl.yml`](../../.github/workflows/benchmark-multinode-tmpl.yml)
- 结果收集： [`.github/workflows/collect-results.yml`](../../.github/workflows/collect-results.yml)
- Eval 收集： [`.github/workflows/collect-evals.yml`](../../.github/workflows/collect-evals.yml)
- AMD runner launcher：[`runners/launch_mi355x-amds.sh`](../../runners/launch_mi355x-amds.sh)
- AMD AgentX recipe：[`benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh`](../../benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh)
- AIPerf 命令构建与结果包装：[`benchmarks/benchmark_lib.sh`](../../benchmarks/benchmark_lib.sh)
- CI 更完整操作参考：[`docs/ci-procedures_zh.md`](../../docs/ci-procedures_zh.md)
