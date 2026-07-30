# spec-04 LLMNode（Phase 4）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 4 / M4 具体节点可用（与 spec-05 并行） |
| 人日估算 | **1.5** |
| 前置 spec | spec-03（`BaseNode` / 工厂 / `utils` 两工具）；**EXP-L1..L5 已闭环**（R-EXP：Chat 构造参数、AIMessage 形态、限流异常层级、SecretStr 脱敏、anthropic 版本区间的实测先于编码） |
| 后续依赖方 | spec-06（集成测试节点）、spec-07 |
| 涉及编号 | K10、R3/R5/R6/R10、H2（落实）/H4（规范）/H6（部分落实）、异常契约 `ConfigError`/`LLMNodeError`、AD-02/03/04 |

## 2. 目标

实现 `nodes/llm_node.py`：多供应商（openai/anthropic）、env 密钥、懒加载实例、429 指数退避重试（tenacity）、显式错误处理。所有失败路径必须有测试覆盖（H2）；所有密钥走环境变量、日志不泄露密钥与完整 state（H6）。

## 3. 前置依赖

- spec 间依赖：spec-03。
- EXP 门禁（R-EXP）：EXP-L1（构造参数）、EXP-L2（AIMessage 形态）、EXP-L3（限流异常层级 → tenacity retry 谓词写法）、EXP-L4（SecretStr 脱敏）结论为【吻合】；谓词写法以 EXP-L3 实测建议为准。
- 代码库依赖：`app/workflow/nodes/base.py`、`app/workflow/utils.py`、`app/workflow/models.py`（异常族）+ langchain-openai 1.0.2 / langchain-anthropic（spec-00 已装）/ tenacity 9.1.2。
- 外部依赖：**测试全程零真实网络**（mock `ChatOpenAI`/`ChatAnthropic`）。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-02**：日志用 structlog；`ExecutionLog.input_data` 只记消息条数与配置摘要（H6）。
- **AD-03**：`_invoke_with_retry` 用 tenacity 实现（`stop_after_attempt(max_retries+1)` + `wait_exponential(multiplier=retry_base_delay)` + retry 谓词），耗尽包装为 `LLMNodeError`（消息含尝试次数）；测试 monkeypatch `tenacity.nap.sleep`，断言重试次数与退避序列 `1,2,4`，不真睡。行为合同（S8）：仅异常消息命中 `429`/`rate`（不区分大小写，以 EXP-L3 实测谓词为准）时重试，其它异常立即重抛。
- **AD-04**：`ChatOpenAI`/`ChatAnthropic` 顶层导入；无 `ImportError` 懒加载分支（覆盖原文档口径）。
- **AD-12**：默认 env 名 openai → `OPENAI_API_KEY`/`OPENAI_BASE_URL`，anthropic → `ANTHROPIC_API_KEY`。

## 5. 任务清单

- [ ] **TC1 LLMConfig + 构造/校验/env 密钥解析（0.375d）**
  - 内容：按 CONTRACT §4.7 实现 `LLMConfig`（`extra="forbid"`；**无明文 `api_key` 字段**，H6/ADR-008）；`LLMNode.__init__`（dict 入参自动转 `LLMConfig`；`super().__init__(name, NodeType.LLM, llm_config.model_dump(), operator_log)`；`self._llm_instance = None` 懒加载 K10）；`validate_config`（`model_name` 非空，非法 `ValueError`）；`_resolve_api_key`（按 `api_key_env`/`base_url_env` 或 llm_type 默认 env 名解析；缺失 → `ConfigError`，消息含 env 名**不含密钥值**）
  - 产出文件：`app/workflow/nodes/llm_node.py`（目标 < 250 行）
  - TDD 节奏：先写 §7 前 3 项（RED）→ 实现（GREEN）
- [ ] **TC2 懒加载 + tenacity 429 退避 + func 进出管线（0.5d）**
  - 内容：`_get_llm_instance`（openai → `ChatOpenAI(...)`，anthropic → `ChatAnthropic(...)`）；`_invoke_with_retry`（tenacity，AD-03）；`build_runnable()` 内部 `func(state)` 七步：`convert_state_to_dict`（R3 入口）→ 取 messages（state 优先，其次实例 messages；皆空 → `ValueError`）→ `system_prompt` 非空前置 `SystemMessage` → `_invoke_with_retry` → 成功输出 `{"response": <content>, "model": model_name}` → `log_execution`（含 `execution_time_ms`）→ `map_output_to_state`（R3 出口）；异常分支：`log_execution(..., error=str(e))` 后 `raise`（H2/R6）；`return self.wrap_runnable(func)`（K4 tags）
  - 产出文件：`app/workflow/nodes/llm_node.py`
- [ ] **TC3 失败路径 + H6 防泄漏测试（0.5d）**
  - 内容：§7 中重试/失败/防泄漏全部用例（monkeypatch `tenacity.nap.sleep`；假 LLM side_effect 序列；`test_execution_log_no_secret_leak` 设哑密钥 env 后断言序列化串不含密钥值与完整 state）
  - 产出文件：`tests/unit/workflow/nodes/test_llm_node.py`
- [ ] **TC4 自注册/导出/DoD 核对（0.125d）**
  - 内容：模块底部 `register_node_type("llm", LLMNode)`；`nodes/__init__.py` 追加导出 `LLMNode`/`LLMConfig`；工厂内置 llm 分支联调转绿；§8 DoD 逐项核对
  - 产出文件：`app/workflow/nodes/llm_node.py`、`app/workflow/nodes/__init__.py`

## 6. 接口契约

见 CONTRACT §4.7（`LLMConfig` / `LLMNode` 签名）、§5（`ConfigError`/`LLMNodeError` 场景）、§6 S5/S8/S14/S15（进出管线、retry 语义、extra=forbid、日志摘要）。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_config_temperature_out_of_range` | `temperature=3` → `ValidationError` | pydantic |
| `test_missing_api_key_raises_clear_error` | 清空 env → `ConfigError`，消息含 `OPENAI_API_KEY` 字样、**不含任何密钥值** | `monkeypatch.delenv` |
| `test_api_key_env_override` | `api_key_env="CUSTOM_KEY"` → 从该 env 读密钥；缺失时消息含 `CUSTOM_KEY` | `monkeypatch.setenv`/`delenv` |
| `test_invoke_success_maps_state` | mock LLM 返回 `content="hi"` → 输出含 `response`、`{name}_result`、平铺键 | 假 `ChatOpenAI`（`_llm_instance` 注入或 patch 导入路径） |
| `test_system_prompt_prepended` | `system_prompt` 非空 → 传给 LLM 的消息首条为 SystemMessage | 断言假 LLM 收到的参数 |
| `test_messages_from_state_win` | state 含 `messages` → 优先于实例 messages | 假 LLM |
| `test_no_messages_raises` | 两处皆空 → `ValueError`，且 `ExecutionLog.error` 被记录 | `pytest.raises` + 断言历史 |
| `test_retry_on_429_then_success` | 前 2 次抛含 "429" 异常、第 3 次成功 → 最终成功；`tenacity.nap.sleep` 被调用 2 次且退避值 `1,2`（`retry_base_delay=1`）【AD-03】 | monkeypatch `tenacity.nap.sleep` + 假 LLM side_effect 序列 |
| `test_non_rate_limit_error_no_retry` | 抛普通异常 → 只调用 1 次即重抛 | 假 LLM |
| `test_retry_exhausted_raises` | 始终 429、`max_retries=2` → `LLMNodeError` 含尝试次数；sleep 调用 3 次 | monkeypatch `tenacity.nap.sleep` |
| `test_anthropic_branch` | `llm_type="anthropic"` → 实例化为 `ChatAnthropic` 参数形态 | patch `langchain_anthropic.ChatAnthropic` |
| `test_execution_log_no_secret_leak`（H6 守护） | 执行后 `ExecutionLog.input_data` 序列化串不含 `api_key` 值与完整 state | 设哑密钥 env 后断言 |
| `test_runnable_tags`（K4 守护） | `build_runnable()` 的 tags 含 `[name]`（以 EXP-C2 实测方式内省） | 内省 RunnableLambda |

## 8. 验收标准 DoD

- [ ] `tests/unit/workflow/nodes/test_llm_node.py` 全绿，**全程零真实网络调用**
- [ ] 失败路径（缺密钥/无消息/重试耗尽/非限流异常）各有独立测试（H2 验证）
- [ ] `grep -n "api_key\s*=" app/workflow/nodes/llm_node.py` 不出现硬编码值；模型无明文 `api_key` 字段（仅 `api_key_env`/`base_url_env`）；日志/ExecutionLog 不含密钥（H6 验证）
- [ ] 无任何 `except: pass` 或裸 `except Exception` 后不处理（R6 验证，ruff `BLE` 兜底）
- [ ] 重试为 tenacity 实现且测试 monkeypatch `tenacity.nap.sleep`、断言退避序列 `1,2,4`（AD-03）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H2（落实）**：每个 except 显式处理（记录 + 重抛）；重试显式、可配（`max_retries`/`retry_base_delay`）、有测试；失败路径全覆盖。
- **H6（部分落实）**：密钥一律 env 解析（`api_key_env`/`base_url_env` 显式指定或按 `llm_type` 默认）；`ExecutionLog` 与日志只记摘要，不落完整 state 与密钥。
- **H4（规范约束）**：本节点**不引入任何缓存**（R10；若未来引入须 maxsize + TTL + 显式开关 + 文档）。

## 10. 交付物清单

- `app/workflow/nodes/llm_node.py`
- `tests/unit/workflow/nodes/test_llm_node.py`
- `app/workflow/nodes/__init__.py`（追加导出 `LLMNode` / `LLMConfig`）

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/nodes/test_llm_node.py -m unit -v
make lint && ruff format --check . && make typecheck
grep -n "api_key\s*=" app/workflow/nodes/llm_node.py   # 人工确认无硬编码值
grep -n "cache\|lru_cache" app/workflow/nodes/llm_node.py   # 期望零命中（H4）
wc -l app/workflow/nodes/llm_node.py   # 期望 < 250
```
