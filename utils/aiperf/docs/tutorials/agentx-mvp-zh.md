<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# InferenceX AgentX MVP 基准测试

> **状态：开发中的 MVP。** 这是 SemiAnalysis InferenceX AgentX-MVP 基准测试的
> 第一个 AIPerf 实现。随着规范逐渐稳定，这里描述的场景、场景锁定的规则以及
> 输出字段都可能发生变化。

本页将引导你在 AIPerf 中运行 **AgentX MVP** 基准测试。它面向此前没有接触过
该场景的用户——在简短介绍之后，你将获得一条可直接复制粘贴的命令，随后还会
了解该命令会做什么以及为何如此设计。

> **想了解底层机制而不是操作指南？** 请参阅
> [SemiAnalysis AgentX：基准测试如何工作（FAQ）](../benchmark-modes/semianalysis-agentx-faq.md)
> ——这是一份面向服务引擎工程师的指南，介绍该基准测试会给服务器施加怎样的负载、
> 如何复现 KV Cache 结构、`t*`/预热/缓存破坏分别起什么作用，以及怎样的运行才有效。

---

## 什么是 AgentX MVP？

AgentX MVP 是 SemiAnalysis 在 InferenceX 工作中提出的一项多轮智能体编码基准测试。
其思路是：不再使用合成的单轮提示词测量推理服务器，而是使用真实的*编码 Agent
会话*——包含 KV Cache 复用和轮次间思考时间的长对话——来进行测量。会话来自
Callan Fox 采集的公开 **Weka 智能体编码追踪语料库**
（[kv-cache-tester](https://github.com/callanjfox/kv-cache-tester)）；该语料库逐字节记录
真实的 Claude Code 会话。AgentX MVP 使用当前**包含子 Agent** 的语料库，其中父编码
会话可以生成辅助会话，这些辅助会话会在父会话恢复之前重新汇合；有关源格式和
SPAWN/JOIN 映射，请参阅 [Weka 教程](weka-trace.md)。

从本质上讲，AgentX MVP 是建立在这些追踪之上的一套*配方*：它规定了一组固定的
重放规则，使两个不同团队在两台不同服务器上运行后能够得到可比较的结果。例如：
“保留每条追踪原始的请求时序”、“整个系统的空闲时间不得超过 10 秒”、“必须允许
服务器生成完整响应（不得提前停止）”、“测量前预热缓存”等。

AIPerf 将所有这些规则封装到一个 CLI 标志中：
`--scenario inferencex-agentx-mvp`。传入该标志后，AIPerf 会锁定相关设置并拒绝
存在冲突的标志。它还会在 JSON 输出（单次运行和聚合输出）中写入
`submission_valid` 字段，以便快速检查该次运行是否遵循了锁定规则。

---

<a id="quick-start"></a>

## 快速开始

你需要：

- 一个正在运行且可访问的 **OpenAI 兼容推理服务器**。`--url` 可以接收不带协议的
  `host:port`（AIPerf 会自动添加 `http://`），也可以接收包含协议的完整 URL；
  端点路径（例如 `/v1/chat/completions`）会根据 `--endpoint-type` 自动追加。
  如果服务器要求身份验证，请添加 `--api-key <key>`（以 `Bearer` Token 发送）。
- 已安装 AIPerf：`pip install aiperf`（也可以用 `uvx aiperf` 临时运行）。
- 能够根据 `--model` 解析模型的 **Hugging Face tokenizer**：Weka 加载器会通过它
  重建每条提示词。对于受限仓库，请导出 `HF_TOKEN`；如果 `--model` 不是可解析的
  Hugging Face 仓库名称（例如它是本地服务器别名或私有构建），请显式传入
  `--tokenizer <hf-repo-or-local-path>`。请参阅
  [Tokenizer 自动检测](../reference/tokenizer-auto-detection.md)。

追踪语料库会自动从 Hugging Face 获取
（`semianalysisai/cc-traces-weka-062126`，公开且无需身份验证），无需手动克隆；
Hugging Face 会在本地缓存它。
下面命令中的 `semianalysis_cc_traces_weka_with_subagents` 是一个跟踪当前
“包含子 Agent”语料库的*滚动*别名（目前指向 `062126` 版本）；按日期固定的
`semianalysis_cc_traces_weka_062126` 则会锁定语料库以确保可复现性。对于任何
准备比较或提交的运行，请优先使用按日期固定的别名——滚动别名会在新语料库发布时
向前移动，而基于不同版本语料库的两次运行不可比较。每个语料库还有一个 `_256k`
变体（例如 `semianalysis_cc_traces_weka_with_subagents_256k`），它会预先剔除
输入与输出之和超过 256k Token 的单个请求；当服务器的上下文窗口约为 256k 时，
请选择该变体（参见
[AgentX FAQ §3](../benchmark-modes/semianalysis-agentx-faq.md#3-how-realistic-are-the-prompts-and-token-counts)）。

然后运行：

```bash
aiperf profile \
    --scenario inferencex-agentx-mvp \
    --url localhost:8000 \
    --model deepseek-ai/DeepSeek-V4-Pro \
    --max-context-length 128000 \
    --endpoint-type chat \
    --public-dataset semianalysis_cc_traces_weka_with_subagents \
    --concurrency 32 \
    --use-server-token-count \
    --streaming \
    --extra-inputs ignore_eos:true \
    --cache-bust first_turn_prefix \
    --system-idle-gap-cap-seconds 10 \
    --trajectory-start-min-ratio 0.0 \
    --trajectory-start-max-ratio 1.0 \
    --benchmark-duration 1800 \
    --random-seed 20260707 \
    --ui simple
```

该命令混合了三类标志——了解各自所属的类别，就能知道哪些必须设置、哪些可以调优，
以及哪些不应修改：

**由你选择的标志**——你的服务器、模型和负载：

- **`--scenario inferencex-agentx-mvp`** 是唯一专属于本基准测试的标志，其他标志
  都是普通 AIPerf 标志。
- `--model` 就是你正在提供服务的模型——无需与追踪语料库中记录的模型名称一致；
  AIPerf 会重写每个追踪请求的 `model` 字段。有关多个 `--model` 值如何映射，
  请参阅 Weka 教程中的
  [逐追踪模型重写](weka-trace.md#per-trace-model-rewriting)。
- **`--max-context-length 128000`** 会在重放前剔除峰值**输入 + 输出**（提示词加上
  该轮请求的 `max_tokens`）超过 128k Token 的追踪。过滤发生在应用
  `--num-dataset-entries` 之前，因此该上限会保留前 N 条*符合条件的*追踪。
  应将其设置为服务器接受的最大上下文，也就是模型的原生最大值，因为 AgentX MVP
  预期服务器按默认最大长度运行。由于该值反映服务器的实际容量，运行只会重放
  服务器能够处理的追踪——正因如此，场景允许使用该参数，同时拒绝任意设置的
  客户端上限 `--synthesis-max-isl`。该标志是可选的，场景不会检查它：若省略，
  客户端不会执行过滤，超长追踪将在服务器端失败，并计入 1% 上下文溢出阈值。
- **`--concurrency`** 设置整个运行期间保持活跃的会话树数量，也就是持续负载。
  在 `--scenario` 下，它必须是单个整数；逗号分隔的扫描列表会被拒绝。当并发度
  超过已加载且互不相同的追踪数量时，追踪池会循环复用——场景锁定了
  `--cache-bust first_turn_prefix`，处于启用状态的缓存破坏目标满足循环复用的
  显式启用条件。如果没有缓存破坏功能，循环复用还需要 `--allow-dataset-wrap`。
- `--url`、`--endpoint-type chat`、`--use-server-token-count` 和 `--ui` 构成
  这一组中的其余标志（下文将解释
  [`--use-server-token-count`](#tokenization-options---apply-chat-template-and---use-server-token-count)）。

**场景强制锁定的标志**——`--streaming`、`--extra-inputs ignore_eos:true`、
`--cache-bust first_turn_prefix` 和 `--system-idle-gap-cap-seconds 10`。你可以全部
省略（在 `--scenario` 下，AIPerf 会准确填充这些值），但显式写出它们可以让命令
自身清楚记录实际运行内容。如果为其中某个标志传入*冲突*值，AIPerf 会在启动前
报错，而不是悄悄生成无效结果。该场景禁止使用 `--inter-turn-delay-cap-seconds`；
`--trace-idle-gap-cap-seconds` 仍是可选的 CLI 控制项，默认不设置。

**场景仅提供默认值的标志**——省略时会自动填充，但显式值会被*静默*接受，既不会
报错，也不会改变 `submission_valid` 标记：

- **`--trajectory-start-min-ratio 0.0` / `--trajectory-start-max-ratio 1.0`**
  设置每条通道采样起始时刻 `t*` 的窗口。场景从不校验这些值——即使覆盖它们，
  运行仍会在重放实质上不同的负载时标记 `submission_valid: true`。任何准备参与
  比较的运行都应将它们保持为 `0.0`/`1.0`。
- **`--benchmark-duration 1800`**（30 分钟）是场景默认值；强制最小值为
  900 秒（15 分钟），AIPerf 会拒绝更短的值。更长的值则会直接接受。
- **`--random-seed`**：若省略，AIPerf 会选择并记录一个新的随机种子；传入你自己
  的任意整数可以从一开始就让运行可复现。固定种子还会参与重建数据集磁盘缓存的
  键生成：未指定种子的场景运行每次都会抽取新种子，并在每次运行时重新承担耗时
  数分钟的完整语料库重建成本（参见
  [AgentX FAQ §8](../benchmark-modes/semianalysis-agentx-faq.md#8-running-the-benchmark-and-why-the-first-run-is-slow)）。

**可选附加项**（未包含在上述命令中）：

- **`--num-profile-runs N`** 会重复运行基准测试 N 次，并新增一个包含跨运行置信区间
  的聚合文件。有关 `submission_valid` 标记所在的位置，请参阅下文
  [读取结果](#reading-the-result-submission_valid)。
- 默认加载完整语料库（393 条追踪）；在 `--max-context-length` 过滤之后，
  `--num-dataset-entries N` 会保留前 N 条*符合条件的*追踪（先过滤、后限量）。
  缩减语料库会改变重放的工作负载，且场景锁无法检测到这一点——运行仍会标记
  `submission_valid: true`——因此只能将它用于冒烟测试，绝不能用于准备与其他
  AgentX MVP 结果比较的运行。如果冒烟测试的并发度大于 N，锁定的缓存破坏目标
  会允许追踪池循环复用；若关闭缓存破坏，则需要使用 `--allow-dataset-wrap` 或
  降低并发度。

无需修改调度或预热选项：场景会选择 agentic-replay 调度器并自动填入上述值。
不要传入 `--fixed-schedule`、`--request-rate` 或 `--ignore-trace-delays`——它们与
锁定的调度模式冲突，会导致运行报错。

<a id="tokenization-options---apply-chat-template-and---use-server-token-count"></a>

### 分词选项：`--apply-chat-template` 和 `--use-server-token-count`

**可选：`--apply-chat-template`。** 关闭该标志（默认）时，报告的 ISL 是对线上载荷
裸文本编码得到的结果。启用后，记录处理器会通过 tokenizer 自身的
`apply_chat_template` 重新对每个请求的线上载荷进行分词，因此报告的 ISL 会统计
完整的线上 Token 总数——包括聊天模板包装和缓存破坏标记——可直接与服务器的
`usage.prompt_tokens` 比较。完整说明请参阅
[输入序列长度（ISL）分词](../reference/isl-tokenization.md)。

**`--use-server-token-count`（已包含在快速开始命令中；用于修复 OSL 不匹配）。**
如果不使用该标志，AIPerf 会用模型的本地 tokenizer 重新对服务器响应进行分词，
以计算输出序列长度（OSL）。如果该 tokenizer 与服务器使用的 tokenizer 不一致——
例如版本、BPE 合并规则或聊天模板不同——报告的 OSL 就会偏离服务器实际发出的
Token 数，即使 `ignore_eos=true` 已锁定且服务器确实发出了 `max_tokens`，单次运行
控制台仍会显示“Output Sequence Length Mismatch Warning”面板。启用该标志后，
AIPerf 会信任服务器的 `usage.completion_tokens`（以及 `usage.prompt_tokens`），
不匹配问题也会消失。

### 通过路由器进行基准测试（多个副本）

应让路由具备会话感知能力，否则跨副本分散请求会破坏本基准测试旨在测量的前缀缓存
复用。服务器端：SGLang Model Gateway 使用 `--policy cache_aware`（或
`--policy manual`），Dynamo 使用 `--router-mode kv`。客户端方面，AIPerf 会维护
稳定的逐会话 ID，并通过环境变量将其暴露为附加的会话亲和性请求头：
`AIPERF_HTTP_X_SMG_ROUTING_KEY_FROM_CORRELATION_ID=1`（SGLang `manual`）、
`AIPERF_HTTP_X_DYNAMO_SESSION_ID_FROM_CORRELATION_ID=1`（Dynamo 会话亲和性），
或 `AIPERF_HTTP_X_SESSION_ID_FROM_CORRELATION_ID=1`（其他任意路由器）。有关详情和
完整启动命令，请参阅
[AgentX FAQ §9](../benchmark-modes/semianalysis-agentx-faq.md#9-multi-replica-serving-conversation-aware-routing-sglang-dynamo)。

### 运行时应看到什么

一次运行会经历四个外部可见阶段；了解这些阶段可以避免误杀健康的运行（按 Ctrl+C
会将其标记为 `run_cancelled` / 无效）：

1. **数据集配置。** 首次运行时，这一步包括从 Hugging Face 下载语料库，以及执行
   CPU 密集型重建，将每条追踪转换为经过分词且带缓存结构的数据集——期间看起来
   可能会有数分钟没有动静；如果超过默认配置超时，请参阅
   [故障排查](#troubleshooting)。后续使用相同语料库、设置以及固定
   `--random-seed` 的运行，可以在数秒内从磁盘缓存恢复数据集。
2. **预热。** 每条通道会重放一个带深层前缀的轮次，为服务器的 KV Cache 预热。
   对于历史很深的真实编码追踪，这本身就会占用相当可观的运行时间。
3. **性能测量。** 按 `--benchmark-duration` 运行（默认 1800 秒）；使用
   `--ui simple` 时，各阶段进度和请求计数会随流量处理不断更新。
4. **排空并导出。** 在宽限期内完成在途请求，然后控制台会打印指标表和准确的
   产物路径（除非设置 `--artifact-dir`，否则位于 `./artifacts/` 下）。

因此，从头到尾的一次冷启动运行会明显超过 duration 标志所表示的 30 分钟——
重建、预热和排空都会额外占用时间。

---

## `--scenario inferencex-agentx-mvp` 会为你锁定哪些设置

传入场景标志后，AIPerf 会在运行开始前检查（并在某些情况下设置）下列设置。如果
其中任何设置与你请求的参数冲突，运行会立即报错，并用清晰的消息指出冲突标志。

| 锁定设置 | 含义 | 重要原因 |
|---|---|---|
| `timing_mode` 为 `agentic_replay` | 使用多轮 agentic-replay 调度器（由场景锁定，不是用户可选标志） | 这是 AgentX MVP 所要求的调度规则（预热 → 稳态，即[性能测量阶段](#profiling-phase-faithful-replay-recycle-global-idle-guard)，并由采样器驱动追踪循环复用、按会话树控制并发）。 |
| `extra_inputs.ignore_eos = true` | 指示服务器忽略流结束 Token，并生成完整的请求长度 | 否则模型会提前停止，而你测量的是模型决定何时停止，不是服务器性能。 |
| 启用 `--streaming` | 响应以逐 Token 方式流式传输（未设置时自动启用；显式使用 `--no-streaming` 会报错） | TTFT、ITL 等逐 Token 延迟指标是本基准测试的核心，需要流式响应。 |
| 重放延迟采用结束到开始语义（始终启用，无标志） | 每轮重放延迟是上一响应*结束*到下一请求*开始*之间记录的空闲间隔，而不是开始到开始的时间差。Weka 追踪重放无条件采用此行为，没有开关。 | 每一轮都在上一轮完成后才会派发，因此开始到开始的时间差会重复计算上一请求的服务器处理时间，使所有会话逐轮向后漂移，并高估同时重叠的会话数。 |
| 关闭 `--ignore-trace-delays` | 保留来自追踪的延迟 | 重放保留采集到的 Agent 节奏和 KV Cache 复用间隔。 |
| 逐追踪空闲间隔上限是可选的 | 默认不设置 `--trace-idle-gap-cap-seconds`，也可以显式传入 CLI 值 | 设置后，可以限制根流及其所有后代流在实际运行中观察到的空闲时间，同时不会重写数据集时间戳或绕过 spawn/join 依赖关系。 |
| 禁止设置逐轮上限 | 必须保持 `--inter-turn-delay-cap-seconds` 未设置 | 分别限制父 Agent 和子 Agent 的延迟会扭曲它们的相对时序。 |
| `--system-idle-gap-cap-seconds = 10` | 没有活跃或就绪请求时，统一平移所有待执行的重放计时器，使下一个请求在 10 秒内到达 | 基准测试避免测量服务器完全无工作的长时间区间，同时保留所有待处理轨迹的请求顺序和相对间隔。 |
| `--cache-bust first_turn_prefix` | 每次播放（追踪的初次或循环派发）时，都在第一个用户轮次开头插入唯一的逐会话标记 | 否则，每当追踪被循环复用时，服务器前缀缓存都会继续针对相同内容升温，运行越久，稳态缓存命中率就会虚高。该标记为每次循环播放提供全新的提示词前缀。 |
| 加载器使用固定版本、包含子 Agent 的 Weka 语料库 | 数据集必须是包含子 Agent 的 `--public-dataset` 别名，或 `--hf-weka-dataset semianalysisai/cc-traces-weka-062126`（`weka_hf`）。本地 `weka_trace` 目录格式兼容但未固定版本——除非传入 `--unsafe-override`（这会标记 `submission_valid: false`），否则运行会拒绝使用。此锁定项的准确标志形式列在[故障排查](#troubleshooting)中。 | 提交有效性要求使用已知的公开语料库标识；任意本地目录无法通过哈希验证。 |
| `--benchmark-duration >= 900`（未设置时默认为 1800） | 运行至少持续 15 分钟；若省略则运行 30 分钟 | 稳态需要时间趋于稳定；短时间运行的噪声过大。 |
| 不允许客户端截断输入 | `--synthesis-max-isl`（基于文件的合成 ISL 过滤器）会遭到拒绝，因为它会丢弃输入长度超过上限的追踪（`--public-dataset` 语料库没有合成过滤器，因此该标志对它也不起作用） | 在客户端截断提示词会篡改工作负载。 |
| 已设置 `--random-seed` | 如果未传入，AIPerf 会选择一个强随机种子并记录它 | 确保可复现——每项重放结果都可以重新生成。 |

如果忘记传入 `ignore_eos` 附加输入、`--streaming`、`--cache-bust` 或
`--random-seed`，AIPerf 会注入锁定值，并在 INFO 级别日志中告知你。未显式设置
`--system-idle-gap-cap-seconds` 和 `--benchmark-duration`（默认 1800 秒）时也是
如此。如果为这些标志显式传入冲突值，AIPerf 会一次性列出所有违规项，而不是让你
逐个修复。

轨迹起始比例（`--trajectory-start-min-ratio` / `--trajectory-start-max-ratio`）
是一个较宽松的例外：场景会自动填入 `0.0`/`1.0`，但从不校验它们，因此显式指定
其他值会被静默接受——既不报错，也不改变 `submission_valid`——即使这实质上改变了
工作负载。对于准备参与比较的运行，请将它们视为固定值（参见
[快速开始](#quick-start)中的标志分组）。

---

<a id="reading-the-result-submission_valid"></a>

## 读取结果：`submission_valid`

所有输出文件都会写入产物目录——默认为运行 `aiperf` 时所在目录下的
`./artifacts/`；可使用 `--artifact-dir` 覆盖该路径。运行结束时，AIPerf 会打印
准确的输出位置。

使用 `--scenario` 后，AIPerf 会在本次运行的指标 JSON 输出中写入提交有效性标志。
单次运行的 `profile_export_aiperf.json` 会将其放在 `metadata` 块中；当传入
`--num-profile-runs >= 2`（并且至少两次运行成功完成）时，聚合文件（产物目录下的
`aggregate/profile_export_aiperf_aggregate.json`）也会包含该标志：

```json
{
  "metadata": {
    "scenario": "inferencex-agentx-mvp",
    "submission_valid": true,
    ...
  },
  "request_throughput": { ... },
  "request_latency": { ... },
  ...
}
```

（在单次运行文件中，指标结果与 `metadata` 并列为顶层字段；聚合文件则将指标嵌套在
`metrics` 键下。）

`submission_valid` 有三种可能状态：

- **`submission_valid: true`**——该运行遵循了 AIPerf 检查的所有场景规则，并且
  顺利完成。
- **`submission_valid: false`**——发生了问题（或者你强制覆盖了规则）。同一个
  metadata 块还会包含 `submission_invalid_reasons`，其中以简短标签说明原因。
  可能的值包括：
  - `"unsafe_override"`——你传入了 `--unsafe-override`，同时使用了一个或多个
    违反规则的标志。请参阅下文 [`--unsafe-override`](#--unsafe-override)。
  - `"context_overflow_rate_exceeded"`——超过 1% 的响应从服务器返回上下文溢出
    错误，说明服务器拒绝了基准测试要求其处理的提示词。通常是因为服务器启动时
    降低了最大模型长度；AgentX MVP 要求使用模型默认值。
  - `"run_cancelled"`——运行提前取消（Ctrl+C）。按一次 Ctrl+C 时，AIPerf 会
    正常取消，并仍使用已经收集到的部分指标写出导出文件（第二次 Ctrl+C 会强制
    退出且不写文件）；被取消的运行始终会被标记为无效。
  - `"scenario_reresolve_failed"`——多次运行的聚合导出无法在基础配置上重新应用
    场景锁（导入/环境损坏）。系统会以失败关闭：聚合结果标记
    `submission_valid: false`，而不是假定场景锁仍然有效。
- **字段不存在**——运行时没有使用 `--scenario`。提交有效性机制仅在场景标志下启用。

如果看到 `submission_valid: false`，请检查 `submission_invalid_reasons` 和 AIPerf
日志。每个原因都与违反的场景规则或跨越的运行时阈值一一对应。

应将该标志视为指引，而非认证。它只反映 AIPerf 自身可以执行的检查——启动时应用的
标志锁以及它跟踪的运行时阈值。`true` 表示 AIPerf 没有检测到规则违规；它无法证明
自身视野之外的情况（例如服务器以降低的上下文窗口启动，只能通过上下文溢出率间接
发现）。两份结果是否真正可比较，仍取决于双方的完整配置，而且 MVP 规则集本身仍在
演进（参见页面顶部的状态说明）。

---

## 一次运行如何执行

### 预热阶段：轨迹与 `k_i`

在 AIPerf 开始测量之前，会先运行一个为服务器 KV Cache 预热的**预热阶段**。这不是
AIPerf 的通用预热，而是 agentic-replay 调度器专用的、基于轨迹的预热。

具体过程如下。假设设置 `--concurrency 100`，调度器就会从数据集采样器中抽取追踪，
构建 100 条活跃轨迹通道。要让通道数量超过已加载且互不相同的根追踪数量，必须使用
`--allow-dataset-wrap` 或启用 `--cache-bust` 目标（场景会锁定后者）；否则，当池
容量不足时，系统会发出警告并限制通道数，而不是悄悄循环填充。启用循环复用后，同一
追踪可以支撑多条通道，每条通道都有确定性的独立起始位置。对于每条通道，调度器会在
该追踪已记录时长的 0% 到 100% 之间随机采样起始时刻 `t*`（即场景自动填充的
`--trajectory-start-min-ratio` / `--trajectory-start-max-ratio` 窗口，它将通用
默认值 25%–75% 扩展到完整范围），并据此推导“起始轮次” `k_i`，同时始终在预热后
至少留出一个性能测量轮次。接着，它为每条通道派发预热轮次：对于简单的（无子 Agent）
轨迹，派发第 `k_i` 轮，并将完整前缀历史（第 0 到 `k_i-1` 轮）附加为消息上下文；
如果某条通道的子 Agent 分支在 `k_i` 时已经活跃，则会为每个活跃流派发一个预热请求。

这样做是为了在任何测量开始之前，让服务器的前缀缓存填充真实的多轮编码上下文组合。
性能测量阶段开始后，每条轨迹从 `k_i + 1` 继续，而服务器缓存中已经保存了相应前缀。

给定随机种子后，`k_i` 的值是确定的：在任何机器上，相同数据集加相同种子都会得到
相同轨迹、相同起始点和相同循环复用顺序。这就是场景强制要求种子的原因。

只要任意**根会话**预热请求发生终态失败，AIPerf 就会立即中止运行——每个预热请求
仅尝试一次，不会重试——也不会等待其余预热请求排空。一次终态失败就意味着性能测量
将从退化的轨迹池开始，因此 AIPerf 会立即取消在途预热请求，在 `WARNING` 日志中记录
失败的追踪（“aborting run early”），然后以已取消状态结束运行，并标记
`submission_valid: false`（原因为 `run_cancelled`）。*子 Agent* 流的预热失败不会
触发中止——只有根（深度为 0）会话才会作为门禁。仅仅缓慢但健康的预热也**不会**被
中止：默认情况下，预热宽限期（`--agentic-warmup-grace-period`；在 `--scenario`
下，如果没有设置专用标志，`--warmup-grace-period` 会成为它的别名——参见
[预热阶段教程](warmup.md)）没有上限，因此只是速度慢的预热仍会运行到完成。

#### 可选的缓存压力预热

设置 `--agentic-cache-warmup-duration SECONDS`，可以在上述初始轨迹预热后增加一个
持续的缓存压力阶段。在指定时长内，AIPerf 会继续使用相同的活跃会话树，但移除记录的
空闲延迟，并把每个请求限制为输出一个 Token。时长结束后，它会停止发出新请求，排空
已经在线路上的请求，对每个活跃根、子 Agent 和尚未完成的 join 生成快照，并从这个
准确状态开始性能测量。每个流都会将完整的下一轮延迟带过阶段边界；等待全局预热排空
所花费的时间不会消耗这段延迟。

若要获得可重复的预热深度，请改用 `--warmup-requests-per-lane REQUESTS`。完成强制
快照预热请求后，每条并发通道会准确发送指定数量的额外线上预热请求。例如，
`--concurrency 16` 与 `--warmup-requests-per-lane 10` 会在初始预热之后增加
160 个缓存压力请求，并对每条通道严格执行 10 个额外请求的配额。

交接时，带时间戳的根流、子 Agent 流和后台流会恢复到各自下一个请求在共享的逐轨迹
数据集时钟上的位置。最早的待处理请求会立即开始性能测量，其余流则保留扁平化后的
跨流顺序和相对起始间隔。对于没有时间戳的数据集，AIPerf 会回退到每个流记录的
结束到开始延迟。

`--agentic-cache-warmup-duration` 与 `--warmup-requests-per-lane` 互斥：应在
有时限的预热和基于确定请求数量的预热之间选择一种。

这些请求仍属于预热，因此不会计入导出的请求指标。

<a id="profiling-phase-faithful-replay-recycle-global-idle-guard"></a>

### 性能测量阶段：忠实重放、循环复用和全局空闲保护

预热结束后，性能测量阶段开始。此时才进入实际测量。每条轨迹从第 `k_i + 1` 轮继续
重放会话，同时遵循最初记录的轮次间空闲间隔；延迟采用结束到开始语义，从上一轮完成
时刻开始计算。在阶段边界，AIPerf 会从待处理的首次请求偏移量中减去一个全局最小值：
最早的请求立即开始，其他所有轨迹保留相同的相对间隔。使用
`--trace-idle-gap-cap-seconds S` 时，会为每条轨迹设置看门狗，它覆盖初始的未来工作，
并在根流及所有后代流中的最后一个在途请求完成时重新启动。如果该会话树空闲达到
`S` 秒，AIPerf 只会统一提前它的待处理计时器；数据集时间戳、相对计时器间隔、请求
顺序和 spawn/join 门禁均保持不变。如果所有请求都已完成，但未来工作仍计划在 10 秒
以后发生，AIPerf 会将所有待处理请求计时器按相同幅度提前。这样可限制真正的系统空闲
时间，同时不改变待处理轨迹之间的请求顺序或相对间隔。计划中的轮次可能会被跨流重放
屏障阻挡，而不是到达线路；该调度任务排空后，AIPerf 会重新评估空闲保护，以避免附近
被阻挡的轮次掩盖一个晚得多但可派发的计时器。阶段结束日志会报告全局跳转发生的次数
以及跳过的秒数。

这里的并发是**按会话树**计算的：每条通道占用一个完整会话树的槽位，包括根会话及其
生成的所有子 Agent 工作流。只有当**整棵树排空**——根会话已经发送最后一轮，*且*
所有子 Agent 都已完成——通道才能循环复用，而不是在根会话最后一轮被确认后就复用。
任意时刻都准确保持 `--concurrency` 棵树处于活跃状态。不会更多：在一棵树完全排空
之前，不能启动新的根会话。也不会更少：如果某条通道的根会话已经完成但其子 Agent
仍在排空，该通道仍会占用槽位。共享树 ID（`root_correlation_id`）会写入产物目录中
`profile_export.jsonl` 的每条记录，因此 `aiperf analyze swim-lane` 会把每棵树
归到同一通道，并以逐通道时间线准确绘制 `--concurrency` 个槽位。

一棵树排空后，通道会从**数据集采样器**中抽取下一个根会话进行循环复用（该采样器
也是最初构建轨迹时使用的采样器，并遵循数据集的 `sampling_strategy`）。只要语料库
大于轨迹数量，顺序/乱序采样器都会至少播放每条追踪一次，之后才会重复。

还需要了解一些细节：

- **循环复用的追踪从第 0 轮开始**，而不是从随机 `k_i` 开始。“从中间某处开始”的
  规则只适用于初始轨迹——其目的是让*初始*状态分散在整个会话长度上，而不是持续
  注入从会话中段开始的跳转。
- **每次播放追踪都会获得新的缓存破坏标记。** 追踪首次派发（或循环复用）时，
  AIPerf 会在第一个用户轮次前添加类似 `[rid:8a3f2c1b9e7d]\n\n` 的唯一短标签。
  每次播放只生成一次该标记，在此次播放的所有轮次中复用；追踪进入新一轮播放时，
  标记会被替换——这样可以防止服务器的前缀缓存随着相同内容反复运行而不断升温。
  不同运行中的标记也不相同，因为每次运行自动生成的 benchmark ID 会参与标记摘要。
  有关标记位置以及预热到性能测量的交接，请参阅
  [AgentX FAQ §4](../benchmark-modes/semianalysis-agentx-faq.md#4-the-kv-cache-story-warmup-t-and-cache-busting)。
- **对于一次给定播放，预热和性能测量共享标记。** 一条轨迹的预热轮次 `k_i` 与首个
  性能测量轮次 `k_i+1` 使用*同一个* `[rid:…]`——这样，预热期间完成的 KV Cache
  前缀工作才能延续到测量阶段，而不是被丢弃。
- **只有启用循环复用时，并发度才可以超过可用追踪数量。** 太短、无法拆分为预热轮次
  和性能测量轮次的追踪会被跳过（如果跳过后无法填满追踪池，系统会发出警告并限制
  池大小）。要让通道数超过已加载且互不相同的根追踪数量，必须使用
  `--allow-dataset-wrap` 或启用 `--cache-bust` 目标。启用循环复用后，一个源追踪
  可以支撑多条通道（每条通道保留自己的起始位置和循环行为）。过滤后得到的*空*池
  仍然属于错误。
- 当 `--benchmark-duration` 到期时，**性能测量结束**。所有在途请求会在宽限排空期
  内完成并计入指标；时长结束后不会启动任何*新*请求。

### 子 Agent

AgentX MVP 语料库使用当前**包含子 Agent** 的变体。父轮次可以生成一个或多个辅助
会话，父会话中下一个带锚点的轮次要等相应的 `SPAWN_JOIN` 前置条件满足后才会恢复。
如[性能测量阶段](#profiling-phase-faithful-replay-recycle-global-idle-guard)所述，
`--concurrency` 统计活跃的会话**树**，每个槽位会一直保持占用，直到整棵树排空。
这里新增的复杂性在于*请求*数量：辅助会话与父会话并行运行，因此在扇出点，即时在途
请求数可以超过 `--concurrency`——限制的是并发树，而不是单个在途请求。

AIPerf 根据追踪中的 `WekaSubagentEntry` 块构建该拓扑：前后都有父会话锚点的
子 Agent 会成为 SPAWN/JOIN 分支；没有后续锚点的后台子 Agent 不会阻塞父会话；
共享相同锚点的相邻子 Agent 会合并为一个多子分支。

在每个子 Agent 条目内，AIPerf 还会检测嵌套上下文链，因此一条记录的子 Agent 可能
重放为多个并行子流（辅助 sidecar、工作组、独立 worker）。其中有两种影响负载的行为：
每个子流会按其相对于 spawn 的**记录偏移量**进行派发，而不是在父轮次完成时形成突发，
因此子 Agent 内部的请求计划会按记录的时间线重放；父会话的 SPAWN_JOIN 则会等待
子 Agent 的*所有*子流完成。有关检测规则和 `::sa:`/`::aux:`/`::wg:`/`::fa:`
流命名方案，请参阅 [Weka 追踪教程](weka-trace.md)。

---

<a id="--unsafe-override"></a>

## `--unsafe-override`

有时你会有意违反场景规则——例如研究某个变量的敏感性、执行一分钟的冒烟测试而不是
规范的 15 分钟运行，或为消融研究尝试不同的缓存破坏目标。此时可以运行：

```bash
aiperf profile \
    --scenario inferencex-agentx-mvp \
    --unsafe-override \
    --benchmark-duration 60 \
    ...
```

`--unsafe-override` 的作用如下：

- **将大多数场景规则违规从错误转换为警告。** 运行可以启动。
- **不会绕过 Weka 加载器缺失问题。** 如果省略 `--public-dataset` / `--input-file`，
  CLI 默认使用合成数据；AgentX 仍会拒绝启动（继续运行看起来像是在加载语料库，
  实际却生成会清空轨迹池的单轮会话）。必须显式传入允许的 Weka 加载器。
- **在每个 JSON 输出中标记 `submission_valid: false`**（包括单次运行；当
  `--num-profile-runs >= 2` 时也包括聚合文件），并在
  `submission_invalid_reasons` 中加入 `"unsafe_override"`——但仅限于确实违反了
  至少一条规则的情况。传入该标志但不违反任何规则时，它不会产生影响。

该标志启用且规则被违反后，本次运行输出中的标记会一直保持为 `false`——之后没有办法
将其改回，因此覆盖行为始终在结果中可见。在不使用 `--scenario` 时，该标志不起作用
（因为没有场景规则可供覆盖）。

请将它用于开发和消融研究。任何准备与其他 AgentX MVP 运行比较的结果都不应使用它。

---

<a id="troubleshooting"></a>

## 故障排查

**`UnknownScenarioError: Unknown scenario 'inferencex-agentx-mvp'. Valid scenarios: …`**
正在运行的 AIPerf 版本早于该场景——场景随软件包一同发布并在代码中注册，不能通过
重新生成数据文件来获得。升级 AIPerf（`pip install -U aiperf`）并重新运行。

**`EmptyTracePoolError: Loader produced 0 traces; trajectories cannot be built.`**
Hugging Face 数据集下载失败，或者行校验没有产生任何可用追踪。请检查与
`huggingface.co` 的网络连接，并确认 `--public-dataset` 使用的是包含子 Agent 的
别名（例如 `semianalysis_cc_traces_weka_with_subagents` 或
`semianalysis_cc_traces_weka_062126`），或者使用 `weka_hf` 并传入
`--hf-weka-dataset semianalysisai/cc-traces-weka-062126`。在离线机器上，可以通过
`--custom-dataset-type weka_trace --input-file <dir> --unsafe-override` 重放本地
Weka 格式的追踪目录（仅限离线冒烟测试——`submission_valid: false`）；有关获取或
采集追踪的方法，请参阅 [Weka 追踪教程](weka-trace.md)。请注意，模型 tokenizer
也必须离线可用：预先填充 Hugging Face 缓存并设置 `HF_HUB_OFFLINE=1`，或让
`--tokenizer` 指向本地路径。

**`ScenarioLockError` … `got synthetic` / `cannot be bypassed with --unsafe-override`**
你设置了 `--scenario inferencex-agentx-mvp`（并且可能为了短时冒烟测试设置了
`--unsafe-override`），但省略了 Weka 数据源。此时 CLI 会退回合成提示词
（`session_000000`，每个会话一轮）。即使使用 `--unsafe-override`，AgentX 也会
拒绝这一组合。请添加 `--public-dataset semianalysis_cc_traces_weka_062126`
（或其他允许的别名 / `--hf-weka-dataset semianalysisai/cc-traces-weka-062126`）。

**无法解析或下载 tokenizer**
Weka 加载器会通过从 `--model` 推导出的 Hugging Face tokenizer 重建每条提示词，
因此如果无法访问该仓库，数据集构建就会失败。如果模型仓库受限，请导出
`HF_TOKEN`；如果 `--model` 不是可解析的 Hugging Face 仓库名（例如本地服务器别名
或私有构建），请显式传入 `--tokenizer <hf-repo-or-local-path>`。请参阅
[Tokenizer 自动检测](../reference/tokenizer-auto-detection.md)。

**运行提前中止：`aborting run early (broadcasting ProfileCancelCommand)` / 预热失败**
推理服务器拒绝了某个预热请求。每个预热请求只尝试一次，不会重试；AgentX MVP 会在
**首个**根会话终态预热失败时立即中止，而不是生成部分结果：运行会立即取消，并在
`WARNING` 日志中指出失败的追踪。请检查 AIPerf 和服务器日志——根本原因通常是连接
错误（`Connection refused`：`--url` 错误或服务器未运行）、身份验证失败
（`401`/`403`：传入 `--api-key`）、模型名称不匹配（`404` / `model not found`：
`--model` 与服务器提供的模型不一致），或者服务器的 `max-model-len` 小于追踪要求
的上下文长度。

**运行完成，但 `submission_valid: false`，原因为 `"context_overflow_rate_exceeded"`**
服务器以“上下文过长”为由拒绝了超过 1% 的请求。最常见的原因是服务器启动时降低了
`--max-model-len`（或等价标志）——AgentX MVP 要求使用模型默认值。请在不覆盖最大
长度的情况下重启服务器并重试。如果模型的*原生*窗口本身约为 256k（例如 MiniMax
系列模型），则应改用匹配的 `_256k` 语料库，例如
`--public-dataset semianalysis_cc_traces_weka_with_subagents_256k`；参见
[AgentX FAQ §3](../benchmark-modes/semianalysis-agentx-faq.md#3-how-realistic-are-the-prompts-and-token-counts)，
了解为何这比在客户端设置上限更合理。溢出的请求数会出现在
`context_overflow_count` 指标中（原始计数，不是比例）：在
`profile_export_aiperf.json` 中是顶层字段，在聚合文件中位于 `metrics` 下。用它除以
`request_count + error_request_count + skipped_context_overflow_count`
（与聚合导出器计算 1% 阈值时使用的分母相同），即可了解距离上限还有多远。

**运行以非零状态退出，并显示 `ProfileMetricCoverageError`**
服务器在达到配置的性能测量时长 98% 之前，就停止产生 TTFT 和 Token 间延迟观测。
任一信号都可以证明服务器全局活跃，因此即使接近边界时没有新请求开始，一个长时间的
流式响应仍然有效。AIPerf 会保留结果产物，将其标记为无效并注明
`insufficient_profile_metric_coverage`，同时报告两种信号的实际覆盖率。请检查推理
服务器日志，确认服务器是否崩溃或请求处理是否停滞。预热指标以及短于场景最小有效时长
的性能测量阶段不计入统计。

**“scenario `'inferencex-agentx-mvp'` requires loader=any of …” / 无法验证本地
weka_trace 目录的语料库标识**
只有使用固定版本的公开 SemiAnalysis Weka 语料库
（`semianalysisai/cc-traces-weka-062126`）时，AgentX MVP 场景才会标记
`submission_valid: true`。该语料库必须通过包含子 Agent 的 `--public-dataset`
别名（滚动或按日期固定；旧的不含子 Agent 别名会被拒绝）重放，或使用限定到该仓库的
通用 Hugging Face Weka 加载器（`weka_hf`）。请传入以下任意一种：

- `--public-dataset semianalysis_cc_traces_weka_with_subagents`（零配置；
  当前语料库的滚动别名）；
- `--public-dataset semianalysis_cc_traces_weka_062126`（零配置；按日期固定）；或
- `--hf-weka-dataset semianalysisai/cc-traces-weka-062126`（自动选择 `weka_hf`）。

本地 `--custom-dataset-type weka_trace --input-file <dir>`（或者可自动检测为
`weka_trace` 的裸 `--input-file <weka-dir>`）仅在同时使用 `--unsafe-override` 时
可用于离线冒烟测试——结果会标记 `submission_valid: false`，因为 AIPerf 无法对任意
本地目录生成指纹，以证明它就是公开语料库。有意在该场景下重放*其他* Weka 格式
语料库时，也适用同一覆盖路径。

**“scenario `'inferencex-agentx-mvp'` requires `cache_bust.target=first_turn_prefix`;
got `<other>`”**
你在使用 `--scenario` 的同时显式传入了 `--cache-bust <other>`（例如
`system_suffix` 或 `none`），AIPerf 不会悄悄覆盖用户的显式选择。如果完全没有传入
`--cache-bust`，校验器会自动注入 `first_turn_prefix`，不会出现此错误。如果确实
需要为消融研究使用不同的缓存破坏目标，请传入 `--unsafe-override`，并接受
`submission_valid: false` 标记。

**“scenario `'inferencex-agentx-mvp'` forbids client-side input truncation;
`--synthesis-max-isl` …”**
你传入了 `--synthesis-max-isl <N>`，它会丢弃记录输入长度超过 `N` Token 的所有追踪。
该场景禁止这样做，因为它会改变重放的工作负载（得到一组不同的语料子集，并移除最
困难的提示词）。请删除该标志，让服务器自行处理上下文长度错误（场景会通过
`context_overflow_rate_exceeded` 原因跟踪此类错误）；或者传入
`--unsafe-override` 并接受 `submission_valid: false`。

**“运行已完成，但在任何位置都找不到 `submission_valid`”**
很可能是运行时没有使用 `--scenario`。有效性标志只会在场景标志下启用——请使用
`--scenario inferencex-agentx-mvp` 重新运行，然后查看单次运行的
`profile_export_aiperf.json`（位于 `metadata` 下）。如果传入了
`--num-profile-runs >= 2`，它也会出现在产物目录下
`aggregate/profile_export_aiperf_aggregate.json` 的 metadata 中。

**发送任何流量之前配置超时**
一台机器上的首次运行会先把完整语料库重建为经过分词、带缓存结构的数据集，然后才会
发送请求；对于包含子 Agent 的语料库，该过程可能超过默认的 300 秒配置超时。请将
`AIPERF_DATASET_CONFIGURATION_TIMEOUT` 和
`AIPERF_SERVICE_PROFILE_CONFIGURE_TIMEOUT`（后者必须不小于前者）提高到约
1800 秒。该成本只需承担一次：重建后的数据集会进入磁盘缓存（默认为
`~/.cache/aiperf/dataset_mmap`），其键包含随机种子，因此请固定 `--random-seed`，
后续运行便可在数秒内恢复。请参阅
[AgentX FAQ §8](../benchmark-modes/semianalysis-agentx-faq.md#8-running-the-benchmark-and-why-the-first-run-is-slow)。

**运行比预期慢**
冷启动首次运行时，发送流量前会进行上一条中描述的语料库重建，因此要额外预留数分钟。
之后，预热阶段会在性能测量开始前为每条轨迹流重放完整的会话前缀；对于历史很深的真实
编码追踪，这本身就会占用相当可观的时间。子 Agent 扇出还可能产生超出活跃父轨迹数量
的额外在途请求。如果服务器并发能力有限，而你将 `--concurrency` 提高到其上限以上，
还会出现排队。请降低 `--concurrency` 或提高服务器限制。

**同一服务器上的不同运行结果存在差异**
使用不同 `--random-seed` 值的两次运行会选择不同轨迹和不同的 `k_i`，因此出现一定
差异是正常的。若要精确复现，请记录 AIPerf 启动时输出的种子，并通过
`--random-seed` 再次传入。服务器端采样的随机性（生成温度）也会造成差异；增加
性能测量运行次数（`--num-profile-runs`）可以为百分位数提供更多数据，使其趋于稳定。

---

## 另请参阅

- [Weka 智能体编码追踪](weka-trace.md)——底层追踪格式和 SPAWN/JOIN 子 Agent 机制。
- [指标参考](../metrics-reference.md)——TTFT、ITL、吞吐量及其他所有报告指标的定义和公式。
- [有效指标与活跃指标](../reference/effective-vs-active-metrics.md)——运行结束时打印的
  时间加权 EFFECTIVE 和 ACTIVE 控制台表格。
- [时序模式参考](../benchmark-modes/timing-modes-reference.md)——`agentic_replay` 在
  其他 AIPerf 时序模式中的定位。
- [预热阶段教程](warmup.md)——通用 AIPerf 预热机制（agentic-replay 预热是其特化形式）。
- [输入序列长度（ISL）分词](../reference/isl-tokenization.md)——`--isl`、
  `--apply-chat-template` 和锁定的 `--cache-bust first_turn_prefix` 标记如何共同影响
  报告的指标。
- [ISL 预算补偿推导](../reference/isl-budget-compensation.md)——聊天模板与标记开销
  补偿背后的数学原理。
- [CLI 选项参考](../cli-options.md)——自动生成的 `--scenario`、`--unsafe-override`、
  `--system-idle-gap-cap-seconds` 及其他所有标志的参考说明。
