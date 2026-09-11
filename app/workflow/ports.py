"""Injection ports for the workflow engine (CONTRACT §3 red line 4, §4.7, S20).

The engine is self-contained: it must never import ``app.core.*`` / ``app.api.*``
/ ``app.services.*``. When the host needs to supply capability the engine cannot
provide itself — resolving LLM credentials from the platform's provider registry
— it injects an **opaque callable** through the constructor chain
(``WorkflowRegistry`` -> ``GraphBuilder`` -> ``create_node`` -> ``LLMNode``).

This module holds only the type aliases, so engine modules can be annotated
without acquiring any dependency on the host. Assembly happens in the
composition root (``app/main.py``).
"""

from __future__ import annotations

from typing import Any, Callable

# (provider_ref, overrides) -> chat model client.
#
# ``provider_ref`` is the ``"<provider_name>/<model_name>"`` reference stored in
# ``LLMConfig.provider_ref``. ``overrides`` carries node-level parameters
# (``temperature``, and ``max_tokens`` when set) that take precedence over the
# provider-side ``ModelConfig.extra_params``.
#
# The returned object is only required to support ``invoke(messages)``; the
# engine never inspects it. Credentials never cross this boundary as data — the
# callable closes over whatever store it needs, which keeps ``api_key`` out of
# node config, YAML and logs (H6).
ChatModelFactory = Callable[[str, dict[str, Any]], Any]
