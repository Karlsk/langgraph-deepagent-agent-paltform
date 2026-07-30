# 工作流引擎重实现 · 实施规格（spec）总览

本目录是把《00·架构总览》《01·分阶段开发计划》《02·开发规范》《03·隐患修复方案》落到可执行层的**任务规格集**。阅读顺序：`CONTRACT.md`（契约基准）→ `api-exploration-1x.md`（1.x 探索门禁）→ `spec-00..09`（逐阶段任务卡）。

**契约效力**：`CONTRACT.md` > 各 spec > 规划文档（00-03）> 原代码惯例。接口签名、AD 决策、红线细则、门禁清单只在 `CONTRACT.md` 定义一次，本 README 与各 spec 一律用编号引用（AD-xx / §x.y），不复制定义。

## 1. 工作量拆分总表

| 文件 | 内容 | 对应里程碑 | 人日 |
| --- | --- | --- | --- |
| `README.md` | 本文：总览、实现路径、依赖关系、风险登记、AI 委派用法 | — | — |
| `CONTRACT.md` | 编码契约：接口冻结清单、行为语义、红线机器门禁、AD-01..12、变更流程 | 全程约束 | — |
| `api-exploration-1x.md` | 1.x API 探索任务书 + 报告（EXP-G/C/L/X），由 spec-00 TC4-6 执行闭环 | M0 | — |
| `spec-00-项目脚手架与基础设施.md` | Phase 0 + EXP 探索执行 | M0 | 1.5 |
| `spec-01-数据模型层.md` | Phase 1 | M1 | 1.0 |
| `spec-02-state自动生成.md` | Phase 2 | M2 | 1.0 |
| `spec-03-节点基础设施.md` | Phase 3 | M3 | 1.0 |
| `spec-04-llmnode.md` | Phase 4 | M4（与 spec-05 并行） | 1.5 |
| `spec-05-httpnode.md` | Phase 5 | M4（与 spec-04 并行） | 1.5 |
| `spec-06-图构建器.md` | Phase 6 | M5 | 1.5 |
| `spec-07-注册表与运行时.md` | Phase 7 | M6 | 2.0 |
| `spec-08-入口集成.md` | Phase 8 | M7 | 1.0 |
| `spec-09-加固与交付.md` | Phase 9 | M8 | 1.5 |
| **合计** | | | **12.5** |

## 2. 实现路径

里程碑链：

```text
M0(1.5d，含 EXP 探索闭环) → M1(1d) → M2(1d) → M3(1d) → [M4: spec-04 ∥ spec-05, 1.5d] → M5(1.5d) → M6(2d) → M7(1d) → M8(1.5d)
```

- **EXP 全项闭环是 M1 开工前置条件**（R-EXP，CONTRACT §7）。
- 关键路径串行 12 人日，总工作量 12.5 人日，单人工期约 12 个工作日。
- 每里程碑退出标准 = 对应 spec 的 DoD 全绿。

## 3. 依赖关系

### 3.1 spec DAG

```text
spec-00 → spec-01 → spec-02 → spec-03 → ┬→ spec-04 ─┐
                                        └→ spec-05 ─┴→ spec-06 → spec-07 → spec-08 → spec-09
```

- spec-04 / spec-05 互为并行窗口（只依赖 spec-03，汇合点是 spec-06）。
- spec-03 不硬依赖 spec-02，但按统一口径串行。

### 3.2 EXP 门禁（R-EXP）

| EXP 组 | 门禁的 spec | 关键 API 点 |
| --- | --- | --- |
| EXP-G（langgraph 1.0.2 图构建） | spec-02 / 06 / 07 | pydantic state schema、reducer channel、add_node/add_conditional_edges、compile/invoke、异常传播 |
| EXP-C（langchain-core 1.0.4 Runnable） | spec-03 / 07 | RunnableLambda 签名、`with_config(tags=...)` 透传、RunnableConfig |
| EXP-L（ChatOpenAI / ChatAnthropic） | spec-04 | 构造参数、AIMessage 形态、限流异常层级（→ tenacity 谓词）、SecretStr 脱敏、版本配套 |
| EXP-X（1.0 迁移影响） | spec-00 / 01 | 最小依赖集、0.x 假设 vs 1.x 实测对照总表 |

对应 EXP 项未在 `api-exploration-1x.md` 闭环（实测记录 + 证据）前，相关编码任务**不得开工**。

### 3.3 代码库依赖矩阵

| spec | 代码库依赖 |
| --- | --- |
| spec-00 | `pyproject.toml`（依赖/marker/ruff）、`Makefile`、`.github/workflows/ci.yaml`、`.env.example` |
| spec-01..07 | 仅 `app/workflow/` 内部模块 + 第三方库（对现有 app 代码零依赖，符合依赖方向红线） |
| spec-08（可选 api.py） | `app/api/v1/api.py`（挂载点）、`app/core/limiter.py`（slowapi）、`app/core/logging.py`（structlog 已配置时幂等跳过） |
| spec-09 | `README.md`、全仓 grep 审计面 |

外部接口依赖：langgraph 1.0.2 公开 API（spec-02/06 验证）、langchain-openai / langchain-anthropic Chat 模型、httpx 同步 API。

## 4. 风险登记

| 风险 | 缓解 |
| --- | --- |
| langgraph 1.x 与原文档 0.2-0.7 的 API 差异（pydantic state / path_map） | R-EXP + `api-exploration-1x.md` 前置消化（spec-00 TC4-6 全量实测）；spec-02/06 保留验证性集成测试兜底；发现冲突停下提问（AI 协作守则） |
| 探索发现核心假设不成立（如 langgraph 1.x 拒绝 pydantic state schema） | 触发 CONTRACT §11 变更流程，评估回退方案（TypedDict state 等），**禁止擅自改设计** |
| ruff 规则扩展（T20/BLE/S）对既有 app 代码产生新告警 | spec-00 以最小 per-file-ignores 收口并记录 |
| tenacity 与原文档手写重试的语义映射（retry 谓词、退避序列、耗尽异常类型） | spec-04/05 契约测试锁定（AD-03） |
| structlog processor 脱敏与原文档 RedactFilter 的行为差异 | spec-08 测试锁定（caplog/capsys 断言，AD-02） |

## 5. AI 委派用法

继承《01·分阶段开发计划》A.6 流程（一次只派一个阶段），材料清单按本目录口径更新：

**第 1 步 · 会话预热（强制阅读）**：

1. `CONTRACT.md` 全文（编码契约，含红线 R1-R10 机器门禁与 AD-01..12）。
2. 目标 `spec-0N` 全文（任务卡 TC、TDD 要点、DoD）。
3. `api-exploration-1x.md` 中该 spec 门禁对应的 EXP 组实测记录（R-EXP：未闭环不得开工）。

**第 2 步 · 任务提示词模板**：

```text
你正在实现"工作流引擎重实现"项目的 spec-{NN}（{阶段名}）。

【强制阅读】
- docs/workflow-reimpl-plan/spec/CONTRACT.md（契约基准，冲突时最高效力）
- docs/workflow-reimpl-plan/spec/spec-{NN}-*.md 全文
- docs/workflow-reimpl-plan/spec/api-exploration-1x.md 中本 spec 门禁 EXP 组的实测记录

【边界（违反即失败）】
- 只做该 spec §5 任务清单中的 TC 卡，不提前实现后续阶段
- 接口签名必须与 CONTRACT §4 冻结清单逐字一致；发现契约冲突：停下，先提问，不得自行折中
- 不新增节点类型、不硬编码业务字段名、不硬编码密钥（R1/R2/R5）
- 测试全程零真实网络/真实 LLM 调用

【流程】
1. 严格 TDD：先写失败测试并运行确认 RED → 最小实现 GREEN → 重构
2. 每完成一张 TC 卡即运行：uv run pytest -m unit、make lint、make typecheck
3. 每张 TC 卡对应 1~N 个 conventional commit

【交付物】
- 代码 + 测试 + DoD 逐条自评表（通用 G1..G8 + 红线自检表 + 该 spec §8 专属 DoD）
- 结尾给出"偏离与疑问清单"（若无偏离写"无"）
```

**第 3 步 · 验收**（人类或评审代理）：

1. 运行该 spec §11 验收命令全量。
2. 逐条核对 DoD 自评表，重点抽查：失败路径是否有测试（H2）、日志是否泄露 state/secret（H6）、是否有范围蔓延（R1）。
3. 对照 CONTRACT §4 核对公开签名——**签名是跨阶段合同，偏差必须打回**。

**AI 协作守则（每阶段重申）**：先读 CONTRACT 与目标 spec；不扩大范围；遇到契约冲突先问；不确定时宁可停下来提问，也不要"自作主张地兼容"。
