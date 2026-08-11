"""05 · 流式输出：astream_events(version="v3") 类型化投影并发消费.

依赖包：deepagents>=0.7.5、langchain-anthropic
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。

传统 API 对照（LangGraph 原生，子代理需自行解析 chunk['ns']）：
  async for chunk in agent.astream(
      {"messages": [...]},
      stream_mode=["updates", "messages"],
      subgraphs=True,
  ):
      ...  # 新代码一律用 v3 投影，传统 astream 仅存量维护
"""

import asyncio

from deepagents import create_deep_agent


def build_agent():
    """构建最小 agent（无工具/子代理时 tool_calls 与 subagents 投影为空流）."""
    return create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        tools=[],
        system_prompt="You are a helpful assistant.",
    )


async def main() -> None:
    """asyncio.gather 并发消费 messages / tool_calls / subagents 三个投影."""
    agent = build_agent()
    stream = await agent.astream_events(
        {"messages": [{"role": "user", "content": "用三句话介绍 deepagents"}]},
        version="v3",
    )

    async def consume_messages() -> None:
        """主代理消息流（逐 token 增量）."""
        async for message in stream.messages:
            print("[coordinator]", await message.text)  # noqa: T201

    async def consume_tool_calls() -> None:
        """主代理工具调用投影（含 completed / error 终态）."""
        async for call in stream.tool_calls:
            print("[tool]", call.tool_name, call.completed)  # noqa: T201

    async def consume_subagents() -> None:
        """子代理 handle 流（.name 即 task 的 subagent_type）."""
        async for subagent in stream.subagents:
            print("[subagent]", subagent.name, subagent.status)  # noqa: T201
            async for message in subagent.messages:
                print(f"  [{subagent.name}]", await message.text)  # noqa: T201

    await asyncio.gather(consume_messages(), consume_tool_calls(), consume_subagents())
    # 同步单出口场景的 interleave 等价写法（按到达顺序合并、逐项标注来源名）：
    # for name, item in stream.interleave("messages", "subagents"): ...  # noqa: ERA001


if __name__ == "__main__":
    asyncio.run(main())
