"""LLM node: multi-provider chat invocation with env-only secrets (spec-04).

CONTRACT §4.7: LLMConfig + LLMNode frozen signatures. Behavior semantics:
S5 (convert_state_to_dict in / map_output_to_state out), S8 (tenacity retry,
EXP-L3 status_code predicate), S14 (extra="forbid"), S15 (log summaries only,
H6). AD-03 tenacity retry, AD-04 top-level imports, AD-12 default env names.

Dependency red-line: never import registry / graph_builder (nodes know nothing
about graphs); never import app.core.* (engine self-contained).
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal, override

import structlog
import tenacity
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field
from tenacity import RetryError, Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.workflow.models import ConfigError, ExecutionLog, LLMNodeError, NodeType, OperatorLog
from app.workflow.nodes.base import BaseNode
from app.workflow.utils import convert_state_to_dict, map_output_to_state

logger = structlog.get_logger(__name__)

# AD-12: default env names per llm_type (api_key only; base_url default is openai-only)
_DEFAULT_API_KEY_ENV: dict[str, str] = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
_DEFAULT_BASE_URL_ENV: dict[str, str] = {"openai": "OPENAI_BASE_URL"}

# EXP-L1 trap 1: non-streaming anthropic calls with huge default max_tokens trip the
# SDK "Streaming is required" guard; fall back to a conservative value when unset.
_ANTHROPIC_DEFAULT_MAX_TOKENS = 4096


class LLMConfig(BaseModel):
    """LLM node configuration (CONTRACT §4.7, S14 extra='forbid').

    No plaintext api_key field exists (H6/ADR-008): secrets are resolved from
    environment variables via api_key_env / base_url_env or llm_type defaults.
    """

    model_config = ConfigDict(extra="forbid")

    llm_type: Literal["openai", "anthropic"] = "openai"
    model_name: str = "gpt-4o-mini"
    base_url: str | None = None
    api_key_env: str | None = None  # 显式 env 名；未设置按 llm_type 取默认（AD-12）
    base_url_env: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = None
    top_p: float | None = None
    system_prompt: str = ""
    extra_params: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=3, ge=0)
    retry_base_delay: float = Field(default=1.0, gt=0)


def _is_retryable_llm_error(exc: BaseException) -> bool:
    """EXP-L3 finalized predicate: retry only on status_code 429 or >= 500 (S8)."""
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


class LLMNode(BaseNode):
    """LLM chat node: env-resolved secrets, lazy client, tenacity 429/5xx retry."""

    def __init__(
        self,
        name: str,
        llm_config: LLMConfig | dict[str, Any],
        messages: list[BaseMessage] | None = None,
        operator_log: OperatorLog | None = None,
    ) -> None:
        """Store the validated config; the provider client is created lazily (K10)."""
        config = llm_config if isinstance(llm_config, LLMConfig) else LLMConfig(**llm_config)
        super().__init__(name, NodeType.LLM, config.model_dump(), operator_log)
        self._llm_config = config
        self.messages = messages
        self._llm_instance: Any = None

    @override
    def validate_config(self) -> bool:
        """model_name must be non-empty; otherwise raise ValueError."""
        if not self._llm_config.model_name:
            msg = f"LLMNode '{self.name}': model_name must be a non-empty string"
            raise ValueError(msg)
        return True

    def _resolve_api_key(self) -> str:
        """Resolve the API key from the environment; ConfigError names the env var only (H6)."""
        env_name = self._llm_config.api_key_env or _DEFAULT_API_KEY_ENV[self._llm_config.llm_type]
        api_key = os.environ.get(env_name)
        if not api_key:
            msg = f"LLMNode '{self.name}': missing required env var '{env_name}' for llm_type '{self._llm_config.llm_type}'"
            raise ConfigError(msg)
        return api_key

    def _resolve_base_url(self) -> str | None:
        """Explicit base_url wins, then base_url_env, then the llm_type default env (AD-12)."""
        if self._llm_config.base_url:
            return self._llm_config.base_url
        env_name = self._llm_config.base_url_env or _DEFAULT_BASE_URL_ENV.get(self._llm_config.llm_type)
        if env_name:
            return os.environ.get(env_name)
        return None

    def _get_llm_instance(self) -> Any:
        """Lazily build and memoize the provider chat client (K10)."""
        if self._llm_instance is None:
            cfg = self._llm_config
            common: dict[str, Any] = {
                "model": cfg.model_name,
                "api_key": self._resolve_api_key(),
                "temperature": cfg.temperature,
                # EXP-L3: disable SDK built-in retries; tenacity is the single retry owner (AD-03)
                "max_retries": 0,
                "model_kwargs": cfg.extra_params,
            }
            if cfg.llm_type == "openai":
                self._llm_instance = ChatOpenAI(base_url=self._resolve_base_url(), **common)
            else:
                # EXP-L1 trap 1: always pass an explicit max_tokens to anthropic.
                # 'max_tokens' is the canonical alias (populate_by_name) of max_tokens_to_sample (EXP-L1).
                self._llm_instance = ChatAnthropic(
                    max_tokens=cfg.max_tokens or _ANTHROPIC_DEFAULT_MAX_TOKENS,  # pyright: ignore[reportCallIssue] — alias kwarg per EXP-L1
                    **common,
                )
        return self._llm_instance

    def _invoke_with_retry(self, llm: Any, messages: list[Any]) -> Any:
        """Invoke with tenacity exponential backoff on 429/5xx (AD-03, S8)."""
        cfg = self._llm_config
        retrying = Retrying(
            stop=stop_after_attempt(cfg.max_retries + 1),
            wait=wait_exponential(multiplier=cfg.retry_base_delay),
            retry=retry_if_exception(_is_retryable_llm_error),
            reraise=True,
            # Dynamic nap reference so tests can monkeypatch tenacity.nap.sleep (AD-03)
            sleep=tenacity.nap.sleep,
        )
        try:
            for attempt in retrying:
                with attempt:
                    result: Any = llm.invoke(messages)
            return result
        except RetryError:
            raise  # unreachable with reraise=True, but satisfies the type checker
        except Exception as exc:
            logger.exception("llm_node_invoke_failed", node=self.name, attempts=cfg.max_retries + 1)
            msg = f"LLMNode '{self.name}': LLM invocation failed after {cfg.max_retries + 1} attempts: {exc}"
            raise LLMNodeError(msg) from exc

    @override
    def build_runnable(self) -> Runnable:
        """唯一执行单元（K4）：七步进出管线（S5/R3）."""

        def func(state: dict[str, Any]) -> dict[str, Any]:
            started = time.perf_counter()
            # 1. R3 入口：state → dict（不 mutate 输入）
            state_dict = convert_state_to_dict(state)
            message_count = 0
            output: dict[str, Any] = {}
            try:
                # 2. 取 messages：state 优先，其次实例 messages；皆空 → ValueError
                messages = state_dict.get("messages") or self.messages
                if not messages:
                    msg = f"LLMNode '{self.name}': no messages provided (state and instance messages are both empty)"
                    raise ValueError(msg)
                message_count = len(messages)
                # 3. system_prompt 非空 → 前置 SystemMessage
                if self._llm_config.system_prompt:
                    messages = [SystemMessage(content=self._llm_config.system_prompt), *messages]
                # 4. 调用（tenacity 429/5xx 退避，S8）
                response = self._invoke_with_retry(self._get_llm_instance(), messages)
                # 5. 成功输出 {"response": <content>, "model": model_name}
                output = {"response": response.content, "model": self._llm_config.model_name}
                # 6. log_execution：input_data 仅摘要（消息条数 + 模型名），H6/S15
                self._log(message_count, output, (time.perf_counter() - started) * 1000, error=None)
            except Exception as exc:
                # 异常分支：记录后重抛（H2/R6，禁止死 except）
                self._log(message_count, output, (time.perf_counter() - started) * 1000, error=str(exc))
                logger.exception("llm_node_execution_failed", node=self.name, error=str(exc))
                raise
            # 7. R3 出口：双写 + history 增量
            return map_output_to_state(self.name, output, state_dict)

        return self.wrap_runnable(func)

    def _log(self, message_count: int, output: dict[str, Any], execution_time_ms: float, error: str | None) -> None:
        """Write an ExecutionLog whose input_data is a summary only (S15/H6)."""
        self.log_execution(
            ExecutionLog(
                node_name=self.name,
                node_type=str(self.node_type.value if isinstance(self.node_type, NodeType) else self.node_type),
                input_data={"message_count": message_count, "model": self._llm_config.model_name},
                output_data=output,
                execution_time_ms=execution_time_ms,
                error=error,
            )
        )
