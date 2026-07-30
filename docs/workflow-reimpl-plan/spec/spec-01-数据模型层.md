# spec-01 数据模型层（DSL & Models，Phase 1）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 1 / M1 DSL 可解析 |
| 人日估算 | **1.0** |
| 前置 spec | spec-00（脚手架 + **EXP 全部闭环**，R-EXP 门禁）；EXP-X1 闭环确认最小依赖集 |
| 后续依赖方 | spec-02..07 全部（类型基石） |
| 涉及编号 | K1、C8、R1/R2/R9、D6、异常契约（CONTRACT §5） |

## 2. 目标

用 pydantic v2 定义全部 DSL 模型（K1 五元组），实现 YAML → `WorkflowDefinition` 的解析与边界校验（R9），并提供一份可运行的示例 YAML。本层是后续所有阶段的类型基石，**签名一旦冻结不再随意改动**（CONTRACT §4.2 / §5）。

## 3. 前置依赖

- spec 间依赖：spec-00（包骨架、pytest 基础设施、PyYAML 依赖就位）。
- 代码库依赖：仅 `app/workflow/` 内部 + pydantic 2.11 + PyYAML 6.0.2；`models.py` 不得 import 任何引擎模块（依赖红线 1）。
- 外部依赖：无网络依赖。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：`models.py` 落位 `app/workflow/models.py`；示例 YAML 落位 `app/workflow/config/examples/minimal.yaml`；测试落位 `tests/unit/workflow/test_models.py`。
- **AD-07**：文件遵守 ruff（line-length 119、`D` docstring 规则）。
- **AD-11**：pyright standard 零错误纳入 DoD。

## 5. 任务清单

- [ ] **TC1 models.py 全部 DSL 模型 + 精简 NodeType + 异常族（0.5d）**
  - 内容：按 CONTRACT §4.2 / §5 实现 `NodeType`（恰 2 成员，C8）、`StateFieldSchema`、`NodeDefinition`、`EdgeDefinition`、`ExecutionLog`、`OperatorLog`、`WorkflowDefinition`（含模型级校验：节点名唯一、nodes 非空；图级校验归 spec-06）与异常族 6 类（单点定义）
  - 产出文件：`app/workflow/models.py`（目标 < 300 行，纯模型无业务逻辑）
  - TDD 节奏：先写契约/校验失败测试（§7 前 5 项，RED）→ 实现（GREEN）
  - 要点：`NodeDefinition.type` 保持 `str`（R4 插件口子）；`WorkflowDefinition` 默认 `extra="ignore"`（K1）；`reducer` 用 `Literal["add","last"]` 让非法值由 pydantic 直接报错
- [ ] **TC2 parse_definition / load_definition_from_yaml + minimal.yaml（0.25d）**
  - 内容：实现 `parse_definition(data)`（yaml.safe_load 结果专用，**禁止 yaml.load**，D6）与 `load_definition_from_yaml(path)`（文件不存在/解析失败 → 带路径上下文的 `ValueError`）；编写示例 `minimal.yaml`（形态见 §6，头部注释写明所需 env，AD-12）
  - 产出文件：`app/workflow/models.py`（追加）、`app/workflow/config/examples/minimal.yaml`
- [ ] **TC3 test_models.py 9 个用例（0.25d）**
  - 内容：按 §7 表补齐 9 个测试并全绿
  - 产出文件：`tests/unit/workflow/test_models.py`

## 6. 接口契约

见 CONTRACT §4.2（全部模型与两个解析函数签名）、§5（异常族与场景映射表）、§6 S16（YAML 安全）。签名冻结，逐字一致。

示例 YAML 基准形态（验收样本）：

```yaml
# 所需环境变量：OPENAI_API_KEY（运行该示例前设置，见 .env.example）
workflow_id: demo_minimal
description: "最小示例：LLM 单节点"   # 允许保留，解析时被忽略（非模型字段）
entry_point: greet
nodes:
  - name: greet
    type: llm
    config:
      llm_type: openai
      model_name: gpt-4o-mini
      system_prompt: "You are a concise assistant."
edges:
  - source: greet
    target: END
state_schema:
  input:
    type: str
    description: 用户输入
```

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 |
| --- | --- | --- |
| `test_parse_minimal_yaml` | 快乐路径：示例 YAML → 模型各字段正确 | `tmp_path` 写临时 YAML |
| `test_missing_required_field_raises` | 缺 `workflow_id` / `entry_point` / `state_schema` → `ValidationError` | `pytest.raises` |
| `test_duplicate_node_names_raises` | 同名节点 → 校验错误（模型级） | 构造 dict |
| `test_invalid_reducer_raises` | `reducer: "append"` → `ValidationError` | 构造 dict |
| `test_extra_yaml_keys_ignored` | YAML 含 `description` 等额外键不报错 | 构造 dict |
| `test_unknown_node_type_allowed` | `type: "my_custom"` 解析通过（插件机制 R4） | 构造 dict |
| `test_load_file_not_found` | 文件不存在 → `ValueError` 且消息含路径 | `tmp_path` |
| `test_load_invalid_yaml_syntax` | 坏 YAML → `ValueError` 带上下文 | `tmp_path` |
| `test_exception_hierarchy` | 五个异常子类均为 `WorkflowEngineError`（暨 `Exception`）子类 | `issubclass` 断言 |

另加守护断言：`NodeType` 成员数恰为 2（R1/C8，可并入 `test_exception_hierarchy` 所在文件）。

## 8. 验收标准 DoD

- [ ] `minimal.yaml` 能被 `load_definition_from_yaml` 解析成 `WorkflowDefinition` 且字段值断言通过
- [ ] §7 测试清单全绿（`uv run pytest -m unit`）；models.py 全量类型标注、公开类有 docstring
- [ ] `NodeType` 恰含 2 个成员（枚举段人工核对 + 守护断言）
- [ ] 无任何领域模型残留（`PlannerConfig` / `DispatcherConfig` 等不得出现；`grep -rnE "circle_|planner_|worker_|dispatcher" app/workflow/models.py` 零命中）
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

无 H 编号隐患（打基础）。落实清理项 **C8**（NodeType 精简）与 **D6**（safe_load-only）。

## 10. 交付物清单

- `app/workflow/models.py`
- `app/workflow/config/examples/minimal.yaml`
- `tests/unit/workflow/test_models.py`

## 11. 验收命令

```bash
uv run pytest tests/unit/workflow/test_models.py -m unit -v
uv run python -c "from app.workflow.models import load_definition_from_yaml as f; d=f('app/workflow/config/examples/minimal.yaml'); print(d.workflow_id, [n.name for n in d.nodes])"
make lint && ruff format --check . && make typecheck
grep -rnE "circle_|planner_|worker_|dispatcher" app/workflow/models.py   # 期望零命中
grep -n "yaml\.load(" app/workflow/models.py                             # 检测被禁止的 yaml.load 调用，期望零命中（全引擎只允许 yaml.safe_load）
```
