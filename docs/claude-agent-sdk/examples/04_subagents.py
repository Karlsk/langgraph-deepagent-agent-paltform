"""04 · 声明式子代理：程序化 AgentDefinition（camelCase）+ Agent 工具放行 + 归因打印.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式）+ ClaudeAgentOptions.agents 程序化子代理。
要点：AgentDefinition 字段保留 camelCase wire 格式（maxTurns 等，写 snake_case 会 TypeError）；
allowed_tools 含 "Agent" 让委派免权限提示（v2.1.63 前该工具名为 Task，见文档 04 章第 3 节）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import AgentDefinition, AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query


def build_options() -> ClaudeAgentOptions:
    """构建选项：程序化定义 code-reviewer 子代理并放行 Agent 工具."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        agents={
            "code-reviewer": AgentDefinition(
                description="Reviews short code snippets for bugs and style issues",  # 必填：何时委派
                prompt="You are a strict code reviewer. Reply in zh-CN, within 100 words.",  # 必填：子代理 system prompt
                model="inherit",  # 继承父代理当前模型
                maxTurns=5,  # 陷阱专列：camelCase wire 格式，非 max_turns
            ),
        },
        allowed_tools=["Agent"],  # Agent 工具调用免权限提示（跨 CLI 版本须与 "Task" 双名匹配）
    )


def print_message(message: object) -> None:
    """按 parent_tool_use_id 归因打印：子代理消息与主会话消息分开标注."""
    if isinstance(message, AssistantMessage):
        origin = "sub-agent" if message.parent_tool_use_id else "main"
        for block in message.content:
            if isinstance(block, TextBlock):
                print(f"[{origin}] {block.text}")  # noqa: T201
    elif isinstance(message, ResultMessage):
        print(f"[result] subtype={message.subtype} cost_usd={message.total_cost_usd}")  # noqa: T201


async def main() -> None:
    """委派 code-reviewer 审查一段代码，观察子代理消息归因."""
    prompt = "Delegate to code-reviewer: review this snippet `def f(x): return x = 1` and summarize its finding."
    async for message in query(prompt=prompt, options=build_options()):
        print_message(message)


if __name__ == "__main__":
    asyncio.run(main())
