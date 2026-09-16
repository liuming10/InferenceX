# `run-sweep.yml` 与 `benchmark-tmpl.yml`：面向初学者的执行流程说明

本文解释 `.github/workflows/` 目录下两个 GitHub Actions workflow 的关系：

- `run-sweep.yml`：一次 sweep（扫描/批量测试）的总控程序，决定“要测试哪些配置、启动多少个 job、何时收集结果”。
- `benchmark-tmpl.yml`：单节点 benchmark 的执行模板，决定“一个具体配置怎样申请机器、启动服务、运行测试、处理结果并上传文件”。

可以先记住一句话：

```text
run-sweep.yml 负责安排一批任务
benchmark-tmpl.yml 负责执行其中一个任务
```

多节点任务使用姊妹模板 `benchmark-multinode-tmpl.yml`，本文也会在相关位置说明它的区别。

---

## 1. 先理解 GitHub Actions 的基本名词

### 1.1 Workflow

Workflow 是一个 YAML 文件，描述一套自动化流程。GitHub 收到指定事件后，会按照 YAML 中的规则运行它。

本文涉及的 workflow 文件是：

```text
.github/workflows/run-sweep.yml
.github/workflows/benchmark-tmpl.yml
```

### 1.2 Event（事件）

事件是触发 workflow 的原因。`run-sweep.yml` 监听两类事件：

```yaml
on:
  push:
    branches:
      - main
    paths:
      - "perf-changelog.yaml"
  pull_request:
    branches:
      - main
    types:
      - synchronize
      - labeled
      - unlabeled
    paths:
      - "perf-changelog.yaml"
```

含义是：

- `push`：代码被推送到 `main`，并且这次提交修改了 `perf-changelog.yaml`。
- `pull_request`：针对 `main` 的 Pull Request 发生同步或标签变化，并且修改了 `perf-changelog.yaml`。
- `synchronize`：PR 分支又推送了新提交。
- `labeled` / `unlabeled`：PR 添加或删除了标签。

如果没有修改 `perf-changelog.yaml`，这些事件通常不会触发该 sweep。

### 1.3 Job

Job 是 workflow 中的一项工作。每个 job 通常在一台 runner 上运行。

例如 `run-sweep.yml` 中有：

```text
check-changelog
reuse-sweep-gate
setup
canary-sweep
sweep-single-node-1k1k
sweep-single-node-8k1k
sweep-multi-node-1k1k
sweep-evals
collect-results
compare-results
trigger-ingest
```

这些 job 之间通过 `needs` 表示依赖关系。

### 1.4 Step

Step 是 job 内的一步操作。例如：

```yaml
- uses: actions/checkout@...
- name: Set up uv
  uses: astral-sh/setup-uv@...
- name: Launch job script
  run: bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

一个 job 会按顺序执行自己的 steps。

### 1.5 Runner

Runner 是真正运行 job 的机器，可以是：

- GitHub 提供的 `ubuntu-latest` 虚拟机；
- 项目自有的 self-hosted GPU 机器；
- 通过 Slurm 调度器分配的 GPU 节点。

注意：这里的 `runner` 既可能指 GitHub Actions 执行机，也可能指配置中的 GPU 集群标签。要结合上下文判断。

### 1.6 Reusable workflow（可复用 workflow）

`benchmark-tmpl.yml` 使用：

```yaml
on:
  workflow_call:
```

这表示它不是依靠 push 或 PR 直接触发，而是由另一个 workflow 调用。

`run-sweep.yml` 中的调用形式是：

```yaml
uses: ./.github/workflows/benchmark-tmpl.yml
with:
  model: ${{ matrix.config.model }}
  runner: ${{ matrix.config.runner }}
  framework: ${{ matrix.config.framework }}
```

可以把它理解为函数调用：

```text
benchmark_tmpl(config=某一个 matrix.config)
```

### 1.7 Matrix（矩阵）

Matrix 是“一次 job 运行多组参数”的机制。例如：

```yaml
strategy:
  matrix:
    config: ${{ fromJson(needs.setup.outputs.search-space-config).single_node['8k1k'] }}
```

如果 `config` 数组中有 5 个配置，GitHub Actions 就会展开成 5 个并行 job，每个 job 使用一个配置。

例如原始数组：

```json
[
  {"model-prefix": "qwen3", "conc": 1},
  {"model-prefix": "qwen3", "conc": 8},
  {"model-prefix": "qwen3", "conc": 32}
]
```

会变成三个独立任务：

```text
任务 A：qwen3，conc=1
任务 B：qwen3，conc=8
任务 C：qwen3，conc=32
```

### 1.8 Artifact（工件）

Artifact 是 GitHub Actions 保存的文件，供后续 job 或其他 workflow 下载。

本项目上传的典型 artifact 包括：

```text
bmk_*                  吞吐 benchmark 聚合结果
bmk_agentic_*          AgentX/Agentic 聚合结果
results_bmk            所有普通 benchmark 的聚合结果
eval_*                 单个评测 job 的结果
results_all / eval_results_all  汇总结果
changelog-metadata     changelog 元数据
run-stats              成功率统计
```

Artifact 不是实时数据库。它是结果文件的运输和暂存方式。

---

## 2. 整体流程图

一次正常的 sweep 大致经过以下步骤：

```text
修改 perf-changelog.yaml
        │
        ▼
GitHub 触发 run-sweep.yml
        │
        ▼
1. check-changelog
   检查标签、跳过策略和 changelog 格式
        │
        ▼
2. reuse-sweep-gate
   判断是否可以复用以前成功的 PR sweep
        │
        ▼
3. setup
   读取配置，生成完整 matrix，计算 CI 优先级
        │
        ▼
4. canary-select / canary-sweep（某些 full sweep 才有）
   先挑一个小任务做 canary 烟雾测试
        │
        ▼
5. sweep-* jobs
   按 matrix 并行运行实际 benchmark/eval
        │
        ▼
6. benchmark-tmpl.yml
   每个具体任务在这里启动 launcher 和 benchmark 脚本
        │
        ▼
7. collect-results / collect-evals
   下载各任务 artifact，汇总，再上传新的 artifact
        │
        ├── PR：compare-results 读取 main 基线并生成对比表
        │
        └── main push：trigger-ingest 通知 InferenceX-app 入库
```

---

## 3. `run-sweep.yml` 做什么

文件：

```text
.github/workflows/run-sweep.yml
```

它是总控 workflow，核心职责是：

1. 决定这次运行是否应该开始；
2. 检查 PR 标签和 changelog；
3. 从 changelog 生成 benchmark/eval 矩阵；
4. 给每个任务加上队列优先级；
5. 按节点类型、序列长度和场景拆成多个 job；
6. 调用 benchmark 模板；
7. 收集和上传结果；
8. 在特定条件下触发下游数据库 ingest。

它本身通常不启动模型服务，也不执行单个 benchmark 的压测命令。

---

## 4. `run-sweep.yml` 的每一步

### 第一步：检查是否应该运行 sweep

`run-sweep.yml` 通过 `if:` 条件判断 PR 是否满足要求，例如：

```yaml
if: >-
  github.event_name == 'pull_request' &&
  github.event.pull_request.head.repo.full_name == github.repository &&
  (...标签条件...)
```

这里会检查：

- 当前是不是 PR；
- PR 是否来自同一个仓库；
- 是否有允许 sweep 的标签；
- 是否存在互相冲突的 sweep 标签。

常见标签含义：

| 标签 | 含义 |
|---|---|
| `sweep-enabled` | 启用普通 sweep，通常会裁剪部分并发范围 |
| `full-sweep-enabled` | 启用完整 sweep，并运行 canary |
| `full-sweep-fail-fast` | 完整 sweep，失败后尽快停止其他矩阵任务 |
| `full-sweep-fail-fast-no-canary` | 完整 sweep，但不先跑 canary |
| `all-evals` | 扩大评测覆盖范围 |
| `evals-only` | 只跑评测，不跑吞吐 benchmark |
| `agentx-fast` | AgentX 使用更快的实验模式 |
| `skip_queue` | 请求跳过普通 CI 队列 |
| `[skip-sweep]` | PR commit message 中的标记，跳过 PR benchmark setup |

`Reject conflicting sweep labels` step 用 `jq` 统计互斥标签数量：

```bash
count=$(jq '...[...] | length' <<<"$SWEEP_LABELS")
if [ "$count" -gt 1 ]; then
    echo "::error::PR has multiple conflicting sweep labels. Pick exactly one."
    exit 1
fi
```

这一步只是在检查标签，不运行模型，也不访问数据库。

### 第二步：checkout 代码

```yaml
uses: actions/checkout@...
with:
  fetch-depth: 0
  persist-credentials: false
```

作用：

- 把仓库代码下载到当前 runner；
- `fetch-depth: 0` 下载完整 Git 历史，便于比较 base commit 和 head commit；
- `persist-credentials: false` 不把 checkout 使用的凭据继续保存在 Git 配置中。

### 第三步：检查 `[skip-sweep]`

```bash
if git log -1 --format=%B "$HEAD_SHA" | grep -Fq '[skip-sweep]'; then
    echo "skip-pr-sweep=true" >> "$GITHUB_OUTPUT"
else
    echo "skip-pr-sweep=false" >> "$GITHUB_OUTPUT"
fi
```

这里读取 PR 最新 commit message：

- 包含 `[skip-sweep]`：设置 job 输出 `skip-pr-sweep=true`；
- 不包含：设置为 `false`。

`GITHUB_OUTPUT` 是 GitHub Actions 用来在 step 之间传递输出值的特殊文件。

### 第四步：验证 `perf-changelog.yaml`

执行：

```bash
uv run --no-project --exclude-newer PT12H --python 3.12 \
  --with "pydantic>=2" --with pyyaml \
  python -m infx.workflows.validate_perf_changelog \
  --changelog-file perf-changelog.yaml \
  --base-ref "origin/${BASE_REF}" \
  --head-ref "${HEAD_SHA}"
```

名词解释：

- `uv`：Python 环境和依赖管理工具；
- `--no-project`：不把当前目录当作一个需要安装的 Python 项目；
- `--with pydantic` / `--with pyyaml`：临时提供所需 Python 依赖；
- `pydantic`：用于严格校验数据结构；
- `pyyaml`：用于读取 YAML 文件。

这一阶段验证 changelog 新增内容是否合法，例如配置名、场景名和字段格式是否正确。

### 第五步：判断能否复用以前的 sweep

job：

```text
reuse-sweep-gate
```

执行：

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

复用的意思是：如果以前已经对同一个 PR commit 成功运行过合格的 sweep，就不必重新占用 GPU，而是直接使用以前的 artifact。

以下情况通常不能复用：

```text
- evals-only
- agentx-fast
```

该步骤主要读取 GitHub Actions run、PR 和 artifact 元数据，不负责写 benchmark 数据库。

### 第六步：生成 search space 和 matrix

job：

```text
setup
```

核心命令：

```bash
uv run --no-project --exclude-newer PT12H --python 3.12 \
  --with pydantic --with pyyaml \
  python -m infx.matrix.plan \
  --changelog-file perf-changelog.yaml \
  --base-ref "$BASE_REF" \
  --head-ref "$HEAD_REF"
```

这个 Python 模块会：

1. 读取 `perf-changelog.yaml` 中本次新增的条目；
2. 读取 `configs/*-master.yaml` 中的模型和硬件配置；
3. 展开并发度、TP、EP、序列长度等搜索空间；
4. 生成单节点、多节点、普通评测和 Agentic 评测配置；
5. 用 Pydantic 验证最终结构；
6. 输出 JSON。

输出大致长这样：

```json
{
  "single_node": {
    "8k1k": [
      {
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "model-prefix": "dsv4",
        "precision": "fp4",
        "framework": "sglang",
        "runner": "mi355x",
        "isl": 8192,
        "osl": 1024,
        "tp": 8,
        "conc": 64,
        "spec-decoding": "mtp",
        "run-eval": false
      }
    ]
  },
  "multi_node": {},
  "evals": [],
  "agentic_evals": [],
  "multinode_evals": [],
  "multinode_agentic_evals": [],
  "changelog_metadata": {
    "base_ref": "origin/main",
    "head_ref": "...",
    "entries": []
  }
}
```

#### 常见字段含义

| 字段 | 含义 |
|---|---|
| `model` | 模型仓库名或模型路径 |
| `model-prefix` | 项目内部使用的模型简称 |
| `image` | 启动模型服务所用的容器镜像 |
| `precision` | 权重/计算精度，如 `fp4`、`fp8` |
| `framework` | 推理框架，如 `vllm`、`sglang` |
| `runner` | 目标机器或集群标签 |
| `isl` | Input Sequence Length，输入 token 数 |
| `osl` | Output Sequence Length，输出 token 数 |
| `tp` | Tensor Parallel，张量并行 GPU 数 |
| `pp` | Pipeline Parallel，流水线并行阶段数 |
| `ep` | Expert Parallel，MoE 专家并行规模 |
| `dp-attn` | 是否启用 Attention 的 Data Parallel 路径 |
| `conc` | concurrency，并发请求数 |
| `spec-decoding` | speculative decoding，推测解码方法 |
| `run-eval` | 是否运行评测 |
| `eval-only` | 是否只运行评测、不运行吞吐测试 |
| `recipe-fingerprint` | 配置 recipe 的确定性 SHA-256 指纹 |

### 第七步：给每个 job 计算优先级

`setup` 随后把前一步的 JSON 交给：

```bash
python -m infx.workflows.ci_priority
```

调用形式：

```bash
CONFIG_JSON=$(printf '%s' "$CONFIG_JSON" |
  uv run --no-project --exclude-newer PT12H --python 3.12 \
  --with pyyaml python -m infx.workflows.ci_priority \
  --event-name "${{ github.event_name }}" \
  --queue-namespace "${{ github.run_id }}:${{ github.run_attempt }}" \
  --labels-json "$PR_LABELS" \
  --pr-number "${{ github.event.pull_request.number || 0 }}" \
  --criteria-json "$PRIORITY_CRITERIA")
```

它不会运行 benchmark，也不写数据库，只给每个矩阵项增加调度信息：

```json
{
  "priority": "3.500",
  "queue-token": "a1b2c3d4e5f6...",
  "skip-queue-pr": 123
}
```

#### `priority`

优先级分数，通常分数越高越早运行。分数由 `configs/ci-priority.yaml` 中的规则计算，例如：

```text
基础分
+ push 事件加分
+ 多节点加分
+ Agentic 加分
+ fp4 加分
+ MTP/EAGLE 加分
+ 特定框架加分
+ 特定模型加分
```

#### `queue-token`

根据 job 的完整配置、matrix 路径和本次 workflow run 生成的哈希。它用于区分不同队列任务，避免两个配置使用同一个调度标签。

#### `skip-queue-pr`

PR 带有 `skip_queue` 标签时写入 PR 编号，后面的 runner 调度逻辑可以据此绕过普通队列。

最后，`setup` 把 JSON 写入：

```bash
echo "search-space-config=$CONFIG_JSON" >> "$GITHUB_OUTPUT"
```

之后的 job 用 `needs.setup.outputs.search-space-config` 读取它。

### 第八步：选择并运行 canary

完整 sweep 可能先运行：

```text
canary-select
canary-sweep
```

`canary` 是“金丝雀测试”，意思是先挑一个较小、较便宜的 benchmark 验证：

- runner 能否分配到；
- 容器能否启动；
- 模型能否加载；
- 服务是否能响应请求；
- 结果文件是否能生成。

如果 canary 失败，后续完整 sweep 通常没有必要继续消耗大量 GPU 资源。

### 第九步：按矩阵启动 benchmark jobs

`run-sweep.yml` 将 search space 分成多类 job，例如：

```yaml
sweep-single-node-8k1k:
  uses: ./.github/workflows/benchmark-tmpl.yml
  strategy:
    matrix:
      config: ${{ fromJson(needs.setup.outputs.search-space-config).single_node['8k1k'] }}
```

含义：

- `single_node`：单节点任务；
- `8k1k`：输入长度约 8K、输出长度约 1K；
- `uses`：调用 benchmark 执行模板；
- `matrix.config`：当前展开出来的一个具体配置。

多节点任务调用：

```yaml
uses: ./.github/workflows/benchmark-multinode-tmpl.yml
```

评测任务则从专门的数组读取：

```yaml
config: ${{ fromJson(needs.setup.outputs.search-space-config).evals }}
```

固定序列长度 benchmark 通常传入：

```yaml
run-eval: false
```

评测 job 通常传入：

```yaml
run-eval: true
eval-only: true
```

### 第十步：收集 benchmark 结果

job：

```yaml
collect-results:
  uses: ./.github/workflows/collect-results.yml
  with:
    result-prefix: "bmk"
```

它会：

1. 下载所有名称匹配 `bmk_*` 的 artifact；
2. 运行：

   ```bash
   python3 -m infx.results.collect_results results/ bmk
   ```

3. 生成：

   ```text
   agg_bmk.json
   ```

4. 重新上传为：

   ```text
   results_bmk
   ```

这里的“收集”只是下载、读取、合并 JSON 文件，不是写数据库。

### 第十一步：收集评测结果

job：

```yaml
collect-evals:
  uses: ./.github/workflows/collect-evals.yml
```

它会下载 `eval_*` artifact，执行：

```bash
python -m infx.results.collect_eval_results eval_results/ all
```

生成聚合评测文件：

```text
agg_eval_all.json
```

并上传为：

```text
eval_results_all
```

### 第十二步：保存 changelog 元数据

job：

```text
upload-changelog-metadata
```

它在本地生成两个 JSON 文件：

```text
changelog_metadata.json
sweep_manifest.json
```

其中 `changelog_metadata.json` 记录本次 changelog 条目，`sweep_manifest.json` 记录 head commit、run ID、run attempt 和完整 matrix。

然后通过：

```yaml
uses: actions/upload-artifact@...
with:
  name: changelog-metadata
  path: changelog_metadata.json
```

上传给后续系统使用。

### 第十三步：计算成功率

job：

```text
calc-success-rate
```

它下载结果 artifact 后执行：

```bash
python -m infx.workflows.calc_success_rate run_stats
```

该脚本使用 PyGithub 读取当前 GitHub Actions run 中各 job 的状态，统计不同硬件类别的成功数和总数，最后写入：

```text
run_stats.json
```

然后将其上传为 `run-stats` artifact。

这一步读取 GitHub job 状态并生成统计文件，不直接写 benchmark 数据库。

### 第十四步：PR 上比较 main 基线

job：

```text
compare-results
```

只在 PR 中运行。它设置：

```yaml
env:
  DATABASE_URL: ${{ secrets.NEON_PROD_RO_URL }}
```

然后执行：

```bash
python -m infx.results.compare_results results/
```

该 Python 程序使用 `psycopg2` 连接 Neon PostgreSQL，执行 `SELECT` 查询，读取 main 分支最新基线，再把当前 PR 结果与基线比较，输出到：

```text
$GITHUB_STEP_SUMMARY
```

它使用的是只读连接和查询逻辑，没有看到 `INSERT`、`UPDATE`、`DELETE` 或 `commit()`。因此：

```text
compare-results = 读取数据库做对比，不负责写库
```

### 第十五步：main push 时触发下游入库

job：

```text
trigger-ingest
```

只在以下条件成立时运行：

```text
事件是 push
分支是 refs/heads/main
setup 成功
结果收集或复用条件满足
```

执行：

```bash
curl -sSf -X POST \
  -H "Authorization: Bearer ${{ secrets.INFX_FRONTEND_PAT }}" \
  -H "Accept: application/vnd.github+v3+json" \
  https://api.github.com/repos/SemiAnalysisAI/InferenceX-app/dispatches \
  -d '{
    "event_type": "ingest-results",
    "client_payload": {
      "source-run-id": "...",
      "merge-run-id": "..."
    }
  }'
```

它做的不是直接连接数据库，而是向另一个仓库 `SemiAnalysisAI/InferenceX-app` 发送 GitHub Repository Dispatch 事件。

下游 `InferenceX-app` 收到 `ingest-results` 后，才会：

- 下载本次 workflow 的 artifacts；
- 解析 benchmark、eval、stats 和 changelog metadata；
- 执行自己的 ETL/数据库写入流程。

Agentic 结果使用单独的 job：

```text
trigger-agentic-ingest
```

发送事件：

```json
{
  "event_type": "ingest-agentic-results",
  "client_payload": {
    "source-run-id": "...",
    "merge-run-id": "...",
    "database-target": "production"
  }
}
```

因此要区分：

```text
run-sweep.yml：触发下游入库
InferenceX-app：实际负责解析并持久化数据库记录
```

---

## 5. `benchmark-tmpl.yml` 做什么

文件：

```text
.github/workflows/benchmark-tmpl.yml
```

它是“单个单节点 benchmark 的执行模板”。每展开一个 matrix 配置，GitHub Actions 就调用一次这个模板。

它接收很多 `workflow_call` 输入，例如：

```yaml
inputs:
  runner:
    required: true
    type: string
  image:
    required: true
    type: string
  model:
    required: true
    type: string
  model-prefix:
    required: true
    type: string
  precision:
    required: true
    type: string
  framework:
    required: true
    type: string
  isl:
    required: true
    type: string
  osl:
    required: true
    type: string
  tp:
    required: true
    type: string
  conc:
    required: true
    type: string
```

这些输入描述“跑什么、用什么模型、在哪台机器、使用什么并行度和并发度”。

它的主要步骤是：

1. 清理上一轮残留资源；
2. 修复 runner 上残留的 Git 状态；
3. checkout 指定代码；
4. 计算结果文件名；
5. 执行 launcher；
6. 检查 benchmark 是否产生结果；
7. 处理结果；
8. 上传结果 artifact；
9. 上传日志、GPU 指标和评测文件；
10. 清理资源。

---

## 6. `benchmark-tmpl.yml` 的每一步

### 第一步：清理旧 Docker 资源

在 `Resource cleanup (pre-run)` step 中，如果机器有 Docker，会执行：

```bash
docker ps -aq | xargs -r docker rm -f
docker network prune -f
```

目的是清理上一个被中断的任务留下的容器和网络，避免端口、GPU 或磁盘资源冲突。

这不是 benchmark 的正式测试步骤，也不是数据库操作。

### 第二步：清理旧 Slurm 任务

如果机器上有 Slurm，会执行：

```bash
scancel --name="${{ runner.name }}" || true
```

并循环等待同名任务消失。

Slurm 是集群资源调度系统：

- `salloc`：申请资源；
- `srun`：在已申请资源上运行命令；
- `sbatch`：提交批处理任务；
- `squeue`：查看队列中的任务；
- `scancel`：取消任务。

### 第三步：修复残留 Git 状态

被中断的任务可能留下：

```text
.git/index.lock
损坏的 submodule Git 目录
```

模板会删除明确确认已经失效的 lock 和损坏的 submodule 状态，避免下一次 `actions/checkout` 失败。

### 第四步：checkout 要测试的代码

```yaml
- uses: actions/checkout@...
  with:
    token: ${{ secrets.REPO_PAT }}
    fetch-depth: 0
    ref: ${{ inputs.ref || github.sha }}
    clean: true
    submodules: true
```

说明：

- `ref`：要测试的 commit 或分支；
- `submodules: true`：同时初始化 Git submodule；
- `clean: true`：checkout 前清理工作区；
- `REPO_PAT`：仓库访问 token。

### 第五步：计算 `RESULT_FILENAME`

模板先设置一个可读的候选文件名：

```yaml
RESULT_FILENAME_BASE: ${{ env.EXP_NAME }}_${{ env.PRECISION }}_${{ env.FRAMEWORK }}_tp${{ env.TP }}-pp${{ env.PP_SIZE }}-dcp${{ env.DCP_SIZE }}-pcp${{ env.PCP_SIZE }}-ep${{ env.EP_SIZE }}-dpa${{ env.DP_ATTENTION }}_disagg-${{ env.DISAGG }}_spec-${{ env.SPEC_DECODING }}_conc${{ env.CONC }}_${{ runner.name }}
```

然后优先运行：

```bash
RESULT_FILENAME=$(python3 utils/result_filename.py)
```

如果旧 commit 没有这个辅助脚本，则使用：

```bash
RESULT_FILENAME=$(printf '%s\0' "$RESULT_FILENAME_BASE" "$RECIPE_FINGERPRINT" | sha256sum)
RESULT_FILENAME="${RESULT_FILENAME%% *}"
```

这样可以得到稳定且不容易冲突的结果文件名。

随后写入：

```bash
echo "RESULT_FILENAME=${RESULT_FILENAME}" >> "$GITHUB_ENV"
```

`GITHUB_ENV` 是 GitHub Actions 用于把环境变量传递给后续 steps 的特殊文件。

### 第六步：计算 GPU 数量

```bash
export RESULT_FILENAME
export GPU_COUNT=$((TP * PP_SIZE * PCP_SIZE))
echo "GPU_COUNT=${GPU_COUNT}" >> "$GITHUB_ENV"
```

例如：

```text
TP=8, PP=1, PCP=1
GPU_COUNT = 8 × 1 × 1 = 8
```

名词：

- `TP`：Tensor Parallel，模型张量切分到多少张 GPU；
- `PP`：Pipeline Parallel，把模型分成多少流水线阶段；
- `PCP`：Context Parallel 的一种配置维度，具体含义取决于运行框架；
- `GPU_COUNT`：本次任务申请的 GPU 数量。

### 第七步：执行 launcher

核心命令是：

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

这是从 workflow 进入实际 shell launcher 的地方。

#### `RUNNER_NAME%%_*` 是什么

这是 Bash 的字符串截断语法：从 `RUNNER_NAME` 中去掉第一个下划线及其后面的内容。

例如：

```text
RUNNER_NAME=mi355x_abc123
RUNNER_NAME%%_* = mi355x
```

最终执行：

```bash
bash ./runners/launch_mi355x.sh
```

如果 runner 名称是：

```text
mi355x-amds_abc123
```

则可能执行：

```bash
bash ./runners/launch_mi355x-amds.sh
```

实际使用哪个 launcher，取决于 GitHub runner 的 `runner.name`，不能只看 matrix 中的 `runner` 字段。

### 第八步：launcher 如何选择 benchmark 脚本

以 `runners/launch_mi355x-amds.sh` 的单节点分支为例，它会根据环境变量拼接脚本路径：

```bash
SCRIPT_BASE="${EXP_NAME%%_*}_${PRECISION}_mi355x"
SCRIPT_FW="benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${SCRIPT_BASE}_${FRAMEWORK}${SPEC_SUFFIX}.sh"
SCRIPT_FALLBACK="benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${SCRIPT_BASE}${FRAMEWORK_SUFFIX}${SPEC_SUFFIX}.sh"

if [[ -f "$SCRIPT_FW" ]]; then
    BENCHMARK_SCRIPT="$SCRIPT_FW"
else
    BENCHMARK_SCRIPT="$SCRIPT_FALLBACK"
fi
```

其中：

- `EXP_NAME`：实验名；
- `PRECISION`：精度，例如 `fp4`；
- `FRAMEWORK`：框架，例如 `sglang`；
- `SPEC_DECODING`：推测解码方式；
- `SCENARIO_SUBDIR`：场景目录，例如 `fixed_seq_len/` 或 `agentic/`；
- `SPEC_SUFFIX`：当 `SPEC_DECODING=mtp` 或 `draft_model` 时通常为 `_mtp`。

举例：

```text
EXP_NAME        = dsv4_fp4_mi355x_sglang_mtp_...
PRECISION       = fp4
FRAMEWORK       = sglang
SPEC_DECODING   = mtp
SCENARIO_SUBDIR = fixed_seq_len/
```

会得到：

```text
SCRIPT_BASE = dsv4_fp4_mi355x
SPEC_SUFFIX = _mtp
```

最终执行：

```bash
benchmarks/single_node/fixed_seq_len/dsv4_fp4_mi355x_sglang_mtp.sh
```

### 第九步：launcher 申请资源并进入容器

典型流程包括：

```bash
salloc --partition=$PARTITION --gres=gpu:$GPU_COUNT --exclusive ...
srun --jobid=$JOB_ID ... --container-image=$SQUASH_FILE ... bash "$BENCHMARK_SCRIPT"
```

含义：

1. `salloc` 向 Slurm 申请 GPU 节点；
2. `enroot import` 或类似逻辑准备容器镜像；
3. `srun` 在申请到的节点上启动容器；
4. 在容器内执行选定的 benchmark shell 脚本。

容器镜像由 matrix 的 `image` 字段决定，例如：

```text
lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260907
```

容器的作用是提供固定的 CUDA/ROCm、PyTorch、推理框架和依赖环境。

### 第十步：benchmark 脚本启动推理服务

例如 SGLang 脚本可能执行：

```bash
python3 -m sglang.launch_server \
    --model-path "$MODEL" \
    --host=0.0.0.0 \
    --port "$PORT" \
    --tensor-parallel-size "$TP" \
    --speculative-algorithm EAGLE \
    --attention-backend dsv4
```

这一步启动一个 OpenAI 兼容或类似的推理 HTTP 服务。

常见参数：

- `--model-path`：模型位置；
- `--port`：服务监听端口；
- `--tensor-parallel-size`：TP 大小；
- `--speculative-algorithm`：推测解码算法；
- `--attention-backend`：Attention 后端实现；
- `--max-running-requests`：服务端允许同时处理的请求数。

脚本通常会先执行：

```bash
wait_for_server_ready --port "$PORT" --server-log "$SERVER_LOG" --server-pid "$SERVER_PID"
```

只有确认服务已经可以接受请求后，才开始正式 benchmark。

### 第十一步：运行实际压测/benchmark

固定序列长度任务通常调用公共函数：

```bash
run_benchmark_serving \
    --model "$MODEL" \
    --port "$PORT" \
    --backend vllm \
    --input-len "$ISL" \
    --output-len "$OSL" \
    --max-concurrency "$CONC" \
    --result-filename "$RESULT_FILENAME" \
    --result-dir /workspace/
```

这一步会：

1. 产生输入请求；
2. 按指定并发度发送到推理服务；
3. 记录每个请求的延迟和 token 数；
4. 计算吞吐、TTFT、TPOT、E2EL 等指标；
5. 写出 JSON 结果。

名词解释：

| 名词 | 含义 |
|---|---|
| benchmark | 性能测试 |
| serving | 测试模型服务端，而不是只测试单个 Python 函数 |
| concurrency | 同时进行中的请求数量 |
| throughput | 单位时间完成的 token 或请求数量 |
| TTFT | Time To First Token，首 token 延迟 |
| TPOT | Time Per Output Token，生成每个输出 token 的平均时间 |
| E2EL | End To End Latency，端到端总延迟 |
| ISL | 输入 token 长度 |
| OSL | 输出 token 长度 |
| MTP/EAGLE | 推测解码方法，通过草稿 token 尝试提高生成速度 |

Agentic 场景会调用 AgentX trace replay 相关函数，例如：

```bash
build_replay_cmd "$RESULT_DIR"
run_agentic_replay_and_write_outputs "$RESULT_DIR"
```

它不是简单发送一批独立随机请求，而是按照 AgentX/Weka trace 的多轮对话和子代理结构回放请求。

### 第十二步：检查结果文件

普通 benchmark 结束后，模板检查：

```bash
if [ -f "$RESULT_FILENAME.json" ]; then
    FOUND_RESULT_FILE=true
fi
```

如果找不到结果文件，job 失败：

```text
Run failed: Benchmark result ...json not found.
```

Agentic 和多节点任务会检查更多结果文件，并验证是否至少有成功请求：

```bash
num_requests_successful
```

注意：进程没有报错不代表 benchmark 一定有效，所以 workflow 还要检查结果文件和成功请求数量。

### 第十三步：处理原始结果

普通 fixed-sequence 任务会执行：

```bash
python3 utils/process_result.py
```

多节点新版本可能执行：

```bash
python3 utils/process_result.py --all
```

处理程序负责把原始 benchmark JSON 变成规范化的聚合 JSON，例如：

```text
agg_<result-filename>.json
```

结果处理可能包括：

- 汇总请求统计；
- 计算吞吐和延迟分位数；
- 加入模型、硬件、框架和并行度元数据；
- 校验失败请求比例；
- 校验 GPU power 数据。

### 第十四步：上传 benchmark artifact

```yaml
- name: Upload result
  uses: actions/upload-artifact@...
  with:
    name: bmk_${{ env.RESULT_FILENAME }}
    path: agg_${{ env.RESULT_FILENAME }}.json
```

这一步把当前 job 的结果上传到 GitHub Actions artifact 存储，供 `collect-results.yml` 下载。

它不是把结果写入 InferenceX 数据库。

### 第十五步：上传日志和 GPU 指标

模板还会上传：

```text
server.log
results/*.log
gpu_metrics.csv
gpu_metrics_identity.json
power_validation_*.json
```

这些文件用于：

- 排查服务启动失败；
- 分析 GPU 利用率和功耗；
- 验证 power 数据是否可信；
- 调查 benchmark 异常。

### 第十六步：运行和上传 eval

当满足以下条件之一时上传评测结果：

```yaml
if: ${{ always() && (env.RUN_EVAL == 'true' || inputs.eval-only) }}
```

评测文件可能包括：

```text
meta_env.json
results*.json
*_report.json
*_results.jsonl
predictions.jsonl
```

评测分数验证：

```bash
python3 utils/evals/validate_scores.py
```

评测和吞吐 benchmark 是两种不同类型的任务：

- 吞吐 benchmark 关注速度、延迟和并发能力；
- eval 关注模型回答是否正确。

### 第十七步：清理资源

最后执行资源清理：

```bash
scancel ...
docker rm -f ...
rm -f ...
```

目的是释放 GPU、Slurm job、临时容器和评测输出，避免污染下一次任务。

---

## 7. 两个文件的职责对比

| 事项 | `run-sweep.yml` | `benchmark-tmpl.yml` |
|---|---|---|
| 角色 | 总控/调度器 | 单任务执行模板 |
| 决定测试哪些配置 | 是 | 否，配置由调用方传入 |
| 读取 changelog | 是 | 否 |
| 生成 matrix | 是，通过 `infx.matrix.plan` | 否 |
| 计算 CI 优先级 | 是，通过 `infx.workflows.ci_priority` | 使用已经传入的 priority |
| 启动模型服务 | 间接调用模板 | 间接调用 launcher 后启动 |
| 运行具体 benchmark | 间接 | 是，通过 launcher 和 benchmark 脚本 |
| 使用 Matrix | 创建/拆分 matrix | 执行一个 matrix 展开的配置 |
| 处理单个结果 | 不负责主要处理 | 是，调用 `process_result.py` |
| 汇总全部结果 | 是，通过 collect workflow | 否 |
| 上传单任务 artifact | 间接 | 是 |
| PR 基线对比 | 是 | 否 |
| 直接写数据库 | 否 | 否 |
| 触发下游入库 | main push 时是 | 否 |
| 申请 GPU/Slurm 资源 | 间接 | 通过 launcher 实际完成 |

---

## 8. 一个配置从 YAML 到 shell 脚本的完整例子

以配置：

```yaml
# configs/amd-master.yaml
 dsv4-fp4-mi355x-sglang-mtp:
  model: deepseek-ai/DeepSeek-V4-Pro
  model-prefix: dsv4
  runner: mi355x
  precision: fp4
  framework: sglang
  scenarios:
    fixed-seq-len:
      - isl: 8192
        osl: 1024
        search-space:
          - tp: 8
            spec-decoding: mtp
```

为例，执行链如下：

### 8.1 changelog 选择配置

`perf-changelog.yaml` 的新增条目选择：

```text
dsv4-fp4-mi355x-sglang-mtp
```

### 8.2 `infx.matrix.plan` 展开配置

生成：

```json
{
  "single_node": {
    "8k1k": [
      {
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "model-prefix": "dsv4",
        "runner": "mi355x",
        "precision": "fp4",
        "framework": "sglang",
        "spec-decoding": "mtp",
        "isl": 8192,
        "osl": 1024,
        "tp": 8
      }
    ]
  }
}
```

### 8.3 `ci_priority` 添加调度字段

结果变成：

```json
{
  "model-prefix": "dsv4",
  "runner": "mi355x",
  "precision": "fp4",
  "framework": "sglang",
  "spec-decoding": "mtp",
  "priority": "...",
  "queue-token": "..."
}
```

### 8.4 `run-sweep.yml` 调用模板

```yaml
uses: ./.github/workflows/benchmark-tmpl.yml
with:
  runner: mi355x
  model-prefix: dsv4
  precision: fp4
  framework: sglang
  spec-decoding: mtp
```

### 8.5 模板调用 launcher

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

根据实际 runner 名称选择对应 launcher。

### 8.6 launcher 选择 benchmark 脚本

环境变量组合出：

```text
benchmarks/single_node/fixed_seq_len/dsv4_fp4_mi355x_sglang_mtp.sh
```

### 8.7 benchmark 脚本启动 SGLang

```bash
python3 -m sglang.launch_server ...
```

### 8.8 benchmark 客户端发请求

```bash
run_benchmark_serving ...
```

### 8.9 结果返回 workflow

```text
原始 JSON
    ↓
utils/process_result.py
    ↓
agg_<name>.json
    ↓
actions/upload-artifact
    ↓
collect-results.yml
    ↓
results_bmk artifact
```

### 8.10 main push 时进入下游系统

```text
results_bmk + eval_results_all + run-stats + changelog-metadata
    ↓ GitHub Repository Dispatch
InferenceX-app
    ↓
下游 ETL 和数据库持久化
```

---

## 9. 数据库边界：到底哪里写数据库

在这两个 workflow 及其直接调用代码中，应区分三种行为。

### 9.1 只读数据库

`run-sweep.yml` 的 `compare-results` 使用：

```yaml
DATABASE_URL: ${{ secrets.NEON_PROD_RO_URL }}
```

并由 `infx.results.compare_results` 执行 `SELECT` 查询。

作用是读取 main 基线，不写入。

### 9.2 Artifact 文件写入

以下操作是写文件或上传 artifact，不等于写业务数据库：

```text
Path(...).write_text(...)
open(..., 'w')
json.dump(...)
actions/upload-artifact
```

它们把结果存到工作区或 GitHub artifact 存储。

### 9.3 间接触发数据库写入

`run-sweep.yml` 的 `trigger-ingest` 和 `trigger-agentic-ingest` 执行 GitHub API：

```text
POST /repos/SemiAnalysisAI/InferenceX-app/dispatches
```

这是一个跨仓库事件通知。实际数据库写入发生在 `InferenceX-app` 的 ingest workflow 和 ETL 代码中，而不是当前 checkout 中的 `run-sweep.yml` 或 `benchmark-tmpl.yml`。

可以简化为：

```text
本仓库 workflow 产生并上传数据
        ↓
本仓库向 InferenceX-app 发通知
        ↓
InferenceX-app 下载 artifact
        ↓
InferenceX-app 负责数据库写入
```

---

## 10. 最常见的误解

### 误解一：`benchmark-tmpl.yml` 是一个具体 benchmark 脚本

不是。它是 workflow 模板，真正的模型服务和压测命令通常在：

```text
runners/launch_*.sh
benchmarks/single_node/**/*.sh
benchmarks/multi_node/**/*.sh
```

### 误解二：`run-sweep.yml` 直接运行所有配置

不是。它先生成 matrix，再通过 GitHub Actions 并行展开任务。每个具体任务调用一次模板。

### 误解三：`upload-artifact` 就是数据库写入

不是。artifact 是文件存储/运输机制。数据库写入是后续 `InferenceX-app` ingest 流程的职责。

### 误解四：`compare-results` 会保存 PR 结果

不是。它读取 main 基线，生成 PR 页面上的 Markdown 对比摘要。PR benchmark 结果主要保留在 artifact 中。

### 误解五：配置中的 `runner: mi355x` 一定对应 `launch_mi355x-amds.sh`

不一定。launcher 入口使用的是实际 GitHub runner 名称：

```bash
runners/launch_${RUNNER_NAME%%_*}.sh
```

`mi355x`、`mi355x-amds` 和 `cluster:mi355x-amds` 可能对应不同的调度和 launcher 路径，必须结合 workflow 运行时的 `runner.name` 和 launcher 代码确认。

---

## 11. 排查一次任务时应该看什么

如果某个配置没有运行，按以下顺序排查：

1. `perf-changelog.yaml` 是否真的选择了配置 key；
2. `check-changelog` 是否通过；
3. PR 是否带有正确的 sweep 标签；
4. 是否被 `[skip-sweep]` 跳过；
5. `setup` 的 `search-space-config` 是否包含目标配置；
6. 配置位于哪个 bucket：`single_node`、`multi_node`、`evals` 还是 Agentic bucket；
7. 对应的 `sweep-*` job 是否因 `if:` 条件被跳过；
8. `matrix.config.runner` 与实际 self-hosted runner 是否匹配；
9. `benchmark-tmpl.yml` 是否成功 checkout；
10. launcher 是否正确拼出 `benchmarks/.../*.sh`；
11. benchmark 脚本是否成功启动模型服务；
12. 是否通过 `wait_for_server_ready`；
13. 是否生成预期的 JSON；
14. `process_result.py` 是否成功；
15. artifact 名称是否符合 `collect-results.yml` 的 pattern；
16. main push 场景下是否成功触发 `InferenceX-app` 的 ingest workflow。

最重要的日志位置通常包括：

```text
GitHub Actions job log
server.log
router.log
GPU metrics artifact
power audit artifact
benchmark result JSON
result_processing_*.json
```

---

## 12. 最简短总结

```text
run-sweep.yml
  = 总控：验证 changelog，生成 matrix，拆分任务，收集结果，触发下游入库

benchmark-tmpl.yml
  = 执行器：接收一个 matrix 配置，清理机器，调用 launcher，运行 benchmark，上传结果

runners/launch_*.sh
  = 机器/集群适配层：申请 Slurm/GPU、准备容器、选择 benchmark shell 脚本

benchmarks/single_node/**/*.sh
benchmarks/multi_node/**/*.sh
  = 真正启动推理服务和压测客户端的脚本

collect-results.yml
collect-evals.yml
  = 下载并聚合 artifact

InferenceX-app
  = 接收 main push 的 ingest 事件，实际执行数据库 ETL 和持久化
```
