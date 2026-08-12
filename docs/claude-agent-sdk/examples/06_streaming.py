"""06 · token 级流式：include_partial_messages + StreamEvent text_delta + 成本打印.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式）+ include_partial_messages=True 产出 StreamEvent。
要点：StreamEvent 是原始 API 流事件的薄壳（非累积文本，需宿主自行累积）；
StreamEvent 仅来自主会话，子代理 delta 不转发（文档 07 章第 3 节）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, StreamEvent, query


def extract_text_delta(message: StreamEvent) -> str:
    """从 StreamEvent 解包 content_block_delta 的 text_delta（无增量时返回空串）."""
    event = message.event  # 原始 Anthropic API 流事件 dict
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta", {})
    return delta.get("text", "") if delta.get("type") == "text_delta" else ""


async def main() -> None:
    """消费 token 级增量并在 ResultMessage 打印成本."""
    options = ClaudeAgentOptions(model="claude-sonnet-4-5", include_partial_messages=True)
    # ⚠️ 迭代纪律：禁止在拿到 ResultMessage 后提前 break——会破坏 asyncio 清理，
    # 必须让迭代自然走完（文档 07 章第 4 节）。
    async for message in query(prompt="用三句话介绍向量数据库。", options=options):
        if isinstance(message, StreamEvent):
            delta_text = extract_text_delta(message)
            if delta_text:
                print(delta_text, end="", flush=True)  # noqa: T201
        elif isinstance(message, ResultMessage):
            print()  # noqa: T201
            # 成本治理数据源：total_cost_usd / usage / model_usage（文档 02 章第 5 节）
            print(f"cost_usd={message.total_cost_usd} usage={message.usage}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
