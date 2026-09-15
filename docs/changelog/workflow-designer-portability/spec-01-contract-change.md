# spec-01 · 契约变更：沙箱 stdlib import 白名单 + 模板 URL 的注册期 SSRF 容忍 + HTTP 节点 headers 编辑

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M0 | 后端（文档） | 0.5 | 第 1、2、3 期已合并 | **§4.14 规则 1 + 约束两条**（修订）、**§4.13 节点类型白名单**（遗留漂移修正）、**§5 场景映射两行**（拆分/限定）、**S22 细则三行**（「内建白名单」修订、「模块白名单」新增、「无网络」改写）、**spec-20 §1/§2/§3/§4/§5/§6/§7**（模板 URL 容忍）、`workflow-node-development.md` **§5.5**、frontend-spec **§5.1 / §5.2 / §9 / §9.3 / §12 / §14** |

> 本文档为**纯契约变更**，不含代码实现。按 CONTRACT §11「禁止先改代码后补契约」要求先行落地，人工确认后再进入实现。

## 1. 动机

用户要求在设计器里创建 `app/sdn/config/sdn_alert_inspection.yaml`（SDN 接口告警巡检：11 节点 / 13 边 / 14 个 state
字段，含一条循环回边）。这份 YAML 今天**在磁盘路径上跑得通**——`python -m app.sdn run` 经
`app/sdn/__main__.py` 直接加载，`PythonNodeConfig.sandboxed` 默认 `False`（`python_node.py:54`）→ 进程内
`exec`，import 随便用，也不过 SSRF 守卫。

但**经 `PUT /workflows/{id}` 保存会被拒**。逐节点跑注册期实际会执行的两个校验器，实测结果（2026-09-14）：

| 节点 | 类型 | 结果 |
| --- | --- | --- |
| `get_token` / `get_alerts` / `pe_info` / `command_exec` | http | **REJECT** — `scheme '' not allowed; only http/https permitted` |
| `pick_vendor` / `parse_result` / `report` | python | **REJECT** — `sandbox code rejected [no-import] at line 1: imports are not allowed in sandboxed code` |
| `extract_token` / `prepare_check` / `prep` | python | PASS |
| `llm_summary` | llm | 无静态守卫（但构建期要求 `OPENAI_API_KEY`，见 §5 残留风险 6） |

**11 个节点里 7 个被拒**，来自三个彼此独立的缺口：

### G-A 沙箱全禁 import（S22 规则 1）

三个 python 节点分别用 `json.dumps`、`re.compile`、`random.randint` + `datetime.now` + `re.search`。
§4.14 规则 1 现文是「**全禁，无白名单模块**」。用户口径明确：「`import json` / `import re` /
`import random` + `from datetime import datetime` 这种内置的是可以 import 的」。这些都是**纯计算 stdlib**，
不携文件系统、网络或进程能力；一刀切禁掉的代价是「设计器里的 python 节点做不了任何像样的数据加工」——
连解析一段正则、格式化一个时间戳都不行。

### G-B 模板 URL 过不了注册期 SSRF（spec-20）

4 个 http 节点的 `url` 以 `{sdn_base_url}` 占位符开头，`urlparse` 取不到 scheme，
`validate_http_url`（`security.py:48`）当场拒。**根因不是校验太严，而是校验时机错了**：注册期校验的是
**未渲染的模板**，而 base URL 天然按环境变化（开发 / 预发 / 生产各一套），不可能写死。

换成字面量也不行：`security.py:61` 会做**真实 DNS 解析**，`sdn-controller.example.com` 解析不了 →
`cannot resolve host`。而执行期其实**已经有**一道对**已渲染 URL** 的校验（`http_node.py:211`，在
`_send_with_retry` **之前**），所以注册期这一道对模板 URL 既不可行、也不必要。

### G-C 前端没有 headers 编辑器

4 个 http 节点都要 `Authorization: Bearer {access_token}` 与 `Content-Type`。`HTTPNodeConfig.headers`
（`http_node.py:56`）在后端存在，但 `HttpNodeForm.vue` 只有 URL / Method / Body Template / Response Path /
Mock 五项，`DEFAULT_CONFIGS.http`（`nodeCatalog.ts:26`）也没有 `headers` 键。**即使 G-A/G-B 修好，设计器仍画不出这份 YAML**。

### 为什么「之前的引擎可以」

这份 YAML 不是「以前能存、现在存不了」，而是**从来没走过 PUT**。三个 commit 决定了时间线：

| 日期 | commit | 事件 |
| --- | --- | --- |
| 2026-08-11 | `8013fbd` | `python_node.py`（159 行，进程内 `exec`）与 `app/sdn/` **同一个 commit** 加入；同期 `api.py` 已上线，但 S18 白名单只有 `{llm, http}`——**python 节点当时根本不能经 HTTP 保存** |
| 2026-09-10 | `75a5a14` | `security.py` SSRF 守卫加入，接进 PUT 注册链 |
| 2026-09-14 | `232d752` | S22 真沙箱加入；S18 第一次修订才把 `python` 放进 PUT 白名单（code-only + AST 通过 + 强制 `sandboxed=true`） |

即：SDN YAML 诞生时，「经 HTTP 保存一个 python 节点」这件事还不存在；等它存在了，门槛已经是沙箱级的。
而它一直走的 `python -m app.sdn` → `build_registry(--dir app/sdn/config)` **磁盘加载路径不过
`api.py` 的校验链**——AST 预检与 SSRF 守卫都挂在那条链上，所以这条路今天依然跑得通。

**8/11 那版 python 节点把 local import 当作设计意图**，不是漏网：函数 docstring 原文
「Wrap inline code into a function so `return` / **local imports** work」，执行体
`exec(wrapped, namespace)` 且 `namespace = {}`——空字典意味着 Python 自动注入**完整内建**，
`__import__` 在手，`json`/`re`/`random`/`datetime` 自然可用；前提写在注释里：
`# noqa: S102 — trusted repository-owned code by design`。这正是 S15 记录的「非沙箱 RCE」隐患。

**关键事实：非沙箱那条路至今还活着。** `python_node.py:116-126` 现在的分支是
`if sandboxed: run_sandboxed(...)`，否则进程内 `exec`；而 `PythonNodeConfig.sandboxed` 默认
`False`（`:54`）。S22 并没有消灭进程内 exec，只是把它挡在 HTTP 之外——**S18 挡的是「从 API 可达」，
不是「这段代码不存在」**。

由此得到本次变更的风险判断：磁盘路径**本来就允许任意 import**（完整内建 + `os.environ` + 文件 + 网络）。
在 PUT 路径上放开一个 18 项白名单，不是把风险面从 0 扩到 18，而是**把一个已存在的无限制面换成它的受限子集**。

代价同样要写清：S22 原来用「全禁」换来的是**不用评审**——「无网络」那行的整个论证建立在
「AST 全禁 import + builtins 无 `__import__` + 空 env」三条一起成立上。改成白名单后，安全负担从
「一次性全禁」变成「**每加一个模块都要逐个评审其公开与单下划线属性**」（实测 `re._compiler` 这类
单下划线属性可达，规则 3 只封 dunder）。这个负担是持续性的，见 §5 残留风险 1。

### 共同根因

S22 与 spec-20 都是为「设计器里现写的代码 / 现填的 URL」设计的；这份 YAML 来自**磁盘态受信路径**。
两条路径的策略差，在设计器一侧表现为「画不出来」。目标：**让同一份 YAML 既能在磁盘路径跑，也能从设计器创建，
且不削弱任何一条既有安全边界**。

## 2. 影响面

### 2.1 §4.14 规则 1 修订（import 白名单）

| 项 | 现文（2026-09-14 新增） | 本次修订后 |
| --- | --- | --- |
| 规则 1 | `import` / `from ... import`（`ast.Import` / `ast.ImportFrom`）——**全禁，无白名单模块** | `import` / `from ... import`——**根模块**须 ∈ `_ALLOWED_MODULES`，否则拒；相对导入（`ast.ImportFrom.level > 0`）**一律拒**（沙箱内无包上下文，且相对导入是绕过根模块判定的唯一路径） |
| 规则名 | `no-import` | **仍为 `no-import`**（刻意不改名，理由见下） |
| 消息 | `imports are not allowed in sandboxed code` | `module '<root>' is not allowed in sandboxed code`——**含模块名，仍不含代码正文**（H6） |
| 规则 2 | `_FORBIDDEN_CALLS` 含 `__import__` | **不变**：直接写 `__import__("os")` 仍拒。用户只能用 import 语句，经受限 importer 落地 |
| 规则 3 | 禁 dunder 标识符 / dunder 属性 | **不变**，且是白名单能成立的**前提**（见 §3.1 逃逸分析） |
| 规则 4 | 64 KiB 上限 | 不变 |

**规则名刻意仍为 `no-import`**：`tests/integration/workflow/test_python_sandbox_pipeline.py:132` 断言
`"no-import" in message`，且该集成测试的反例正是 `import os`——`os` 不在白名单，**这张卡不需要改就应当继续通过**。
改名会打破既有守护，而语义上「不允许（这个）import」依然成立。

**新增模块级常量**（`sandbox.py`，与 `_FORBIDDEN_CALLS` / `_MAX_CODE_BYTES` 同处，便于守护测试直接断言）：

```python
_ALLOWED_MODULES = frozenset({...})   # 具体集合见 §3.2 拍板项
"""允许在沙箱内 import 的 stdlib 根模块。判定只看根模块：`import a.b` / `from a.b import c` 均取 `a`。"""
```

**白名单常量在两个模块各存一份，并由守护测试钉住相等**（拍板结论，见 §3.4）：`sandbox.py`（AST 期）与
`sandbox_worker.py`（运行期复查）不能共享 import——worker 以 `sys.executable -I` 拉起、**不得进入任何模块的
import 图**（§4.14 约束原文），而把白名单塞进 stdin 文档则要改**已冻结的通信协议行**。故取「双份常量 +
守护测试」：测试**以文本方式 `ast.literal_eval` 解析两个源文件**取出常量比对，不 import worker。

### 2.2 S22 细则：两行修订 + 一行新增

| 行 | 现文要点 | 修订后 |
| --- | --- | --- |
| 内建白名单 | `SAFE_BUILTINS` **不含** `open/exec/eval/compile/__import__/globals/...` | `SAFE_BUILTINS` **新增 `__import__`**，其值是 worker 内的**受限 importer**（`_restricted_import`），**不是** `builtins.__import__`：只放行根模块 ∈ `_ALLOWED_MODULES` 的导入，`level > 0` 直接拒，运行期**复查**（纵深防御：调用方可能绕过注册期 AST 校验）。其余禁项（`open/exec/eval/compile/globals/locals/vars/dir/getattr/setattr/delattr/type/input/breakpoint/exit/quit/help`）**逐字不变** |
| 无网络 | 「AST 全禁 import + builtins 无 `__import__`/`open`/`getattr` + 空 env，使沙箱内代码在语言层面拿不到 `socket`/`urllib`/`http.client` 任何入口」 | 保证的**来源**改写为：「**受限 importer 的白名单不含任何网络模块**（`socket`/`urllib`/`http`/`ftplib`/`smtplib`/`webbrowser`/`asyncio` 全部不在 `_ALLOWED_MODULES`）+ builtins 无 `open`/`getattr` + 空 env + AST 规则 3 封死内省链」。**这一行必须改**，否则契约自相矛盾（一边说「无 `__import__`」一边白名单要生效） |

**第三行是新增的「模块白名单」行**（内容见 §2.1 的双份存储约定与 §3.2 的 18 项拍板），它与上面两行同处
S22 细则表；「无网络」行另附一条 *修订* 注记，显式记录该保证由**机制保证降级为评审保证**——这正是「模块白名单」行
要求逐模块评审的原因。

**不变更项（逐条钉住）**：进程隔离 `-I`、stdin/stdout 各恰好一个 JSON 文档的**协议形态**、退出码 0/2/3、
超时 kill、rlimit 四项自我施加 + `applied`/`refused` 报告、空 env `{"PATH": "/usr/bin:/bin"}`、输出上限、
摘要日志、R3 管线不变——**全部零改动**。白名单是 worker 内部常量，**不进协议**。

### 2.3 spec-20 修订（注册期容忍未解析模板）

| 位置 | 现文 | 修订后 |
| --- | --- | --- |
| spec-20 §2 **In** | 「http 节点且 `mock_enabled=false` → 校验，失败 422」 | 「http 节点且 `mock_enabled=false` 且 **url 不含未解析占位符** → 校验，失败 422；**含占位符 → 注册期跳过**，由执行期对**已渲染 URL** 的二次校验兜住」 |
| spec-20 §3 拦截规则第 4 条 | 「校验在**注册期**（spec-16）+ **执行期**（请求前）双点执行」 | 「**字面量 URL** 双点执行；**模板 URL** 只在执行期执行（注册期无从解析——占位符的值此刻还不存在）。执行期校验对两类 URL **一律生效**，故不存在无守卫的请求路径」 |
| spec-20 §2 **Out** | 「`mock_enabled=true`；DNS rebinding 深度防护」 | 追加一项：「模板 URL 的注册期校验（见 §3 第 4 条）」 |

**判定规则冻结**：`"{" in url` → 视为模板，跳过注册期校验。取最保守形态（**任何**占位符都跳过，不试图判断
占位符是否落在 host 位置）：`https://api.example.com/{path}` 这类「host 是字面量、路径是模板」的 URL 也会被
跳过注册期检查，代价是多一类 URL 失去构建期反馈，换来的是判定规则**无需解析 URL 结构**、不会因边界写法
（`https://{host}:{port}/x`、`{scheme}://host/x`）而漏判。

**安全性不降，理由必须写进契约**：执行期校验在 `http_node.py:211`，位置在 `_send_with_retry` **之前**，
对渲染后的**具体** URL 做完整 scheme + 私网段 + 白名单检查；重试复用同一个已渲染 URL，不存在「校验一次、
请求另一个」的缝。`mock_enabled=true` 分支在更前面短路（`http_node.py:206`），零网络（S9），与本修订无关。

**取舍**：host 拼错从「保存即 422」退化为「执行才报错」——与 S18 第二次修订对**悬空引用**的取舍同款
（「保存能过、执行才失败」优于「把顺序/可解析性变成隐性契约」）。

### 2.4 前端：HTTP 节点表单增加 headers

| 位置 | 变更 |
| --- | --- |
| `nodeCatalog.ts` `DEFAULT_CONFIGS.http` | 新增 `headers: {}` |
| `HttpNodeForm.vue` | 新增 headers 编辑区；表单形态见 §3.3 拍板项 |
| `NodeConfigPanel` | 无逻辑改动（透传 `update:config`），但 patch 守护测试须覆盖 headers |
| `PythonNodeForm.vue` | **仅改沙箱限制文案**（`:63`「禁止 import：无第三方库，也无标准库」→ 白名单口径），结构与逻辑零改动；详见 §2.5 更正项 |

**与 `mock_responses` 的关键差异**：`headers` 的值**只能是字符串**（`HTTPNodeConfig.headers: dict[str, str]`），
不存在嵌套形态，故无论用键值行还是 JSON 文本域，都**不会**重蹈 `mock_responses` 的覆辙（上一期 §5 残留风险 6：
后端要 `dict[str, str]` 而前端文本域诱导用户写嵌套对象 → 前端能构造出后端必拒的载荷）。

### 2.5 不变更项（显式声明）

- **冻结签名零改动**：`validate_code_ast(code) -> None`、`SandboxLimits`（三字段与默认值）、
  `run_sandboxed(code, state, limits)`、`validate_http_url(url) -> None`、`HTTPNodeConfig`（八字段）。
  本次只改**规则语义**与**调用条件**，不改任何签名——故下游代码提交用 `refactor!` 而非 `feat!`。
- `PythonNodeConfig`（`code`/`entry`/`sandboxed` 三键 + 互斥校验）**零改动**；`sandboxed` 仍由服务端强制 `true`（S18 第一次修订）。
- `security.py` **内部逻辑零改动**：`validate_http_url` 仍对任何传入 URL 严格校验；改动只在 `api.py` 的**调用条件**。
- S18 白名单集合仍为 `{llm, http, python, subworkflow}`（4 类），不加新节点类型 → §8 R1 carve-out、
  `NodeType` 成员数恒 2、R4 内置分支恒 2 **三个守护测试全部不动**。
- S21（输入合成）、S23/S24（嵌套守护与日志合并）、S17（YAML 落盘）、S19（写端点鉴权）**零改动**。
- `WorkflowDefinition` **不加字段**（`recursion_limit` 见 §5 残留风险 5，本期不做）。
- `state.py` / `utils.py` / `store.py` / `registry.py` / `graph_builder.py` / `factory.py` /
  `nodes/*.py`（含 `python_node.py`、`http_node.py`）/ `sandbox_worker.py` 的**协议处理与 rlimit 逻辑**：**零改动**
  （worker 只新增 `_ALLOWED_MODULES` 常量与 `_restricted_import`，并在 `SAFE_BUILTINS` 里加一个键）。
- `WorkflowTraceDrawer.vue` **零改动**。
- ~~`PythonNodeForm.vue` 零改动~~ → **不成立，已更正**：该组件 `:63` 的沙箱限制文案是
  「**禁止 import：无第三方库，也无标准库**」，白名单放开后这句话变成**假的**，必须改为
  「只能 import 白名单内的标准库（`json`/`re`/`math`/`random`/`datetime`/`collections`/`functools` 等）；
  第三方库、`os`/`sys`/`socket` 等一律拒绝」。除此之外**结构与逻辑零改动**（仍只暴露 `code`、无 `entry`、无 `sandboxed`）。
  `tests/components/python-node-form.spec.ts` 的四张文案卡断言的是 `text` 含
  `'import'` / `'文件'` / `'网络'` / `'dict'` / `'超时'` / `'内存'` / `'422'`——新文案**全部仍满足**，故**无需改写既有断言**，
  只需按 §4 新增一张「文案不得再声称禁止一切 import」的反向卡。

### 2.6 顺带同步的下游文档（消除自相矛盾，不含新决策）

CONTRACT §11.3「禁止只改 CONTRACT 不同步 spec」。本次除 §2.1-§2.4 外还改了以下位置，
**全部是为消除与修订后契约的矛盾**，不引入任何新决策：

| 位置 | 原文问题 | 处理 |
| --- | --- | --- |
| CONTRACT §4.13「节点类型白名单」 | 仍写 `{llm, http, python}` **3 类**，而 §6 S18 第二次修订后已是 4 类——**本次之前即存在的遗留漂移** | 补 `subworkflow` 及其「注册期仅结构校验、存在性为运行期检查」 |
| CONTRACT §4.14 约束 | 只说「`sandbox_worker.py` 不进入 import 图」，未给出白名单双份存储的**推论**；R8 的模块级常量清单没有 `_ALLOWED_MODULES` | 各补一句 |
| CONTRACT §5 场景映射 | SSRF 只有一行「→ 422」，与模板 URL 修订矛盾；AST 行的 `import` 未限定 | SSRF 拆两行（字面量 / 模板）；AST 行限定为「**白名单外** import 或相对导入」 |
| CONTRACT S22 细则「内建白名单」 | 冻结的异常清单有笔误 `LookError` | 改 `LookupError`（**纯笔误**，不改语义） |
| `workflow-node-development.md` §5.5 | S18 条件 ② 写「拒绝 import」；S22 冻结约定清单无「受限 `__import__`」与「模块白名单」；「两点必须记住」缺评审制那条 | 条件 ② 限定为白名单外 + 相对导入，并写明根模块判定与消息内容；清单补两项；「两点」改「三点」，新增「无网络由机制保证降级为评审保证」 |
| `workflow-frontend-spec.md` §5.1 | `python` 面板文案要求「明示**无 import**」，修订后为假；`subworkflow` 的 `input_map` 指向「http 节点 headers/mock 的编辑模式」而 headers 当时并不存在 | 面板文案改为「仅白名单 stdlib 可 import」+ 新增「前端不复制白名单常量」；新增 **`http` 节点前端约束**（headers 键值行 / 占位符不做前端校验 / 模板 URL 提示非边界）；`input_map` 改指 headers 键值行 |
| `workflow-frontend-spec.md` §5.2 | 无模板 URL 的校验时机说明 | 新增一条，含「前端不得自行做私网段判断」（前端看不到 DNS） |
| `workflow-frontend-spec.md` §9 / §9.3 / §12 / §14 | 组件树与表单字段表无 `headers`；测试覆盖点与验收标准无对应项 | 各补齐（§12 新增覆盖点 6；§14 新增 http 一条，python 一条补白名单正反例） |
| `spec-20` §1 / §2 / §3 / §4 / §5 / §6 / §7 | §1 目标与 §3 第 4 条都断言「注册期即校验」；RED 卡无模板 URL 用例；GREEN/REFACTOR/DoD 未记录「改动只落在 `api.py` 调用条件」 | §1 加限定语；§2 In/Out 各改一处；§3 第 4 条改写 + **新增第 5 条模板判定**（含 `validate_http_url` 零改动与安全性不降的依据）；§4 加两张卡；§5 加落点与 R8 提醒（helper 应进 `security.py`）；§6 加「判定与 `mock_enabled` 短路同处一条链」；§7 DoD 加模板 URL 一项 |

## 3. 备选方案对比（§11.4 + R-EXP 第 5 条：给 2-3 个附影响面）

### 3.1 import 放开的**机制**

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| **A. AST 白名单 + worker 受限 `__import__`** | 规则 1 改为「根模块须在白名单内」；`SAFE_BUILTINS` 加一个受限 importer，运行期复查同一份白名单 | `sandbox.py` 改规则 1 + 新增常量（现 228 行，余量充足）；`sandbox_worker.py` 新增常量 + 约 15 行 importer（现 189 行）。**协议零改动**、**签名零改动**。既有 YAML 字面 `import json` 直接可用，无需改写 | **采纳** |
| B. 预注入模块名，仍禁 import 语句 | worker 把 `json`/`re`/... 直接放进 exec 命名空间，AST 规则 1 不动 | 改动更小、完全不碰「无 `__import__`」这条保证。但**现有 YAML 一行都过不了**（它们字面写着 `import json`）；要兼容就得让 AST 容忍并**静默删除** import 语句——「代码里写了 import 但实际没执行」比直接拒绝更难查，且用户看到的 YAML 与落盘 YAML 语义不一致 | 否决 |
| C. 给受信来源开 `sandboxed=false` | 设计器加「受信代码」开关，或 `examples/` 目录豁免沙箱 | 直接复活 S15 的 HTTP RCE。S18 第一次修订的原话是「`sandboxed` 是**安全属性**而非用户偏好，交给客户端等于把 RCE 开关暴露给请求方」；目录豁免则让「谁能写 examples/」变成新的信任边界，且 PUT 落盘目录（`config/user/`）与 examples 只隔一次路径混淆 | 否决 |

**A 的逃逸分析（为什么白名单在这里是可辩护的）**：拿到一个真实模块对象后，经典逃逸链是
`json.__loader__` / `().__class__.__bases__.__subclasses__()` / `func.__globals__`——**全部依赖 dunder 属性**，
而规则 3 对 `ast.Name.id` 与 `ast.Attribute.attr` 双向往禁；动态取属性要靠 `getattr`/`vars`/`dir`/`type`/
`globals`/`locals`，**全部在规则 2 的 `_FORBIDDEN_CALLS` 里**。因此白名单模块暴露的可达面 = 其**具名公开属性 +
单下划线属性**。这是**评审制而非机制制**的边界，故 §5 残留风险 1 要求「未来每加一个模块都必须逐个评审」。

### 3.2 白名单**范围**

| 方案 | 模块集 | 影响面 | 结论 |
| --- | --- | --- | --- |
| A. 纯计算 16 项 | `json` `re` `math` `statistics` `decimal` `fractions` `random` `datetime` `itertools` `bisect` `heapq` `copy` `string` `textwrap` `base64` `hashlib` | 已覆盖本 YAML 的全部需要（`json`/`re`/`random`/`datetime`）。**无类创建能力**，可达面最小。但分组计数、累加、偏函数都要手写 | 否决（用户 2026-09-14 拍板选 B） |
| **B. 16 项 + `collections` + `functools`（18 项）** | 上述 + `collections`（`OrderedDict`/`defaultdict`/`deque`/`Counter`）+ `functools`（`partial`/`reduce`/`lru_cache`） | 数据加工顺手得多（分组计数、累加、偏函数）。代价：`collections.namedtuple` 与 `functools.singledispatch` **会创建类**，是白名单里最锋利的两处。评审结论：创建类本身不构成逃逸——拿到类之后仍要靠 dunder 才能上溯到 `object.__subclasses__()`，而规则 3 封死；实测 `namedtuple` 的实现确实经 `exec` 生成 `__init__`，但那是 stdlib 自己的代码、模板固定，用户只能提供字段名 | **采纳**（用户 2026-09-14 拍板） |
| C. 全 stdlib 黑名单制 | 默认放行，只禁 `os`/`sys`/`subprocess`/`socket`/… | 与 S22 的**能力剥夺**思路相反，且黑名单必然漏。实测三个反例：`uuid.getnode()` 的模块源码里**确有 `subprocess.Popen`**（会 shell out 找 MAC 地址）；`typing.get_type_hints()` 源码里**确有 `eval(`**（对字符串注解求值）；`logging` 能开文件、`sqlite3` 能写盘、`pickle.loads` 能执行任意代码。新增 stdlib 模块默认放行 = 每次 Python 升级都要重新审计 | 否决 |

**明确排除清单（每条一句理由，写进契约以免下次重新讨论）**：

| 排除 | 理由 |
| --- | --- |
| `os` `sys` `subprocess` `shutil` `pathlib` `glob` `tempfile` `io` `resource` `signal` `pty` | 文件系统 / 进程 / 解释器控制，直接等于逃逸 |
| `socket` `urllib` `http` `ftplib` `smtplib` `webbrowser` `asyncio` `ssl` `selectors` | 网络能力；S22「无网络」行的直接对象 |
| `importlib` `inspect` `gc` `builtins` `types` `ctypes` `code` `threading` `multiprocessing` | 内省 / 帧访问 / 动态导入 / FFI——绕过规则 3 的标准手段 |
| `pickle` `shelve` `marshal` `dill` | 反序列化即任意代码执行 |
| `logging` `sqlite3` `dbm` `csv`（写路径） | 能落地文件；`RLIMIT_FSIZE=0` 是兜底而非设计意图 |
| `uuid` | **实测**：模块源码含 `subprocess.Popen`，`getnode()` 在某些平台 shell out 取 MAC |
| `typing` `dataclasses` `enum` | **实测**：`typing.get_type_hints` 源码含 `eval(`；三者都会创建类且无本 YAML 需要的收益 |
| `time` | `time.sleep` 只是把 timeout 预算烧掉；时间需求由 `datetime` 覆盖 |
| `re` 的风险单列 | 不是排除项，但 `re` 进白名单即引入 **ReDoS**：灾难性回溯靠 `RLIMIT_CPU` + `timeout_s` 兜，最坏占满一个 CPU 核到超时（§5 残留风险 3） |

### 3.3 headers 的**表单形态**

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| **A. 键值行** | 「+ 添加请求头」按钮，每行 key 输入框 + value 输入框 + 删除；与 `StateSchemaPanel` 的字段行同款交互 | 类型天然只能是 `dict[str, str]`，**不可能**构造出后端拒绝的载荷；无需 JSON 解析与错误态；组件约 +40 行 | **采纳**（用户 2026-09-14 拍板） |
| B. 单个 JSON 文本域 | 与 `HttpNodeForm` 现有 `mock_responses`、`SubWorkflowNodeForm` 的 `input_map` 同款 | 视觉一致、代码最少。代价：要处理非法 JSON 的「不 emit」分支，且用户会写出 `{"Authorization": 123}` 这类**值非字符串**的载荷——正是残留风险 6 的同型问题 | 否决 |

**拍板记录（2026-09-14）**：§3.2 取 **B（18 项）**、§3.3 取 **A（键值行）**；交付节奏为
**一次做完 G-A + G-B + G-C 再演示**——后端两项合一个 `refactor!` commit，前端一个 `feat` commit，
容器 PUT 与浏览器创建演示放在门禁全绿之后。

### 3.4 白名单常量的**存放方式**

| 方案 | 描述 | 影响面 | 结论 |
| --- | --- | --- | --- |
| **A. 双份常量 + 文本解析守护测试** | `sandbox.py` 与 `sandbox_worker.py` 各存一份 `_ALLOWED_MODULES`；守护测试用 `ast.literal_eval` **从源文件文本**取出两份比对相等 | 不碰冻结协议行，不让 worker 进入 import 图（§4.14 约束原文）。代价：同一事实两处存储，靠测试而非结构防漂移 | **采纳** |
| B. 经 stdin 文档下发白名单 | 父进程把白名单放进请求文档，worker 不再存常量 | 单一真相来源，但**改动已冻结的通信协议行**（入参文档「恰三键」），且每次执行多序列化一份数据 | 否决 |
| C. worker `import sandbox` | 让 worker 直接复用常量 | 违反「`sandbox_worker.py` 不进入任何模块的 import 图」，且 `-I` 下 `app.workflow.models` 不可导入 → worker 启动即崩 | 否决 |

## 4. 验收

### 守护测试修订（允许改动的既有断言）

- `tests/unit/workflow/test_sandbox.py` 中「任何 import 都拒」的卡 → 拆成「白名单外拒（规则名 `no-import`、消息含模块名）/ 白名单内过」。
- `SAFE_BUILTINS` 相关的成员断言（若有「不含 `__import__`」的卡）→ 改为「含 `__import__`，且其**不是** `builtins.__import__`」。
- **不动**：`tests/integration/workflow/test_python_sandbox_pipeline.py` 的 `import os` 反例（`os` 不在白名单，仍须 422 + `no-import` + 不泄漏代码正文）；`test_http_ssrf.py` 用**字面量** host 的全部卡；`NodeType` 成员数 == 2；R4 内置分支 == 2；`test_exception_hierarchy`。

### 新增测试（后端）

- **AST 放行**：`import json`、`from datetime import datetime`、`import collections.abc`（根模块判定）、
  `import re as _re`（别名不改根模块判定）→ 通过。
- **AST 拒绝**：`import os`、`from os import path`、`import uuid`、`import typing`、`from . import x`（相对导入）、
  `import socket` → 拒；消息含 `no-import` + 行号 + **根模块名**，且**不含代码正文**（H6）。
- **受限 importer 运行期复查**：经 `run_sandboxed` 执行 `import json; return {"k": json.dumps({"a": 1})}` → 成功；
  断言 `SAFE_BUILTINS["__import__"] is not builtins.__import__`；断言直接调
  `SAFE_BUILTINS["__import__"]("os", ...)` 抛 `ImportError`（不经 AST 的运行期防线）。
- **逃逸回归卡（必须写）**：`import json` 后 `json.__loader__` / `().__class__` / `getattr(json, "x")` /
  `json.__builtins__` → 全部被规则 2/3 在**注册期**拒（不是运行期）。
- **白名单双份一致性**：文本解析 `sandbox.py` 与 `sandbox_worker.py` 的 `_ALLOWED_MODULES` → 两个集合相等且非空。
- **端到端语义卡**：本 YAML 三个节点的**原样代码**逐一经 `validate_code_ast` → 全通过（这张卡直接钉住动机）。
- **api**：`url` 含 `{placeholder}` 且 `mock_enabled=false` → PUT **200**；字面量私网 host → 仍 **422**；
  `url` 为字面量公网 host → 仍走注册期校验（钉住「只有模板才跳过」）。
- **执行期兜底**：模板渲染结果为 `http://169.254.169.254/latest/meta-data` → `WorkflowValidationError`
  （钉住「跳过注册期 ≠ 没有边界」）。

### 新增测试（前端）

- `http-node-form.spec.ts`：加一行 header → emit 的 config 含 `headers: {K: V}`；删除行 → 键消失；
  readonly 下控件禁用；`props.config.headers` 为 `undefined` 时不崩且初始为空。
- `node-config-panel.spec.ts`：http 节点的 patch 透传 headers（不丢键）。
- `node-palette.spec.ts` / `use-workflow-graph.spec.ts`：新拖出的 http 节点默认 config 含 `headers: {}`。
- `python-node-form.spec.ts`（**文案反向卡**，见 §2.5 更正项）：`.python-node-form__limits` 文本
  **不得**再含「禁止 import」/「无标准库」，且须**点名至少一个白名单模块**（如 `json`）；
  既有四张卡断言的 `'import'` / `'文件'` / `'网络'` / `'dict'` / `'超时'` / `'内存'` / `'422'` **仍须全部命中**
  （即改文案不许削弱原有提示）。

### 门禁

`uv run pytest -m unit`、`uv run pytest -m integration`、`make lint`、`make typecheck` 全绿；
前端 `npm run type-check`、`npm test`、`npm run build` 全绿；覆盖率 ≥ 80%（R7）。
已知无关失败：`tests/integration/sdn` 2 卡（依赖真实 DNS）、前端 `agent-list.spec.ts` 2 卡（`interrupt_on`）。

### E2E（本次验收的主体，直接对齐用户诉求）

1. **原样落盘**：容器内以 in-process ASGI PUT 这份 YAML 的**原样内容**（凭据保留 `CHANGE_ME_*` 占位符）→ **200**，
   且落盘 YAML 中三个 python 节点 `sandboxed: true`（服务端强制，S18）。
2. **浏览器创建**：登录 → 工作流列表 → 新建设计器 → 建出 11 个节点（4 http + 6 python + 1 llm，含 END）、
   13 条边（含 4 条条件边与 `parse_result → prepare_check` 循环回边）、14 个 state 字段（含 `reducer: add`
   的 `check_result` 与 4 个带 `default` 的凭据字段）→ 保存 → 列表出现 `sdn_alert_inspection`。
3. **语义比对**：打开 YAML 预览，与 `app/sdn/config/sdn_alert_inspection.yaml` 逐项比对节点 / 边 / 条件 /
   schema / 默认值一致。
4. **反例**：把某个 python 节点的 `import re` 改成 `import os` → 保存 **422**，UI 显示 `no-import` + 行号，
   且**不回显代码正文**（H6）。

> **不执行真实运行**：跑通需要可达的 SDN 控制器（自签证书，且入口靠 `app/sdn/__main__.py` 进程内放宽
> `verify`——引擎的 HTTPNode 无 verify 开关）与 `OPENAI_API_KEY`。本期验收止于「能创建 + 能落盘 + 能注册 +
> 能编译成图」。真实运行仍走磁盘路径。

## 5. 残留风险与 open questions

1. **白名单模块的属性面是评审制而非机制制**：规则 3 封死 dunder，但**单下划线属性可达**——实测
   `re._compiler` 存在。未来每加一个模块，必须逐个评审其公开与单下划线属性是否携带能力；
   「加了再说」在这里不成立。
2. **`collections.namedtuple` / `functools.singledispatch` 能创建类**（若选 §3.2 方案 B）：实测 `namedtuple`
   源码经 `exec` 生成 `__init__`。评审结论是可接受（模板固定、用户只提供字段名、拿到类后仍须 dunder 才能上溯），
   但这是白名单里最锋利的两处，**记录在案以便复审**。
3. **ReDoS**：`re` 进白名单后，灾难性回溯（如 `(a+)+$` 对 `"a"*30`）靠 `RLIMIT_CPU` + `timeout_s` 兜底，
   最坏占满一个 CPU 核到超时。生产建议把 `timeout_s` 从默认 10s 下调，或在容器层限 CPU。
4. **模板 URL 失去构建期反馈**：host 拼错、或占位符名写错，从「保存即 422」退化为「执行才报错」。
   与 S18 悬空引用同款取舍。前端可加体验层提示（**不是边界**）。
5. **`recursion_limit` 在设计器路径无开关**：本 YAML 每条告警走 5 步循环，langgraph 默认 25 → 约 4 条告警即撞上限。
   磁盘路径由 `app/sdn/__main__.py` 设 `LANGGRAPH_DEFAULT_RECURSION_LIMIT=200`，**设计器路径没有等价手段**。
   是否给 `WorkflowDefinition` 加 `recursion_limit` 字段 → **本期不做**（属新增冻结字段的 S 级契约变更），记为 open question。
6. **`llm_summary` 的构建期 env 依赖**：无 `provider_ref` 时走 AD-12 默认 env 名，`LLMNode.validate_config`
   在缺 `OPENAI_API_KEY` 时抛 `ValueError` → PUT **422**。E2E 前**须先确认容器 env**，否则第 1 步会失败在
   与本次变更无关的地方。
7. **`mock_responses` 值类型陷阱**（上一期 §5 残留风险 6）本期**不修**；headers 表单选 §3.3 方案 A 正是为了
   不再造一个同型问题。
8. **`api.py` 已 497 行，超 R8 的 400 行红线**（本期之前即如此，Phase 2 开始时 462 行）。本次要在
   `_validate_definition_payload` 里加占位符判定（约 +3 行），会把它推得更高。**列为待办拆分项，不在本期夹带**
   ——但若实现时发现必须新增独立 helper，应把该 helper 放进 `security.py`（它已是 SSRF 的归属模块）而不是 `api.py`。
9. **前端文案与后端白名单会漂移**：§5.1 明确要求「前端**不复制**白名单常量」（白名单是后端安全属性，复制一份就等于
   造第二个真相来源）。代价是 `PythonNodeForm.vue` 的限制说明只能**举例**（`json`/`re`/`datetime` 等），
   后端增删模块时这段文案不会自动跟随。缓解：文案卡只钉「不得声称禁止一切 import」+「至少点名一个白名单模块」，
   具体某个模块能不能用**以后端 422 的 `message` 为准**（该 message 含根模块名），前端不做二次解释。
