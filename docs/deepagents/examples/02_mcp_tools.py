"""02 · MCP 工具接入：langchain-mcp-adapters MultiServerMCPClient.

依赖包：deepagents>=0.7.5、langchain-anthropic、langchain-mcp-adapters
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
（http server 的 Authorization 头为占位符 <your-mcp-token>，严禁提交真实密钥）
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from deepagents import create_deep_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

MCP_SERVERS = {
    # streamable http transport（远程/托管 MCP server）
    "weather": {
        "transport": "http",
        "url": "http://localhost:8000/mcp",
        "headers": {"Authorization": "Bearer <your-mcp-token>"},
    },
    # stdio transport（本地子进程 MCP server）
    "math": {
        "transport": "stdio",
        "command": "python",
        "args": ["/abs/path/to/math_server.py"],
    },
}


async def main() -> None:
    """Stateless 模式拉取 MCP 工具并注入 deep agent（每次工具调用新建 session）."""
    # ≥0.1.0 已移除上下文管理器用法（async 上下文管理器会抛
    # NotImplementedError）；一律用 stateless 构造写法。
    client = MultiServerMCPClient(MCP_SERVERS)
    tools = await client.get_tools()

    # 需要持久 session 时的替代写法（工具在整个 with 块内复用同一会话）：
    # async with client.session("math") as session:  # noqa: ERA001
    #     tools = await load_mcp_tools(session)  # from langchain_mcp_adapters.tools import load_mcp_tools  # noqa: ERA001, E501

    # MCP 工具与内置文件工具 / task 工具叠加合并，不会顶掉内置工具
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        tools=tools,
        system_prompt="Answer with the help of the available tools.",
    )

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "北京今天天气如何？顺便算一下 123*456"}]})
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
