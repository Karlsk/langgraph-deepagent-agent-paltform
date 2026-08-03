"""HTTP node: template-rendered requests with explicit retry/mock switches (spec-05).

CONTRACT §4.8: HTTPNodeConfig + HTTPNode frozen signatures. Behavior: S5
(convert_state_to_dict in / map_output_to_state out), S8 (tenacity retry on
retry_on_status, max_retries default 0), S9 (mock explicit switch: miss must
raise HTTPNodeError, never silently fall back to a real call), S14
(extra="forbid"), S15 (log summaries only, H6). AD-02 structlog summaries,
AD-03 tenacity retry, AD-05 httpx main dependency.

Dependency red-line: never import registry / graph_builder (nodes know nothing
about graphs); never import app.core.* (engine self-contained).
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Literal, override

import httpx
import structlog
import tenacity
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field
from tenacity import RetryError, Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.workflow.models import ExecutionLog, HTTPNodeError, NodeType, OperatorLog
from app.workflow.nodes.base import BaseNode
from app.workflow.nodes.factory import register_node_type
from app.workflow.utils import convert_state_to_dict, map_output_to_state

logger = structlog.get_logger(__name__)

# {key} placeholders: braces holding JSON syntax (quotes/colons/whitespace) are
# not placeholders and survive rendering untouched.
_PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_]+\])?)\}")

# User-finalized (spec-05 supplement): a mock hit simulates a successful response,
# so its output carries status_code=200 to stay structurally identical to the real branch.
_MOCK_HIT_STATUS_CODE = 200


class HTTPNodeConfig(BaseModel):
    """HTTP node configuration (CONTRACT §4.8, S14 extra='forbid').

    Retry and mock are both explicit opt-in switches (S8/S9, H2): max_retries
    defaults to 0 (no retry) and mock_enabled defaults to False.
    """

    model_config = ConfigDict(extra="forbid")

    url: str
    method: Literal["GET", "POST", "PUT", "DELETE"] = "POST"
    headers: dict[str, str] | None = None
    body_template: str | None = None
    response_path: str | None = None  # 点路径，如 "data.result"
    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=0, ge=0)  # 默认不重试；显式开启（H2）
    retry_base_delay: float = Field(default=1.0, gt=0)
    retry_on_status: list[int] = Field(default_factory=lambda: [429, 500, 502, 503, 504])
    mock_enabled: bool = False  # 默认关闭；显式启用才生效（H2/H6）
    mock_responses: dict[str, str] | None = None


def _flatten_context(state_dict: dict[str, Any]) -> dict[str, Any]:
    """Top-level keys plus one-level nested dicts flattened to ``parent[child]`` (TC1)."""
    flat: dict[str, Any] = {}
    for key, value in state_dict.items():
        flat[key] = value
        if isinstance(value, dict):
            for child, child_value in value.items():
                flat[f"{key}[{child}]"] = child_value
    return flat


class HTTPNode(BaseNode):
    """HTTP request node: rendered templates, retry_on_status backoff, explicit mock."""

    def __init__(
        self,
        name: str,
        config: dict[str, Any] | HTTPNodeConfig,
        operator_log: OperatorLog | None = None,
    ) -> None:
        """Store the validated config; requests are sent synchronously per call (K10)."""
        node_config = config if isinstance(config, HTTPNodeConfig) else HTTPNodeConfig(**config)
        super().__init__(name, NodeType.HTTP, node_config.model_dump(), operator_log)
        self._node_config = node_config

    @override
    def validate_config(self) -> bool:
        """Url must be non-empty; mock_enabled requires non-empty mock_responses."""
        if not self._node_config.url:
            msg = f"HTTPNode '{self.name}': url must be a non-empty string"
            raise ValueError(msg)
        if self._node_config.mock_enabled and not self._node_config.mock_responses:
            msg = f"HTTPNode '{self.name}': mock_enabled=True requires non-empty mock_responses"
            raise ValueError(msg)
        return True

    def render_template(self, template: str, context: dict[str, Any]) -> str:
        """Replace {key} placeholders; one-level nested dicts flatten to parent[child]."""
        flat = _flatten_context(context)

        def _sub(match: re.Match[str]) -> str:
            key = match.group(1)
            if key in flat:
                return str(flat[key])
            logger.debug("http_node_placeholder_unresolved", node=self.name, placeholder=key)
            return match.group(0)

        return _PLACEHOLDER_PATTERN.sub(_sub, template)

    def _extract(self, data: Any, path: str | None) -> Any:
        """Walk a dot path layer by layer; missing segment -> None; None path -> whole data."""
        if path is None:
            return data
        value = data
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _send_once(self, method: str, url: str, headers: dict[str, str] | None, body: Any) -> httpx.Response:
        """One synchronous HTTP request (K10); retry ownership lives in tenacity (AD-03)."""
        return httpx.request(method, url, headers=headers, json=body, timeout=self._node_config.timeout)

    def _is_retryable(self, exc: BaseException) -> bool:
        """S8 predicate: only HTTPStatusError whose status hits retry_on_status retries."""
        return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in self._node_config.retry_on_status

    def _send_with_retry(self, method: str, url: str, headers: dict[str, str] | None, body: Any) -> httpx.Response:
        """Send with tenacity exponential backoff on retry_on_status (AD-03, S8)."""
        cfg = self._node_config
        retrying = Retrying(
            stop=stop_after_attempt(cfg.max_retries + 1),
            wait=wait_exponential(multiplier=cfg.retry_base_delay),
            retry=retry_if_exception(self._is_retryable),
            reraise=True,
            # Dynamic nap reference so tests can monkeypatch tenacity.nap.sleep (AD-03)
            sleep=tenacity.nap.sleep,
        )
        result: httpx.Response | None = None
        try:
            for attempt in retrying:
                with attempt:
                    response = self._send_once(method, url, headers, body)
                    response.raise_for_status()
                    result = response
            assert result is not None  # noqa: S101 — tenacity guarantees assignment on success
            return result
        except RetryError:
            raise  # unreachable with reraise=True, but satisfies the type checker
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.exception("http_node_request_failed", node=self.name, method=method, url=url, status_code=status)
            msg = (
                f"HTTPNode '{self.name}': {method} {url} failed with status {status} "
                f"after {cfg.max_retries + 1} attempts"
            )
            raise HTTPNodeError(msg) from exc
        except Exception as exc:
            logger.exception("http_node_request_failed", node=self.name, method=method, url=url)
            msg = f"HTTPNode '{self.name}': {method} {url} failed after {cfg.max_retries + 1} attempts: {exc}"
            raise HTTPNodeError(msg) from exc

    def _resolve_mock(self, method: str, rendered_url: str) -> Any:
        """S9: look up "{METHOD} {url}" then "{url}"; a miss raises, never falls back."""
        responses = self._node_config.mock_responses or {}
        key = f"{method} {rendered_url}"
        raw = responses.get(key, responses.get(rendered_url))
        if raw is None:
            msg = f"HTTPNode '{self.name}': mock enabled but no mock_responses entry for '{key}' or '{rendered_url}'"
            raise HTTPNodeError(msg)
        return json.loads(raw)

    @override
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）：七步进出管线（S5/R3）."""

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            cfg = self._node_config
            # 1. R3 入口：state → dict（不 mutate 输入）；渲染上下文由 render_template 扁平化
            state_dict = convert_state_to_dict(state)
            rendered_url = ""
            output: dict[str, Any] = {}
            try:
                rendered_url = self.render_template(cfg.url, state_dict)
                # 2. 渲染 headers/body；body 非空必须为合法 JSON（显式 ValueError 带节点名）
                headers = (
                    {k: self.render_template(v, state_dict) for k, v in cfg.headers.items()} if cfg.headers else None
                )
                body: Any = None
                if cfg.body_template:
                    rendered_body = self.render_template(cfg.body_template, state_dict)
                    try:
                        body = json.loads(rendered_body)
                    except json.JSONDecodeError as exc:
                        msg = f"HTTPNode '{self.name}': rendered body_template is not valid JSON: {exc}"
                        raise ValueError(msg) from exc
                # 3. mock 分支（仅 mock_enabled 显式启用）：命中即返回，未命中报错禁回退（S9）
                if cfg.mock_enabled:
                    data = self._resolve_mock(cfg.method, rendered_url)
                    status_code = _MOCK_HIT_STATUS_CODE
                else:
                    # 4. 真实分支：tenacity 按 retry_on_status 退避 + raise_for_status（S8）
                    response = self._send_with_retry(cfg.method, rendered_url, headers, body)
                    data = response.json()
                    status_code = response.status_code
                # 5. 提取 + 成功输出 {"status_code", "url", "response"}
                output = {
                    "status_code": status_code,
                    "url": rendered_url,
                    "response": self._extract(data, cfg.response_path),
                }
                # 6. log_execution：input_data 仅摘要（method + rendered_url），H6/S15
                self._log(cfg.method, rendered_url, output, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                # 异常分支：记录后重抛（H2/R6，禁止死 except）
                self._log(cfg.method, rendered_url, output, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("http_node_execution_failed", node=self.name, error=str(exc))
                raise
            # 7. R3 出口：双写 + history 增量
            return map_output_to_state(self.name, output, state_dict)

        return self.wrap_runnable(func)

    def _log(
        self,
        method: str,
        rendered_url: str,
        output: dict[str, Any],
        execution_time_ms: float,
        error: str | None,
    ) -> None:
        """Write an ExecutionLog whose input_data is a summary only (S15/H6)."""
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type.value if isinstance(self.node_type, NodeType) else self.node_type),
                input_data={"method": method, "url": rendered_url},
                output_data=output,
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )


# 模块底部自注册（CONTRACT §4.8；factory 内置分支作双保险）
register_node_type("http", HTTPNode)
