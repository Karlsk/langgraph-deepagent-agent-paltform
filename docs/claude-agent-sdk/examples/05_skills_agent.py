"""05 · Skill 系统：目录式 skill 加载（skills + setting_sources + cwd）.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式）+ skills / setting_sources / cwd 选项。

发现路径机制（文档 05 章第 2 节）：skills 仅文件系统形态，无程序化注册 API；
setting_sources 含 "project" 时，SDK 启动时扫描 <cwd>/.claude/skills/<name>/SKILL.md
的元数据（调用时才加载全文），名单按 SKILL.md 的 name 字段或目录名匹配。
本仓为文档体例把 skill 放在 05_skills/demo-skill/（展示布局）；独立 venv 验证前
须先把它复制到 <cwd>/.claude/skills/ 下（下方命令注释给出）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

EXAMPLE_DIR = Path(__file__).resolve().parent

# 验证前置（shell）：mkdir -p .claude/skills && cp -r 05_skills/demo-skill .claude/skills/
# （在 EXAMPLE_DIR 下执行，使官方发现路径 <cwd>/.claude/skills/demo-skill/SKILL.md 成立）


def build_options() -> ClaudeAgentOptions:
    """构建选项：cwd 指向示例目录 + project 来源 + 精确 skills 名单."""
    return ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        cwd=EXAMPLE_DIR,  # skills 发现以 cwd 为基点
        setting_sources=["project"],  # skills 发现的前提：加载 project 文件系统设置
        skills=["demo-skill"],  # 与目录名 / SKILL.md name 字段一致；设置后 Skill 工具自动进 allowed_tools
    )


async def main() -> None:
    """触发 demo-skill：Claude 依据 SKILL.md description 自主决定调用."""
    prompt = "Please format the following text with the demo skill: 'claude-agent-sdk ships a bundled CLI.'"
    async for message in query(prompt=prompt, options=build_options()):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)  # noqa: T201
        elif isinstance(message, ResultMessage):
            print(f"subtype={message.subtype} cost_usd={message.total_cost_usd}")  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
