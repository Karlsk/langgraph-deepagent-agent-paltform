"""02 · 模型选择、重试与回退：model + fallback_model + env 重试参数注入.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式）。
重试要点：ClaudeAgentOptions 无重试字段，重试/超时全在 CLI 侧，只能经 env 注入
（CLAUDE_CODE_MAX_RETRIES / API_TIMEOUT_MS），见文档 02 章第 2 节。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query


def build_options() -> ClaudeAgentOptions:
    """构建带重试/回退语义的选项：model + fallback_model + env 注入重试参数."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5",  # 主模型（别名 "sonnet" 亦可）
        fallback_model="haiku",  # 主模型失败时的一级回退（单值、非链式）
        env={
            # 重试/超时只能经 env 注入给 CLI 子进程（文档 02 章第 2 节）
            "API_TIMEOUT_MS": "120000",  # 单请求超时 2 分钟
            "CLAUDE_CODE_MAX_RETRIES": "2",  # 最多重试 2 次（上限 15）
        },
        max_turns=5,  # 轮数闸，防工具循环失控
    )


async def main() -> None:
    """跑一次带重试/回退配置的最小请求并打印用量."""
    options = build_options()
    async for message in query(prompt="用一句话解释重试退避策略。", options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)  # noqa: T201
        elif isinstance(message, ResultMessage):
            # 最坏墙钟耗时 ≈ API_TIMEOUT_MS × (MAX_RETRIES + 1) + backoff，宿主超时须留余量
            print(f"subtype={message.subtype} cost_usd={message.total_cost_usd} usage={message.usage}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
