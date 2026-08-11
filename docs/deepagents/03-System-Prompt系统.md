# 03 · System Prompt 系统

> 本章覆盖：最终 system prompt 的组装顺序（`USER` → `BASE` → `SUFFIX`）、`BASE_AGENT_PROMPT` 弃用时间线、`SystemMessage` 与 `cache_control` 断点、中间件动态注入链路、`memory=` 参数、多环境模板化建议。每条关键结论均标注源码出处。
>
> 版本基准：`deepagents 0.7.5`（调研日期 2026-08-11）。导航见 [README.md](./README.md)。

---

## 目录

1. [组装顺序：USER → BASE → SUFFIX](#1-组装顺序user--base--suffix)
2. [BASE_AGENT_PROMPT 弃用时间线（防踩坑）](#2-base_agent_prompt-弃用时间线防踩坑)
3. [SystemMessage 与 cache_control 断点](#3-systemmessage-与-cache_control-断点)
4. [中间件动态注入链路](#4-中间件动态注入链路)
5. [memory= 参数：AGENTS.md 注入](#5-memory-参数agentsmd-注入)
6. [多环境模板化建议（对照本项目）](#6-多环境模板化建议对照本项目)

---

## 1. 组装顺序：USER → BASE → SUFFIX

实现状态：✅ 官方已实现。

`create_deep_agent(system_prompt=...)` 接收的是 **`USER` 段**（调用方撰写的指令）。最终 authored prompt 按 `USER` → `BASE` → `SUFFIX` 组装，段间以**空行**分隔（出处：`libs/deepagents/deepagents/graph.py::create_deep_agent` docstring 的 `system_prompt` 段与函数体组装代码）：

| 段 | 来源 | 0.7.5 默认 |
|---|---|---|
| `USER` | 调用方 `system_prompt` 参数（`str` 或 `SystemMessage`） | `None`（空） |
| `BASE` | 激活 `HarnessProfile` 的 `base_system_prompt`（设置时**整体替换**底层 base） | **空**（主代理默认无 authored base prompt） |
| `SUFFIX` | profile 的 `system_prompt_suffix`（排在最后，靠近对话历史） | **空** |

> 关键结论：**自 0.7.0 起，`BASE` 与 `SUFFIX` 默认均为空**。`system_prompt=None` 且无 profile prompt 内容时，模型收到的是空的 authored system prompt——deepagents 不再自带任何默认行为提示词（出处：`harness_profiles.py::HarnessProfile.base_system_prompt` docstring——“the main agent has no authored base prompt by default”）。

组装代码（出处：`graph.py` 尾部，`_apply_profile_prompt` 定义于 `profiles/harness/harness_profiles.py`）：

```python
base_prompt = _apply_profile_prompt(_profile, "")   # BASE + SUFFIX 的合并结果
if system_prompt is None:
    final_system_prompt = base_prompt
elif isinstance(system_prompt, SystemMessage):
    if base_prompt:
        # profile 内容作为追加的 text content block，不打断原有 blocks
        final_system_prompt = SystemMessage(content_blocks=[
            *system_prompt.content_blocks,
            {"type": "text", "text": f"\n\n{base_prompt}"},
        ])
    else:
        final_system_prompt = system_prompt
else:
    final_system_prompt = system_prompt + (f"\n\n{base_prompt}" if base_prompt else "")
```

`_apply_profile_prompt` 的叠加语义（出处：`harness_profiles.py::_apply_profile_prompt`）：`base_system_prompt` 设置时**整体替换**传入的 base；`system_prompt_suffix` 设置时以空行分隔追加；两者独立可选。

子代理同样走这条链路：声明式 `SubAgent` 的 `system_prompt` 在 `graph.py` 中经 `_apply_profile_prompt(_subagent_profile, spec["system_prompt"])` 叠加其自身模型对应 profile 的内容（子代理可用不同模型、命中不同 profile）。

---

## 2. BASE_AGENT_PROMPT 弃用时间线（防踩坑）

> ⚠️ 旧教程与旧代码中常见的 `from deepagents.graph import BASE_AGENT_PROMPT` 已进入弃用通道，**不要在新代码中依赖**。

| 版本 | 事件 | 出处 |
|---|---|---|
| `< 0.7.0` | `BASE_AGENT_PROMPT` 作为模块级常量提供默认基础提示词 | 历史版本 |
| `0.7.0` | **deprecated**：常量从模块移除，改由 `graph.py::__getattr__` 拦截访问并触发 `warn_deprecated`（`since="0.7.0"`），返回 `_LEGACY_BASE_AGENT_PROMPT` 兼容文本 | `graph.py::__getattr__` |
| `0.9.0` | **计划移除**：届时访问 `BASE_AGENT_PROMPT` 将直接抛 `AttributeError` | `warn_deprecated(removal="0.9.0")` |

官方给出的理由（弃用消息原文）：*“Deep Agents no longer provides an authored base prompt.”*——行为指引职责已转移给各模型的 `HarnessProfile` 与调用方自己的 `system_prompt`。

迁移建议：

- 需要旧文案时，自行复制 `_LEGACY_BASE_AGENT_PROMPT` 内容（0.7.5 源码仍可见全文）作为 `system_prompt` 传入；
- 或注册 `HarnessProfile(base_system_prompt=...)` 让特定模型族自动获得 BASE 段。

---

## 3. SystemMessage 与 cache_control 断点

实现状态：✅ 官方已实现。

`system_prompt` 除 `str` 外可传 `SystemMessage`，**其既有 content blocks 上的 `cache_control` 标记会被完整保留**——这是显式控制 Anthropic prompt-cache 断点的官方通道（出处：`graph.py::create_deep_agent` docstring——“Passing a `SystemMessage` preserves any `cache_control` markers on its existing content blocks”）。

写法示例（关键片段；完整可运行示例见 [examples/08_system_prompt_cache_control.py](./examples/08_system_prompt_cache_control.py)）：

```python
from langchain_core.messages import SystemMessage

system_prompt = SystemMessage(
    content=[
        {
            "type": "text",
            "text": LONG_STABLE_INSTRUCTIONS,   # 大段稳定指令
            "cache_control": {"type": "ephemeral"},  # Anthropic 缓存断点
        },
    ]
)

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    system_prompt=system_prompt,
)
```

行为要点（出处：`graph.py` 组装段）：

- profile 有 prompt 内容时，`BASE`/`SUFFIX` 合并结果作为**追加的 text block** 接在调用方 blocks 之后，不会改写或打断调用方 block 上的 `cache_control`；
- profile 无内容时，`SystemMessage` 原样使用；
- 与尾部 prompt-caching 中间件（见 [02 章第 6 节](./02-LLM集成.md#6-prompt-caching-中间件行为)）配合：中间件自动打断点适用于常规场景，`SystemMessage` 断点适用于需要精确控制缓存位置的进阶场景。

---

## 4. 中间件动态注入链路

实现状态：✅ 官方已实现。

组装期的静态 prompt 只是起点——运行期每次模型调用前，各中间件通过 `wrap_model_call` 钩子**动态追加**内容。共用工具是 `append_to_system_message`（出处：`libs/deepagents/deepagents/middleware/_utils.py`）：

```python
def append_to_system_message(system_message: SystemMessage | None, text: str) -> SystemMessage:
    new_content = list(system_message.content_blocks) if system_message else []
    if new_content:
        text = f"\n\n{text}"
    new_content.append({"type": "text", "text": text})
    return SystemMessage(content_blocks=new_content)
```

语义：把追加文本作为**新的 text content block** 附加（而非字符串拼接），对 `SystemMessage` 多 block 结构与 `cache_control` 标记安全。

典型注入方（出处：各中间件的 `wrap_model_call` / `awrap_model_call`）：

| 中间件 | 动态追加内容 | 触发条件 |
|---|---|---|
| `SkillsMiddleware` | 加载到的 skill 索引片段 | 传入 `skills=` |
| `MemoryMiddleware` | `AGENTS.md` 记忆内容（见第 5 节） | 传入 `memory=` |
| `SubAgentMiddleware` / `AsyncSubAgentMiddleware` | 子代理使用指引 + 可用代理清单 | 提供 `system_prompt` 时（`graph.py` 默认不传，可用代理清单改经 `task` 工具描述下发，见 [04 章第 2 节](./04-Sub-Agent架构.md#2-task-工具调度机制)） |
| `FilesystemMiddleware` | composite backend 的 host-path 路由说明（非 composite 时为空） | 按 backend 类型 |

设计动机（出处：`graph.py` 装配段注释）：内置工具的用法 prose 与工具 schema 描述重复，deepagents 自有中间件默认不再注入这类静态文案，只保留必要的**动态**部分（技能索引、记忆内容、可用代理清单、host-path 路由）——既缩小 prompt 体积，也让缓存前缀更稳定。

> 对自定义中间件的启示：向 system prompt 追加内容时复用 `append_to_system_message`，保持 block 语义一致；动态内容尽量放尾部，避免破坏缓存前缀（详见 09 章）。

---

## 5. memory= 参数：AGENTS.md 注入

实现状态：✅ 官方已实现。

`create_deep_agent(memory=["/memory/AGENTS.md", ...])`（出处：`graph.py::create_deep_agent` 的 `memory` 参数与装配段）：

- `memory` 是 **`AGENTS.md` 记忆文件路径列表**，路径相对 backend 根目录（POSIX 约定）；显示名自动从路径派生；
- 装配 `MemoryMiddleware(backend=backend, sources=memory, add_cache_control=True)`——**agent 启动时加载记忆文件并注入 system prompt**；
- `add_cache_control=True` 是安全的：该中间件仅在请求模型为 Anthropic 时才真正打 `cache_control` 断点（出处：`graph.py` 装配段注释）；
- 位置在尾部链的 prompt-caching 之后、`HumanInTheLoopMiddleware` 之前（栈顺序见 [01 章第 3 节](./01-架构总览与运行时数据流.md#3-默认中间件栈完整顺序)）。

与 `StateBackend`（默认）配合时，记忆文件需经 `invoke(files={...})` 预置进 state；`FilesystemBackend` 则直接从磁盘读取。skills 与 memory 的 backend 存取细节见 05 章。

---

## 6. 多环境模板化建议（对照本项目）

deepagents 的 `system_prompt` 只是组装期一次性入参，**不提供模板引擎与环境切换机制**（🔧 模板化需自行实现）。本项目已有现成模式可直接移植（出处：本地 `app/core/prompts/__init__.py`）：

```python
# 本项目现状：模块加载期一次性读 .md 模板，运行期 .format() 填槽，无每请求文件 I/O
with open(os.path.join(_PROMPTS_DIR, "system.md")) as _f:
    _SYSTEM_PROMPT_TEMPLATE = _f.read()

def load_system_prompt(username=None, **kwargs):
    return _SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=settings.PROJECT_NAME + " Agent",
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_context=...,
        **kwargs,
    )
```

组合建议：

| 需求 | 落地方式 |
|---|---|
| 模板外置、按环境差异化 | 沿用本项目 `app/core/prompts/*.md` + `.format()` 模式，产出字符串后传 `create_deep_agent(system_prompt=load_system_prompt(...))` |
| 按模型族差异化（而非按环境） | 注册 `HarnessProfile` 的 `base_system_prompt` / `system_prompt_suffix`，让 `USER` 段保持环境维度、`BASE/SUFFIX` 承担模型维度 |
| 运行期动态上下文（用户信息、时间等） | 静态槽位用模板填好；频繁变化的内容建议走自定义中间件 `wrap_model_call` 动态追加（第 4 节链路），避免每次变更都击穿缓存前缀 |
| 缓存敏感的大段稳定指令 | 用 `SystemMessage` + `cache_control`（第 3 节） |

两个维度正交：**环境差异走模板文件，模型差异走 HarnessProfile**，混在一起是常见坏味道。

---

*上一章：[02-LLM集成.md](./02-LLM集成.md) · 返回导航：[README.md](./README.md) · 下一章：[04-Sub-Agent架构.md](./04-Sub-Agent架构.md)*
