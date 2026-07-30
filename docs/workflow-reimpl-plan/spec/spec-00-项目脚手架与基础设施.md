# spec-00 项目脚手架与基础设施（Phase 0 + EXP 探索执行）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 0 / M0 脚手架就绪 |
| 人日估算 | **1.5**（脚手架 0.5 + EXP 探索 1.0） |
| 前置 spec | 无（项目起点） |
| 后续依赖方 | 全部 spec-01..09 |
| 涉及编号 | K-、H6（预防性）、D1/D5/D6/D7、AD-01/02/04/05/06/07/08/09/12、R5/R7/R9、**R-EXP（EXP-G/C/L/X 执行闭环）** |

## 2. 目标

在现有仓库内搭好"可长期演进"的工程骨架：`app/workflow/` 包结构、依赖与工具链配置、pytest 分层测试基础设施、structlog 日志初始化、`.env` 约定与 CI 步骤；**并完成 `api-exploration-1x.md` 全部 EXP 项的实测闭环**（R-EXP），为 M1 开工解锁门禁。本阶段结束时：`uv sync` 可装、空包可导入、冒烟测试绿、`make check` 零问题、EXP 全部闭环。

## 3. 前置依赖

- spec 间依赖：无。
- 代码库依赖：`pyproject.toml`、`Makefile`、`.github/workflows/ci.yaml`、`.env.example`（白名单内改动）；`app/workflow/`（已存在的空目录）。
- 外部依赖：uv.lock 锁定版本（langgraph 1.0.2 / langchain-core 1.0.4 / pydantic 2.11 / Python 3.13）；EXP 探索只用 `.venv` 已装包，禁止真实网络。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-01**：包骨架落位 `app/workflow/`；测试落位 `tests/unit|integration/workflow/`。
- **AD-02**：`logging_conf.py` 用 structlog 实现 `setup_logging`（幂等；已被 `app.core.logging` 配置过时跳过）；本阶段只落骨架，脱敏 processor 在 spec-08 补全。
- **AD-04**：langchain-anthropic 作为正式依赖安装（顶层导入口径）。
- **AD-05**：依赖并入现有 `pyproject.toml`：`dependencies` 新增 `PyYAML>=6.0`、`langchain-anthropic`（区间由 EXP-L5 定）、`httpx`（从 test group 提升）；dev group 新增 `pytest-cov`；`[tool.pytest.ini_options]` 追加 `unit`/`integration` marker（保留既有 `slow`，`--strict-markers`）。
- **AD-06**：版本以 uv.lock 为准；EXP 探索按 `api-exploration-1x.md` 执行。
- **AD-07**：ruff `select` 追加 `T20`/`BLE`/`S`；`per-file-ignores`：`app/workflow/cli.py` → `T201`、`tests/**` → `S101`；既有代码若产生新告警，用最小 per-file-ignores 收口并在此记录清单（见 §10 交付物）。
- **AD-08**：新建 `tests/` 骨架与 `tests/conftest.py`（`load_dotenv` + 公共 fixture 占位）。
- **AD-09**：Makefile 新增 `test`/`test-unit`/`test-integration`/`test-cov`；`ci.yaml` 增加 `uv run pytest -m unit` 步骤。
- **AD-12**：`.env.example` 增补 `ANTHROPIC_API_KEY=`（空值）。

## 5. 任务清单

> TC 粒度 0.125-0.5 人日；严格 TDD（契约测试先于实现）；合计 1.5 人日。

- [ ] **TC1 包骨架 + tests 骨架（0.25d）**
  - 内容：创建 `app/workflow/__init__.py`（`__version__ = "0.1.0"`，不导入重依赖）、`app/workflow/nodes/__init__.py`（空，spec-03 填充导出）、`app/workflow/config/examples/.gitkeep`；创建 `tests/__init__.py`、`tests/unit/__init__.py`、`tests/unit/workflow/__init__.py`、`tests/integration/__init__.py`、`tests/integration/workflow/__init__.py`、`tests/conftest.py`（`load_dotenv()` + 公共 fixture 占位）
  - 产出文件：上述骨架文件
  - TDD 节奏：本卡为骨架，冒烟测试在 TC3 补齐
- [ ] **TC2 pyproject 依赖/marker/ruff 扩展 + Makefile/CI（0.125d）**
  - 内容：按 AD-05 改 `pyproject.toml`（依赖、markers；保留既有配置键，最小增量）；按 AD-07 改 ruff select/per-file-ignores；按 AD-09 改 Makefile 与 `ci.yaml`；按 AD-12 改 `.env.example`
  - 产出文件：`pyproject.toml`、`Makefile`、`.github/workflows/ci.yaml`、`.env.example`
  - 注意：`uv add` 后跑 `uv sync` 确认 lock 更新；langchain-anthropic 的区间先按 EXP-L5 结论写（若 EXP 未竟先写 `>=` 下界占位并在 TC6 收口）
- [ ] **TC3 logging_conf structlog 骨架 + 2 个冒烟测试（0.125d）**
  - 内容：实现 `app/workflow/logging_conf.py` 的 `setup_logging(level="INFO", *, json_output=False)`（structlog 配置，幂等，重复调用不叠加 handler；已配置时跳过）；编写 `tests/unit/workflow/test_package_import.py`：`test_import_package`、`test_setup_logging_idempotent`（可选 `test_setup_logging_level`）
  - 产出文件：`app/workflow/logging_conf.py`、`tests/unit/workflow/test_package_import.py`
  - TDD 节奏：先写两个冒烟测试（RED）→ 实现（GREEN）→ 重构
- [ ] **TC4 EXP-G 图构建 API 探索（0.375d，R-EXP）**
  - 内容：闭环 `api-exploration-1x.md` 的 EXP-G1..G8（langgraph 1.0.2：pydantic state schema、reducer channel、add_node 形态、path_map、START/END、compile/invoke、异常传播、pydantic 2.11 边界）；允许 SRC/REPL/TEST 手段；characterization test 可留在 `tests/integration/workflow/` 作回归
  - 产出文件：`api-exploration-1x.md`（填写 G 组实测结果与结论）、可选 characterization test 文件
  - 偏差处置：任何不吻合 → 停止，按 CONTRACT §11 提问决策
- [ ] **TC5 EXP-C/EXP-X 探索（0.25d，R-EXP）**
  - 内容：闭环 EXP-C1..C3（RunnableLambda 签名、tags 透传、RunnableConfig）与 EXP-X1（最小依赖集）；EXP-X2 对照总表在 TC6 后统一回填
  - 产出文件：`api-exploration-1x.md`（C/X 组实测）
- [ ] **TC6 EXP-L LLM 客户端探索 + 对照总表回填（0.375d，R-EXP）**
  - 内容：闭环 EXP-L1..L5（ChatOpenAI/ChatAnthropic 构造参数、AIMessage 形态、限流异常层级与 tenacity 谓词建议、SecretStr 脱敏、anthropic 版本区间）；按 EXP-L5 结论最终确定 pyproject 中 `langchain-anthropic` 约束；回填 EXP-X2 对照总表；完成"闭环签字"
  - 产出文件：`api-exploration-1x.md`（全部闭环）、`pyproject.toml`（anthropic 约束定稿）

## 6. 接口契约

见 CONTRACT §4.1（`__version__`）与 §4.11（`setup_logging`）。要点复述（冻结）：

- `app/workflow/__init__.py` 只定义 `__version__: str = "0.1.0"`，不导入任何重依赖（保持导入廉价）。
- `setup_logging(level: str = "INFO", *, json_output: bool = False) -> None`：幂等；structlog 形态【AD-02】；脱敏 processor（`redact` / `redact_processor`）在 spec-08 补全并挂入本初始化流程。

## 7. TDD 测试要点

| 先写的测试 | 覆盖分支 | Mock/工具 | 落位 |
| --- | --- | --- | --- |
| `test_import_package` | 包可导入、`__version__` 为 str | 无 | `tests/unit/workflow/test_package_import.py` |
| `test_setup_logging_idempotent` | 重复初始化不叠加 handler | `caplog` / handler 计数 | 同上 |
| `test_setup_logging_level`（可选） | `level="DEBUG"` 生效 | `caplog` | 同上 |

另：EXP 的 characterization test（如有）标记 `@pytest.mark.integration`，落位 `tests/integration/workflow/`。

## 8. 验收标准 DoD

- [ ] `uv sync` 成功；`uv run python -c "import app.workflow; print(app.workflow.__version__)"` 输出 `0.1.0`
- [ ] `uv run pytest -m unit` 全绿（≥ 2 个冒烟测试）；CI 中 `pytest -m unit` 步骤通过
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题（全仓）
- [ ] `.env.example` 与仓库内任何文件不含真实密钥（`grep -rniE "(sk-|key-)" --exclude-dir=.git --exclude-dir=.venv .` 人工确认）
- [ ] ruff 新增规则对既有代码的告警已用最小 per-file-ignores 收口，清单记录于本 spec 交付物
- [ ] **`api-exploration-1x.md` 全部 EXP 项闭环（实测结果 + 结论 + 闭环签字）；偏差项已走 CONTRACT §11 流程**
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

无 H 编号编码修复（打基础阶段）。**H6 预防性落地**：`.env.example` 空值键名 + 既有 `.gitignore` 覆盖 `.env`；**D6**（yaml.safe_load-only）、**D7**（注册表隔离 fixture）规范自本阶段起生效。**R-EXP 闭环 = M1 门禁解除**。

## 10. 交付物清单

- `app/workflow/__init__.py`、`app/workflow/nodes/__init__.py`、`app/workflow/config/examples/.gitkeep`
- `app/workflow/logging_conf.py`（骨架）
- `tests/` 骨架 + `tests/conftest.py`、`tests/unit/workflow/test_package_import.py`
- `pyproject.toml`（依赖/markers/ruff 扩展）、`Makefile`（test 目标）、`.github/workflows/ci.yaml`（unit 步骤）、`.env.example`（ANTHROPIC_API_KEY）
- `api-exploration-1x.md`（全部 EXP 闭环 + 对照总表 + 签字）
- ruff per-file-ignores 收口清单（写入本文件附录或提交信息）
- 可选：EXP characterization test（`tests/integration/workflow/`）

## 11. 验收命令

```bash
uv sync
uv run python -c "import app.workflow; print(app.workflow.__version__)"
uv run pytest -m unit
uv run pytest -m integration        # 若本阶段落了 EXP characterization test
make lint && ruff format --check . && make typecheck
grep -rniE "(sk-|key-)" --exclude-dir=.git --exclude-dir=.venv .   # 人工确认无真实密钥
# EXP 闭环检查：api-exploration-1x.md 无 "_（待填）_" 残留
grep -c "（待填）" docs/workflow-reimpl-plan/spec/api-exploration-1x.md   # 期望 0
```
