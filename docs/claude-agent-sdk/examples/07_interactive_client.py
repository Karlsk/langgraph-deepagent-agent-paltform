"""07 · ClaudeSDKClient 多轮交互：多轮对话 + interrupt + 进程内工具/hooks 演示面.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：ClaudeSDKClient（async context manager）——多轮 query/receive_response、
interrupt()（streaming mode 专属）、hooks 回调（0.2.135 中 query() 亦可用，文档 06 章 §2.3）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
)


async def audit_hook(input_data: dict[str, Any], tool_use_id: str | None, context: Any) -> dict[str, Any]:
    """PreToolUse 审计 hook：仅观测不决策（返回空 dict = 不干预权限流）."""
    print(f"[hook] PreToolUse tool={input_data.get('tool_name')}")  # noqa: T201
    return {}


def build_options() -> ClaudeAgentOptions:
    """构建选项：挂载 PreToolUse hook（HookMatcher 控制匹配面与超时）."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[audit_hook], timeout=10)]},
    )


async def drain(client: ClaudeSDKClient) -> None:
    """消费一轮响应直到 ResultMessage（receive_response 自然结束，无需 break）."""
    async for message in client.receive_response():
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(f"[assistant] {block.text}")  # noqa: T201
        elif isinstance(message, ResultMessage):
            print(f"[result] subtype={message.subtype}")  # noqa: T201


async def main() -> None:
    """多轮会话演示：两轮问答 + 一次 interrupt 中断."""
    async with ClaudeSDKClient(options=build_options()) as client:  # 进入即 connect()
        await client.query("记住暗号 atlas。只回复'已记住'。")
        await drain(client)
        await client.query("刚才的暗号是什么？只回复暗号。")  # 会话常驻，上下文自然延续
        await drain(client)
        # interrupt 演示：发起长任务后立即中断（仅 streaming mode 可用）
        await client.query("写一篇五千字的长文。")
        await client.interrupt()
        await drain(client)  # 把被中断一轮的剩余消息自然消费完


if __name__ == "__main__":
    asyncio.run(main())
