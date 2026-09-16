# InferenceX AgentX 官方 AIPerf 参数组合分析

本文整理 InferenceX workflow 对 AgentX MVP 场景实际构造的
`aiperf profile --scenario inferencex-agentx-mvp` 参数、官方配置矩阵规模及例外项。

> 分析日期：2026-09-15  
> InferenceX 提交：`57bec457c08e5223d7143f1e533ae7abf046ebd5`  
> 提交说明：`fix: repair TileRT startup and eval dispatch (#3067)`  
> 远端主机：`root@10.211.11.28`  
> 容器：`agextX_test_lium3`  
> Python：`3.12.14`  
> AIPerf：`0.12.0`  
> Python 路径：`/stortest/lium_space/agentX/agentx-harness/.venv312/bin/python`  
> AIPerf 路径：`/stortest/lium_space/agentX/agentx-harness/.venv312/bin/aiperf`

本次仅执行了版本检查、配置解析和矩阵生成，没有安装依赖、修改远端文件、启动推理服务或运行 GPU benchmark。

## 1. 结论

“官方参数组合有几组”有两种不同口径：

| 口径 | 数量 | 含义 |
| --- | ---: | --- |
| 最新提交 `HEAD^ → HEAD` 的实际 AgentX workflow 计划 | 0 | 该提交只选择了 TileRT `fixed-seq-len` 测试，没有选择 AgentX |
| 顶层 AgentX recipe | 89 | NVIDIA 与 AMD master 配置中包含 `agentic-coding` 的顶层 recipe 数 |
| 配置声明的 AgentX matrix job / AIPerf 命令 | 805 | 完整 `agentic-coding full-sweep` 的理论总量 |
| 官方生成器当前可成功验证的命令 | 797 | 排除缺少 runner 元数据的 8 条 `cluster:b300-nv` 配置 |
| 单节点命令 | 541 | 包括当前被 runner 元数据阻塞的 8 条 |
| 多节点命令 | 264 | 多节点每个 `conc-list` 元素分别调用一次 AIPerf |
| 去重后的“模型 × 并发”组合 | 281 | 忽略框架、硬件和部署拓扑的重复后得到 |

`run-sweep.yml` 本身没有硬编码固定的 AgentX 组数。每次 workflow 都会根据
`perf-changelog.yaml`、base ref、head ref 和 PR labels 运行 `infx.matrix.plan`，只选择该次变更涉及的子矩阵。因此，某一次 workflow 的 AgentX 数量可能为 0，也可能只是 805 条全量配置中的一部分。

## 2. 权威实现位置

以下链接以 InferenceX 仓库根目录为基准：

- Workflow 动态计划：`.github/workflows/run-sweep.yml`
- 单节点 workflow：`.github/workflows/benchmark-tmpl.yml`
- 多节点 workflow：`.github/workflows/benchmark-multinode-tmpl.yml`
- AIPerf 命令拼装：`benchmarks/benchmark_lib.sh` 中的 `build_replay_cmd()`
- NVIDIA master 配置：`configs/nvidia-master.yaml`
- AMD master 配置：`configs/amd-master.yaml`
- Runner 元数据：`configs/runners.yaml`
- 矩阵生成器：`infx/matrix/generate.py`
- AgentX 场景定义：`utils/aiperf/src/aiperf/common/scenario/inferencex_agentx_mvp.py`

当说明文档与实现不一致时，应以 workflow、配置、launcher 和 `benchmark_lib.sh` 为准。某次真正执行的最终命令会写入：

```text
<RESULT_DIR>/benchmark_command.txt
```

对应执行日志位于：

```text
<RESULT_DIR>/benchmark.log
```

## 3. 一条完整的 canonical 基线命令

以下示例使用单节点默认端口、DeepSeek V4、并发 1 和单节点默认结果目录。所有静态占位符均已填充：

```bash
/stortest/lium_space/agentX/agentx-harness/.venv312/bin/aiperf profile \
  --scenario inferencex-agentx-mvp \
  --url http://localhost:8888 \
  --endpoint /v1/chat/completions \
  --endpoint-type chat \
  --streaming \
  --model deepseek-ai/DeepSeek-V4-Pro \
  --tokenizer deepseek-ai/DeepSeek-V4-Pro \
  --concurrency 1 \
  --benchmark-duration 3600 \
  --stats-interval 30 \
  --random-seed 42 \
  --failed-request-threshold 0.10 \
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
  --output-artifact-dir /workspace/results/aiperf_artifacts \
  --public-dataset semianalysis_cc_traces_weka_062126
```

这是一条完整的默认实例，不代表所有 runner 的 URL 和结果目录都固定为这些值：

- `benchmark_lib.sh` 的默认端口是 `8888`。
- 某些 runner 会选择动态空闲端口，或显式设置 `AIPERF_SERVER_URL`。
- 单节点 workflow 的默认 `RESULT_DIR` 是 `/workspace/results`。
- 多节点通常为每个并发值写入 `<BASE_RESULT_DIR>/conc_<N>/aiperf_artifacts`。
- 某次实跑的 URL 和输出目录应以该次 `benchmark_command.txt` 为准。

## 4. `build_replay_cmd()` 的固定参数

Canonical workflow 对每条命令使用以下基础值：

| 参数 | 实际值 | 来源 |
| --- | --- | --- |
| `--scenario` | `inferencex-agentx-mvp` | `build_replay_cmd()` |
| `--endpoint` | `/v1/chat/completions` | `build_replay_cmd()` |
| `--endpoint-type` | `chat` | `build_replay_cmd()` |
| `--streaming` | 启用 | `build_replay_cmd()` |
| `--benchmark-duration` | `3600` 秒 | 矩阵中的 AgentX 默认 duration |
| `--stats-interval` | `30` 秒 | `build_replay_cmd()` |
| `--random-seed` | `42` | `build_replay_cmd()` |
| `--failed-request-threshold` | 默认 `0.10` | `AIPERF_LIVE_FAILED_REQUEST_THRESHOLD` |
| `--trajectory-start-min-ratio` | `0.25` | InferenceX 显式覆盖场景默认值 |
| `--trajectory-start-max-ratio` | `0.75` | InferenceX 显式覆盖场景默认值 |
| `--warmup-requests-per-lane` | `10` | Canonical 模式默认值 |
| `--trace-idle-gap-cap-seconds` | `300` | `AIPERF_TRACE_IDLE_GAP_CAP_SECONDS` |
| `--warmup-grace-period` | 默认 `1800` 秒 | recipe 可覆盖为 `3600` |
| `--use-server-token-count` | 启用 | `build_replay_cmd()` |
| `--no-gpu-telemetry` | 启用 | GPU telemetry 由 InferenceX 外部采集 |
| `--tokenizer-trust-remote-code` | 启用 | `build_replay_cmd()` |
| `--num-dataset-entries` | `393` | `build_replay_cmd()` |
| `--slice-duration` | `1.0` 秒 | `build_replay_cmd()` |

常规 workflow 显式传入 `max-model-len: 0`。`build_replay_cmd()` 只有在
`MAX_MODEL_LEN` 非空且不等于 `0` 时才追加 `--max-context-length`，因此这些常规 AgentX 命令中没有该参数。

### 场景隐式锁定的行为

以下行为由 `inferencex-agentx-mvp` 场景执行，不一定作为显式 CLI 参数出现在 `benchmark_command.txt` 中：

- timing mode 为 `AGENTIC_REPLAY`；
- 必须 streaming；
- 必须 `ignore_eos=true`；
- 禁止忽略 trace delay；
- 禁止客户端输入截断；
- benchmark duration 不得低于 900 秒；
- 全系统 idle gap cap 为 10 秒；
- 禁止设置 inter-turn delay cap；
- cache bust 固定作用于 first-turn prefix；
- profile metric coverage ratio 至少为 `0.95`。

这里的 10 秒系统 idle gap cap 与显式的
`--trace-idle-gap-cap-seconds 300` 不是同一限制：前者作用于整个系统，后者作用于单棵 trajectory tree。

## 5. AgentX Fast 模式

PR 带有 `agentx-fast` label 时，workflow 设置：

```bash
AIPERF_EXPERIMENTAL_FAST=1
```

它不改变矩阵中的模型、部署和并发组合，只修改两个客户端参数：

```text
--benchmark-duration 1200
--warmup-requests-per-lane 1
```

其余基础参数保持不变。

## 6. 全量模型与并发参数组合

下表是 805 条配置声明的紧凑表示。“命令数”保留同一模型/并发在不同硬件、框架和部署拓扑中的重复执行；“唯一并发数”用于计算 281 个去重后的“模型 × 并发”组合。

数据集缩写：

- `F`：`semianalysis_cc_traces_weka_062126`
- `K`：`semianalysis_cc_traces_weka_062126_256k`
- `S`：`semianalysis_cc_traces_weka_with_subagents_256k`

| `--tokenizer` / HF 模型 | 命令数 | 唯一并发数 | `--concurrency` 值 | 数据集 |
| --- | ---: | ---: | --- | --- |
| `MiniMaxAI/MiniMax-M3-MXFP8` | 30 | 12 | 1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 18 | F |
| `Qwen/Qwen3.5-397B-A17B-FP8` | 153[^b300] | 33 | 1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 28, 32, 34, 36, 38, 40, 42, 44, 48, 52, 56, 60, 62, 64, 66, 68, 70, 72, 76, 80 | K；H100/H200 配置使用 S |
| `Qwen/Qwen3.8-Flash-Next-FP8` | 10 | 5 | 1, 4, 8, 12, 16 | S |
| `RadixArk/Qwen3.8-Flash-Next-NVFP4` | 5 | 5 | 1, 4, 8, 12, 16 | K |
| `amd/GLM-5.2-MXFP4` | 17 | 6 | 1, 2, 4, 8, 10, 12 | F |
| `amd/MiniMax-M3-MXFP4` | 15 | 12 | 1, 2, 4, 5, 8, 10, 12, 15, 20, 24, 28, 32 | F |
| `amd/Qwen3.5-397B-A17B-MXFP4` | 24 | 13 | 1, 4, 8, 12, 16, 20, 24, 28, 32, 40, 48, 56, 64 | K |
| `deepseek-ai/DeepSeek-V4-Pro` | 136 | 39 | 1, 2, 4, 6, 8, 12, 14, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 68, 72, 76, 80, 96, 128, 160, 192, 196, 256, 384, 388, 512, 576, 736, 1024, 1152, 2626, 4096 | F |
| `deepseek-ai/DeepSeek-V4-Pro-0813` | 26 | 19 | 1, 2, 3, 4, 5, 8, 10, 16, 32, 48, 64, 96, 128, 160, 256, 480, 960, 1440, 1920 | F |
| `deepseek-ai/DeepSeek-V4.1-Flash` | 51 | 8 | 1, 2, 4, 8, 16, 32, 64, 128 | 通常 K；MI355x 的 6 条使用 F |
| `moonshotai/Kimi-K3` | 116 | 33 | 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 40, 44, 48, 52, 56, 64, 70, 72, 96, 128, 192, 256, 384 | F |
| `nvidia/GLM-5.2-NVFP4` | 43 | 20 | 1, 2, 4, 8, 10, 12, 16, 20, 24, 28, 30, 32, 40, 45, 48, 60, 128, 192, 227, 260 | F |
| `nvidia/MiniMax-M3-NVFP4` | 68 | 19 | 1, 5, 8, 10, 15, 16, 20, 24, 25, 30, 32, 34, 35, 36, 38, 40, 45, 48, 120 | F |
| `nvidia/Qwen3.5-397B-A17B-NVFP4` | 83[^b300] | 30 | 1, 4, 7, 8, 12, 14, 16, 18, 20, 22, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 62, 64, 66, 68, 70, 72, 96, 128, 565, 704 | K |
| `nvidia/Qwen3.5-397B-A17B-NVFP4-V2` | 17 | 17 | 1, 2, 4, 7, 8, 12, 20, 22, 24, 32, 40, 44, 48, 52, 96, 565, 704 | K |
| `zai-org/GLM-5.1-FP8` | 1 | 1 | 1 | F |
| `zai-org/GLM-5.2-FP8` | 10 | 9 | 1, 2, 3, 4, 5, 6, 8, 12, 16 | F |
| **合计** | **805** | **281** | — | — |

[^b300]: 两个带标记模型各包含 4 条 `cluster:b300-nv` Power A/B 配置。这 8 条存在于 master 配置中，但当前 `configs/runners.yaml` 缺少 `cluster:b300-nv` 的 label 与 `available-cpu-dram-mib` 等硬件元数据，因而完整生成器会在验证阶段停止。排除这 8 条后，可成功生成 797 条。

## 7. Weka 数据集分布

考虑 launcher 和 recipe 的显式覆盖后，805 条命令的数据集分布为：

| `--public-dataset` | 命令数 |
| --- | ---: |
| `semianalysis_cc_traces_weka_062126` | 468 |
| `semianalysis_cc_traces_weka_062126_256k` | 287 |
| `semianalysis_cc_traces_weka_with_subagents_256k` | 50 |
| **合计** | **805** |

默认选择规则为：

```text
dsv4*、glm5.2*、minimaxm3*、kimik3*
  -> semianalysis_cc_traces_weka_062126

其他模型前缀
  -> semianalysis_cc_traces_weka_062126_256k
```

特殊覆盖包括：

- DeepSeek V4.1 Flash 的 MI355x 配置使用完整 corpus；
- GLM 5.1 TileRT AgentX 配置使用完整 corpus；
- Qwen 3.5 FP8 的 H100/H200 配置使用 `with_subagents_256k`；
- Qwen 3.8 Flash Next FP8 的 H100/H200 配置使用 `with_subagents_256k`。

## 8. Recipe 和框架追加的实跑参数

基础命令之外，部分配置还会追加或覆盖以下参数。

### 8.1 Dynamo 会话路由

当前全量矩阵中有 216 条 Dynamo 命令：

| Framework | 命令数 |
| --- | ---: |
| `dynamo-sglang` | 66 |
| `dynamo-trt` | 25 |
| `dynamo-vllm` | 125 |

在没有采用 `X-Dynamo-Session-ID` 请求头时，它们追加：

```bash
--use-dynamo-conv-aware-routing \
--dynamo-session-timeout-seconds 3600
```

### 8.2 Served model alias

`--tokenizer` 始终使用完整 HF 模型 ID；`--model` 则使用
`${SERVED_MODEL_NAME:-$MODEL}`。以下部署具有重要别名：

| `--tokenizer` | `--model` 实际值 |
| --- | --- |
| `deepseek-ai/DeepSeek-V4-Pro` | `DeepSeek-V4-Pro` |
| `nvidia/GLM-5.2-NVFP4` | `GLM-5.2-NVFP4` |
| `nvidia/Qwen3.5-397B-A17B-NVFP4-V2` | `Qwen3.5-397B-A17B-NVFP4-V2` |
| `nvidia/MiniMax-M3-NVFP4` | `nvidia/MiniMax-M3-NVFP4` |

### 8.3 失败阈值

26 条 Kimi-K3 多节点命令使用：

```bash
--failed-request-threshold 0.25
```

其余命令默认使用 `0.10`。

### 8.4 Extra inputs

16 条 MiniMax-M3 多节点命令追加：

```bash
--extra-inputs thinking:true
```

11 条 Qwen 3.5 多节点命令追加：

```bash
--extra-inputs '{"chat_template_kwargs":{"enable_thinking":true},"presence_penalty":0,"temperature":0.6,"top_k":20,"top_p":0.95}'
```

### 8.5 Warmup grace period

14 条命令将默认的 1800 秒改为：

```bash
--warmup-grace-period 3600
```

其中包括 10 条多节点配置，以及 4 条 MI355x DeepSeek V4 高并发配置。

### 8.6 Server metrics

5 条配置追加：

```bash
--server-metrics http://localhost:8000/metrics
```

## 9. 多节点并发如何换算为命令数

矩阵中的多节点 `conc` 字段是列表。多节点 launcher 会逐项执行：

```text
CONC_LIST=[C1, C2, ...]
  -> conc_C1/build_replay_cmd()
  -> conc_C2/build_replay_cmd()
  -> ...
```

每个并发值都会：

1. 设置独立的 `CONC`；
2. 构造独立的 `RESULT_FILENAME`；
3. 使用独立的 `conc_<N>` 结果目录；
4. 调用一次 `build_replay_cmd()`；
5. 调用一次 `run_agentic_replay_and_write_outputs()`。

当前矩阵生成器将多节点 AgentX concurrency 分批时，每个批次只包含一个并发值，因此 264 个多节点 matrix job 恰好对应 264 条 AIPerf 命令。

## 10. 在远端 Linux 中复现统计

以下命令只生成矩阵，不会启动推理服务器或 benchmark：

```bash
docker exec \
  --workdir /stortest/lium_space/agentX/InferenceX \
  agextX_test_lium3 \
  /stortest/lium_space/agentX/agentx-harness/.venv312/bin/python \
  -m infx.matrix.generate full-sweep \
  --config-files configs/nvidia-master.yaml configs/amd-master.yaml \
  --runner-config configs/runners.yaml \
  --scenario-type agentic-coding \
  --no-evals
```

在当前提交中，该完整命令会因 `cluster:b300-nv` 元数据缺失而停止。为了只读验证其余 797 条，可显式限定已有 runner：

```bash
docker exec \
  --workdir /stortest/lium_space/agentX/InferenceX \
  agextX_test_lium3 \
  /stortest/lium_space/agentX/agentx-harness/.venv312/bin/python \
  -m infx.matrix.generate full-sweep \
  --config-files configs/nvidia-master.yaml configs/amd-master.yaml \
  --runner-config configs/runners.yaml \
  --scenario-type agentic-coding \
  --no-evals \
  --runner-type \
    cluster:b200-nscale \
    cluster:b300-dsxe \
    cluster:gb200-nv \
    cluster:gb300-nv \
    cluster:h100-dgxc \
    cluster:h200-dgxc \
    cluster:mi300x-amd \
    cluster:mi325x-amds \
    cluster:mi355x-amds
```

最新提交对应的动态 workflow 计划可用以下只读命令生成：

```bash
docker exec \
  --workdir /stortest/lium_space/agentX/InferenceX \
  agextX_test_lium3 \
  /stortest/lium_space/agentX/agentx-harness/.venv312/bin/python \
  -m infx.matrix.plan \
  --base-ref 8360aac563f6d99c00f1f61004ccf3349c17a52c \
  --head-ref 57bec457c08e5223d7143f1e533ae7abf046ebd5 \
  --changelog-file perf-changelog.yaml
```

该次输出的 AgentX 子矩阵为空；这只说明该提交没有选择 AgentX，不代表全量 master 配置中没有 AgentX recipe。

## 11. 已知边界

- 805 是提交 `57bec457...` 中 NVIDIA 与 AMD master 配置的全量声明数，不是每次 PR workflow 都会执行的固定数量。
- 797 是当前生成器能够通过 runner 元数据验证的数量。
- 生成矩阵只能证明配置可展开，不能证明模型服务能够启动或 benchmark 能够通过。
- URL、端口、服务模型别名、server metrics URL 和结果目录可能由具体 runner/recipe 在运行时决定。
- 若需要审计某次真实执行，应优先查看该次 artifact 中的 `benchmark_command.txt` 和 `benchmark.log`，而不是仅根据本报告推断。
- 本报告没有启动任何 GPU 工作负载。



