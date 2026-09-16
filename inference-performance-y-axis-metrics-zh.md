# InferenceX 推理性能页面 Y 轴指标说明

> 来源：公开页面 [InferenceX 推理性能](https://inferencex.semianalysis.com/zh/inference) 的前端指标注册表与派生字段计算逻辑。
>
> 页面中的原始 Benchmark 结果会经过部署级聚合、按芯片数归一化，并在部分指标中结合成本或功耗模型，形成以下可选 Y 轴指标。

#### 吞吐量（Throughput）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每芯片 Token 吞吐量**（Token Throughput per Chip） | `tok/s/chip` | 每张 GPU 或每颗加速器芯片每秒处理的总 Token 数，包含输入 Token 和输出 Token。用于比较单芯片整体推理效率，数值越高越好。 |
| **每芯片输入 Token 吞吐量**（Input Token Throughput per Chip） | `tok/s/chip` | 每张芯片每秒处理的输入 Token 数，主要反映 Prefill 阶段的处理能力。适合分析长 Prompt 或长上下文场景，数值越高越好。 |
| **每芯片输出 Token 吞吐量**（Output Token Throughput per Chip） | `tok/s/chip` | 每张芯片每秒生成的输出 Token 数，主要反映 Decode 阶段的生成能力，数值越高越好。 |
| **每全站公用电力 MW 的 Token 吞吐量**（Token Throughput per All in Utility MW） | `tok/s/MW` | 总 Token 吞吐量除以全站公用电力功耗，表示每 1 MW 电力能够支撑的总 Token 吞吐量。该指标不仅考虑 GPU，还考虑整体供电能力，数值越高越好。 |
| **每全站公用电力 MW 的输入 Token 吞吐量**（Input Token Throughput per All in Utility MW） | `tok/s/MW` | 每 1 MW 全站公用电力能够处理的输入 Token 数，主要反映系统的 Prefill 能效，数值越高越好。 |
| **每全站公用电力 MW 的输出 Token 吞吐量**（Output Token Throughput per All in Utility MW） | `tok/s/MW` | 每 1 MW 全站公用电力能够生成的输出 Token 数，主要反映系统的 Decode 能效，数值越高越好。 |

> 当选择“每芯片输入 Token 吞吐量”时，页面会将 X 轴切换为 **P90 Time to First Token（P90 TTFT，单位：秒）**，用于同时观察输入处理吞吐量和首 Token 延迟之间的关系。

#### GPU 小时 Token 产出与成本效率

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每 GPU 小时 Token 收入**（Token Revenue per GPU Hour） | `$/GPU/hr` | 根据每芯片 Token 吞吐量折算每 GPU 小时的 Token 产出或收入能力。页面前端的基础计算为：`3600 × 每芯片总 Token 吞吐量 ÷ 1,000,000`。数值越高越好。 |
| **每 1 美元 TCO 的总 Token 数**（Total Tokens per $1 TCO） | `tok/$` | 在给定总拥有成本（TCO）假设下，每花费 1 美元能够处理的总 Token 数，包含输入 Token 和输出 Token。数值越高越好。 |
| **每 1 美元 TCO 的输出 Token 数**（Output Tokens per $1 TCO） | `tok/$` | 在给定 TCO 假设下，每花费 1 美元能够生成的输出 Token 数，主要反映 Decode 阶段的经济效率。数值越高越好。 |
| **每 1 美元 TCO 的输入 Token 数**（Input Tokens per $1 TCO） | `tok/$` | 在给定 TCO 假设下，每花费 1 美元能够处理的输入 Token 数，主要反映 Prefill 和长上下文处理效率。数值越高越好。 |

页面中的成本模型通常区分以下几种方式：

- **Hyperscaler**：大规模云服务商自建或自有基础设施成本模型；
- **Rental**：例如三年期租赁成本模型；
- **Custom**：用户自定义的成本参数。

同一个 Benchmark 数据点在不同成本模式下，原始吞吐量可以相同，但每美元 Token 数和单位 Token 成本可能不同。

页面的基础折算关系如下：

```text
每 GPU 小时总 Token 数
= 3600 × 每芯片总 Token 吞吐量

每 GPU 小时输出 Token 数
= 3600 × 每芯片输出 Token 吞吐量

每 GPU 小时输入 Token 数
= 3600 × 每芯片输入 Token 吞吐量
```

#### 单位 Token 成本（Cost per Million Tokens）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每百万总 Token 成本**（Cost per Million Total Tokens） | `$` | 处理 100 万个总 Token（输入 Token 加输出 Token）所需的成本。数值越低越好。 |
| **每百万输出 Token 成本**（Cost per Million Output Tokens） | `$` | 生成 100 万个输出 Token 所需的成本，主要反映 Decode 阶段的经济效率。数值越低越好。 |
| **每百万输入 Token 成本**（Cost per Million Input Tokens） | `$` | 处理 100 万个输入 Token 所需的成本，主要反映 Prefill 和长上下文处理的经济效率。数值越低越好。 |

基本计算关系如下：

```text
每百万总 Token 成本
= 每 GPU 小时 TCO ÷ 每 GPU 小时产生的百万总 Token 数

每百万输出 Token 成本
= 每 GPU 小时 TCO ÷ 每 GPU 小时产生的百万输出 Token 数

每百万输入 Token 成本
= 每 GPU 小时 TCO ÷ 每 GPU 小时处理的百万输入 Token 数
```

> **注意：**每百万总 Token 成本不能替代每百万输出 Token 成本。输入 Token 和输出 Token 的比例会显著影响这三个指标。

#### 全配置 Token 能耗（All-in Provisioned Energy per Token）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每总 Token 的全配置能耗**（All-in Provisioned Joules per Total Token） | `J/tok` | 根据配置功耗模型，计算处理一个总 Token（输入 Token 加输出 Token）所需的能量。数值越低越好。 |
| **每输出 Token 的全配置能耗**（All-in Provisioned Joules per Output Token） | `J/tok` | 根据配置功耗模型，计算生成一个输出 Token 所需的能量，主要反映 Decode 能效。数值越低越好。 |
| **每输入 Token 的全配置能耗**（All-in Provisioned Joules per Input Token） | `J/tok` | 根据配置功耗模型，计算处理一个输入 Token 所需的能量，主要反映 Prefill 能效。数值越低越好。 |

该组指标属于配置或模型推导值，不一定来自运行过程中的直接功耗遥测。基本关系可以理解为：

```text
单位 Token 能耗
≈ 配置功耗 ÷ Token 吞吐量
```

由于：

```text
1 W = 1 J/s
```

因此：

```text
W ÷ tok/s = J/tok
```

#### 实测功耗（Measured Power）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每芯片实测平均功耗**（Measured Average Power per Chip） | `W` | Benchmark 运行期间，每张芯片的平均实际功耗。该指标应结合 Token 吞吐量一起分析，不能单独用低功耗判断系统效率。数值越低越好。 |
| **每芯片实测 P75 集群功耗**（Measured P75 Fleet Power per Chip） | `W` | 对整个 GPU 集群的功耗采样值计算 P75 后，再归一到每张芯片的功耗。用于观察较高负载状态下的功耗水平。数值越低越好。 |
| **每芯片实测 P90 集群功耗**（Measured P90 Fleet Power per Chip） | `W` | 对整个 GPU 集群的功耗采样值计算 P90 后，再归一到每张芯片的功耗。用于观察高功耗尾部情况，比平均值更能反映极端负载。数值越低越好。 |
| **每芯片实测 Prefill 功耗**（Measured Prefill Power per Chip） | `W` | Prefill 阶段每张芯片的实际平均功耗，用于分析输入处理阶段的功耗特征。数值越低越好。 |
| **每芯片实测 Decode 功耗**（Measured Decode Power per Chip） | `W` | Decode 阶段每张芯片的实际平均功耗，用于分析输出生成阶段的功耗特征。数值越低越好。 |
| **实测平均功耗占 TDP 百分比**（Measured Average Power as Percent of TDP） | `% TDP` | 实测平均功耗相对于芯片热设计功耗（TDP）的百分比。计算公式为：`实测平均功耗 ÷ 芯片 TDP × 100%`。数值越低表示相对 TDP 的功耗占比越低。 |

P75 和 P90 的含义：

- **P75**：75% 的功耗采样值小于或等于该数值，另外 25% 的采样值更高；
- **P90**：90% 的功耗采样值小于或等于该数值，另外 10% 的采样值更高。

因此，P90 通常比平均功耗更适合观察高负载尾部功耗。

#### 实测单位 Token 和单请求能耗（Measured Energy）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每输出 Token 实测能耗**（Measured Joules per Output Token） | `J/tok` | 实际测量得到的总能耗除以输出 Token 数，反映生成输出 Token 的实际能效。数值越低越好。 |
| **每输出 Token 实测 Decode 能耗**（Measured Decode Joules per Output Token） | `J/tok` | 仅统计 Decode 阶段的能耗，再除以输出 Token 数，用于单独观察输出生成阶段的能效。数值越低越好。 |
| **每输入 Token 实测能耗**（Measured Joules per Input Token） | `J/tok` | 实际测量得到的能耗除以输入 Token 数，反映输入处理阶段的实际能效。数值越低越好。 |
| **每输入 Token 实测 Prefill 能耗**（Measured Prefill Joules per Input Token） | `J/tok` | 仅统计 Prefill 阶段的能耗，再除以输入 Token 数，用于单独观察输入处理阶段的能效。数值越低越好。 |
| **每 Token 实测能耗（包含 Prompt）**（Measured Joules per Token, including prompt） | `J/tok` | 总实测能耗除以输入 Token 与输出 Token 的总数，表示整个请求生命周期的平均单位 Token 能耗。数值越低越好。 |
| **每次成功请求的实测能耗**（Measured Joules per Successful Query） | `J/query` | 总实测能耗除以成功请求数，表示完成一次成功请求平均消耗的能量。数值越低越好。 |
| **每次成功请求的实测瓦时**（Measured Watt-hours per Successful Query） | `Wh/query` | 每次成功请求的实测能耗，以瓦时表示。与上一指标的换算关系为：`Wh/query = J/query ÷ 3600`。数值越低越好。 |

> `J/query` 会受到每个请求输入长度、输出长度和请求处理复杂度的影响，因此比较不同 Benchmark 时，需要保证工作负载具有可比性。

#### 建模机箱交流功耗（Modeled System Power）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每 GPU 建模机箱交流功耗（8k1k）**（Modeled Chassis AC Power per GPU） | `W/GPU` | 根据系统级功耗模型估算机箱从交流电源侧消耗的功率，并将其分摊到每张 GPU。该指标是系统级估算值，不等于 GPU 芯片遥测读数。数值越低越好。 |

该指标与芯片功耗的区别：

- **芯片功耗**：通常指 GPU 或加速器本身的功耗；
- **机箱交流功耗**：还可能包含 CPU、内存、网络设备、风扇、电源转换损耗等系统级功耗；
- 页面标题中的 `8k1k` 表示该建模功耗指标对应的 8K 输入、1K 输出工作负载条件。

#### 自定义用户指标（Custom User Values）

| Y 轴指标 | 单位 | 指标说明 |
|---|---:|---|
| **每 1 美元 TCO 的总 Token 数（自定义）**（Total Tokens per $1 TCO, Custom User Values） | `tok/$` | 使用用户自定义的 TCO、租赁或设备成本参数，计算每 1 美元能够处理的总 Token 数。数值越高越好。 |
| **每百万总 Token 成本（自定义）**（Cost per Million Total Tokens, Custom User Values） | `$` | 使用用户自定义的成本参数，计算处理 100 万个总 Token 所需的成本。数值越低越好。 |
| **每全站公用电力 MW 的 Token 吞吐量（自定义）**（Token Throughput per All in Utility MW, Custom User Values） | `tok/s/MW` | 使用用户自定义的系统功耗或供电参数，计算每 1 MW 全站公用电力能够支持的 Token 吞吐量。数值越高越好。 |

#### 页面指标与 AIPerf 指标的关系

InferenceX 页面上的 Y 轴指标并不全部是 AIPerf 的原始请求级指标。

AIPerf 通常直接产生或聚合以下类型的性能数据：

- Time to First Token（TTFT）；
- Inter-Token Latency（ITL）；
- Full Decode Duration；
- E2E Output Token Throughput；
- Output Token Throughput；
- Input Token Throughput；
- 请求成功率和失败率；
- 实测功耗与能耗数据（如果运行环境提供遥测）。

InferenceX 页面会在这些 Benchmark 结果之上继续计算：

```text
AIPerf / Benchmark 原始结果
  → 部署级聚合吞吐量
  → 按芯片数量归一化
  → 按系统功耗归一化
  → 按 TCO 成本归一化
  → 生成网页 Y 轴派生指标
```

例如：

```text
AIPerf 的 Output Token Throughput
  → 每芯片输出 Token 吞吐量
  → 每 MW 输出 Token 吞吐量
  → 每美元输出 Token 数
  → 每百万输出 Token 成本
  → 每输出 Token 的焦耳能耗
```

因此：

- **每百万输出 Token 成本**不是单个 AIPerf 请求的原始字段；
- **每输出 Token 实测能耗**是由实测能耗和输出 Token 数得到的派生指标；
- **每芯片 Token 吞吐量**是对部署级吞吐量按芯片数归一化后的指标；
- **每 MW Token 吞吐量**还会进一步引入系统供电功耗模型。

#### 指标方向汇总

| 指标类型 | 判断方向 |
|---|---|
| Token 吞吐量 | 越高越好 |
| 每 GPU 小时 Token 产出 | 越高越好 |
| 每美元 Token 数 | 越高越好 |
| 每 MW Token 吞吐量 | 越高越好 |
| 单位 Token 成本 | 越低越好 |
| 单位 Token 能耗 | 越低越好 |
| 每次请求能耗 | 越低越好 |
| 实测功耗 | 越低越好，但必须结合吞吐量 |
| 功耗占 TDP 百分比 | 越低越好 |
| 端到端延迟 | 越低越好 |
| TTFT | 越低越好 |
| ITL | 越低越好 |
