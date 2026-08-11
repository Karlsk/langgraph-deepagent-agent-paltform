"""06 · 重试与回退：init_chat_model + with_retry + with_fallbacks 后直传实例.

依赖包：deepagents>=0.7.5、langchain、langchain-anthropic、langchain-openai
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
             OPENAI_API_KEY=<your-openai-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。

deepagents 不内置重试/回退（02 章第 4 节）；本示例走 LangChain Runnable
组合子路线。注意取舍：包装后传入的是外层 RunnableWithFallbacks 包内层
RunnableRetry 而非裸 BaseChatModel（isinstance(wrapped, BaseChatModel) 为
False），harness profile 匹配（依赖 _get_ls_params()）是否命中需实测；拿不
准时可自行构造实例直传，或改走自定义中间件包 tenacity 路线。
"""

import asyncio

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model


def build_resilient_model():
    """主模型指数退避重试，重试耗尽后依序切换后备模型.

    Returns:
        包装后的 Runnable（with_retry / with_fallbacks 组合子）。
    """
    primary = init_chat_model("anthropic:claude-sonnet-4-5")
    fallback = init_chat_model("openai:gpt-5")
    return primary.with_retry(
        stop_after_attempt=3,
        wait_exponential_jitter=True,
    ).with_fallbacks([fallback])


async def main() -> None:
    """包装好的模型实例直传 create_deep_agent（model 实例原样透传）."""
    agent = create_deep_agent(
        model=build_resilient_model(),
        system_prompt="You are a concise assistant.",
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "一句话介绍你自己，并说明当前使用的模型"}]})
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
