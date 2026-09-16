# `benchmark-multinode-tmpl.yml` 原文与逐 Step 中文注释（多节点 Benchmark / AgentX 模板）

> 源文件：[`.github/workflows/benchmark-multinode-tmpl.yml`](.github/workflows/benchmark-multinode-tmpl.yml)。
>
> 调用方：[`run-sweep.yml`](run-sweep-yml-annotated-zh.md) 的多节点固定长度、`sweep-multi-node-agentic` 和多节点 Eval Job。
>
> **作用**：将多节点矩阵的一行 Prefill/Decode 拓扑、并发列表和 AgentX 参数映射到 self-hosted runner 与多节点 launcher，验证批量结果，上传结果/日志/Eval artifact。
>
> **安全边界**：它取消 Slurm 任务、修复持久 Git 工作区并通过 runner launcher 申请多节点资源。只能由专用 CI 集群执行，不应在共享 Slurm 或已有服务的机器上手动运行。

## 1. 与单节点模板的核心区别

| 维度 | `benchmark-tmpl.yml` | 本文件 |
|---|---|---|
| 硬件规模 | 一个节点 | `node-count` 个 Slurm 节点。 |
| 并发 | 单个 `conc`，通常每点一个 matrix Job | `conc-list`，一次多节点 Job 可覆盖多个并发点。 |
| 拓扑 | TP/PP/EP 等单组参数 | Prefill 与 Decode 各有 worker 数、TP/PP/DCP/PCP/EP。 |
| 超时 | 500 分钟 | 固定长度 480 分钟；AgentX 780 分钟，给全上下文 warmup/上传留余量。 |
| 启动 | 单节点 launcher 分支 | `IS_MULTINODE=true` 后走 launcher 的多节点分支。 |

## 2. 输入原文与解释

### 2.1 调度、身份和并发

```yaml
on:
  workflow_call:
    inputs:
      runner: { required: true, type: string }
      node-count: { required: true, type: number }
      priority: { required: true, type: string }
      queue-token: { required: true, type: string }
      skip-queue-pr: { required: false, type: string, default: '' }
      image: { required: true, type: string }
      model: { required: true, type: string }
      model-prefix: { required: true, type: string }
      framework: { required: true, type: string }
      precision: { required: true, type: string }
      exp-name: { required: true, type: string }
      recipe-fingerprint: { required: false, type: string, default: '' }
      isl: { required: true, type: string }
      osl: { required: true, type: string }
      conc-list: { required: true, type: string }
      spec-decoding: { required: true, type: string }
      disagg: { required: true, type: string }
```

- `node-count` 是该 Benchmark 总共需要的 Slurm 节点数；
- `conc-list` 是 JSON 数组字符串，不是单一值；模板转为 `CONC_LIST="1 4 8"`；
- `recipe-fingerprint` 用于稳定结果/配方身份；
- 对 AgentX，`isl/osl/max-model-len` 由调用方传 `0`，不代表空 trace。

### 2.2 Prefill / Decode 拓扑

```yaml
      prefill-hardware: { type: string, default: "" }
      decode-hardware: { type: string, default: "" }
      prefill-num-worker: { required: true, type: string }
      prefill-tp: { required: true, type: string }
      prefill-pp: { default: '1' }
      prefill-dcp-size: { default: '1' }
      prefill-pcp-size: { default: '1' }
      prefill-ep: { required: true, type: string }
      prefill-dp-attn: { required: true, type: string }
      prefill-additional-settings: { default: "[]" }
      decode-num-worker: { required: true, type: string }
      decode-tp: { required: true, type: string }
      decode-pp: { default: '1' }
      decode-dcp-size: { default: '1' }
      decode-pcp-size: { default: '1' }
      decode-ep: { required: true, type: string }
      decode-dp-attn: { required: true, type: string }
      decode-additional-settings: { default: "[]" }
```

多节点解耦服务会把 Prefill（处理输入）和 Decode（生成输出）作为不同 worker 组。它们可以使用不同硬件、数量与并行参数。`additional-settings` 是 JSON 字符串数组，运行时会转成额外环境变量。

### 2.3 Eval 与 AgentX 输入

```yaml
      run-eval: { default: false }
      eval-only: { default: false }
      eval-framework: { default: "lm-eval" }
      eval-suite: { default: "" }
      eval-conc: { default: "" }
      eval-limit: { default: "" }
      scenario-type: { default: fixed-seq-len }
      conc: { default: "" }
      duration: { default: "3600" }
      agentx-fast: { default: false }
      kv-offloading: { type: string }
      kv-offload-backend: { type: string }
      total-cpu-dram-gb: { default: '600' }
```

- `eval-conc` 覆盖默认的“并发列表最大值”选择；
- AgentX 使用 `conc-list` 跑整批，并用 `conc` 保存第一个/评估用并发；
- `duration` 是 AgentX trace replay profile 时长；
- `total-cpu-dram-gb` 是 KV offload CPU DRAM 配置；
- `agentx-fast` 与单节点模板相同：profile 1200 秒、warmup 每 lane 1 次。

## 3. 环境变量原文与作用

```yaml
env:
  HF_TOKEN: ${{ secrets.INFERENCEX_OFFICIAL_RO_HF_TOKEN }}
  EXP_NAME: ${{ inputs.exp-name }}
  MODEL: ${{ inputs.model }}
  CONC_LIST: ${{ join(fromJson(inputs.conc-list), ' ') }}
  PREFILL_NUM_WORKERS: ${{ inputs.prefill-num-worker }}
  PREFILL_TP: ${{ inputs.prefill-tp }}
  ...
  DECODE_NUM_WORKERS: ${{ inputs.decode-num-worker }}
  DECODE_TP: ${{ inputs.decode-tp }}
  ...
  SCENARIO_SUBDIR: ${{ inputs.scenario-type == 'agentic-coding' && 'agentic/' || 'fixed_seq_len/' }}
  IS_AGENTIC: ${{ inputs.scenario-type == 'agentic-coding' && '1' || '0' }}
  CONC: ${{ inputs.conc }}
  DURATION: ${{ inputs.duration }}
  AIPERF_EXPERIMENTAL_FAST: ${{ inputs.agentx-fast && '1' || '0' }}
```

重要点：

- `fromJson(inputs.conc-list)` 将调用方传来的 JSON 数组恢复，再 `join(..., ' ')` 供 Bash/Slurm 使用；
- Prefill/Decode 每组变量都独立，避免把解耦服务的两种并行度混在一起；
- `SCENARIO_SUBDIR` 在 launcher 中决定多节点 SGLang disaggregation 是否从 `benchmarks/multi_node/agentic/` 选 recipe；
- `HF_TOKEN` 是 secret，用于避免多个 worker 并发下载大模型时的匿名限流，绝不应输出。

## 4. Job：`benchmark` 的 runner 与超时

### 原文（缩略）

```yaml
runs-on: ${{ fromJSON(...
  format('["self-hosted", runner, "nodes:<node-count>",
          "ci-job-<priority>-<queue-token>", "ci-attempt-N"]')
...) }}
timeout-minutes: ${{ inputs.scenario-type == 'agentic-coding' && 780 || 480 }}
```

- runner 标签中包含 `nodes:<node-count>`，队列系统据此选择可提供足够节点的 self-hosted capacity；
- AgentX 允许 780 分钟（13 小时），比固定长度 480 分钟多出时间；源码注释说明高并发全上下文 warmup 可以超过通常的固定长度窗口；
- GitHub 超时包括 checkout、Slurm 排队/启动、日志收集与 artifact 上传，不是单纯 AIPerf 时长。

## 5. 每个 Step 的原文与解释

### Step 1：`Slurm cleanup (pre-run)`

```yaml
- name: Slurm cleanup (pre-run)
  run: |
    for job_name in "${{ runner.name }}" "inferencex-${{ runner.name }}"; do
      scancel --user="$USER" --name="$job_name" || true
      while [ -n "$(squeue --user="$USER" --name="$job_name" ...)" ]; do sleep 5; done
    done
```

取消当前 CI 用户名下、以 runner 名或 `inferencex-<runner>` 命名的残留 Slurm Job。它防止中断 Run 残留资源影响下一次运行。**这是项目专用集群恢复动作，不能用于共享 Slurm 环境。**

### Step 2：`Repair stale git state (pre-run)`

```yaml
- name: Repair stale git state (pre-run)
  run: |
    find "${repo_dir}/.git" -name index.lock -type f -delete || true
    ...移除无法解析 HEAD 的损坏 submodule git dir 与 worktree...
```

与单节点模板相同：修复 persistent self-hosted workspace 中的陈旧 lock 和中断 submodule clone，避免 checkout 失败。

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

检出目标 commit 与子模块。`clean: true` 是持久 runner 工作区的一部分，前提是前两步已修复受容器影响的所有者/Git 状态。

### Step 4：`Launch multi-node job script`

```yaml
- name: Launch multi-node job script
  env:
    RUNNER_NAME: ${{ runner.name }}
    RUNNER_TYPE: ${{ inputs.runner }}
    RESULT_FILENAME_BASE: ...prefill-...decode-...conc...
  run: |
    RESULT_FILENAME=$(python3 utils/result_filename.py || sha256 fallback)
    echo "RESULT_FILENAME=..." >> "$GITHUB_ENV"
    eval_artifact_conc="$(python3 -c '...sha256(CONC_LIST)...')"
    export ${{ join(fromJson(inputs.prefill-additional-settings), ' ') }} \
           ${{ join(fromJson(inputs.decode-additional-settings), ' ') }}
    export IS_MULTINODE=true
    bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

这一 Step：

1. 用完整 Prefill/Decode/并发拓扑构造基础结果身份，再用 helper 或 SHA 回退生成最终 ID；
2. 计算 Eval artifact 所需的 recipe 短哈希和 `CONC_LIST` 哈希，避免不同拓扑/并发的 Eval artifact 冲突；
3. 将 YAML 额外设置数组变为环境变量；应只来自受控 config，不能把不可信文本直接当 shell 变量；
4. `export IS_MULTINODE=true`；
5. 由 runner 名推导 launcher，例如 `launch_mi355x-amds.sh`。

#### launcher 多节点分支做什么

`launch_mi355x-amds.sh` 检测到 `IS_MULTINODE=true` 后：

```bash
SCRIPT_NAME="${EXP_NAME%%_*}_${PRECISION}_mi355x_${FRAMEWORK}.sh"
if [[ "${SCENARIO_SUBDIR}" == "agentic/" ]]; then
  BENCHMARK_SUBDIR="multi_node/agentic"
else
  BENCHMARK_SUBDIR="multi_node"
fi
JOB_ID=$(bash "benchmarks/${BENCHMARK_SUBDIR}/${SCRIPT_NAME}")
```

随后 launcher：

1. 设置 Slurm、模型路径、RDMA 和共享日志目录；
2. 执行 benchmark 提交脚本取得 Slurm Job ID；
3. 等待/跟随 Slurm 日志；
4. 固定长度时收集结果 JSON；
5. AgentX 时保留每个 `conc_<N>` 的 AIPerf artifact 与服务日志；
6. 同步取消 Slurm Job，清理临时日志。

### Step 4 后半段：根据模式验证输出

```yaml
if [ "${{ inputs.eval-only }}" = "true" ]; then
  test -n "$(ls results*.json)"
elif [ "${{ inputs.scenario-type }}" = "agentic-coding" ]; then
  expected_count=$(wc -w <<< "$CONC_LIST")
  agentic_results=("${RESULT_FILENAME}"_conc*.json)
  # 数量必须匹配，每个 JSON 必须有 num_requests_successful
else
  test -n "$(ls ${RESULT_FILENAME}_*.json)"
fi
```

| 模式 | 验证规则 |
|---|---|
| Eval-only | 至少产生一个 `results*.json`。 |
| 多节点 AgentX | 每个 `CONC_LIST` 项都必须有一个 `<id>_conc*.json`，并且每个 JSON 的 `num_requests_successful` 非零。 |
| 多节点固定长度 | 至少产生一个符合 `<id>_*.json` 的原始结果。 |

这比“文件存在即成功”更严格，尤其 AgentX 防止聚合器写出零成功请求的空壳结果。

### Step 5：`Process result`

```yaml
- name: Process result
  if: ${{ always() && env.RESULT_FILENAME != '' && !inputs.eval-only &&
           inputs.scenario-type != 'agentic-coding' }}
  run: |
    # 尝试 utils/process_result.py --all
    # 旧 commit 无 batch API 时逐 result file 解析 concurrency 并处理
```

仅处理固定长度多节点结果。优先使用较新的批处理 API；旧 ref 没有该 API 时，逐文件提取并发与 GPU 数，检查观察到的并发集合是否与 `CONC_LIST` 完全一致。AgentX 跳过，因为其结果已经由 AgentX 聚合路径产生。

### Step 6：`Upload result`

```yaml
- name: Upload result
  if: ${{ success() && ... && inputs.scenario-type != 'agentic-coding' }}
  with:
    name: bmk_${{ env.RESULT_FILENAME }}
    path: agg_${{ env.RESULT_FILENAME }}_*.json
```

上传多个并发点的固定长度聚合 JSON，供 `collect-results.yml` 处理。

### Step 7：`Upload power audit bundle`

```yaml
- name: Upload power audit bundle
  if: ${{ always() && !inputs.eval-only }}
  with:
    name: power_audit_${{ env.RESULT_FILENAME }}
    path: ... LOGS/power/** ... LOGS/agentic/**/power_validation.json
```

保存功耗验证、原始/聚合 JSON 与 AgentX 每并发功耗窗口，支持页面实测功耗/能耗数据的审计。

### Step 8：`Upload server logs`

```yaml
- name: Upload server logs
  if: always()
  with:
    name: multinode_server_logs_${{ env.RESULT_FILENAME }}
    path: multinode_server_logs.tar.gz
```

launcher 将多节点服务、路由、prefill/decode 相关日志打包为 tarball。无论成功失败都尝试上传。

### Step 9：`Upload agentic aggregated result`

```yaml
- name: Upload agentic aggregated result
  if: ${{ always() && !inputs.eval-only && inputs.scenario-type == 'agentic-coding' }}
  with:
    name: bmk_agentic_${{ env.RESULT_FILENAME }}
    path: ${{ env.RESULT_FILENAME }}_conc*.json
```

上传每个并发的 AgentX 汇总 JSON。多节点不同于单节点：一个 Job 覆盖一个并发列表，所以路径是多个 `_conc*.json` 文件。

### Step 10：`Upload agentic raw results`

```yaml
- name: Upload agentic raw results
  if: ${{ always() && inputs.scenario-type == 'agentic-coding' }}
  with:
    name: agentic_${{ env.RESULT_FILENAME }}
    path: |
      LOGS/agentic/**
      !LOGS/agentic/**/aiperf_artifacts/inputs.json
      !LOGS/agentic/**/aiperf_artifacts/profile_export_raw.jsonl
```

上传保留 `conc_<N>` 结构的 AgentX 日志/产物，同时排除原始请求输入和 raw trace export，以控制隐私与 artifact 大小。

### Step 11：`Upload eval results (if any)`

```yaml
- name: Upload eval results (if any)
  if: ${{ always() && (env.RUN_EVAL == 'true' || inputs.eval-only) }}
  with:
    name: eval_${{ env.EXP_NAME }}_..._${{ env.EVAL_ARTIFACT_CONC }}_...
    path: results*.json ... predictions.jsonl ... *.traj*
```

多节点 Eval artifact 名包含 Prefill/Decode 拓扑、recipe 短哈希、并发哈希、KV、框架、suite、runner 和 attempt，防止不同多节点配置的结果混淆。Eval-only 缺文件为 error，性能 Job 附带 Eval 可 ignore。

### Step 12：`Verify eval scores`

```yaml
- name: Verify eval scores
  if: ${{ (success() || failure()) && inputs.eval-only }}
  run: |
    expected_concs="${EVAL_CONC}"
    if [[ -z "${expected_concs}" ]]; then
      expected_concs="$(...CONC_LIST 最大值...)"
    fi
    python3 utils/evals/validate_scores.py --expected-concs "${expected_concs}"
```

Eval-only 时验证分数及期望并发。若没有明确 `EVAL_CONC`，默认用 `CONC_LIST` 最大值。

### Step 13：`Cleanup eval outputs (post-upload)`

```yaml
- name: Cleanup eval outputs (post-upload)
  if: ${{ always() && (inputs.run-eval || inputs.eval-only) }}
  run: rm -f meta_env.json results*.json ...
```

上传后清理持久 workspace 的 Eval 临时文件，避免后续 Job 误拾取旧结果。

### Step 14：`Slurm cleanup (post-run)`

```yaml
- name: Slurm cleanup (post-run)
  if: always()
  run: *slurm-cleanup
```

复用 Step 1 的 YAML anchor。即使启动、验证或上传失败，也尽力释放同名 Slurm 任务。

## 6. AgentX 多节点链路小结

```text
run-sweep: multi_node['agentic']
  → benchmark-multinode-tmpl.yml
  → SCENARIO_SUBDIR=agentic/, IS_AGENTIC=1
  → launcher 选择 benchmarks/multi_node/agentic/<recipe>.sh
  → 提交 Slurm 多节点服务与 trace replay
  → 每个 conc 生成一份 AgentX 聚合 JSON
  → 模板验证每个 conc 都有 successful requests
  → 上传 bmk_agentic_*、agentic_*、日志与功耗审计 artifact
  → main push 时 trigger-agentic-ingest 通知 InferenceX-app
```

## 7. 安全提醒

该模板和 runner launcher 会影响 Slurm 任务、持久工作区、共享日志目录及容器/镜像资源。分析 YAML 不需要执行这些操作；除非明确得到项目授权，不应在共享环境、个人容器或已运行 vLLM/SGLang 服务的宿主机启动这条链路。
