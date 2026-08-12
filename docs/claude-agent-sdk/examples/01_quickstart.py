"""01 · claude-agent-sdk 最小闭环：query() 单次模式.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式；双 API 选型见文档 01 章第 2 节）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query


async def main() -> None:
    """最小闭环：query() 一问一答，迭代到 ResultMessage 自然结束."""
    # 生产勿省略 model：默认模型随 CLI 版本漂移（见文档 02 章第 1 节）
    options = ClaudeAgentOptions(model="claude-sonnet-4-5")
    async for message in query(prompt="用一句话解释什么是 prompt cache。", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)  # noqa: T201
        elif isinstance(message, ResultMessage):
            # 末条恒为 ResultMessage；禁止拿到它就 break（迭代纪律见文档 07 章第 4 节）
            print(f"subtype={message.subtype} turns={message.num_turns} cost_usd={message.total_cost_usd}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
