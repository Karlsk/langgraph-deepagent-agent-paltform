"""08 · System Prompt：SystemMessage + cache_control 断点（Anthropic）.

依赖包：deepagents>=0.7.5、langchain-anthropic、langchain-core
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。

组装顺序（03 章 §1）：system_prompt 是 USER 段，最终按 USER → BASE → SUFFIX
组装（BASE/SUFFIX 来自激活的 HarnessProfile，0.7.x 默认均为空，段间空行分隔）。
传 SystemMessage 时，其既有 content blocks 上的 cache_control 标记被完整保留
（graph.py 对 SystemMessage 分支原样透传；profile 有 prompt 内容时仅以追加
text block 方式合并，不打断既有 blocks）——这是显式控制 Anthropic prompt-cache
断点的官方通道。
"""

import asyncio

from deepagents import create_deep_agent
from langchain_core.messages import SystemMessage

# 大段稳定指令：打上 ephemeral 断点后，这段前缀在多轮对话中命中 Anthropic 缓存
LONG_STABLE_INSTRUCTIONS = (
    "You are a meticulous research assistant. Always cite sources, prefer primary sources, and never fabricate data. "
    # ...（此处省略数百行稳定 SOP / 领域规范文本）
)

system_prompt = SystemMessage(
    content=[
        {
            "type": "text",
            "text": LONG_STABLE_INSTRUCTIONS,
            "cache_control": {"type": "ephemeral"},  # Anthropic 缓存断点
        },
    ]
)


async def main() -> None:
    """SystemMessage 直传 system_prompt，cache_control 标记原样保留."""
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=system_prompt,
    )
    # 尾部 AnthropicPromptCachingMiddleware 仍会装配（非 Anthropic 模型下 no-op）；
    # 自动断点适用于常规场景，SystemMessage 断点用于需要精确控制缓存位置的进阶场景
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "用一句话说明你的工作方式"}]})
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
