# Workflow 画布编排 · Spec 集 README（落地执行视图）

> 本目录是 `docs/workflow-frontend-spec.md`「Dify 式画布编排 → 保存全量落 YAML」的**可执行拆分**：
> 22 份 1-2 人天的细粒度 spec（`spec-01..22`，编号即开发顺序），每份含 **TDD（RED→GREEN→REFACTOR）+ 验收门限（DoD Gate）**。
>
> - **规格总览**（里程碑 / 人日 / 约束 / TDD 模板）看 → [`overview.md`](overview.md)
> - **落地执行视图**（落地顺序 / 依赖 / 前后端分工，即本文）看 → `README.md`
>
> **状态**：计划（本期仅落盘 spec，不实现代码）。**决策已批准（2026-09-07）**：D1 Vue Flow / D2 前端传 JSON·后端落 YAML / D3 全量更新 / D4 `ui_layout` 注解键 / D5 YAML 预览由后端返回 / D6 画布不设 per-workflow `no_match_policy`。

---

## 1. 前端 / 后端 Spec 分类

| 分类 | spec（编号即开发顺序） | 份数 | 人日 |
| --- | --- | --- | --- |
| **后端** | `01`(契约·文档) `02`(list) `03`(get) `04`(execute logs) `16`(PUT注册) `17`(YAML落盘) `18`(DELETE) `20`(SSRF) | 8 | 10 |
| **前端** | `05`(api/路由) `06`(列表页) `07`(执行+轨迹) `08`(VueFlow基建) `09`(序列化) `10`(画布) `11`(palette) `12`(节点表单) `13`(schema面板) `14`(条件边) `15`(设计器集成) `21`(保存流程) `22`(YAML预览) | 13 | 23 |
| **前后端交叉** | `19`(写端点鉴权 + 前端角色门禁) | 1 | 2 |
| **合计** | `spec-01 .. spec-22` | 22 | **35** |

> 与 [`overview.md`](overview.md) §2 里程碑口径一致：交叉的 `spec-19`（2 人日）在里程碑表中计入 **M3 后端侧**，故 overview 记「后端 12 + 前端 23」；本文按「纯后端 10 / 纯前端 23 / 交叉 2」三分，两者都为 35 人日。

**按端快速索引：**

- 只碰 `app/`（后端）：`01 02 03 04 16 17 18 20`
- 只碰 `agent-web/`（前端）：`05 06 07 08 09 10 11 12 13 14 15 21 22`
- 两端都碰：`19`

---

## 2. 落地顺序（波次 + 泳道）

编号 `spec-01 → spec-22` 已是**单人串行**的推荐顺序。双人并行时按下列波次拆成**后端泳道**与**前端泳道**：

| 波次 | 里程碑 | spec | 端 | 说明 |
| --- | --- | --- | --- | --- |
| **W0 契约先行** | M0 | `01` | 后端(文档) | **阻塞全部后端代码任务**；禁先改代码后补契约 |
| **W1 后端只读+执行** | M1 | `02 → 03 → 04` | 后端 | 依赖 `01`；list/get/execute 内嵌 logs |
| **W2 前端只读+执行+轨迹** | M1 | `05 → 06 → 07` | 前端 | 依赖 W1 契约（`05` 需 `01..04`） |
| **W3 前端画布设计器**（可与 W2 并行） | M2 | `08 → 09 → 10 → {11 → 12, 13, 14} → 15` | 前端 | 纯前端；`08` 依赖 `05`+D1；`15` 载入依赖 `03` |
| **W4 后端写路径**（紧耦合集群） | M3 | `16 ↔ 17 → 18 → 19 → 20` | 后端(+`19`前端) | 注册/落盘/删除/鉴权协同落地；依赖 `01 03` |
| **W5 前端写路径** | M3 | `21 → 22` | 前端 | `21` 依赖 `15 09 16 19`；`22` 依赖 `15 03` |

**关键路径**：`W0(01) → W1(02-04) → W4(16-20) → W5(21)`（写路径强依赖后端）。
**可并行**：W3（前端画布 M2）在 `spec-05` + D1 就绪后即可启动，**与 W1/W2 并行**，无需等后端写路径。

---

## 3. 依赖速查表

| spec | 端 | 里程碑 | 人日 | 前置依赖 |
| --- | --- | --- | --- | --- |
| `spec-01` | 后端(文档) | M0 | 1 | 无（所有后端代码任务的前置） |
| `spec-02` | 后端 | M1 | 1 | `01` |
| `spec-03` | 后端 | M1 | 1 | `01` `02` |
| `spec-04` | 后端 | M1 | 1 | `01` |
| `spec-05` | 前端 | M1 | 2 | `01..04`（后端契约） |
| `spec-06` | 前端 | M1 | 2 | `05` |
| `spec-07` | 前端 | M1 | 2 | `05` `06` `04` |
| `spec-08` | 前端 | M2 | 2 | `05`；D1（Vue Flow 已批准） |
| `spec-09` | 前端 | M2 | 2 | `05`(DTO) `08` |
| `spec-10` | 前端 | M2 | 2 | `08` `09` |
| `spec-11` | 前端 | M2 | 1 | `09` `10` |
| `spec-12` | 前端 | M2 | 2 | `10` `11` |
| `spec-13` | 前端 | M2 | 1 | `09` `15`（设计器承载） |
| `spec-14` | 前端 | M2 | 2 | `09`(S7解析器) `10` |
| `spec-15` | 前端 | M2 | 2 | `09..14`；`03`（载入既有定义） |
| `spec-16` | 后端 | M3 | 2 | `01` `03` `17`(落盘) `19`(鉴权) |
| `spec-17` | 后端 | M3 | 2 | `01` `16` |
| `spec-18` | 后端 | M3 | 1 | `01` `17`(删文件) `19`(鉴权) |
| `spec-19` | 前后端 | M3 | 2 | `16` `18`；`auth.py::get_current_user` |
| `spec-20` | 后端 | M3 | 1 | `16`（注册期校验）；http 执行器 |
| `spec-21` | 前端 | M3 | 2 | `15` `09` `16`(PUT) `19`(门禁) |
| `spec-22` | 前端 | M3 | 1 | `15`（设计器）`03`(`getWorkflow(id,'yaml')`) |

> **M2 内部互承载**：`spec-13`（schema 面板）与 `spec-15`（设计器集成）互相依赖——面板由设计器承载、设计器需面板就位，实践中二者**协同开发**（先出面板组件、再在设计器集成）。
> **M3 后端紧耦合集群**：`16`(注册)/`17`(落盘)/`18`(删除)/`19`(鉴权) 相互引用（PUT 挂鉴权、注册触发落盘、删除清文件），需作为**一个单元协同落地**，推荐序 `16→17→18→19→20`。

依赖关系的 ASCII 图见 [`overview.md`](overview.md) §3。

---

## 4. 用什么 skill 实现

| 任务类型 | skill |
| --- | --- |
| **后端 spec**（`01 02 03 04 16 17 18 20` + `19` 后端部分） | [`rdp-implementation`](../../../.qoder/skills/rdp-implementation/SKILL.md)（DDD + TDD，`uv run pytest -m unit` / `make lint` / `make typecheck`） |
| **前端 spec**（`05..15 21 22` + `19` 前端部分） | [`frontend-rdp-implementation`](../../../.qoder/skills/frontend-rdp-implementation/SKILL.md)（前端分层 + Vitest TDD，`npm run type-check` / `npm test` / `npm run build`） |
| 纯 CRUD 列表页子任务（如 `spec-06`） | 前端 skill 内部**委托复用** [`frontend-feature-codegen`](../../../.qoder/skills/frontend-feature-codegen/SKILL.md) 模板 |
| 实现后验收 | [`rdp-verification`](../../../.qoder/skills/rdp-verification/SKILL.md)（传同一份 spec 路径） |

每份 spec 的 §7 是**最终验收门限（DoD Gate）**：全绿才算完成（RED 用例通过 + lint/type-check + 安全/契约红线 grep 闸门 + 无回归）。

---

## 5. 相关文档

- 规格总览（本目录）：[`overview.md`](overview.md)
- 上游前端蓝图：[`docs/workflow-frontend-spec.md`](../../workflow-frontend-spec.md)
- 后端 API 与 Trace：[`docs/workflow-api-and-trace.md`](../../workflow-api-and-trace.md)
- 前端开发规范（组件 / 请求层 / 测试）：[`docs/frontend-development-guide.md`](../../frontend-development-guide.md)
- Node 开发规范（LLM/HTTP config 字段）：[`docs/workflow-node-development.md`](../../workflow-node-development.md)
- 编码契约（§4 / §11 变更流程）：[`spec/CONTRACT.md`](../../workflow-reimpl-plan/spec/CONTRACT.md)
