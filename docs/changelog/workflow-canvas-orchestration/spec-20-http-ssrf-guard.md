# spec-20 · http 节点 SSRF 防护（host 白名单 / 内网拦截）

| 里程碑 | 端 | 人日 | 依赖 | 契约编号 |
| --- | --- | --- | --- | --- |
| M3 | 后端 | 1 | spec-16（注册期校验）；http 节点执行器 | S6（构建期失败优于运行期）；H6；frontend-spec §5.1 |

## 1. 目标

为 `http` 节点的真实请求（`mock_enabled=false`）加 **SSRF 防护**：注册期（PUT）即校验 `config.url` 的 host，阻断内网 / 环回 / 链路本地 / 云元数据地址，并支持可选 host 白名单；执行期做二次防御。

## 2. 范围（In / Out）

- **In**：`validate_http_url(url) -> None`（抛错即拒绝）工具；接入 spec-16 PUT 注册校验（http 节点且 `mock_enabled=false` → 校验，失败 422）；http 节点执行器请求前二次校验（defense in depth）；`settings.WORKFLOW_HTTP_ALLOWED_HOSTS`（可选白名单，env）。
- **Out**：`mock_enabled=true` 的请求（零网络，S9，不校验）；DNS rebinding 深度防护（记录为后续项）。

## 3. 接口契约

```python
# app/workflow/security.py（或 http 节点模块内）
def validate_http_url(url: str) -> None:
    """校验 http(s) scheme + 解析 host；命中私网/环回/链路本地/元数据或不在白名单则抛 WorkflowValidationError。"""
```

**拦截规则**：

- scheme 仅 `http`/`https`（拒 `file`/`gopher`/`ftp` 等）。
- host 解析后 IP 落入以下段则拒绝：`127.0.0.0/8`、`10/8`、`172.16/12`、`192.168/16`、`169.254/16`（含云元数据 `169.254.169.254`）、`::1`、`fc00::/7`、`fe80::/10`。
- 若 `settings.WORKFLOW_HTTP_ALLOWED_HOSTS` 非空 → host 必须在白名单内（否则拒绝）。
- 校验在**注册期**（spec-16，构建期失败优于运行期 S6）+ **执行期**（请求前）双点执行。

## 4. TDD · RED（测试先行）

新增 `tests/unit/workflow/test_http_ssrf.py`：

- [ ] `http://127.0.0.1/x`、`http://localhost/x`、`http://10.0.0.1`、`http://192.168.1.1`、`http://169.254.169.254/latest/meta-data`、`http://[::1]` → **全部拒绝**。
- [ ] `https://example.com/api`（公网）→ 通过。
- [ ] `file:///etc/passwd`、`gopher://...` → 拒绝（scheme 白名单）。
- [ ] 白名单非空时：名单内 host 通过、名单外公网 host 拒绝。
- [ ] `mock_enabled=true` 的 http 节点 → **不校验**（零网络，S9），PUT 通过。
- [ ] PUT 注册含非法 url（mock 关闭）→ **422**（接入 spec-16）。
- [ ] 执行期：即使绕过注册（如直接构造），执行器请求前二次校验拦截。

## 5. GREEN（最小实现）

- 实现 `validate_http_url`（`ipaddress` + `urllib.parse` + `socket.getaddrinfo` 解析）；接入 spec-16 校验链与 http 节点执行器。

## 6. REFACTOR

- 私网段 / scheme 常量集中；校验错误统一映射到 `WorkflowValidationError` → 422；与 spec-16 `_validate_definition_payload` 协同（http 节点遍历校验）。

## 7. 验收门限（DoD Gate）

- [ ] RED 用例全绿（私网 / 环回 / 元数据 / scheme / 白名单 / mock 豁免 / 双点校验）；`make test` 通过，覆盖率不降。
- [ ] `make lint` / `ruff format --check` / `make typecheck` 全绿。
- [ ] 白名单来自 env/settings（H6，无硬编码）；`mock_enabled=true` 不误伤（S9）。
- [ ] 注册期 + 执行期双点校验均生效（defense in depth）。
- [ ] 提交 `feat(workflow): add SSRF guard for http node urls`。

## 8. 交付物清单

- 新：`app/workflow/security.py`（`validate_http_url`）
- 改：`app/workflow/api.py`（spec-16 校验链接入）、http 节点执行器（请求前校验）、`app/core/config.py`（`WORKFLOW_HTTP_ALLOWED_HOSTS`）
- 新：`tests/unit/workflow/test_http_ssrf.py`
