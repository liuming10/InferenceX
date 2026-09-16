# `collect-results.yml` 原文与逐 Step 中文注释

> 源文件：[`.github/workflows/collect-results.yml`](.github/workflows/collect-results.yml)。
>
> 调用方：[`run-sweep.yml`](run-sweep-yml-annotated-zh.md) 的 `collect-results` Job。
>
> 作用：下载已由各 Benchmark Job 上传的性能 JSON artifact，调用聚合器，上传一个总结果 artifact。它不启动模型、不申请 GPU，也不重跑 Benchmark。

## 1. 完整原文

```yaml
name: Template - Collect Results

on:
  workflow_call:
    inputs:
      result-prefix:
        required: false
        type: string
        default: ''

permissions:
  contents: read

jobs:
  collect-results:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          token: ${{ secrets.REPO_PAT }}
          fetch-depth: 0

      - name: Download JSON artifacts
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          path: results/
          pattern: ${{ inputs.result-prefix && format('{0}_*', inputs.result-prefix) || '*' }}

      - name: Aggregate results
        run: python3 -m infx.results.collect_results results/ ${{ inputs.result-prefix || 'all' }}

      - name: Upload aggregated results
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: results_${{ inputs.result-prefix || 'all' }}
          path: agg_${{ inputs.result-prefix || 'all' }}.json
```

## 2. 顶层：这是 reusable workflow

```yaml
on:
  workflow_call:
```

它没有 `push`、`pull_request` 或 `workflow_dispatch` 触发器，因此不能作为普通入口自动运行。只有另一个 workflow 用：

```yaml
uses: ./.github/workflows/collect-results.yml
```

才能调用它。

### 输入：`result-prefix`

```yaml
inputs:
  result-prefix:
    required: false
    type: string
    default: ''
```

- 输入为空时收集所有 artifact；
- `run-sweep.yml` 传入 `result-prefix: "bmk"`；
- 因此下载 pattern 实际为 `bmk_*`，例如固定长度 Job 上传的 `bmk_<result-id>`。

`permissions.contents: read` 只允许读仓库内容；该模板没有写仓库、评论 PR 或触发外部服务的权限。

## 3. Job：`collect-results`

```yaml
jobs:
  collect-results:
    runs-on: ubuntu-latest
```

聚合运行在 GitHub 托管的普通 Linux runner，而不是 GPU/self-hosted runner。它只处理 artifact 文件。

### Step 1：Checkout code

```yaml
- name: Checkout code
  uses: actions/checkout@... # v7.0.1
  with:
    token: ${{ secrets.REPO_PAT }}
    fetch-depth: 0
```

目的：取得当前 commit 中的 `infx.results.collect_results` 模块及其依赖源码。

- `REPO_PAT` 是 secret，传给 `actions/checkout`，不应写入日志、文档或命令行输出；
- `fetch-depth: 0` 取得完整历史，虽然本模板的聚合命令本身主要使用工作区源码和下载结果。

### Step 2：下载 JSON artifact

```yaml
- name: Download JSON artifacts
  uses: actions/download-artifact@... # v8.0.1
  with:
    path: results/
    pattern: ${{ inputs.result-prefix && format('{0}_*', inputs.result-prefix) || '*' }}
```

- `actions/download-artifact` 从**当前 workflow run** 下载前面 Benchmark Job 上传的 artifact；
- 下载目录为 `results/`；
- 有 `result-prefix=bmk` 时，expression 展开为 `bmk_*`；无 prefix 时为 `*`。

这里的“JSON artifacts”是约定名称：实际 artifact 内容由上游模板决定。固定长度模板会上传处理后的 `agg_<id>.json`，artifact 名通常以 `bmk_` 开头。

### Step 3：聚合结果

```yaml
- name: Aggregate results
  run: python3 -m infx.results.collect_results results/ ${{ inputs.result-prefix || 'all' }}
```

执行当前仓库的 Python 模块：

```text
infx.results.collect_results
```

参数含义：

| 参数 | 本次 `run-sweep.yml` 调用下的值 | 含义 |
|---|---|---|
| 第一个位置参数 | `results/` | 已下载 artifact 的目录。 |
| 第二个位置参数 | `bmk` | 聚合命名/过滤前缀；输入为空时使用 `all`。 |

模块将上游单点结果汇总为：

```text
agg_bmk.json
```

此 Step 只汇总已有文件，不会调用 launcher、Slurm、Docker、模型服务或 AIPerf。

### Step 4：上传聚合结果

```yaml
- name: Upload aggregated results
  uses: actions/upload-artifact@... # v7.0.1
  with:
    name: results_${{ inputs.result-prefix || 'all' }}
    path: agg_${{ inputs.result-prefix || 'all' }}.json
```

在 `result-prefix=bmk` 的实际调用中：

| 字段 | 实际值 |
|---|---|
| Artifact 名称 | `results_bmk` |
| 上传文件 | `agg_bmk.json` |

后续 `calc-success-rate` 与 `compare-results` 会下载 `results_*` 或 `results_bmk`。这就是为什么 artifact 名和文件名都属于 workflow 间契约。

## 4. 与 AgentX 的关系

`run-sweep.yml` 的 `collect-results` 条件目前由 canary 或固定长度 Job 的状态驱动。纯 AgentX 扫描可能不会调用该模板，即使单节点/多节点 AgentX Job 已上传 `bmk_agentic_*` artifact。

这不是该模板主动排斥 AgentX，而是入口 workflow 的当前条件设计。AgentX 在 main 合并后使用专门的 `trigger-agentic-ingest` 路径；不要把 `agg_bmk.json` 的存在误认为 AgentX 结果已完成生产入库。

## 5. 排查顺序

1. 检查上游 benchmark 模板是否实际上传 `bmk_*` artifact；
2. 检查 `run-sweep.yml` 中 `collect-results` 是否被 `skipped`；
3. 检查下载 Step 的 pattern 是否与 artifact 名匹配；
4. 检查 `infx.results.collect_results` 的错误输出；
5. 检查 `agg_bmk.json` 是否被上传为 `results_bmk`。

不要在共享 GPU 主机上手动运行此 workflow 的下游清理脚本来排查 artifact 问题；收集器本身只需要本地结果文件和 Python 环境。
