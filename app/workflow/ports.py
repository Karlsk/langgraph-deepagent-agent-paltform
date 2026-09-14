"""Injection ports for the workflow engine (CONTRACT §3 red line 4, §4.7, §4.15, S20/S23).

The engine is self-contained: it must never import ``app.core.*`` / ``app.api.*``
/ ``app.services.*``. When capability has to reach a node without the node
knowing where it comes from, it travels as an **opaque callable** down the
constructor chain (``WorkflowRegistry`` -> ``GraphBuilder`` -> ``create_node``
-> node). Two such ports exist, differing in who supplies the implementation:

- ``ChatModelFactory`` — the **host** supplies it (resolving LLM credentials from
  the platform's provider registry); assembly happens in the composition root
  (``app/main.py``).
- ``WorkflowRunner`` — the **engine itself** supplies it (``WorkflowRegistry``
  injects its own bound ``_run_nested``), so the composition root wires nothing.

This module holds only the type aliases, so engine modules can be annotated
without acquiring any dependency on the host — and, for ``WorkflowRunner``,
without ``nodes/*`` importing ``registry`` (red line 2).
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

# (workflow_id, input_data, caller_label) -> inner run result envelope.
#
# ``caller_label`` is the name of the node making the call; the callee uses it to
# prefix the inner run's execution logs before merging them outward (S24).
#
# The envelope has exactly three frozen keys: ``output`` (dict), ``run_id`` (str)
# and ``inner_log_count`` (int).
# ``output`` is the inner RunResult.output — the whole inner final state, which
# is precisely why the node writes it with dual_write=False (CONTRACT §4.15):
# flattening it would let inner channels silently overwrite same-named outer
# ones, ``input`` above all. ``run_id`` and ``inner_log_count`` exist only on the
# inner RunResult, so the node cannot derive them itself; they ride back purely
# to build the S24 log summary and must never enter the outer state.
WorkflowRunner = Callable[[str, dict[str, Any], str], dict[str, Any]]
