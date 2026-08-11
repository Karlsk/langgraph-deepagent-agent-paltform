"""03 · 声明式 SubAgent：task 委派 + response_format 结构化回传.

依赖包：deepagents>=0.7.5、langchain-anthropic、pydantic
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from deepagents import SubAgent, create_deep_agent
from pydantic import BaseModel, Field

# TypedDict 亦可（from typing import TypedDict），接受形态还包括
# ToolStrategy / ProviderStrategy / AutoStrategy 与 JSON schema dict。


class Findings(BaseModel):
    """子代理结构化回传的 schema（作为 ToolMessage 内容 JSON 序列化回传）."""

    summary: str = Field(description="调研结论摘要")
    key_points: list[str] = Field(description="关键要点列表")


analyzer: SubAgent = {
    "name": "analyzer",
    "description": "Analyzes a topic and returns structured findings",
    "system_prompt": "Analyze the given topic thoroughly and return findings.",
    # tools/model/permissions/interrupt_on 缺省继承父代理；tools=[] 可清零
    "tools": [],
    "response_format": Findings,
}


async def main() -> None:
    """主代理经 task 工具委派 analyzer 子代理（由提示词触发）."""
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        subagents=[analyzer],
        system_prompt="Delegate analysis work to the analyzer subagent via the task tool.",
    )
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "请 analyzer 调研 deepagents 的子代理架构并汇报"}]}
    )
    # 父代理仅收到一条 ToolMessage 摘要回流；子代理中间过程留在隔离上下文
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
