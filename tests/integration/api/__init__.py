"""Integration tests for the agent management REST API (provider / model / asset endpoints).

These tests reuse the full-stack ``db_engine`` / ``client`` / ``user_headers``
fixtures declared by the sibling conftest in this directory, exercising the
real ``app.api.v1.api.api_router`` wired against an in-memory SQLite engine
with zero real network and zero real LLM calls.
"""
