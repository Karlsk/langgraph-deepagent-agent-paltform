# G4 Chat 交互层回归测试报告

**测试时间**：2026-08-28 05:15 – 05:35（UTC）
**测试环境**：development（Docker 全新构建：`make docker-destroy` → `make docker-up`，迁移链执行至 `k6d8f2b4e7a8`；前端 Vite dev `localhost:5173`）
**测试文档**：`docs/agentapp-manual-testing.md`（§2/§6/§7/§8，含本轮补全的 G4 配置）
**测试账号**：tester@example.com / tester2@example.com
**LLM**：MiniMax-M3（OpenAI 兼容端点，真实调用）

## 一、结果总览

| 类别 | 通过 | 失败 | 部分通过 | 通过率 |
|---|---|---|---|---|
| API 测试（14 用例） | 14 | 0 | 0 | 100% |
| 浏览器测试（10 步） | 9 | 0 | 1 | 90% |
| **总体** | **23** | **0** | **1** | **96%** |

**总体结论：通过**。无阻断缺陷；发现 1 项 Major（HIL 会话 L2 历史完整性，根因在落库语义设计取舍）、4 项 Minor、2 项观察项。

## 二、API 测试详细结果

前置：注册 tester（200）→ 登录（200，OAuth2 form）→ 创建 reg-test-app（id=2）与 reg-test-hil（id=3，`interrupt_on={"duckduckgo_results_json": true}`）→ 双双发布（200）→ 会话 `$SID` / `$SID_HIL`。

| # | 用例（文档节） | 结果 | 关键证据 |
|---|---|---|---|
| 1 | 注册+登录（§2） | ✅ | 扁平 LoginResponse：access_token + refresh_token |
| 2 | 创建+发布应用（§6.1/6.3） | ✅ | 201 → publish 200（system_prompt 必填，见 Minor-4） |
| 3 | HIL 应用创建（§8.3 前置） | ✅ | interrupt_on 配置生效 |
| 4 | 创建会话（§7.1） | ✅ | 201，session_id/name/agent_app_id 齐全 |
| 5 | 非流式 POST /chat（§8.1） | ✅ | 200；messages 仅本轮 assistant 段；interrupt=null |
| 6 | 流式 POST /chat/stream（§8.2） | ✅ | message×N → tool_call×3 → done；compressed=false；message_count=96 |
| 7 | GET /messages（§8.4） | ✅ | 轮级 L2 投影（user/assistant 对）；seq/ts/summary 字段齐全；pending=null |
| 8 | HIL 中断+approve+reject（§8.3） | ✅ | interrupt 帧 tool/args 正确；done(interrupted=true)；approve→tool_call 真实执行→再中断；reject→合成「User rejected」tool_call 帧→模型自行回复→done(interrupted=false) |
| 9 | pending 恢复（§8.3/8.4） | ✅ | 中断态 GET /messages：pending_interrupt 含完整 action_requests |
| 10 | rebuild（§8.5） | ✅ | pending 时 409；空会话 422（"no readable L2 rows"）；PG 清 checkpoint（16+10+24 行）后 rebuild 200（rebuilt=4, skipped=0, l2_lines=4）；续聊正确复述重建前查到的「1.2.11」 |
| 11 | GET /chat/traces（§8.6） | ✅ | 倒序；source=chat；status/turns/duration/events（llm_call/tool_call/run_finished）齐全 |
| 12 | 边界（§8.1/8.4） | ✅ | 无 X-Session-Id→422；错误 SID→404；他人会话→404（不泄露存在性） |
| 13 | 会话自动起名（§8.1） | ✅ | name 由 '' →「用一句话介绍 LangGraph 是什么」 |
| 14 | 会话导出（§7.2） | ✅ | JSON（元信息+messages 数组）/JSONL（6 行）均含全部轮次 |

补充验证：SSE 心跳实现确认存在（`chat_service.py:379`，`_HEARTBEAT_SECONDS=15.0` 空闲阈值）；本次测试流式 chunk 持续活跃未达空闲阈值，未触发 `: ping` 属预期。

## 三、浏览器测试详细结果（§8.7）

| 步骤 | 结果 | 截图（`.qoder/`） |
|---|---|---|
| 1 登录 | ✅ | reg-g4-01-login-success.png |
| 2 会话列表+自动命名 | ✅ | reg-g4-02-chat-list.png |
| 3 历史渲染（3 轮） | ✅ | reg-g4-03-chat-history.png |
| 4 流式发送+回复 | ✅ | reg-g4-04-docker-reply.png |
| 5 HIL 审批卡 | ✅ | reg-g4-05-hil-approval-card.png |
| 6 批准→工具执行→回复 | ✅（连续 3 次审批均正常恢复） | reg-g4-06/07 |
| 7 刷新恢复 | ⚠️ 部分通过：审批卡恢复 ✅；HIL 原始提问文本缺失（见 Major-1） | reg-g4-08 |
| 8 拒绝→对话结束 | ✅ | reg-g4-09 |
| 9 轨迹抽屉 | ✅（9 轮次，事件详情展开正常） | reg-g4-10/11 |
| 10 重建确认+成功 | ✅（rebuild 200，rebuilt_messages=8） | reg-g4-12/13 |

Console：页面自身无红色 JS 错误（探测性 4xx 为测试脚本产生）。

## 四、发现的问题

### Major-1：HIL 会话刷新后原始提问文本丢失（后端 L2 落库语义）

- **现象**：HIL 会话中用户提问「上海天气」→ 审批中断 → 批准完成。刷新后 `GET /messages` 与聊天页均无该提问原文，仅有 `{"decisions":[...]}` 形式的 user 行。
- **根因**（已实证）：`runtime.py:_fire_context_record`（L377-434）仅在**成功轮**写 L2（user=本轮 invoke 最后一条 user 消息，assistant=最后 AIMessage）；中断轮不写。resume 轮成功时 invoke 输入即 decisions JSON 串，原始 fresh 提问未被补落。对照实验：fresh 无工具轮成功后 seq9/seq10 双行齐全。
- **影响**：HIL 会话历史可读性受损（用户看不到自己问过什么）；不影响 checkpoint 续聊与 rebuild（L1/L2 各司其职）。
- **建议**：resume 成功轮落库时将线程中尚未落库的 fresh user 输入一并补落（或在 decisions 行附加原始问题引用）；属 G4 后续优化项，不在本次回归修复范围。

### Minor

1. 登录成功后侧边栏用户名不即时更新（需手动刷新）——G1 前端既有问题，非 G4 引入。
2. 会话列表 `message_count` 全为 null（单查 `GET /sessions/{id}` 有值）；列表端点未回填统计。
3. HIL 会话自动命名截断为 20 字符（「必须使用 duckduckgo_resu」）——**spec C 阶段设计行为**（name ≤ 20 字），非缺陷，列出备查。
4. §6.1 请求体 `system_prompt` 必填，但 0.4/6.1 的示例未显式列出（首次创建报 422 后补齐）——建议文档示例补该字段。

### 观察项（不计入失败）

- `<think>` 推理标签原文渲染于 assistant 气泡（模型行为 + 前端无过滤）。
- tool_call 折叠面板标题拼接完整搜索结果，折叠态标题过长。

### 环境项

- 打开页面残留旧账号失效 token，出现一次 "User not found" 后正常跳登录页（本地缓存，非缺陷）。

## 五、测试数据与清理

- 测试用户：tester@example.com、tester2@example.com（保留，供复测）
- 测试应用：reg-test-app（id=2）、reg-test-hil（id=3）；会话 5 个（含 2 空会话）
- 原始证据：`/tmp/reg_*.json|txt`（API 响应/SSE 流原样落盘）、`.qoder/reg-g4-*.png`（13 张截图）

## 六、结论

G4 五端点（/chat、/chat/stream、/messages、/rebuild、/chat/traces）+ HIL 决策链 + rebuild 灾难恢复 + 前端聊天页全部按 spec 语义工作；自动起名、SSE 帧契约、错误边界（422/404/409）实测符合。唯一实质性发现为 Major-1（HIL 原始提问不落 L2），建议列入 G4 收尾 backlog。
