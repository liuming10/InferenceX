# 本地 Python Sweep 执行器

[English](./README.md)

`py_workflow` 可在本地运行指定的 InferenceX 配置，不依赖 GitHub Actions。它继续使用现有矩阵生成器、Runner 启动器、Enroot/Slurm 行为、结果处理器、AgentX 校验、聚合、按日期归档，以及默认拒绝不完整输入的本地复用门禁。

## 源码选择

必须且只能选择一种源码模式。

### 使用已下载的项目

```bash
<project>/.venv/bin/python -m py_workflow.run_sweep \
  --project-dir <project> \
  --config-key dsv41flash-fp4-mi355x-vllm-agentic-dspark \
  --archive-root /stortest/lium_space/agentX/InferenceX_result
```

`--project-dir` 对 Git 与虚拟环境只读：

- 主项目、所有子项目/子模块和 `<project>/.venv` 必须事先准备完成。
- 执行器不会 clone、fetch、clean、checkout、reset、初始化子模块、安装包或修改 `.venv`。
- 项目有未提交改动时默认拒绝；仅在明确传入 `--allow-dirty-project` 时才允许。
- 项目的 `.venv` 是矩阵生成、AIPerf、校验和聚合唯一使用的 Python 环境。

### 使用执行器管理的克隆

```bash
python -m py_workflow.run_sweep \
  --repository https://example.invalid/InferenceX.git \
  --ref main \
  --config-key dsv41flash-fp4-mi355x-vllm-agentic-dspark \
  --archive-root /stortest/lium_space/agentX/InferenceX_result
```

受管克隆模式仅会在 `<archive-root>/.worktrees` 下递归 clone、fetch 或更新自己的副本。该项目仍必须提供已准备好的 `.venv`；执行器不会安装依赖。

## 配置与模式

`--config-key` 必填且可重复，接受与 `infx.matrix.generate test-config` 相同的精确配置键和 glob 模式；可选 `--conc` 用于限制生成的并发点。不需要 GitHub 事件、路径过滤、PR 标签或 changelog diff。

默认 `--mode sweep-enabled` 对应原来的 `sweep-enabled` 最小并发扫描。完整扫描模式会保留 canary 与 fail-fast 选择。`--no-evals` 直接对应 workflow 输入 `no-evals: true`，并向既有矩阵生成器传入 `--no-evals`；它与 `--evals-only` 互斥，也不能与 `--all-evals` 组合。当前 `--max-parallel` 必须为 `1`：现有启动器会在项目根目录写入部分输出，因此并发任务可能互相破坏结果。

## 本地复用门禁

使用 `--reuse auto`、`--reuse required` 或 `--reuse-from <archive>`。候选归档只有同时满足下列条件才会被接受：

- 仓库身份和解析后的 Git SHA 完全相同；
- 生成的矩阵摘要和配方指纹集合完全相同；
- 执行相关选项兼容；
- 产物清单完整，文件大小和 SHA-256 校验值未变化；
- 全部任务记录成功，且通过已有的可复用产物校验器。

旧 GitHub Actions 归档不含此完整性契约，因此会被拒绝。自动复用同样拒绝 `--evals-only` 与 `--agentx-fast` 来源。复用会生成新的按日期归档，并在 manifest 中记录通过验证的来源。

## 归档与可选对比

成功任务会在给定 archive root 下以原子方式发布。`artifacts/` 子目录镜像 GitHub Actions 的产物名称：`bmk_<stem>/`、`bmk_agentic_<stem>/`、`agentic_<stem>/`、`server_logs_<stem>/`（评测任务为 `eval_` 变体）、有遥测文件时才创建的 `gpu_metrics_<stem>/`、`power_audit_<stem>/`、`eval_<stem>/`，以及矩阵提供 changelog metadata 时的 `changelog-metadata/`。AgentX 原始产物保留 workflow 对 `inputs.json` 和 `profile_export_raw.jsonl` 的排除规则。本地 bookkeeping 位于 `artifacts/` 外部：`plan.json`、`archive_manifest.json`、`aggregates/` 和 `run_stats.json`。失败任务会留在 `.staging-<pid>` 目录，永远不可复用。

`--compare-results` 默认关闭且需要 `DATABASE_URL`，仅用于现有的只读结果对比。不使用 GitHub API、Actions artifact 传输、PR 评论、GitHub Secret 或托管 Runner。
