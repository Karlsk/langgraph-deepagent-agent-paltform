"""01 · deepagents 最小闭环：create_deep_agent + invoke.

依赖包：deepagents>=0.7.5、langchain-anthropic
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

from deepagents import create_deep_agent


def build_agent():
    """构建最小 deep agent：显式传 model，不依赖已弃用的默认模型.

    Returns:
        编译好的 LangGraph CompiledStateGraph。
    """
    # model 支持 "provider:model" 字符串（经 resolve_model → init_chat_model 解析），
    # 也支持直传 BaseChatModel 实例（原样透传，见 06 号示例的重试/回退包装）。
    return create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        system_prompt="You are a concise assistant. Answer in one sentence.",
    )


def main() -> None:
    """同步最小闭环：invoke 一次性问答."""
    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": "deepagents 的默认 backend 是什么？"}]})
    print(result["messages"][-1].text)  # noqa: T201


async def amain() -> None:
    """异步等价写法：ainvoke；多轮记忆需另配 checkpointer 与 thread_id."""
    agent = build_agent()
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "deepagents 的默认 backend 是什么？"}]})
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    main()
    # 异步入口等价：asyncio.run(amain())  # noqa: ERA001
    # 多轮记忆还需：checkpointer（如 MemorySaver / AsyncPostgresSaver）
    # + config={"configurable": {"thread_id": "<会话 id>"}}，详见 09 章第 4 节
