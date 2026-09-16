# PR #3118：Qwen3.5 MI355X AgentX HiCache 完整执行链路

> PR：[AMD][Qwen3.5] AgentX 20260915 image and HiCache kernel / page_first  
> 链接：<https://github.com/SemiAnalysisAI/InferenceX/pull/3118>  
> PR head commit：`a8b2fcbc9eb58fced4a4ad4e6be1b55d4ccc3121`  
> 父提交：`b3c2f1efa81f14458347e652281070031bfee232`

## 1. 结论

PR #3118 不是新增 AgentX 基准测试架构，而是更新已有的 AMD MI355X、Qwen3.5-397B-A17B MXFP4、SGLang MTP AgentX 配方。

它只修改三个文件：

```text
configs/amd-master.yaml
benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
perf-changelog.yaml
```

实际改变为：

1. 将 AgentX recipe 所用 SGLang ROCm 镜像升级到 `20260915`。
2. 当 AgentX 配置启用 DRAM HiCache 时，将默认值改为：
   ```text
   --hicache-io-backend kernel
   --hicache-mem-layout page_first
   ```
3. 保持 HiCache ratio `1.5` 与 write policy `write_through` 不变。
4. 在 `perf-changelog.yaml` 中登记该性能配方变更。

它不改变 AIPerf 的 AgentX scenario、Weka 数据集、工作流分流规则、Slurm launcher 的路径选择逻辑，也不影响 `kv-offloading: none` 的 AgentX 配置点。

---

## 2. 全链路概览

```text
perf-changelog.yaml
  │  登记 PR 对应的性能配方变更
  ▼
configs/amd-master.yaml
  │  定义模型、镜像、runner、TP/EP、并发和 HiCache 策略
  ▼
infx.matrix.generate
  │  将 search-space × conc-list 展开为独立 matrix rows
  ▼
.github/workflows/e2e-tests.yml
  │  依据 scenario-type=agentic-coding 分流至 AgentX workflow job
  ▼
.github/workflows/benchmark-tmpl.yml
  │  将 matrix row 转换为 shell 环境变量
  │  IS_AGENTIC=1，SCENARIO_SUBDIR=agentic/
  ▼
runners/launch_mi355x-amds.sh
  │  Slurm 分配、Enroot 容器执行、拼接 benchmark 脚本路径
  ▼
benchmarks/single_node/agentic/
qwen3.5_fp4_mi355x_sglang_mtp.sh
  │  PR #3118 的 HiCache 默认值在这里生效
  │  启动 SGLang EAGLE/MTP 服务
  ▼
benchmarks/benchmark_lib.sh
  │  build_replay_cmd()
  ▼
aiperf profile --scenario inferencex-agentx-mvp
  │
  ▼
AgentX artifacts、服务指标校验、结果验证、GitHub Actions artifacts
```

---

## 3. PR #3118 的精确 diff

### 3.1 镜像更新

`configs/amd-master.yaml` 中，配置 key：

```yaml
qwen3.5-fp4-mi355x-sglang-agentic-mtp:
```

镜像从：

```text
lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260913
```

改为：

```text
lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260915
```

### 3.2 HiCache 默认值更新

文件：`benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh`

```diff
-HICACHE_IO_BACKEND="${HICACHE_IO_BACKEND:-direct}"
-HICACHE_MEM_LAYOUT="${HICACHE_MEM_LAYOUT:-page_first_direct}"
+HICACHE_IO_BACKEND="${HICACHE_IO_BACKEND:-kernel}"
+HICACHE_MEM_LAYOUT="${HICACHE_MEM_LAYOUT:-page_first}"
```

参数对比如下：

| 参数 | PR 前默认值 | PR #3118 后默认值 |
|---|---|---|
| `--hicache-io-backend` | `direct` | `kernel` |
| `--hicache-mem-layout` | `page_first_direct` | `page_first` |
| `--hicache-ratio` | `1.5` | `1.5` |
| `--hicache-write-policy` | `write_through` | `write_through` |

这是默认值变化。若外部环境已经设置：

```bash
HICACHE_IO_BACKEND=...
HICACHE_MEM_LAYOUT=...
```

shell 的 `${变量:-默认值}` 语义会保留外部显式值，PR 不会强制覆盖。

### 3.3 性能变更登记

`perf-changelog.yaml` 新增：

```yaml
- config-keys:
    - qwen3.5-fp4-mi355x-sglang-agentic-mtp
  scenario-type:
    - agentic-coding
  description:
    - "Bump image to lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260915."
    - "Switch HiCache defaults to --hicache-io-backend kernel and --hicache-mem-layout page_first (from direct / page_first_direct). Ratio 1.5 and write_through are unchanged."
  pr-link: https://github.com/SemiAnalysisAI/InferenceX/pull/3118
```

这个 changelog 记录用于性能变更追踪与 changelog 驱动的计划流程，本身不启动 benchmark。

---

## 4. PR 时点的 AgentX 配置

以下是 PR #3118 时对应 config key 的重要字段。它是历史 PR 快照；当前 `main` 可能已被后续 PR 修改，不应将二者混为一谈。

```yaml
qwen3.5-fp4-mi355x-sglang-agentic-mtp:
  image: lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260915
  model: amd/Qwen3.5-397B-A17B-MXFP4-AttnFP8-V2
  model-prefix: qwen3.5
  runner: cluster:mi355x-amds
  precision: fp4
  framework: sglang
  multinode: false
  scenarios:
    agentic-coding:
    - dram-utilization: 0.80
      search-space:
      - { tp: 4, ep: 1, spec-decoding: mtp, kv-offloading: none,
          conc-list: [1, 4, 8, 12, 16] }
      - { tp: 2, ep: 1, spec-decoding: mtp, kv-offloading: none,
          conc-list: [1, 4, 8, 12, 16, 20] }
      - { tp: 2, ep: 1, spec-decoding: mtp, kv-offloading: dram,
          kv-offload-backend: { name: hicache },
          conc-list: [20, 24, 28, 32, 36, 40] }
```

| YAML 字段 | 作用 |
|---|---|
| `image` | 传给 AMD launcher，用 Enroot 导入并运行对应 SGLang ROCm 镜像。 |
| `model` | Hugging Face 模型 ID、SGLang `--served-model-name`、AIPerf `--model`/`--tokenizer`。 |
| `model-prefix` | 矩阵身份与 `EXP_NAME` 前缀；也是脚本选择所需的名称成分。 |
| `runner` | 指向 AMD MI355X 集群的 runner 标签 `cluster:mi355x-amds`。 |
| `precision` | `fp4`；参与脚本文件路径拼接。 |
| `framework` | `sglang`；参与脚本文件路径拼接，决定执行 SGLang recipe。 |
| `multinode` | `false`，所以选择 `benchmarks/single_node/...`。 |
| `agentic-coding` | 使 workflow 设置 `IS_AGENTIC=1` 和 `SCENARIO_SUBDIR=agentic/`。 |
| `tp` / `ep` | 传给 SGLang `--tp` 和 `--ep-size`。 |
| `spec-decoding: mtp` | 添加 `_mtp` 脚本后缀，recipe 内配置 EAGLE MTP。 |
| `kv-offloading` | `none` 时不用 HiCache；`dram` 时进入 HiCache 分支。 |
| `conc-list` | 每一个并发值都会生成独立 benchmark job。 |

PR #3118 时，这个 key 共生成 17 个 AgentX 单节点点：

```text
TP=4, KV offload=none:  5
TP=2, KV offload=none:  6
TP=2, DRAM HiCache:     6
──────────────────────────
总计：                  17
```

只有最后 6 个 DRAM HiCache 点使用本 PR 修改的 `kernel` / `page_first` 默认值。

---

## 5. Matrix generator：配置如何展开

矩阵生成器读取：

```python
config["scenarios"]["agentic-coding"]
```

并遍历每个 `search-space` 与对应 `conc-list`。对单节点 AgentX，`conc-list` 中的每一个并发值生成一个独立 matrix row。

例如：

```yaml
- { tp: 2, ep: 1, spec-decoding: mtp,
    kv-offloading: dram,
    kv-offload-backend: { name: hicache },
    conc-list: [20, 24, 28, 32, 36, 40] }
```

会生成 6 个任务。`CONC=32` 的逻辑 row 类似：

```json
{
  "image": "lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260915",
  "model": "amd/Qwen3.5-397B-A17B-MXFP4-AttnFP8-V2",
  "model-prefix": "qwen3.5",
  "runner": "cluster:mi355x-amds",
  "precision": "fp4",
  "framework": "sglang",
  "tp": 2,
  "pp": 1,
  "ep": 1,
  "dp-attn": false,
  "spec-decoding": "mtp",
  "conc": 32,
  "kv-offloading": "dram",
  "kv-offload-backend": {"name": "hicache"},
  "scenario-type": "agentic-coding",
  "duration": 3600,
  "exp-name": "qwen3.5_tp2_conc32_dram-hicache_spec-mtp"
}
```

重要语义：

- `scenario-type` 明确标识为 `agentic-coding`。
- 默认 AgentX profile duration 为 `3600` 秒。
- 每个并发点是一次独立运行，而不是一个任务内部扫描多个并发。
- `dram-utilization: 0.80` 用于结合 runner 硬件信息计算可用 CPU DRAM offload 预算，并形成 `TOTAL_CPU_DRAM_GB`。

涉及源码：

```text
infx/matrix/generate.py
  _expand_configs()
  _agentic_entries()
```

---

## 6. e2e-tests：如何进入 AgentX workflow

`.github/workflows/e2e-tests.yml` 生成 matrix JSON 后，按：

```python
x.get('scenario-type') == 'agentic-coding'
```

将单节点 AgentX 行筛选到：

```yaml
test-sweep-agentic:
  uses: ./.github/workflows/benchmark-tmpl.yml
```

它向 template 传递：

```yaml
image: ${{ matrix.config.image }}
model: ${{ matrix.config.model }}
model-prefix: ${{ matrix.config.model-prefix }}
framework: ${{ matrix.config.framework }}
precision: ${{ matrix.config.precision }}
tp: ${{ matrix.config.tp }}
ep: ${{ matrix.config.ep }}
conc: ${{ matrix.config.conc }}
spec-decoding: ${{ matrix.config.spec-decoding }}
kv-offloading: ${{ matrix.config.kv-offloading }}
kv-offload-backend: ${{ matrix.config['kv-offload-backend'].name }}
total-cpu-dram-gb: ${{ matrix.config.total-cpu-dram-gb }}
duration: ${{ matrix.config.duration }}
scenario-type: agentic-coding
```

对于 AgentX，它有意传入：

```yaml
isl: '0'
osl: '0'
max-model-len: '0'
```

原因是 AgentX replay 的输入长度、输出长度、会话轮数、agent/subagent 分叉、工具调用和 trace idle delay 都来自数据集，不能以 fixed-seq-len 的 `ISL/OSL` 表示。

---

## 7. benchmark template：matrix 字段如何成为环境变量

`.github/workflows/benchmark-tmpl.yml` 将 reusable workflow 输入映射为 shell 环境变量。上文的 `CONC=32` HiCache example 会得到近似：

```bash
EXP_NAME=qwen3.5_tp2_conc32_dram-hicache_spec-mtp
MODEL=amd/Qwen3.5-397B-A17B-MXFP4-AttnFP8-V2
MODEL_PREFIX=qwen3.5
IMAGE=lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260915

FRAMEWORK=sglang
PRECISION=fp4
TP=2
PP_SIZE=1
EP_SIZE=1
CONC=32
SPEC_DECODING=mtp

KV_OFFLOADING=dram
KV_OFFLOAD_BACKEND=hicache
TOTAL_CPU_DRAM_GB=<由生成器计算>
DURATION=3600

SCENARIO_TYPE=agentic-coding
SCENARIO_SUBDIR=agentic/
IS_AGENTIC=1
RESULT_DIR=/workspace/results
```

其中最关键的两个路径控制变量为：

```bash
SCENARIO_SUBDIR=agentic/
IS_AGENTIC=1
```

若遗漏 `SCENARIO_SUBDIR=agentic/`，launcher 会按默认值选择 `fixed_seq_len/` 目录，而不是 AgentX recipe。

---

## 8. AMD launcher：如何选择到该 shell 脚本

GitHub workflow 中的实际 launcher 命令是：

```bash
bash ./runners/launch_${RUNNER_NAME%%_*}.sh
```

如果 self-hosted runner 名称为：

```text
mi355x-amds_03
```

则 `${RUNNER_NAME%%_*}` 是 `mi355x-amds`，因此执行：

```bash
bash ./runners/launch_mi355x-amds.sh
```

其单节点路径的核心逻辑：

```bash
SCRIPT_BASE="${EXP_NAME%%_*}_${PRECISION}_mi355x"
SCRIPT_FW="benchmarks/single_node/${SCENARIO_SUBDIR:-fixed_seq_len/}${SCRIPT_BASE}_${FRAMEWORK}${SPEC_SUFFIX}.sh"
```

将 HiCache example 展开：

```bash
EXP_NAME=qwen3.5_tp2_conc32_dram-hicache_spec-mtp
PRECISION=fp4
FRAMEWORK=sglang
SPEC_DECODING=mtp
SCENARIO_SUBDIR=agentic/
```

得到：

```text
${EXP_NAME%%_*} = qwen3.5
SCRIPT_BASE      = qwen3.5_fp4_mi355x
SPEC_SUFFIX      = _mtp

SCRIPT_FW = benchmarks/single_node/agentic/
            qwen3.5_fp4_mi355x_sglang_mtp.sh
```

也就是：

```text
benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
```

launcher 会优先使用 `SCRIPT_FW`。只有文件不存在时才使用兼容用 fallback 名称。

随后它会：

1. 使用 Slurm 申请独占 GPU allocation；
2. 导入或复用 `$IMAGE` 对应的 Enroot squash 镜像；
3. 挂载 GitHub workspace、Hugging Face cache、AIPerf mmap cache；
4. 在容器内运行已选择的 benchmark shell；
5. 结束后释放 Slurm job。

> 注意：官方 workflow/launcher 含 Docker 和 Slurm 清理逻辑，应只在受授权的专用 benchmark runner 上执行，不能直接用于运行了其他服务的共享主机。

---

## 9. AgentX recipe：PR #3118 的实际生效位置

文件：

```text
benchmarks/single_node/agentic/qwen3.5_fp4_mi355x_sglang_mtp.sh
```

### 9.1 前置校验与模型准备

脚本要求：

```bash
MODEL TP CONC EP_SIZE KV_OFFLOADING \
TOTAL_CPU_DRAM_GB RESULT_DIR DURATION
```

随后通过 `hf download` 下载或复用模型，准备 AgentX Weka 数据集和必要 Python 依赖：

```bash
export WEKA_LOADER_OVERRIDE=semianalysis_cc_traces_weka_062126_256k
resolve_trace_source
install_agentic_deps
```

`resolve_trace_source` 最终会形成：

```text
--public-dataset semianalysis_cc_traces_weka_062126_256k
```

### 9.2 HiCache 分支

只要 matrix row 同时满足：

```bash
KV_OFFLOADING=dram
KV_OFFLOAD_BACKEND=hicache
```

则 `require_agentic_kv_offload_backend hicache` 成立，PR #3118 后会构造：

```bash
--enable-hierarchical-cache \
--hicache-ratio 1.5 \
--hicache-write-policy write_through \
--hicache-io-backend kernel \
--hicache-mem-layout page_first
```

如果：

```bash
KV_OFFLOADING=none
```

则不会添加任何 HiCache 参数；该 PR 对该类 job 的服务端命令行无直接影响。

### 9.3 SGLang MTP 服务

脚本会以后台进程启动：

```bash
python3 -m sglang.launch_server \
  --model-path "$MODEL_PATH" \
  --served-model-name "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --trust-remote-code \
  --tp "$TP" \
  --dp 1 \
  --ep-size "$EP_SIZE" \
  --attention-backend aiter \
  --mem-fraction-static 0.80 \
  --kv-cache-dtype fp8_e4m3 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --speculative-algorithm EAGLE \
  --speculative-num-steps 3 \
  --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 4 \
  --enable-metrics \
  --enable-cache-report \
  "${CACHE_ARGS[@]}"
```

字段来源：

| SGLang 参数 | 来源 |
|---|---|
| `--model-path` / `--served-model-name` | YAML `model` 与模型下载结果 |
| `--tp` | YAML `tp` |
| `--ep-size` | YAML `ep` |
| EAGLE/MTP 参数 | `_mtp.sh` 专用 recipe 固定逻辑 |
| HiCache 参数 | YAML KV strategy + PR #3118 默认值 |
| `--enable-metrics` | 供 AIPerf 拉取 Prometheus 指标 |
| `--enable-cache-report` | 输出缓存相关统计 |

脚本同时设置：

```bash
export AIPERF_SERVER_METRICS_URLS="http://localhost:${PORT}/metrics"
export AIPERF_REQUIRED_SERVER_METRIC_PREFIX="sglang:"
```

因此 AIPerf 不仅收集客户端请求指标，也会采集 SGLang `/metrics`；产物验证要求其中出现 `sglang:` 前缀。

---

## 10. AIPerf AgentX profile 命令

服务 ready 后，recipe 调用：

```bash
build_replay_cmd "$RESULT_DIR"
REPLAY_CMD+=" --apply-chat-template"
run_agentic_replay_and_write_outputs "$RESULT_DIR"
```

共享函数 `build_replay_cmd()` 构建的核心命令：

```bash
aiperf profile \
  --scenario inferencex-agentx-mvp \
  --url http://localhost:$PORT \
  --endpoint /v1/chat/completions \
  --endpoint-type chat \
  --streaming \
  --model "$MODEL" \
  --tokenizer "$MODEL" \
  --concurrency "$CONC" \
  --benchmark-duration 3600 \
  --stats-interval 30 \
  --random-seed 42 \
  --trajectory-start-min-ratio 0.25 \
  --trajectory-start-max-ratio 0.75 \
  --warmup-requests-per-lane 10 \
  --trace-idle-gap-cap-seconds 300 \
  --warmup-grace-period 1800 \
  --use-server-token-count \
  --no-gpu-telemetry \
  --tokenizer-trust-remote-code \
  --num-dataset-entries 393 \
  --slice-duration 1.0 \
  --server-metrics http://localhost:$PORT/metrics \
  --output-artifact-dir "$RESULT_DIR/aiperf_artifacts" \
  --public-dataset semianalysis_cc_traces_weka_062126_256k \
  --apply-chat-template
```

实际命令还会受环境变量影响。例如：

```bash
AIPERF_EXPERIMENTAL_FAST=1
```

会把 profile duration 从 `3600` 降为 `1200` 秒，并将：

```text
--warmup-requests-per-lane 10
```

降为：

```text
--warmup-requests-per-lane 1
```

PR #3118 不改变这些 AIPerf 参数。

---

## 11. 结果产出、验证与上传

`run_agentic_replay_and_write_outputs()` 会：

1. 保存最终命令到 `benchmark_command.txt`；
2. 执行 AIPerf，并写入 `benchmark.log`；
3. 将 AIPerf artifacts 写至：
   ```text
   $RESULT_DIR/aiperf_artifacts
   ```
4. 生成 AgentX 聚合结果 JSON；
5. 可选采集 GPU power 数据；
6. 验证服务端 metrics 工件和 `sglang:` 指标前缀。

之后 GitHub workflow 运行：

```bash
python3 -m utils.agentic.validation.validate_agentic_result \
  results/aiperf_artifacts \
  --failed-request-threshold 0.10
```

因此有效的 PR #3118 AgentX 结果至少要求：

```text
- SGLang 服务成功就绪；
- AIPerf AgentX profile 完成；
- AgentX artifacts 完整；
- 失败请求比例符合阈值；
- SGLang server metrics 被采集；
- metrics 中有 sglang: 前缀；
- AgentX artifact validation 通过。
```

---

## 12. 影响范围与非影响范围

### 受影响

```text
Qwen3.5-397B-A17B MXFP4-AttnFP8-V2
AMD MI355X 专用 cluster runner
SGLang MTP / EAGLE
单节点 AgentX trace replay
KV offloading=dram、backend=hicache 的运行点
```

### 不受直接影响

```text
fixed-seq-len qwen3.5-fp4-mi355x-sglang-mtp 配方
AgentX 中 kv-offloading=none 的运行点
AIPerf inferencex-agentx-mvp scenario 规则
Weka AgentX 数据集选择
matrix generator 的 AgentX 展开逻辑
e2e-tests 的 AgentX 分流逻辑
launch_mi355x-amds.sh 的路径选择与 Slurm 调度逻辑
当前独立的 vLLM / Mooncake 服务
```

## 13. 一句话总结

PR #3118 使 `qwen3.5-fp4-mi355x-sglang-agentic-mtp` 的 **HiCache DRAM AgentX 分支** 使用更新后的 `20260915` SGLang ROCm image，并在没有外部显式覆盖时将 HiCache 默认实现从：

```text
direct + page_first_direct
```

切换为：

```text
kernel + page_first
```

随后仍沿用原有的 MI355X Slurm、SGLang EAGLE/MTP、AIPerf `inferencex-agentx-mvp` replay、artifact 验证和 GitHub artifact 上传链路。
