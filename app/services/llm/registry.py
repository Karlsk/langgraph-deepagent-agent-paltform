"""LLM model registry with pre-initialized instances."""

from typing import (
    Any,
    Dict,
    List,
)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

_API_KEY = SecretStr(settings.OPENAI_API_KEY)


def _build_llms(model_names: List[str]) -> List[Dict[str, Any]]:
    """Build pre-initialized registry entries for the given model names.

    Args:
        model_names: Ordered model ids (the exact names the provider accepts).

    Returns:
        List of ``{"name": ..., "llm": ...}`` entries in the given order.
    """
    return [
        {
            "name": name,
            "llm": ChatOpenAI(
                model=name,
                api_key=_API_KEY,
                max_completion_tokens=settings.MAX_TOKENS,
            ),
        }
        for name in model_names
    ]


class LLMRegistry:
    """Registry of available LLM models with pre-initialized instances.

    The model list is driven by ``settings.LLM_MODELS`` (env ``LLM_MODELS``,
    falling back to ``DEFAULT_LLM_MODEL``), so the fallback chain always
    matches the configured provider endpoint. Each entry's ``name`` is the
    exact model id sent to the provider — no provider-specific parameters
    are hardcoded, keeping the registry portable across OpenAI-compatible
    gateways.
    """

    LLMS: List[Dict[str, Any]] = _build_llms(settings.LLM_MODELS)

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """Get an LLM by name with optional argument overrides.

        When kwargs are provided a fresh ChatOpenAI instance is returned with
        those overrides applied, leaving the shared registry entry untouched.

        Args:
            model_name: Name of the model to retrieve.
            **kwargs: Optional arguments to override default model configuration.

        Returns:
            BaseChatModel instance.

        Raises:
            ValueError: If model_name is not found in LLMS.
        """
        model_entry = next((e for e in cls.LLMS if e["name"] == model_name), None)

        if not model_entry:
            available = ", ".join(e["name"] for e in cls.LLMS)
            raise ValueError(f"model '{model_name}' not found in registry. available models: {available}")

        if kwargs:
            logger.debug("creating_llm_with_custom_args", model_name=model_name, custom_args=list(kwargs.keys()))
            return ChatOpenAI(model=model_name, api_key=_API_KEY, **kwargs)

        logger.debug("using_default_llm_instance", model_name=model_name)
        return model_entry["llm"]

    @classmethod
    def get_all_names(cls) -> List[str]:
        """Return all registered model names in order.

        Returns:
            List of model name strings.
        """
        return [e["name"] for e in cls.LLMS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """Return the model entry at a specific index, wrapping to 0 if out of range.

        Args:
            index: Index into LLMS.

        Returns:
            Model entry dict.
        """
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0]


logger.info("llm_registry_initialized", models=settings.LLM_MODELS)
