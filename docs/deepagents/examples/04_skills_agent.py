r"""04 · Skills 加载：FilesystemBackend 读盘方式（root_dir 指向 examples/04_skills）.

依赖包：deepagents>=0.7.5、langchain-anthropic
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。

另一种供给方式（StateBackend 播种，create_deep_agent 缺省 backend）：
  from deepagents.backends.utils import create_file_data
  agent = create_deep_agent(model=..., skills=["/skills/user/"])
  await agent.ainvoke({
      "messages": [...],
      "files": {
          "/skills/user/demo-skill/SKILL.md": create_file_data(
              "---\nname: demo-skill\ndescription: ...\n---\n\n# Demo Skill\n..."
          ),
      },
  })
"""

import asyncio
from pathlib import Path

from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend

SKILLS_DIR = Path(__file__).resolve().parent / "04_skills"


async def main() -> None:
    """FilesystemBackend 从 root_dir 读盘加载 skill 目录."""
    backend = FilesystemBackend(root_dir=SKILLS_DIR)
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        backend=backend,
        # source 为相对 backend 根的 POSIX 路径；"/" 下即 demo-skill/SKILL.md
        skills=["/"],
        system_prompt="Use available skills when the task matches.",
    )
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "请用 demo skill 给我一个 demo greeting"}]})
    print(result["messages"][-1].text)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(main())
