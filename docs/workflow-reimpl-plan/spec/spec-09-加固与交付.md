# spec-09 加固与交付（Phase 9）

## 1. 元信息

| 项 | 值 |
| --- | --- |
| Phase / 里程碑 | Phase 9 / M8 交付就绪 |
| 人日估算 | **1.5** |
| 前置 spec | spec-08（端到端链路）；无 EXP 门禁 |
| 后续依赖方 | 无（收官阶段） |
| 涉及编号 | R1（只加固不新增特性）/R10、H4（终检）/H5（终检）/H6（终检）、D5、AD-07/08/09 |

## 2. 目标

交付前的系统性加固：安全审计、并发压测固化、覆盖率达标（≥ 80%）、可运行示例与 README、全套规划文档与实现的一致性校对。本阶段**只加固、不新增特性**（R1）。

## 3. 前置依赖

- spec 间依赖：spec-08。
- 代码库依赖：`README.md`（仓库根，既有文件改动白名单内）、全仓 grep 审计面。
- 外部依赖：无。

## 4. 适配决策（本 Phase 涉及的 AD 条目）

- **AD-07**：`make lint` 全仓零告警复核（T20/BLE/S 扩展规则下无遗留）。
- **AD-08**：覆盖率命令 `uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80`。
- **AD-09**：CI（`ci.yaml`）在本阶段追加覆盖率门禁步骤（既有文件改动白名单内）。

## 5. 任务清单

- [ ] **TC1 安全审计（grep 三检 + 加固清单）（0.375d）**
  - 内容：① `grep -rniE "(api[_-]?key|token|secret|password)\s*[:=]" app/workflow/ tests/` 人工逐条确认无硬编码密钥，误报项写入审计记录；② 确认仓库不含 `.env`（只含 `.env.example`）；③ 以 DEBUG 级别运行示例，人工确认无完整 state / 密钥落日志（H6 终检）；④ 确认 `app/workflow/` 内无模块级 dict 缓存 / `lru_cache`（H4 终检：本期默认无缓存）；⑤ 确认 `GraphBuilder` 无任何注册表快照（H5 终检）；⑥ 产出《安全加固清单》勾选记录（写入 README 附录或 `docs/`）
  - 产出文件：《安全加固清单》（README 附录或 `docs/workflow-reimpl-plan/` 附录，白名单内）
- [ ] **TC2 并发压测固化 + 覆盖率补齐至 ≥80%（0.5d）**
  - 内容：spec-07 的并发测试（16 线程 × 64 次）纳入 `pytest -m integration` 常规执行并在 CI 加跑一次；**新增**"不同 workflow_id 并发互不阻塞"测试（断言两 workflow 交错执行完成，D3）；`--cov-fail-under=80` 跑全量，逐模块补齐至总覆盖率 ≥ 80%（重点：各节点 except 分支、路由器未命中分支、注册表删除/查询边界）；CI 追加覆盖率门禁步骤
  - 产出文件：`tests/integration/workflow/test_concurrency.py`（追加一组用例）、`.github/workflows/ci.yaml`（覆盖率门禁）
- [ ] **TC3 三个示例 YAML + README 完善（0.375d）**
  - 内容：`minimal.yaml`（LLM 单节点，校对）、`http_demo.yaml`（HTTP 节点 + mock_enabled 演示，README 强调 mock 仅演示用途）、`condition_branch.yaml`（"LLM -> 条件边 -> HTTP"组合 + `default_edges` 说明注释）；每个示例头部注释写明所需环境变量（AD-12）；README：快速开始（安装/配置 env/运行示例/跑测试）、架构一图流（引用《00·架构总览》）、目录结构、FAQ（reducer 语义、双写开关、条件表达式语法、错误信封）、扩展指南（`register_node_type` 示例，含 H4 缓存规范约束沉淀）
  - 产出文件：`app/workflow/config/examples/*.yaml`（3 个）、`README.md`（既有文件改动）
- [ ] **TC4 契约符合性矩阵 + 接口签名校对（0.25d）**
  - 内容：对照 CONTRACT §4 接口冻结清单逐一核对最终代码签名，偏差项回归代码或走 CONTRACT §11 变更流程；对照 K1–K10 / C1–C9 / H1–H7 / R1–R10 逐条勾选落实情况，产出《契约符合性矩阵》；`__version__` 保持 `0.1.0`；`chore:` 提交收尾
  - 产出文件：《契约符合性矩阵》（README 附录或 `docs/workflow-reimpl-plan/` 附录）

## 6. 接口契约

本阶段不新增公开接口。若补齐测试时发现需要微调既有签名，**必须走 CONTRACT §11 变更流程**（同步更新 CONTRACT + 相关 spec，提交信息标注 `refactor!`/`docs:`），禁止单点改动。

## 7. TDD 测试要点

本阶段以"补测试"为主：

- [ ] 覆盖率报告中每一处 `Miss` 逐条判断：能补则补（优先失败路径），确属不可达的在《契约符合性矩阵》备注
- [ ] 并发测试重复运行稳定性验证：`pytest -m integration` 循环 5 次（或循环脚本）无偶发失败
- [ ] 新增 `test_different_workflows_not_blocked`：两个不同 workflow_id 并发执行交错完成、互不阻塞（D3）

## 8. 验收标准 DoD

- [ ] 安全加固清单全部勾选，无 CRITICAL/HIGH 遗留
- [ ] `uv run pytest` 全量绿；总覆盖率 ≥ 80%（附 `term-missing` 输出存档）
- [ ] 三个示例 YAML 均可通过 CLI 跑通（其中 `condition_branch.yaml` 验证"LLM -> 条件 -> HTTP"组合路径；LLM 示例允许以 mock/测试目录方式演示，README 说明）
- [ ] README 快速开始被一名未参与者照做跑通（或自测模拟）
- [ ] 《契约符合性矩阵》中 K1–K10 / C1–C9 / H1–H7 / R1–R10 无未决项
- [ ] `make lint`、`ruff format --check .`、`make typecheck` 零问题
- [ ] 通用 DoD G1-G8 + 红线自检表（CONTRACT §12）逐项勾选

## 9. 本阶段涉及的隐患修复

- **H4（审计终检）**：确认全仓库无无界缓存；规范约束沉淀进 README 扩展指南。
- **H5（审计终检）**：确认无构建期注册表快照反模式。
- **H6（审计终检）**：密钥/日志卫生最终过清单。

## 10. 交付物清单

- 《安全加固清单》、《契约符合性矩阵》
- 完善后的 `README.md`、三个示例 YAML
- 覆盖率报告存档（`term-missing` 输出）
- 稳定的集成/并发测试集（含新增"不同 workflow 互不阻塞"用例）
- `.github/workflows/ci.yaml`（覆盖率门禁步骤）

## 11. 验收命令

```bash
uv run pytest --cov=app.workflow --cov-report=term-missing --cov-fail-under=80
for i in 1 2 3 4 5; do uv run pytest -m integration -q || break; done   # 并发稳定性 5 次
grep -rniE "(api[_-]?key|token|secret|password)\s*[:=]" app/workflow/ tests/   # 人工逐条确认（H6）
grep -rn "lru_cache" app/workflow/                                            # 期望零命中（H4）
grep -n "registry" app/workflow/graph_builder.py                              # 人工确认无快照（H5）
uv run python -m app.workflow run --workflow demo_minimal --input '{"input":"hi"}'   # 示例跑通
make lint && ruff format --check . && make typecheck
```
