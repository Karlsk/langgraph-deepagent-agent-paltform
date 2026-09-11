# spec-01 · 契约变更：LLM 节点接入 provider 体系（provider_ref + ChatModelFactory）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 0.5 | 无 | **S20**（新增）、§4.5 / §4.7（签名）、§3 红线 4（澄清）、H6、K10、S6 |

> 本文档为**纯契约变更**，不含代码实现。按 CONTRACT §11「禁止先改代码后补契约」要求先行落地。

## 1. 动机

工作流引擎与平台 provider 体系是**两套割裂的模型解析路径**：

| | Agent 资产（AgentApp / SubAgent） | 工作流 LLM 节点（变更前） |
| --- | --- | --- |
| 模型标识 | `"<provider_name>/<model_name>"` ref | `llm_type` + `model_name` 两个字面量 |
| 凭据来源 | `Provider.auth_config.api_key`（DB） | **仅环境变量** `OPENAI_API_KEY` / `OPENAI_BASE_URL` |
| 解析入口 | `app/services/llm/llm_store.py:133` `load_model_config` | `app/workflow/nodes/llm_node.py:97-113` |

后果：

1. 设计器里 `model_name` 是**自由文本**（`LlmNodeForm.vue:60`），不校验模型是否存在，拼错要到运行期才炸。
2. 用户在「模型管理」页配好的 provider（含 `base_url` 与密钥）对工作流**完全无效**——引擎只认 env 变量。只有当 env 恰好指向同一端点时才碰巧能跑通。
3. `OLLAMA` / `OPENAI_COMPATIBLE` 类型的 provider 在工作流里根本无法正确寻址（`llm_type` 只有 `openai`/`anthropic` 两值，且 `base_url` 无人填写）。

## 2. 影响面

### 2.1 契约签名变更（全部为**新增带默认值的可选参数**，向后兼容）

| 位置 | 变更 |
| --- | --- |
| §4.7 `LLMConfig` | 新增 `provider_ref: str \| None = None` |
| §4.7 `LLMNode.__init__` | 新增 `chat_model_factory: ChatModelFactory \| None = None` |
| §4.5 `create_node` | 新增 `chat_model_factory=None`（仅透传给 llm 分支） |
| `GraphBuilder.__init__` | 新增 keyword-only `chat_model_factory=None` |
| `WorkflowRegistry.__init__` | 新增 keyword-only `chat_model_factory=None` |
| 新模块 `app/workflow/ports.py` | 承载 `ChatModelFactory = Callable[[str, dict[str, Any]], Any]` |

既有 YAML（`config/examples/*.yaml`）与既有调用方**零改动**即可继续工作：`provider_ref` 缺省为 `None`，走原 env 路径。

### 2.2 行为语义新增

**S20（LLM 凭据解析）** — 详见 CONTRACT §6。要点：

- `_get_llm_instance()` 三分支：
  1. `provider_ref` 非空 + 工厂已注入 → `factory(provider_ref, overrides)`
  2. `provider_ref` 非空 + 工厂为 `None` → **`ConfigError`**（含节点名与 ref）
  3. `provider_ref` 为空 → 现有 env 路径逐字不变
- **分支 2 不静默回退 env**：用错端点/密钥（可能把请求打到另一家供应商、或用错账号计费）比直接失败危险得多。
- `overrides` 携节点级 `temperature`（及 `max_tokens`，若设置），**节点配置优先于** `ModelConfig.extra_params`——工作流作者的显式意图压过 provider 侧默认值。
- `provider_ref` 存在性 / enabled 校验在**注册期**完成（S6 构建期失败优先）：`PUT /api/v1/workflows/{id}` 对每个携 `provider_ref` 的 llm 节点校验，失败 → HTTP 422。

### 2.3 §3 红线 4 澄清

红线 4 原文禁止 `app/workflow/` import `app.core.*` / `app.api.*` / `app.services.*`。本次补充：

> 宿主经构造参数注入的**不透明 callable**（如 `ChatModelFactory`）不构成依赖——引擎只持有 `app/workflow/ports.py` 里的类型别名，不感知其实现，装配责任在组合根（`app/main.py`）。

这是 AD-02「反向集成时由外部装配」原则的具体化，**不是红线放宽**：引擎侧仍零 `app.*` import，grep 守卫继续生效。

## 3. 备选方案对比（R-EXP 第 5 条：给 2-3 个附影响面）

| 方案 | 做法 | 影响面 | 取舍 |
| --- | --- | --- | --- |
| **A（采纳）** | 注入 `ChatModelFactory` callable，凭据在**调用期**从 DB 解析 | 5 处签名新增可选参数；引擎零 `app.*` 依赖；复用 `build_chat_model` | 引擎保持自包含；provider 改密钥需重注册（K10 memoize）——已文档化 |
| B | 注册期预解析凭据，把 `api_key` 值塞进节点实例 | 同样要改签名；密钥进入引擎内存对象 | 省一次 DB 查询，但密钥生命周期与注册表绑定、轮换更难，且离 H6 更近（值在引擎内流转）。**否决** |
| C | 仅前端下拉，后端不动 | 只改 `LlmNodeForm.vue` | 零契约变更，但**运行期仍用 env 凭据**——下拉选中的 provider 与实际调用端点可能不一致，属"看起来打通了其实没打通"。**否决** |

## 4. 红线自检

| 红线 | 是否受影响 | 说明 |
| --- | --- | --- |
| H6（密钥 env-only / config 不承载密钥） | **强化** | `provider_ref` 只存引用字符串；`LLMConfig` 仍无 `api_key` 字段，守卫测试 `test_config_has_no_plaintext_api_key_field` 必须继续通过 |
| H4（引擎无模块级可变全局） | 不受影响 | 工厂经构造参数注入，**不使用模块级 slot** |
| H5（节点不知道注册表） | 不受影响 | 注入的是不透明 callable，非注册表引用 |
| K10（客户端懒加载 memoize） | 保留 | `_get_llm_instance()` 仍 memoize；新增语义仅在其内部三分支 |
| S6（构建期校验优先） | 强化 | `provider_ref` 悬空在 PUT 注册期即 422，不推迟到执行期 |
| S14（`extra="forbid"`） | 保留 | `provider_ref` 成为**声明字段**，故合法；其余未知键仍被拒 |
| 红线 4（引擎自包含） | 澄清 | 见 §2.3；grep 守卫 `app/workflow/` 无 `app.core`/`app.services` 必须继续通过 |

## 5. 已知遗留（本次不改，记录备查）

**`provider_ref` 悬空引用无删除保护**：`app/api/v1/providers.py:_referencing_owners` 仅扫描 `AgentApp.model` 与 `SubAgentConfig.model`，**不扫描工作流定义**（含 `config/user/*.yaml`）。删除一个被工作流引用的 provider/model 后，该工作流会在**执行期**失败（注册期的 S20 校验只在下次 PUT 时触发）。

后续项：`_referencing_owners` 增扫已注册工作流定义的 `provider_ref`，或在 provider 删除端点提示受影响的工作流清单。

## 6. 交付物

- 改：`docs/workflow-reimpl-plan/spec/CONTRACT.md`（§3 红线 4、§4.5、§4.7、§6 S20、§11 变更记录）
- 改：`docs/workflow-reimpl-plan/spec/spec-04-llmnode.md`（§6 引用新增字段）
- 改：`docs/workflow-node-development.md`（§3.1 字段表加 `provider_ref`；§3.2 补工厂解析优先级）
- 改：`docs/changelog/workflow-canvas-orchestration/spec-12-node-config-forms.md`（§3 `model_name` 控件 `input`→`select（provider 分组）`、新增 `provider_ref` 行；§7 DoD 字段集基准由「example YAML」改为「`LLMConfig` 冻结字段集」）
- 新：本文件
- 后续代码 spec：`spec-02-engine-injection.md`（引擎侧）、`spec-03-composition-root.md`（组合根 + 注册期校验）、`spec-04-frontend-model-select.md`（前端下拉）

## 7. 验收门限（DoD Gate）

- [ ] CONTRACT.md §4.5 / §4.7 含新签名，§6 含 S20，§11 变更记录含本次条目
- [ ] 下游三处文档（spec-04-llmnode / workflow-node-development / spec-12）与 CONTRACT **零冲突**
- [ ] 引擎代码**本次零改动**（纯契约提交，`git diff --stat` 仅 `docs/`）
- [ ] 提交信息 `docs: record contract change for llm node provider_ref resolution`
