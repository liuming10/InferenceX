# `run-sweep.yml` 原文与逐 Job / Step 中文注释

> 源文件：[`.github/workflows/run-sweep.yml`](.github/workflows/run-sweep.yml)。本文按当前本地工作树解读；GitHub Actions 实际执行时使用目标 commit 中的版本。
>
> **定位**：这是 InferenceX 面向 `main` 的自动性能扫描编排入口。它负责校验、授权、生成矩阵、调用模板、收集 artifact、PR 结果展示和 main 合并后的入库通知。它本身不直接启动推理服务或 AIPerf。
>
> **安全边界**：该 workflow 下游会调度专用 self-hosted runner、Slurm、容器和 GPU；不能把它的 launcher 或清理命令直接复制到共享服务器、已有推理服务的宿主机或个人环境运行。

## 1. 先看完整调用图

```text
PR / main push（perf-changelog.yaml 改动）
  └─ run-sweep.yml
     ├─ check-changelog
     │  └─ python -m infx.workflows.validate_perf_changelog
     ├─ reuse-sweep-gate
     │  └─ python -m infx.workflows.reuse
     ├─ setup
     │  ├─ python -m infx.matrix.plan
     │  │  └─ 进程内调用 infx.matrix.generate 的生成函数
     │  ├─ python -m infx.workflows.ci_priority
     │  └─ python -m infx.workflows.reuse
     ├─ canary-select / canary-sweep（仅固定长度 canary）
     ├─ benchmark-tmpl.yml（单节点、AgentX、单节点 Eval）
     │  └─ runners/launch_<runner>.sh → benchmark recipe → 推理服务/AIPerf 或 Eval
     ├─ benchmark-multinode-tmpl.yml（多节点、AgentX、多节点 Eval）
     ├─ collect-results.yml
     ├─ collect-evals.yml
     ├─ compare_results / calc_success_rate
     ├─ main：repository dispatch 到 InferenceX-app
     └─ PR：评论 unofficialRun 可视化链接
```

## 2. GitHub Actions 语法最小词汇

| 写法 | 含义 |
|---|---|
| `jobs.<name>` | 一个可独立调度的 Job。 |
| `steps` | Job 内按顺序执行的动作。 |
| `needs` | 当前 Job 等待的前置 Job。前置失败或 skipped 是否阻止后续，还取决于 `if`。 |
| `if` | 条件为 false 时 Job/Step 显示 `skipped`，不表示程序运行失败。 |
| `uses: ./.github/workflows/x.yml` | 调用仓库内 reusable workflow，不是执行本地 Shell 文件。 |
| `with` | 向 reusable workflow 输入参数。 |
| `secrets: inherit` | 将调用者可用 secret 传给 reusable workflow；不是把 secret 输出到日志。 |
| `strategy.matrix` | 每个数组元素派发一个独立 Job 实例。 |
| `${{ ... }}` | GitHub expression。`github` 是事件上下文，`needs` 是前置输出，`matrix` 是本次矩阵行。 |
| `always()` | 即使依赖 Job 失败也允许继续计算 `if` 的其余条件。 |
| `fromJson()` / `toJson()` | 在 GitHub expression 中把 JSON 字符串和对象相互转换。 |

## 3. 工作流头部：名称、并发取消和触发器

### 3.1 原文：Run 名称与并发组

```yaml
name: "Run Sweep"
run-name: Run Sweep - ${{ github.event.pull_request.title || github.event.head_commit.message }}

concurrency:
  group: >-
    sweep-${{ github.event.pull_request.number || github.sha }}-${{ ... github.run_id || 'active' }}
  cancel-in-progress: true
```

- `name` 是 GitHub Actions 页面显示的 workflow 名称。
- `run-name`：PR 使用标题，push 使用提交消息。
- `group`：相同 PR 或同一 SHA 的普通新 Run 被分到同一并发组。
- `cancel-in-progress: true`：较新的 Run 可取消较旧 Run，避免旧提交继续占用稀缺 GPU。
- 代码对会改变扫描语义的标签（如 `sweep-enabled`、`agentx-fast`、`all-evals`）单独处理；这些标签事件使用 `github.run_id`，避免误取消有意义的不同执行模式。

### 3.2 原文：事件过滤

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

| 字段 | 对初学者的含义 |
|---|---|
| `push` | 合并或直接推送到 `main` 时触发。 |
| `pull_request` | PR 更新、添加标签、移除标签时触发。 |
| `branches: main` | PR 目标分支必须是 `main`；push 必须发生在 `main`。 |
| `paths` | 该次差异必须涉及 `perf-changelog.yaml`。普通代码变更不会仅因本 workflow 而创建 Run。 |
| `synchronize` | PR 有新 commit。 |
| `labeled` / `unlabeled` | PR 标签变化；标签是 GPU 扫描授权和模式输入。 |

> Run 被创建不等于 GPU 已运行。下文 `setup` 的条件才决定是否派发 benchmark 模板。

## 4. Job：`check-changelog`

### 原文：Job gate 与输出

```yaml
check-changelog:
  runs-on: ubuntu-latest
  permissions:
    contents: read
  if: >-
    github.event_name == 'pull_request' &&
    github.event.pull_request.head.repo.full_name == github.repository &&
    (...被认可的标签事件或非标签事件...)
  outputs:
    skip-pr-sweep: ${{ steps.sweep_policy.outputs.skip-pr-sweep }}
```

- 在 GitHub 托管 Linux runner 上执行，不占 GPU。
- 只接受同仓库 PR，避免 Fork 的未受信任代码自动获得受保护的 CI 行为。
- 对 `labeled` / `unlabeled` 事件，只有本 workflow 理解的标签才继续；无关标签不会触发后续检查。
- 输出 `skip-pr-sweep` 由后面的 `Read PR sweep policy` Step 写入，供 `setup` 判断。

### Step 1 原文：拒绝冲突扫描标签

```yaml
- name: Reject conflicting sweep labels
  env:
    SWEEP_LABELS: ${{ toJson(github.event.pull_request.labels.*.name) }}
  run: |
    count=$(jq '[.[] | select(. == "sweep-enabled" or ...)] | length' <<<"$SWEEP_LABELS")
    if [ "$count" -gt 1 ]; then
      echo "::error::PR has multiple conflicting sweep labels. Pick exactly one."
      exit 1
    fi
```

把 PR 标签名单转换为 JSON，使用 `jq` 统计五种主扫描标签：`sweep-enabled`、`full-sweep-enabled`、`non-canary-full-sweep-enabled`、`full-sweep-fail-fast`、`full-sweep-fail-fast-no-canary`。多于一个即失败，避免“裁剪并发”和“完整扫描”等相互冲突的语义同时出现。

### Step 2 原文：Checkout

```yaml
- name: Checkout code
  uses: actions/checkout@... # v7.0.1
  with:
    fetch-depth: 0
    persist-credentials: false
```

- `fetch-depth: 0` 获取完整 Git 历史，因为后面的校验需要比较 base/head。
- `persist-credentials: false` 不把 checkout 凭据长期写入本地 Git 配置。

### Step 3 原文：读取 `[skip-sweep]`

```yaml
- name: Read PR sweep policy
  id: sweep_policy
  env:
    HEAD_SHA: ${{ github.event.pull_request.head.sha }}
  run: |
    if git log -1 --format=%B "$HEAD_SHA" | grep -Fq '[skip-sweep]'; then
      echo "skip-pr-sweep=true" >> "$GITHUB_OUTPUT"
    else
      echo "skip-pr-sweep=false" >> "$GITHUB_OUTPUT"
    fi
```

- `id` 让后续通过 `steps.sweep_policy.outputs.skip-pr-sweep` 读取输出。
- `$GITHUB_OUTPUT` 是 GitHub Actions 的 Step 输出文件。
- 最新 PR commit message 含 `[skip-sweep]` 时，请求跳过该 PR 的扫描；这不是模型测试失败。

### Step 4 原文：安装 uv

```yaml
- name: Set up uv
  uses: astral-sh/setup-uv@... # v10.0.1
```

`uv` 是 Python 环境和临时依赖运行工具。后续通过 `uv run --with ...` 以 Python 3.12 运行仓库模块，而不要求系统预先安装项目依赖。

### Step 5 原文：校验 changelog 与矩阵

```yaml
- name: Validate perf-changelog matrix
  env:
    ALL_EVALS: ${{ contains(..., 'all-evals') }}
    EVALS_ONLY: ${{ contains(..., 'evals-only') }}
  run: |
    uv run ... --with "pydantic>=2" --with pyyaml \
      python -m infx.workflows.validate_perf_changelog \
      --changelog-file perf-changelog.yaml \
      --base-ref "origin/${{ github.base_ref }}" \
      --head-ref "${{ github.event.pull_request.head.sha }}"
```

`infx.workflows.validate_perf_changelog`：

1. 校验 YAML 字节和格式约束；
2. 校验 `perf-changelog.yaml` schema；
3. 比较 base/head；
4. 结合 `--all-evals` 或 `--evals-only` 检查计划是否合法。

通过只表示配置清单和生成规则有效，不表示镜像、GPU、模型或 AIPerf 已经成功。

## 5. Job：`reuse-sweep-gate`

### 原文

```yaml
reuse-sweep-gate:
  needs: check-changelog
  runs-on: ubuntu-latest
  permissions:
    actions: read
    contents: read
    issues: read
    pull-requests: read
  if: >-
    always() && needs.check-changelog.result == 'success' &&
    github.event_name == 'pull_request' &&
    github.event.action == 'synchronize' &&
    !contains(..., 'evals-only') && !contains(..., 'agentx-fast')
```

只有 changelog 校验成功、PR 更新事件、且不是 `evals-only` 或 `agentx-fast` 模式时才考虑复用。读取 actions/PR 权限是为了查询已有 Run 和授权信息。

### Step 1：Checkout

```yaml
- name: Checkout code
  uses: actions/checkout@...
```

取出当前 commit 中的 `infx.workflows.reuse` 源码。

### Step 2：检查可复用授权

```yaml
- name: Check for reusable sweep authorization
  id: gate
  env:
    GH_TOKEN: ${{ github.token }}
  run: |
    python3 -m infx.workflows.reuse \
      --repo "${{ github.repository }}" \
      --commit-sha "${{ github.event.pull_request.head.sha }}" \
      --event-name "${{ github.event_name }}" \
      --event-action "${{ github.event.action }}" \
      --pr-number "${{ github.event.pull_request.number }}" \
      --ref "${{ github.ref }}" \
      --workflow-id "run-sweep.yml"
```

该模块检查是否有可授权复用的扫描，而非运行 Benchmark。`GH_TOKEN` 仅在 Job 内供 GitHub API 查询使用。若输出 `skip-pr-sweep=true`，后面的 `setup` 会停止新 GPU 扫描。

## 6. Job：`setup`——唯一的矩阵规划中心

### 原文：关键 gate

```yaml
setup:
  needs: [check-changelog, reuse-sweep-gate]
  runs-on: ubuntu-latest
  if: >-
    always() &&
    (check-changelog success 或 skipped) &&
    (reuse gate skipped 或未要求跳过) &&
    (同仓库、有效 changelog、未 skip、拥有一个主 sweep 标签的 PR
     或 main push)
```

这是“workflow 创建”到“允许派发 GPU Job”的关键门。PR 必须有一个主扫描标签；main push 不依赖 PR 标签。它输出：

- `search-space-config`：按固定长度/AgentX/单节点/多节点/Eval 分桶的 JSON；
- `reuse-enabled` 与来源 Run 的 ID、attempt、URL、PR、SHA：后续可避免重复运行并让合并后入库引用正确来源。

### Step 1：Checkout

```yaml
- name: Checkout code
  uses: actions/checkout@...
  with: { fetch-depth: 0 }
```

完整历史用于比较 base/head 中的 changelog 和配置。

### Step 2：安装 uv

```yaml
- uses: astral-sh/setup-uv@...
```

为 planner、优先级模块准备临时 Python 运行环境。

### Step 3：可选的优先级分类

```yaml
- name: Classify priority criteria
  id: classify
  if: vars.PRIORITY_SCHEDULER_ENABLED == 'true' && github.event_name == 'pull_request'
  continue-on-error: true
  uses: anthropics/claude-code-action@...
```

仅在仓库变量启用时运行。它被限制为只读工具（`Read, Glob, Grep, Bash(git diff:*)`），根据 PR diff 输出 `multi-node`、`agentic`、`fp4`、`mtp`、框架、模型族、patchwork 等优先级条件。`continue-on-error: true` 表示分类器故障不应让性能扫描整体中断。

### Step 4：标准化分类结果

```yaml
- name: Normalize priority classification
  id: priority-criteria
  if: vars.PRIORITY_SCHEDULER_ENABLED == 'true' && always()
  run: |
    if jq -e 'type == "object" and (.criteria | type == "array")' ...; then
      criteria=$(jq -c '.criteria' <<<"$OUTPUT")
    else
      criteria='["patchwork"]'
    fi
    echo "criteria=$criteria" >> "$GITHUB_OUTPUT"
```

验证分类器结构化输出。无效结果退化为 `patchwork`，使优先级策略保守而不是默默丢失风险标记。

### Step 5：生成并标记矩阵

```yaml
- id: setup
  env:
    TRIM_CONC: ${{ contains(..., 'sweep-enabled') }}
    ALL_EVALS: ${{ contains(..., 'all-evals') }}
    EVALS_ONLY: ${{ contains(..., 'evals-only') }}
  run: |
    # PR: BASE_REF=origin/base branch, HEAD_REF=PR head SHA
    # push: BASE_REF=before SHA, HEAD_REF=after SHA
    uv run ... python -m infx.matrix.plan \
      --changelog-file "$GITHUB_WORKSPACE/perf-changelog.yaml" \
      --base-ref "$BASE_REF" --head-ref "$HEAD_REF"
    # 可选追加 --trim-conc / --all-evals / --evals-only
    CONFIG_JSON=$(...)
    CONFIG_JSON=$(printf '%s' "$CONFIG_JSON" | ... python -m infx.workflows.ci_priority ...)
    echo "search-space-config=$CONFIG_JSON" >> "$GITHUB_OUTPUT"
    python3 -m infx.workflows.reuse ...
```

这一步按顺序做了四件事：

1. **确定比较边界**：PR 比 `origin/main` 与 PR head；main push 比 before/after。
2. **运行 `infx.matrix.plan`**：读取 changelog 差异，加载 master config 与 runner config，选择 config key/场景，调用生成器逻辑并输出 JSON 桶。
3. **运行 `infx.workflows.ci_priority`**：给每个矩阵点补充 priority、queue token、可选 skip-queue 信息。
4. **写 Job 输出并再次执行 reuse 模块**：后续 matrix Job 与入库 Job 从 `needs.setup.outputs` 消费这些信息。

`infx.matrix.plan` 不申请 GPU、不开服务、不下载模型；它只是生成 JSON。

## 7. Job：`canary-select`

### 原文

```yaml
canary-select:
  needs: setup
  if: >-
    needs.setup.outputs.reuse-enabled != 'true' &&
    github.event_name == 'pull_request' &&
    (contains(..., 'full-sweep-enabled') || contains(..., 'full-sweep-fail-fast'))
  runs-on: ubuntu-latest
```

只有完整 sweep 的两种 canary 模式才选 canary。`non-canary-full-sweep-enabled` 明确不走这里。

### 唯一 Step：选一个固定长度低并发候选

```yaml
- id: pick
  env: { SEARCH_SPACE: ${{ needs.setup.outputs.search-space-config }} }
  run: |
    (((.single_node["1k1k"] // []) + (.single_node["8k1k"] // []))
      | map(select(.["run-eval"] != true))) as $candidates
    | (if ... then null else ($candidates | min_by(.conc)) end) as $canary
```

- 从单节点 `1k1k` 与 `8k1k` 固定长度桶拼出候选；排除以 Eval 为主的行；取最小 `conc`。
- 用 `remove_one` 从后续固定长度桶中移除该点，避免它既跑 canary 又跑正式 sweep。
- 输出 `canary-config` 与 `remaining-search-space-config`。
- **AgentX (`single_node['agentic']`) 不在候选集合内。**canary 成功不代表 AgentX trace replay 已成功。

## 8. Job：`canary-sweep`

### 原文

```yaml
canary-sweep:
  needs: canary-select
  if: ${{ needs.canary-select.outputs.canary-config != '' && ... != '[]' }}
  uses: ./.github/workflows/benchmark-tmpl.yml
  strategy:
    fail-fast: false
    matrix:
      config: ${{ fromJson(needs.canary-select.outputs.canary-config) }}
  secrets: inherit
```

- 调用单节点 reusable workflow，而不是直接在入口文件执行 Shell。
- `matrix.config` 是刚刚挑出的一个固定长度点。
- `fail-fast: false`：canary matrix 不因一个实例失败而提前取消其它实例。
- `secrets: inherit`：让模板可读取其所需的仓库 secret；不会在此处展示 secret 值。

### 原文：`with` 的关键含义

```yaml
with:
  runner: ${{ matrix.config.runner }}
  priority: ${{ matrix.config.priority }}
  queue-token: ${{ matrix.config['queue-token'] }}
  image: ${{ matrix.config.image }}
  model: ${{ matrix.config.model }}
  tp: ${{ matrix.config.tp }}
  ep: ${{ matrix.config.ep }}
  conc: ${{ matrix.config.conc }}
  run-eval: false
```

这些字段把 planner 的矩阵行传给 [`benchmark-tmpl.yml`](benchmark-tmpl-yml-annotated-zh.md)。完整字段分组解释见该文档：模型/镜像、并行拓扑、队列、路由、KV、Eval。canary 明确 `run-eval: false`，因为它是性能 smoke test。

## 9. 固定长度性能 Job

以下 Job 都依赖 `[setup, canary-select, canary-sweep]`，且共同要求：未被取消、`setup` 成功、未复用、canary 成功或 skipped、对应 JSON 桶存在。

| Job | 可复用 YAML | 矩阵桶 | 作用 |
|---|---|---|---|
| `sweep-multi-node-1k1k` | `benchmark-multinode-tmpl.yml` | `multi_node['1k1k']` | 多节点固定长度 1K 输入 / 1K 输出。 |
| `sweep-multi-node-8k1k` | 同上 | `multi_node['8k1k']` | 多节点固定长度 8K 输入 / 1K 输出。 |
| `sweep-single-node-1k1k` | `benchmark-tmpl.yml` | `single_node['1k1k']` | 单节点固定长度 1K / 1K。canary 成功时使用移除 canary 后的 remaining matrix。 |
| `sweep-single-node-8k1k` | 同上 | `single_node['8k1k']` | 单节点固定长度 8K / 1K。也会去除已跑的 canary。 |

### 原文：共同 fail-fast 语义

```yaml
strategy:
  fail-fast: ${{
    contains(github.event.pull_request.labels.*.name, 'full-sweep-fail-fast') ||
    contains(github.event.pull_request.labels.*.name, 'full-sweep-fail-fast-no-canary')
  }}
```

只有这两种标签才会在同一 matrix 中一个实例失败时取消未完成实例；普通 full sweep 不这样做。

### 原文：多节点 `with` 关键字段

```yaml
with:
  node-count: ${{ matrix.config.node-count }}
  conc-list: ${{ toJson(matrix.config.conc) }}
  prefill-num-worker: ${{ matrix.config.prefill.num-worker }}
  prefill-tp: ${{ matrix.config.prefill.tp }}
  prefill-ep: ${{ matrix.config.prefill.ep }}
  decode-num-worker: ${{ matrix.config.decode.num-worker }}
  decode-tp: ${{ matrix.config.decode.tp }}
  decode-ep: ${{ matrix.config.decode.ep }}
```

多节点行把 Prefill 与 Decode 拓扑独立传入。`conc-list` 是 JSON 数组，模板将其转成 `CONC_LIST` 环境变量；单节点则为每个 `conc` 创建一个 matrix Job。细节见 [`benchmark-multinode-tmpl-yml-annotated-zh.md`](benchmark-multinode-tmpl-yml-annotated-zh.md)。

## 10. Job：`sweep-agentic`（单节点 AgentX）

### 原文

```yaml
sweep-agentic:
  needs: [setup, canary-select, canary-sweep]
  if: >-
    !cancelled() && needs.setup.result == 'success' &&
    needs.setup.outputs.reuse-enabled != 'true' &&
    (needs.canary-sweep.result == 'success' || needs.canary-sweep.result == 'skipped') &&
    toJson(fromJson(needs.setup.outputs.search-space-config).single_node['agentic']) != 'null'
  uses: ./.github/workflows/benchmark-tmpl.yml
  name: agentic /
```

对应 planner 的 `single_node['agentic']` 桶。与固定长度不同，canary 没有从该桶删除任何条目；这里的 canary 依赖只用于确保完整 sweep 的固定长度先行门控没有失败。

### 原文：AgentX 特有输入

```yaml
with:
  kv-offloading: ${{ matrix.config.kv-offloading }}
  kv-offload-backend: ${{ matrix.config['kv-offload-backend'].name }}
  total-cpu-dram-gb: ${{ matrix.config.total-cpu-dram-gb }}
  duration: ${{ matrix.config.duration }}
  isl: '0'
  osl: '0'
  max-model-len: '0'
  scenario-type: agentic-coding
  agentx-fast: ${{ contains(github.event.pull_request.labels.*.name, 'agentx-fast') }}
```

- `isl/osl/max-model-len='0'` 表示不用固定序列长度 workload；不是发送空请求。
- `scenario-type: agentic-coding` 使模板导出 `SCENARIO_SUBDIR=agentic/` 与 `IS_AGENTIC=1`。
- `kv-*`、CPU DRAM 与 duration 来自 agentic recipe。
- `agentx-fast` 传到模板后变为 `AIPERF_EXPERIMENTAL_FAST=1`；AgentX recipe 的 replay 命令会将 profile 缩短为 1200 秒、每 lane 额外 warmup 降为 1。
- 模板下游会执行 AgentX artifact 验证，而非固定长度 `process_result.py` 路径。

## 11. Job：`sweep-multi-node-agentic`

### 原文要点

```yaml
sweep-multi-node-agentic:
  uses: ./.github/workflows/benchmark-multinode-tmpl.yml
  matrix:
    config: ${{ fromJson(needs.setup.outputs.search-space-config).multi_node['agentic'] }}
  with:
    isl: '0'
    osl: '0'
    max-model-len: '0'
    conc-list: ${{ toJson(matrix.config.conc) }}
    conc: ${{ matrix.config.conc[0] }}
    scenario-type: agentic-coding
```

它是多节点 AgentX 路径。`conc-list` 保留该拓扑的全部并发点；`conc` 取第一个并发作为 AgentX 相关单值环境变量。模板会检查每个并发均产出成功的 AgentX result JSON。详见多节点模板文档。

## 12. Eval Job（四个）

| Job | 模板 | 矩阵桶 | 为什么单独存在 |
|---|---|---|---|
| `sweep-evals` | 单节点模板 | `evals` | 固定长度的 Eval-only 行。 |
| `sweep-agentic-evals` | 单节点模板 | `agentic_evals` | Agentic Eval 行没有固定 `isl/osl/max-model-len`，因此使用 AgentX 输入形状。 |
| `sweep-multi-node-evals` | 多节点模板 | `multinode_evals` | 多节点固定长度 Eval。 |
| `sweep-multi-node-agentic-evals` | 多节点模板 | `multinode_agentic_evals` | 多节点 AgentX Eval；使用一条 `eval-conc`。 |

### 固定长度 Eval 原文

```yaml
with:
  run-eval: true
  eval-only: true
  eval-framework: ${{ matrix.config['eval-framework'] || 'lm-eval' }}
  eval-suite: ${{ matrix.config['eval-suite'] || '' }}
```

`eval-only` 告诉模板跳过吞吐 result 文件处理，要求产生 `results*.json` 等 Eval 结果。`run-eval` 是服务启动后执行 Eval 的开关。

### AgentX Eval 原文

```yaml
# agentic 行不含固定 seq-len 字段
isl: '0'
osl: '0'
max-model-len: '0'
scenario-type: agentic-coding
run-eval: true
eval-only: true
```

注释明确解释：Agentic Eval 的输入形状不同，不能照抄固定长度 Eval 的字段。

### 多节点 Eval 并发原文

```yaml
eval-conc: ${{
  (matrix.config['eval-framework'] || 'lm-eval') == 'lm-eval' &&
  matrix.config['eval-all-concs'] && join(matrix.config.conc, ' ') ||
  matrix.config['eval-conc']
}}
```

- `lm-eval` 且 `eval-all-concs` 时，传入所有并发；
- 否则传 planner 已选的单个 `eval-conc`；
- 多节点 AgentX Eval 明确用最高的单个 `eval-conc`，不会像固定长度 all-evals 那样传完整列表。

## 13. Job：`collect-results`

### 原文

```yaml
collect-results:
  needs: [canary-sweep, sweep-single-node-1k1k, sweep-single-node-8k1k,
          sweep-agentic, sweep-multi-node-1k1k, sweep-multi-node-8k1k,
          sweep-multi-node-agentic, setup]
  if: >-
    always() && needs.setup.result == 'success' &&
    (canary 成功 或 任一固定长度 sweep 未 skipped)
  uses: ./.github/workflows/collect-results.yml
  with: { result-prefix: "bmk" }
```

调用 [`collect-results.yml`](collect-results-yml-annotated-zh.md)：下载 `bmk_*` artifact，运行 `infx.results.collect_results`，上传 `results_bmk`。

**当前条件的细节**：它检查 canary 和固定长度 Job 是否执行，却没有以 `sweep-agentic` 单独作为触发条件。因此纯 AgentX sweep 可能跳过传统 `collect-results` 路径；AgentX 仍有独立的 main 入库 Job `trigger-agentic-ingest`。

## 14. Job：`collect-evals`

### 原文

```yaml
collect-evals:
  needs: [sweep-evals, sweep-agentic-evals, sweep-multi-node-evals,
          sweep-multi-node-agentic-evals, setup]
  if: ${{ always() && needs.setup.result != 'skipped' &&
           (任一 Eval Job 未 skipped) }}
  uses: ./.github/workflows/collect-evals.yml
  secrets: inherit
```

调用 [`collect-evals.yml`](collect-evals-yml-annotated-zh.md)，下载 `eval_*` artifact，汇总并上传 `eval_results_all`。即使某个 Eval 失败，`always()` 仍允许收集已有 artifact，便于诊断。

## 15. Job：`upload-changelog-metadata`

### 原文：依赖与条件

```yaml
upload-changelog-metadata:
  needs: [setup, collect-results]
  if: ${{ always() && needs.setup.result == 'success' }}
  runs-on: ubuntu-latest
```

只要 planner 成功，即使传统收集 skipped，也会生成审计元数据。

### Step 1：提取元数据与 manifest

```yaml
- name: Extract and save changelog metadata
  run: |
    python3 - <<'PY'
    matrix = json.loads(os.environ['SWEEP_MATRIX'])
    Path('changelog_metadata.json').write_text(json.dumps(matrix['changelog_metadata']))
    Path('sweep_manifest.json').write_text(json.dumps({
      'head': ..., 'run-id': ..., 'run-attempt': ...,
      'full-sweep': ..., 'matrix': matrix,
    }))
    PY
```

内嵌 Python：从 planner 输出取 `changelog_metadata`，再保存包含 head SHA、Run ID、attempt、是否是完整 sweep 和完整 matrix 的 manifest。它是可追溯性资料，不运行模型。

### Step 2：上传 changelog artifact

```yaml
- name: Upload changelog artifact
  uses: actions/upload-artifact@...
  with:
    name: changelog-metadata
    path: changelog_metadata.json
```

把 changelog 元数据保存为当前 Run 的 artifact。

### Step 3：仅 Klaud Run 上传 manifest

```yaml
- name: Upload Klaud validation manifest
  if: *klaud-run
  uses: actions/upload-artifact@...
  with:
    name: klaud-sweep-manifest
    path: sweep_manifest.json
```

`*klaud-run` 是前面 YAML anchor 的别名，仅对满足特定同仓库/分支/作者条件的后台 Run 上传该 manifest。

## 16. Job：`calc-success-rate`

### 原文与 Steps

```yaml
calc-success-rate:
  needs: collect-results
  if: ${{ always() && needs.collect-results.result != 'skipped'}}
  runs-on: ubuntu-latest
```

1. **Checkout code**：用 `REPO_PAT` 获取所需源码与历史。
2. **Download results artifacts**：下载 `results_*` 到 `results/`。
3. **Set up uv**：准备 Python 3.12 与临时依赖。
4. **Calculate success rate**：
   ```bash
   python -m infx.workflows.calc_success_rate "$STATS_FILENAME"
   ```
   计算当前 Run 的统计成功率，写入 `run_stats.json`。
5. **Upload artifact**：上传名为 `run-stats` 的统计 JSON。

这是 CI 运行结果统计，不是推理请求的 AIPerf failure threshold。

## 17. Job：`compare-results`

### 原文与 Steps

```yaml
compare-results:
  needs: [collect-results, setup]
  if: >-
    always() && github.event_name == 'pull_request' &&
    needs.collect-results.result == 'success'
  env:
    DATABASE_URL: ${{ secrets.NEON_PROD_RO_URL }}
```

仅 PR 且传统结果收集成功时运行：

1. **Checkout**；
2. **Download results artifacts**：下载 `results_bmk`；
3. **Set up uv**；
4. **Compare results against main**：
   ```bash
   python -m infx.results.compare_results results/ >> "$GITHUB_STEP_SUMMARY"
   ```
   用只读数据库 URL 查询参考数据，将差异表写入 GitHub Run Summary。

它不写生产数据库，也不触发入库。

## 18. Job：`trigger-ingest`（非 AgentX 的 main 入库）

### 原文：关键条件

```yaml
trigger-ingest:
  if: >-
    always() && github.event_name == 'push' &&
    github.ref == 'refs/heads/main' &&
    needs.setup.result == 'success' &&
    (single_node['agentic'] 为空) && (multi_node['agentic'] 为空) &&
    (collect-results 未 skipped 或 collect-evals 未 skipped 或 reuse-enabled)
```

只在合并/推送到 `main` 后执行，并且 planner 没有 AgentX 桶。PR 永不进入这里。

### 唯一 Step：跨仓库 dispatch

```yaml
- name: Trigger database ingest
  run: |
    curl -sSf -X POST \
      -H "Authorization: Bearer ${{ secrets.INFX_FRONTEND_PAT }}" \
      https://api.github.com/repos/SemiAnalysisAI/InferenceX-app/dispatches \
      -d '{ "event_type": "ingest-results", "client_payload": {...} }'
```

向 `SemiAnalysisAI/InferenceX-app` 发 GitHub repository dispatch。payload 带来源 Run ID（复用时是原始 Run）和当前 merge Run ID。该 Step 使用 secret，但文档不记录其值。

## 19. Job：`trigger-agentic-ingest`（AgentX 的 main 入库）

### 原文：关键条件

```yaml
trigger-agentic-ingest:
  needs: [setup, sweep-agentic, sweep-multi-node-agentic, upload-changelog-metadata]
  if: >-
    always() && github.event_name == 'push' && github.ref == 'refs/heads/main' &&
    needs.setup.result == 'success' && needs.upload-changelog-metadata.result == 'success' &&
    (已授权复用且存在 AgentX 桶，或 AgentX 单/多节点 Job 成功且另一个成功或 skipped)
```

专门覆盖 AgentX 结果。相比普通入库，它要求 changelog metadata 成功，并允许使用已授权复用的 AgentX 扫描。

### 唯一 Step：AgentX repository dispatch

```yaml
- name: Trigger agentic database ingest
  run: |
    curl -sSf -X POST .../SemiAnalysisAI/InferenceX-app/dispatches \
      -d '{
        "event_type": "ingest-agentic-results",
        "client_payload": {
          "source-run-id": "...",
          "merge-run-id": "...",
          "database-target": "production"
        }
      }'
```

这通知结果应用处理 AgentX artifact，`database-target: production` 表明这是 main 合并后的正式路径；不适用于 PR 预览。

## 20. Job：`comment-unofficial-run-visualizer`

### 原文：条件与权限

```yaml
comment-unofficial-run-visualizer:
  needs: [collect-results, collect-evals, calc-success-rate,
          upload-changelog-metadata, setup]
  if: >-
    always() && needs.setup.result == 'success' &&
    github.event_name == 'pull_request' && (存在主 sweep 标签) &&
    (非标签事件或本 workflow 认可的标签事件)
  permissions:
    pull-requests: write
```

只在 PR 上评论；`always()` 让用户即使有部分 Benchmark/收集失败，也能获得该 Run 的非正式入口。

### 唯一 Step：写 PR 评论

```yaml
- name: Comment unofficial run visualizer link on PR
  uses: actions/github-script@...
  with:
    script: |
      const inferenceUrl = `https://inferencex.semianalysis.com/inference?unofficialRun=${context.runId}`;
      const evaluationUrl = `https://inferencex.semianalysis.com/evaluation?unofficialRun=${context.runId}`;
      await github.rest.issues.createComment({...})
```

`actions/github-script` 使用 GitHub API 在 PR（GitHub 的 PR 同时是 Issue）创建评论。链接明确叫 `unofficialRun`：它是本次 PR Run 的非正式可视化，不表示生产数据已入库。

## 21. 快速对照：标签如何改变执行

| 标签 | 入口实际行为 |
|---|---|
| `sweep-enabled` | `setup` 增加 `--trim-conc`，通常仅保留每个部署形状的低并发点。 |
| `full-sweep-enabled` | 完整矩阵 + 固定长度 canary。 |
| `non-canary-full-sweep-enabled` | 完整矩阵，不选 canary。 |
| `full-sweep-fail-fast` | 完整矩阵 + canary + matrix fail-fast。 |
| `full-sweep-fail-fast-no-canary` | 完整矩阵 + fail-fast，不跑 canary。 |
| `all-evals` | 校验器/planner 增加 `--all-evals`。 |
| `evals-only` | planner 增加 `--evals-only`，并阻止复用 gate。 |
| `agentx-fast` | 传给 AgentX 模板，最终导出 `AIPERF_EXPERIMENTAL_FAST=1`。 |
| `skip_queue` | 作为 queue metadata 传入模板；不绕过仓库授权或资源管理。 |

## 22. 最终结论

`run-sweep.yml` 不等于“运行 AIPerf 的脚本”。它是一个严格 gated 的 orchestration workflow：先从 changelog 生成矩阵，再把每个矩阵点委托给单节点或多节点模板。真正的 GPU 分配、容器运行、服务启动、AIPerf AgentX replay 与结果生成发生在模板下游的 launcher 和 benchmark recipe。

关联注释文档：

- [`benchmark-tmpl-yml-annotated-zh.md`](benchmark-tmpl-yml-annotated-zh.md)
- [`benchmark-multinode-tmpl-yml-annotated-zh.md`](benchmark-multinode-tmpl-yml-annotated-zh.md)
- [`collect-results-yml-annotated-zh.md`](collect-results-yml-annotated-zh.md)
- [`collect-evals-yml-annotated-zh.md`](collect-evals-yml-annotated-zh.md)
