# `collect-evals.yml` 原文与逐 Step 中文注释

> 源文件：[`.github/workflows/collect-evals.yml`](.github/workflows/collect-evals.yml)。
>
> 调用方：[`run-sweep.yml`](run-sweep-yml-annotated-zh.md) 的 `collect-evals` Job。
>
> 作用：下载各 Eval Job 已上传的评估产物，在普通 Linux runner 上汇总评分和摘要，上传统一 Eval JSON。它不启动模型、不执行 AIPerf，也不重新运行 Eval。

## 1. 完整原文

```yaml
name: Template - Collect Evals

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
  collect-evals:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          token: ${{ secrets.REPO_PAT }}
          fetch-depth: 0

      - name: Download eval artifacts
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          path: eval_results/
          pattern: ${{ inputs.result-prefix && format('eval_{0}_*', inputs.result-prefix) || 'eval_*' }}

      - name: Set up uv
        uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1

      - name: Summarize evals
        run: |
          echo "## Eval Summary" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          uv run --no-project --exclude-newer PT12H --python 3.12 --with tabulate \
            python -m infx.results.collect_eval_results eval_results/ ${{ inputs.result-prefix || 'all' }} >> $GITHUB_STEP_SUMMARY

      - name: Upload aggregated evals
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: eval_results_${{ inputs.result-prefix || 'all' }}
          path: agg_eval_${{ inputs.result-prefix || 'all' }}.json

      - name: Cleanup downloaded eval artifacts
        if: ${{ always() }}
        run: |
          rm -rf eval_results/ || true
```

## 2. 顶层与输入

```yaml
on:
  workflow_call:
```

这表示该文件只能被其他 workflow 调用。`run-sweep.yml` 调用时没有传 `result-prefix`，所以使用默认空字符串。

```yaml
result-prefix:
  required: false
  type: string
  default: ''
```

因此本次常规调用的实际 pattern 是：

```text
eval_*
```

如果未来调用者传入 `foo`，pattern 变成 `eval_foo_*`，聚合名也变成 `foo`。

`permissions.contents: read` 只允许读取源码；不授予写 PR、写数据库或跨仓库 dispatch 能力。

## 3. Job：`collect-evals`

```yaml
jobs:
  collect-evals:
    runs-on: ubuntu-latest
```

该 Job 在 GitHub 托管 Linux 环境中工作，负责文件整理和 Python 汇总，通常不需要 GPU。

### Step 1：Checkout code

```yaml
- name: Checkout code
  uses: actions/checkout@... # v7.0.1
  with:
    token: ${{ secrets.REPO_PAT }}
    fetch-depth: 0
```

取得当前 commit 中的 `infx.results.collect_eval_results` 源码。

- `REPO_PAT` 是 secret，仅供 checkout；
- 不应把该值输出到日志或复制到本地文档；
- 完整历史是 checkout 配置的一部分，聚合本身主要消费下载的 artifact。

### Step 2：下载 Eval artifact

```yaml
- name: Download eval artifacts
  uses: actions/download-artifact@... # v8.0.1
  with:
    path: eval_results/
    pattern: ${{ inputs.result-prefix && format('eval_{0}_*', inputs.result-prefix) || 'eval_*' }}
```

上游单/多节点 benchmark 模板在启用 Eval 时上传形如：

```text
eval_<result identity>_<eval framework>_<eval suite>_<attempt>
```

的 artifact。此 Step 将它们下载到 `eval_results/`。输入为空时下载当前 Run 中全部 `eval_*` artifact。

### Step 3：安装 uv

```yaml
- name: Set up uv
  uses: astral-sh/setup-uv@... # v10.0.1
```

安装 `uv`。下一 Step 使用 `uv run --no-project` 建立临时 Python 3.12 环境，仅额外安装 `tabulate`。

### Step 4：汇总 Eval 并写 Run Summary

```yaml
- name: Summarize evals
  run: |
    echo "## Eval Summary" >> $GITHUB_STEP_SUMMARY
    echo "" >> $GITHUB_STEP_SUMMARY
    uv run --no-project --exclude-newer PT12H --python 3.12 --with tabulate \
      python -m infx.results.collect_eval_results eval_results/ ${{ inputs.result-prefix || 'all' }} >> $GITHUB_STEP_SUMMARY
```

逐项解释：

| 原文 | 含义 |
|---|---|
| `$GITHUB_STEP_SUMMARY` | GitHub Actions 提供的摘要文件。写入内容会显示在该 Run 的 Summary 页面。 |
| `uv run --no-project` | 不使用当前工作区项目环境，创建临时依赖环境。 |
| `--exclude-newer PT12H` | 限制解析依赖的时间边界，提高 CI 依赖可重复性。 |
| `--python 3.12` | 选择 Python 3.12。 |
| `--with tabulate` | 临时安装表格格式化依赖。 |
| `python -m infx.results.collect_eval_results` | 调用仓库内的 Eval 汇总器。 |
| `eval_results/` | 已下载 artifact 目录。 |
| `all` | prefix 为空时的汇总名。 |

模块的标准输出被追加到 GitHub Summary，通常形成一个便于审阅的 Eval 表格；它同时生成聚合 JSON。

### Step 5：上传聚合 Eval JSON

```yaml
- name: Upload aggregated evals
  uses: actions/upload-artifact@... # v7.0.1
  with:
    name: eval_results_${{ inputs.result-prefix || 'all' }}
    path: agg_eval_${{ inputs.result-prefix || 'all' }}.json
```

默认调用下：

| 字段 | 实际值 |
|---|---|
| artifact 名称 | `eval_results_all` |
| 上传文件 | `agg_eval_all.json` |

这个 artifact 是后续系统或人工审阅可下载的统一 Eval 结果，而不是原始逐样本日志的替代品。

### Step 6：无论结果如何都清理下载目录

```yaml
- name: Cleanup downloaded eval artifacts
  if: ${{ always() }}
  run: |
    rm -rf eval_results/ || true
```

- `always()`：即使汇总失败也尝试清理；
- `|| true`：即使目录不存在或删除失败，清理错误也不会覆盖主失败原因；
- 这是临时 GitHub-hosted runner 工作目录的整理，不是对远程 GPU 宿主机、模型缓存或用户文件的清理。

## 4. 与上游模板的契约

`benchmark-tmpl.yml` 和 `benchmark-multinode-tmpl.yml` 在 `RUN_EVAL=true` 或 `eval-only=true` 时上传 Eval artifact。收集器依赖：

1. artifact 名以 `eval_` 开头；
2. artifact 内包含 `results*.json`、报告、JSONL、预测或轨迹等约定文件；
3. eval-only 时上游模板会要求至少产生结果并执行 `utils/evals/validate_scores.py`。

因此，收集器失败应先检查上游 Eval Job 的上传 Step 和分数校验，而不是在本模板里寻找模型服务问题。

## 5. 与 AgentX 的关系

`run-sweep.yml` 会把 `agentic_evals` 和 `multinode_agentic_evals` 分别派发到 benchmark 模板。只要它们上传 artifact 名遵循 `eval_*` 契约，本收集器无需区分它是固定长度还是 AgentX；区别主要在上游怎样启动服务与构造 workload。

相关文档：

- [`run-sweep-yml-annotated-zh.md`](run-sweep-yml-annotated-zh.md)
- [`benchmark-tmpl-yml-annotated-zh.md`](benchmark-tmpl-yml-annotated-zh.md)
- [`benchmark-multinode-tmpl-yml-annotated-zh.md`](benchmark-multinode-tmpl-yml-annotated-zh.md)
