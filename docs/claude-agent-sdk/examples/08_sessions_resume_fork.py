"""08 · sessions 持久化：resume 恢复 + fork_session 分叉 + session_id 传递.

依赖包：claude-agent-sdk>=0.2.135
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）
说明：示例按 claude-agent-sdk 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：query()（单次模式）+ resume / fork_session / session_id 选项。
transcript 落盘于 ~/.claude/projects/<encoded-cwd>/*.jsonl；模块级会话函数
（list_sessions / get_session_info / get_session_messages / fork_session 等）
可同步读本地 transcript，此处不演示（文档 07 章第 7 节）。
print 为演示输出用途，逐行以 noqa: T201 豁免 ruff T20 规则。
"""

import asyncio

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query


async def run_turn(options: ClaudeAgentOptions, prompt: str) -> str | None:
    """跑一轮并返回该会话的 session_id（取自 ResultMessage）."""
    session_id: str | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            session_id = message.session_id
            print(f"[result] session_id={session_id} subtype={message.subtype}")  # noqa: T201
    return session_id


async def main() -> None:
    """三轮演示：新建会话 → resume 续写 → fork_session 分叉为新会话."""
    base = ClaudeAgentOptions(model="claude-sonnet-4-5")
    # 第一轮：新开会话，记下 session_id
    session_id = await run_turn(base, "记住暗号 atlas。只回复'已记住'。")
    if session_id is None:  # 错误处理前置：拿不到 session_id 则无法续接
        print("no session_id captured, abort")  # noqa: T201
        return
    # 第二轮：按 session id 恢复同一会话（transcript 在本机 ~/.claude/projects/ 下）
    resume_opts = ClaudeAgentOptions(model="claude-sonnet-4-5", resume=session_id)
    await run_turn(resume_opts, "刚才的暗号是什么？只回复暗号。")
    # 第三轮：fork_session=True 从旧会话分叉为新会话（不续写原会话）
    fork_opts = ClaudeAgentOptions(model="claude-sonnet-4-5", resume=session_id, fork_session=True)
    await run_turn(fork_opts, "基于此前上下文，用一句话总结本会话。")


if __name__ == "__main__":
    asyncio.run(main())
