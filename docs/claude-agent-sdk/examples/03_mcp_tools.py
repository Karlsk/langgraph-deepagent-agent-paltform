"""03 · MCP 集成：进程内 @tool + create_sdk_mcp_server，与外部 stdio server 配置.

依赖包：claude-agent-sdk>=0.2.135；外部 stdio server 另需 Node.js 与
`npx -y @modelcontextprotocol/server-filesystem`（仅外部形态演示所需）。
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：ClaudeSDKClient（演示交互式会话面；进程内自定义工具在 query() 中同样可用，
不构成选型依据；见文档 06 章第 2.3 节与 01 章第 2 节）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)


@tool("add", "Add two numbers and return the sum", {"a": float, "b": float})
async def add(args: dict) -> dict:
    """进程内自定义工具 handler：单参 dict 入参，返回 content 列表的 dict."""
    total = args["a"] + args["b"]
    return {"content": [{"type": "text", "text": str(total)}], "is_error": False}


def build_options() -> ClaudeAgentOptions:
    """构建选项：进程内 SDK MCP server + 外部 stdio server，并在 allowed_tools 放行 mcp__ 前缀."""
    calc_server = create_sdk_mcp_server(name="calculator", version="1.0.0", tools=[add])
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        mcp_servers={
            "calc": calc_server,  # 进程内：无子进程、无 IPC 开销、永不延迟首轮
            # 外部 stdio server（需本机可用 npx；仅演示配置形态）
            "fs": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
        },
        # 放行规则用 mcp__<server>__<tool>；__* 通配整个 server（文档 06 章第 5 节）
        allowed_tools=["mcp__calc__add", "mcp__fs__*"],
    )


async def main() -> None:
    """用 ClaudeSDKClient 调用进程内工具并打印结果与用量."""
    async with ClaudeSDKClient(options=build_options()) as client:
        await client.query("Use the calc add tool to compute 12.5 + 7.25, then give the number only.")
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        print(block.text)  # noqa: T201
            elif isinstance(message, ResultMessage):
                print(f"subtype={message.subtype} cost_usd={message.total_cost_usd}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
