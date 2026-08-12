# 02 · LLM 集成与成本控制

> 本章覆盖：模型选择与别名、运行时切换、重试与超时（🔧 Python 层无重试 API，须经 env 注入）、thinking / effort / beta 能力、成本与用量控制（`max_turns` / `max_budget_usd` / `task_budget`）及观测点。
>
> 版本基准：`claude-agent-sdk 0.2.135`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。
> 主要出处：官方 Python 参考页 <https://code.claude.com/docs/en/agent-sdk/python>、`src/claude_agent_sdk/types.py`。

---

## 目录

1. [模型选择：别名、完整 ID 与回退模型](#1-模型选择别名完整-id-与回退模型)
2. [重试与超时：Python 层无重试 API（🔧）](#2-重试与超时python-层无重试-api)
3. [thinking、effort 与 beta 能力](#3-thinkingeffort-与-beta-能力)
4. [网关接入（Bedrock / Vertex / Foundry）](#4-网关接入bedrock--vertex--foundry)
5. [成本控制与用量观测](#5-成本控制与用量观测)
6. [后续章节导航](#6-后续章节导航)

---

## 1. 模型选择：别名、完整 ID 与回退模型

出处：官方 Python 参考页（`ClaudeAgentOptions` 字段表）、`src/claude_agent_sdk/types.py::ClaudeAgentOptions`。

| 字段 | 类型 | 语义 |
|---|---|---|
| `model` | `str \| None` | 模型**别名**（如 `sonnet` / `opus` / `haiku` / `inherit`）或**完整模型 ID**；`None` = 用 CLI 默认模型（随 CLI 版本漂移，生产建议显式指定） |
| `fallback_model` | `str \| None` | 主模型**失败时**的回退模型；**单值、非链式**（只有一级回退） |

```python
from claude_agent_sdk import ClaudeAgentOptions, query

options = ClaudeAgentOptions(
    model="claude-sonnet-4-5",      # 别名 "sonnet" 亦可
    fallback_model="haiku",         # 主模型失败时回退（仅一级）
)
```

补充两点：

- **运行时切换**：`ClaudeSDKClient.set_model(model)` 可在长会话中途换模型，传 `None` 重置为默认（出处：官方 Python 参考页 `ClaudeSDKClient` 方法表、`src/claude_agent_sdk/client.py`）。
- **`"inherit"` 别名**：用于 `AgentDefinition.model`，表示子代理继承父代理当前模型（详见 [04 章](./04-Sub-Agent架构.md)）。

---

## 2. 重试与超时：Python 层无重试 API（🔧）

**关键结论（🔧 需自行实现的缺口）**：`ClaudeAgentOptions` **没有**任何重试参数字段——重试、超时、看门狗全部发生在 CLI 侧，只能**经环境变量注入**（通过 `ClaudeAgentOptions.env` 传给 CLI 子进程）。LLM 调用不在 SDK 进程内（见 [01 章第 1 节](./01-架构总览与运行时数据流.md#1-子进程架构)），因此宿主层的 tenacity 装饰器**包不住** LLM 调用本身。

出处：官方 Python 参考页「Handle slow or stalled API responses」节、`src/claude_agent_sdk/types.py::ClaudeAgentOptions.env`。

| 环境变量 | 默认 | 语义 |
|---|---|---|
| `API_TIMEOUT_MS` | `600000` | Anthropic client 单请求超时（毫秒）；作用于主循环与全部子代理 |
| `CLAUDE_CODE_MAX_RETRIES` | `10`（上限 `15`） | API 最大重试次数；每次重试独享一个 `API_TIMEOUT_MS` 窗口 |
| `CLAUDE_CODE_RETRY_WATCHDOG` | 未设 | 设 `1`：容量错误（capacity error）**无限重试**，适配无人值守长跑；v2.1.199 起其他瞬时错误默认提到 `300` 次且移除该变量上限 |
| `CLAUDE_STREAM_IDLE_TIMEOUT_MS` | `300000`（且被钳制为该下限） | 流看门狗：响应头已到但 body 停止流式时中止请求；配合 `CLAUDE_ENABLE_STREAM_WATCHDOG`（默认全 provider 开启，设 `0` 关闭） |

```python
options = ClaudeAgentOptions(
    env={
        "API_TIMEOUT_MS": "120000",          # 单请求 2 分钟
        "CLAUDE_CODE_MAX_RETRIES": "2",      # 最多重试 2 次
    },
)
```

**最坏墙钟耗时 ≈ `API_TIMEOUT_MS × (CLAUDE_CODE_MAX_RETRIES + 1) + backoff`**——宿主侧 HTTP 超时与任务 SLA 必须按此公式留余量，否则会出现「宿主先超时、CLI 子进程仍在重试」的悬挂。

**与本项目 tenacity 模式的对照**（本项目规约：重试用 tenacity 指数退避，见 AGENTS.md）：

| 维度 | 本项目（tenacity） | claude-agent-sdk |
|---|---|---|
| 重试声明位置 | Python 代码（`@retry` 装饰器） | CLI 侧 env 变量（`ClaudeAgentOptions.env` 注入） |
| 退避策略 | `wait_exponential` 可编程 | CLI 内建 backoff，不可编程 |
| 可重试谓词 | `retry_if_exception` 自定义 | CLI 内建（含容量错误 watchdog 开关） |
| 宿主可观测 | tenacity 回调 | 只能经消息流（`RateLimitEvent` 等）间接观测 |

> 集成建议：宿主 tenacity 仍可用于**包 `query()` 整体调用**（进程崩溃、`ProcessError` 级别的会话级重试），但**单请求级重试只能交给 CLI env**。

---

## 3. thinking、effort 与 beta 能力

出处：官方 Python 参考页（`ThinkingConfig` / `EffortLevel` / `SdkBeta` 节）、`src/claude_agent_sdk/types.py`。

| 字段 | 类型 | 语义 |
|---|---|---|
| `thinking` | `ThinkingConfig \| None` | 控制扩展思考：`adaptive` / `enabled` + `budget_tokens` / `disabled`；可附加 `display: "summarized" \| "omitted"` |
| `max_thinking_tokens` | `int \| None` | **已废弃**——用 `thinking` 替代；两者同传时 `thinking` 优先 |
| `effort` | `EffortLevel \| None` | 思考深度档位：`low` / `medium` / `high` / `xhigh` / `max` |
| `betas` | `list[SdkBeta]` | beta 能力开关，如 `betas=["context-1m-2025-08-07"]` 启用 **1M 上下文** |

```python
options = ClaudeAgentOptions(
    model="claude-sonnet-4-5",
    thinking={"type": "enabled", "budget_tokens": 8000},  # TypedDict：运行时是 dict
    betas=["context-1m-2025-08-07"],                      # 1M 上下文 beta
)
```

> ⚠️ `ThinkingConfigEnabled` 等是 `TypedDict`，**运行时为普通 dict**，取值须 `config["budget_tokens"]` 而非属性访问（出处：官方 Python 参考页 Types 节 Note）。

---

## 4. 网关接入（Bedrock / Vertex / Foundry）

除直连 Anthropic API（`ANTHROPIC_API_KEY`）外，亦可经云厂商网关调用，**切换方式为对应网关的环境变量开关**（经宿主环境或 `ClaudeAgentOptions.env` 注入），SDK/CLI 侧无需额外代码（出处：官方文档 <https://code.claude.com/docs/en/agent-sdk/overview> provider 节及 Claude Code 各 provider 专页；官方页本次调研环境直连超时，变量名经 Microsoft Learn 官方页与多个二手源交叉核实，落地前复核）：

| 网关 | 启用开关 | 凭证要求 |
|---|---|---|
| AWS Bedrock | `CLAUDE_CODE_USE_BEDROCK=1` | AWS 凭证（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` 或 profile / IAM）+ `AWS_REGION` |
| Google Vertex | `CLAUDE_CODE_USE_VERTEX=1` | GCP 应用默认凭证（ADC）+ 项目/区域变量（如 `ANTHROPIC_VERTEX_PROJECT_ID`） |
| Microsoft Foundry | `CLAUDE_CODE_USE_FOUNDRY=1` | Foundry endpoint / key 相关变量（见官方 microsoft-foundry 页） |

```python
# 示例：经 AWS Bedrock 网关调用（凭证由宿主环境注入，勿硬编码）
options = ClaudeAgentOptions(
    model="<bedrock-model-id>",  # 占位：网关路径下 model 需 provider 专属 ID
    env={
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": "us-west-2",
    },
)
```

> 具体变量全集与模型 ID 映射以官方 overview / model-config 页为准（出处：官方 Python 参考页 `model` 字段行链接的 model-config 页）。本项目密钥红线不变：一律 env 注入，禁止硬编码。

---

## 5. 成本控制与用量观测

出处：官方 Python 参考页（字段表 `max_budget_usd` / `task_budget` 行）、官方成本追踪页 <https://code.claude.com/docs/en/agent-sdk/cost-tracking>、`src/claude_agent_sdk/types.py::ResultMessage`。

### 5.1 控制手段

| 字段 | 语义 |
|---|---|
| `max_turns` | 最大 agentic turn 数（一次工具调用往返 = 一轮）；防工具循环失控 |
| `max_budget_usd` | **客户端成本估算**达到该 USD 值即停止查询；与 `total_cost_usd` 同一口径估算（精度注意事项见官方 cost-tracking 页）；超限产出 `ResultMessage.subtype = "error_max_budget_usd"` |
| `task_budget` | **API 侧 token 预算**（beta）：以 `output_config.task_budget` 随 `task-budgets-2026-03-13` beta header 发送，传 `{"total": <int>}` |

```python
options = ClaudeAgentOptions(
    max_turns=20,          # 轮数闸
    max_budget_usd=2.0,    # 美元闸（客户端估算）
)
```

### 5.2 观测点

| 数据源 | 内容 |
|---|---|
| `ResultMessage.total_cost_usd` | 本次会话成本估算（USD） |
| `ResultMessage.usage` / `ResultMessage.model_usage` | token 用量（按模型维度） |
| `RateLimitEvent` | 限流事件（消息流中产出） |

> ⚠️ `max_budget_usd` 是**客户端估算**而非 API 计费口径，做计费硬闸时须留余量；宿主侧应按会话持久化 `ResultMessage` 的用量字段做账（出处：官方 cost-tracking 页的 accuracy caveats）。
>
> 完整示例见 [`examples/02_model_retry_fallback.py`](./examples/02_model_retry_fallback.py)。

---

## 6. 后续章节导航

| 下一步 | 文档 |
|---|---|
| system prompt 三形态、setting_sources 与 prompt cache | [03-System-Prompt系统.md](./03-System-Prompt系统.md) |
| 子代理的模型继承（`inherit`）与独立 effort | [04-Sub-Agent架构.md](./04-Sub-Agent架构.md) |
| 上一章：子进程架构与消息流 | [01-架构总览与运行时数据流.md](./01-架构总览与运行时数据流.md) |
| 返回导航 | [README.md](./README.md) |
