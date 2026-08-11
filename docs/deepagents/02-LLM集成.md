# 02 · LLM 集成

> 本章覆盖：`model` 参数的两种传法与 `resolve_model` 解析链路、`ProviderProfile` 与 `HarnessProfile` 的定位区别、重试/回退的能力边界（显式声明）、默认模型弃用史、prompt-caching 中间件行为。每条关键结论均标注源码出处。
>
> 版本基准：`deepagents 0.7.5`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。

---

## 目录

1. [model 参数的两种传法](#1-model-参数的两种传法)
2. [字符串路径：resolve_model 与 ProviderProfile 叠加](#2-字符串路径resolve_model-与-providerprofile-叠加)
3. [ProviderProfile 与 HarnessProfile 的定位区别](#3-providerprofile-与-harnessprofile-的定位区别)
4. [重试与回退：deepagents 不内置（显式声明）](#4-重试与回退deepagents-不内置显式声明)
5. [默认模型弃用史：model=None](#5-默认模型弃用史modelnone)
6. [prompt-caching 中间件行为](#6-prompt-caching-中间件行为)

---

## 1. model 参数的两种传法

`create_deep_agent(model=...)` 的类型签名是 `str | BaseChatModel | None`（出处：`libs/deepagents/deepagents/graph.py::create_deep_agent`）。两种有效传法：

| 传法 | 行为 | 实现状态 |
|---|---|---|
| `"provider:model"` 字符串 | 经 `_models.py::resolve_model()` → LangChain `init_chat_model` + `ProviderProfile` 叠加参数 | ✅ 官方已实现 |
| `BaseChatModel` 实例 | **原样透传**，不经任何解析或改写 | ✅ 官方已实现 |

`resolve_model` 的实现只有两条分支，非常克制（出处：`libs/deepagents/deepagents/_models.py::resolve_model`）：

```python
def resolve_model(model: str | BaseChatModel) -> BaseChatModel:
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model, **apply_provider_profile(model))
```

要点：

- **实例透传意味着调用方完全掌控模型构造**——`temperature`、`max_tokens`、`base_url`、自定义 header、绑定行为等全部由调用方负责，deepagents 不做二次加工。需要精细控制（含本项目的 tenacity 重试包装，见第 4 节）时优先直传实例。
- 字符串路径的 provider 识别、类名映射全部委托给 LangChain 的 `init_chat_model`，deepagents 只在其上叠加 `ProviderProfile`（第 2 节）。
- 辅助内省函数：`get_model_identifier`（读 `model_name` / `model` 属性）与 `get_model_provider`（读 `_get_ls_params()` 的 `ls_provider`），供 harness profile 匹配与模型替换判断使用（出处：`_models.py`）。

---

## 2. 字符串路径：resolve_model 与 ProviderProfile 叠加

字符串形态的解析链路：

```text
"openai:gpt-5.5"
  → apply_provider_profile(spec)        # 查注册表、跑 pre_init、合并 kwargs
  → init_chat_model(spec, **merged)     # LangChain 标准模型工厂
  → BaseChatModel 实例
```

出处：`libs/deepagents/deepagents/_models.py`、`libs/deepagents/deepagents/profiles/provider/provider_profiles.py`。

### 2.1 ProviderProfile 的三个字段

`ProviderProfile` 是 frozen dataclass，只管「模型怎么构造」这一件事（出处：`provider_profiles.py::ProviderProfile`）：

| 字段 | 作用 |
|---|---|
| `init_kwargs` | 静态 kwargs，原样转发给 `init_chat_model`（构造后冻结为只读视图） |
| `pre_init` | 初始化前副作用钩子（如最低版本校验），抛异常即中止模型构造 |
| `init_kwargs_factory` | 运行时动态 kwargs 工厂（如读环境变量）；与 `init_kwargs` 键冲突时**工厂胜出** |

### 2.2 内置注册（懒加载）

首次访问注册表时懒加载三个内置 profile（出处：`provider_profiles.py` 头部注释与 `_builtin_profiles`）：

| provider | 内置行为 |
|---|---|
| `nvidia` | 注入 NVIDIA NIM app 归因头 |
| `openrouter` | 强制最低版本校验 + 注入 OpenRouter app 归因头 |
| `openai` | 默认启用 OpenAI Responses API |

> OpenAI 数据保留提示：`openai:` 字符串默认走 Responses API。要关闭或调整（如 `store=False`），需自行 `init_chat_model("openai:...", use_responses_api=True, store=False, include=["reasoning.encrypted_content"])` 后直传实例（出处：`graph.py::create_deep_agent` docstring 的 "OpenAI Models and Data Retention" 框）。

### 2.3 注册与合并语义

- `register_provider_profile(key, profile)`：key 可以是 provider 级（`"openai"`）或整 spec 级（`"openai:gpt-5.4"`）。注册是**叠加式合并**而非替换：`init_kwargs` 按键合并新者胜出、`pre_init` 链式执行、`init_kwargs_factory` 双跑合并（出处：`provider_profiles.py::register_provider_profile`、`_merge_provider_profiles`）。
- 查找顺序（`get_provider_profile`）：精确 spec 匹配 → provider 前缀匹配 → `None`；两级同时命中时合并，精确级胜出。
- 实现状态：内置三项 ✅ 官方已实现；自定义 provider 接入 ✅ 官方已实现（`register_provider_profile` 为公共导出）。

---

## 3. ProviderProfile 与 HarnessProfile 的定位区别

deepagents 把「按 provider 调模型构造」与「按模型调 harness 行为」拆成两套 profile，源码中明确划界（出处：`provider_profiles.py::ProviderProfile` docstring——“Runtime and harness behavior … belongs in `HarnessProfile`, … not here.”）：

| 维度 | `ProviderProfile` | `HarnessProfile` |
|---|---|---|
| 回答的问题 | 模型**怎么构造**（kwargs / header / 前置校验） | 构造好之后 harness **怎么跑**（prompt / 工具面 / 中间件） |
| 键粒度 | provider 或 `provider:model` | provider 或 `provider:model` |
| 关键能力 | `init_kwargs`、`pre_init`、`init_kwargs_factory` | `base_system_prompt` / `system_prompt_suffix`、`extra_middleware`、`excluded_middleware` / `excluded_tools`、`tool_description_overrides`、`general_purpose_subagent` |
| 消费方 | `resolve_model`（模型解析期） | `create_deep_agent`（图组装期，`_harness_profile_for_model`） |
| 注册入口 | `register_provider_profile` | `register_harness_profile`（另支持 entry-point 插件注册） |
| 出处 | `deepagents/profiles/provider/provider_profiles.py` | `deepagents/profiles/harness/harness_profiles.py` |

两者均为 beta API（官方 docstring 标注），且均在 `deepagents/__init__.py` 公共导出。profiles 在中间件栈中的参与方式见 [01 章第 6 节](./01-架构总览与运行时数据流.md#6-profiles-机制)。

---

## 4. 重试与回退：deepagents 不内置（显式声明）

> 🔧 **需自行实现** —— **deepagents 0.7.5 自身没有任何重试（retry）或多模型回退（fallback）实现**。`resolve_model` 只做一次性模型解析，`create_deep_agent` 组装出的图在模型调用失败时直接向上抛错。`graph.py`、`_models.py` 全文检索无 retry / fallback / backoff 相关逻辑（已核实）。
>
> 同理注意：deepagents 也**没有** `init_model` 之类的 API、**没有**内置 MCP 集成——这些在其他章节另行说明。

### 4.1 生态组合路径（🔶 生态库组合实现）

模型以 `BaseChatModel`（LangChain `Runnable`）形态存在，可直接套用 LangChain 原生的 Runnable 组合子，在**传入 deepagents 之前**包装：

```python
from langchain.chat_models import init_chat_model

primary = init_chat_model("anthropic:claude-sonnet-4-6")

model = primary.with_retry(
    stop_after_attempt=3,
    wait_exponential_jitter=True,
).with_fallbacks([
    init_chat_model("openai:gpt-5.5"),
])

agent = create_deep_agent(model=model, ...)
```

- `.with_retry(...)`：对可重试异常做指数退避重试（返回 `RunnableRetry`）。
- `.with_fallbacks([...])`：主模型耗尽重试后依序切换后备模型；最终包装产物是外层 `RunnableWithFallbacks` 包内层 `RunnableRetry`。
- 注意取舍：包装后传入的是 `RunnableWithFallbacks`（外层）包 `RunnableRetry`（内层）而非裸 `BaseChatModel`，`isinstance(wrapped, BaseChatModel)` 为 False——deepagents 的 `isinstance(model, BaseChatModel)` 透传分支不命中，以及依赖 `_get_ls_params()` 的 harness profile 匹配是否命中，需在集成验证时实测（`_models.py::get_model_provider` 在取不到 provider 时仅记日志并返回 `None`）。拿不准时，也可改为自行构造实例并直传。

### 4.2 与本项目 app/services/llm/ 的对照

本项目 `app/services/llm/service.py` 的 `LLMService` 已有一套自研方案，可作为集成评估的参照（出处：本地 `app/services/llm/service.py`、`app/services/llm/registry.py`）：

| 维度 | deepagents | 本项目 `LLMService` |
|---|---|---|
| 重试 | 无（🔧） | tenacity `@retry`：指数退避（min=2s, max=10s），仅对 `RateLimitError` / `APITimeoutError` / `APIError` 重试（符合项目规范「重试一律 tenacity」） |
| 回退 | 无（🔧） | **循环回退**：`LLMRegistry` 中的模型按序轮转，全量模型耗尽才抛 `RuntimeError` |
| 工具绑定保持 | 不适用 | 默认路径切换模型后重新 `bind_tools`，保证 agent 路径工具绑定不丢 |
| 总预算 | 无 | `asyncio.wait_for` 总超时（`LLM_TOTAL_TIMEOUT`） |
| 结构化输出 | 经 `create_agent(response_format=...)` | `call(response_format=Schema)` 链式 `.with_structured_output` |

**对照结论**：若引入 deepagents，重试/回退缺口有三条落地路径——① LangChain `.with_retry()/.with_fallbacks()` 包装后直传（最省事）；② 自定义中间件在 `wrap_model_call` 钩子里包 tenacity（符合项目规范，且能拿到 structlog 上下文）；③ 保留 `LLMService` 作为模型供给层，由它产出实例后直传 `create_deep_agent`。方案取舍详见 09 章。

---

## 5. 默认模型弃用史：model=None

> ⚠️ **防踩坑**：`model=None`（依赖默认模型 `ChatAnthropic(model_name="claude-sonnet-4-6")`）**自 `0.5.3` 起弃用，将在 `1.0.0` 移除**，届时 `model` 参数类型收窄为 `BaseChatModel | str`（出处：`graph.py::create_deep_agent` 的 `warn_deprecated` 调用与 docstring deprecated 框）。

时间线：

| 版本 | 事件 |
|---|---|
| `0.5.3` | `model=None` 与 `get_default_model()` 标记 deprecated，每进程警告一次 |
| `0.7.5`（当前） | 仍可工作：内部走未加装饰器的 `_build_default_model()` 构造 `ChatAnthropic("claude-sonnet-4-6")`，避免与 `get_default_model` 的警告去重标志互相吞噬 |
| `1.0.0` | 移除默认模型与 `model=None` 支持 |

补充：默认模型要求环境变量 `ANTHROPIC_API_KEY`（出处：`graph.py::get_default_model` docstring）。**本项目集成时一律显式传 `model`**，不依赖该缺省路径。

---

## 6. prompt-caching 中间件行为

出处：`libs/deepagents/deepagents/middleware/_prompt_caching.py::append_prompt_caching_middleware`。实现状态：✅ 官方已实现（自动挂载，无需调用方干预）。

装配规则（主代理、每个声明式子代理、general-purpose 子代理的中间件栈**均会调用**）：

| 中间件 | 装配条件 | 非目标模型行为 |
|---|---|---|
| `AnthropicPromptCachingMiddleware` | **无条件装配**（来自 `langchain-anthropic`） | `unsupported_model_behavior="ignore"`，即 no-op |
| `BedrockPromptCachingMiddleware` | 仅当已安装 `langchain-aws`（`import_module` 探测，缺失静默跳过） | 同上 no-op |
| `FireworksPromptCachingMiddleware` | 仅当已安装 `langchain-fireworks` | 同上 no-op |

与中间件栈顺序的配合（出处：`graph.py` 装配段注释）：

- prompt-caching 位于**尾部链**（profile `extra_middleware` 之后、`MemoryMiddleware` 之前）；
- memory 更新会改 system prompt，若在其后再叠中间件会破坏 Anthropic prompt cache 前缀——当前顺序保证缓存断点打在稳定前缀上。

对非 Anthropic/Bedrock/Fireworks 模型（如 OpenAI），这三个中间件全部 no-op，无运行时开销。若需显式控制 Anthropic 的 `cache_control` 断点，改从 `system_prompt` 传 `SystemMessage`，见 [03 章第 3 节](./03-System-Prompt系统.md#3-systemmessage-与-cache_control-断点)。

---

*上一章：[01-架构总览与运行时数据流.md](./01-架构总览与运行时数据流.md) · 返回导航：[README.md](./README.md) · 下一章：[03-System-Prompt系统.md](./03-System-Prompt系统.md)*
