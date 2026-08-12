"""09 · FastAPI SSE 集成骨架：lifespan 单例 client 注册表 + event_generator 帧协议.

依赖包：claude-agent-sdk>=0.2.135、fastapi、uvicorn（运行：uvicorn 09_fastapi_sse_integration:app）
所需环境变量：ANTHROPIC_API_KEY="<your-anthropic-api-key>"（SDK 不自动读 .env，须宿主注入）。说明：示例按 0.2.135 API 编写，未在本仓运行，须在独立 venv 验证。
所用 API 面：ClaudeSDKClient（常驻复用 + 空闲回收，见文档 01 章第 4 节、07 章第 8 节）；帧协议对齐本项目 app/api/v1/chatbot.py（data 帧 + done 帧 + 错误也走帧）。
"""

import json
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
)

CLIENTS: dict[str, ClaudeSDKClient] = {}  # session_id -> 常驻 client（生产须配空闲回收与并发上限）
OPTIONS = ClaudeAgentOptions(model="claude-sonnet-4-5")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """lifespan：应用退出时断开全部常驻 client，回收 CLI 子进程."""
    yield
    for client in CLIENTS.values():
        await client.disconnect()
    CLIENTS.clear()


app = FastAPI(lifespan=lifespan)


async def get_client(session_id: str) -> ClaudeSDKClient:
    """按 session_id 取或新建常驻 client（get-or-create）."""
    if session_id not in CLIENTS:
        client = ClaudeSDKClient(options=OPTIONS)
        await client.connect()
        CLIENTS[session_id] = client
    return CLIENTS[session_id]


@app.post("/chat/stream")
async def chat_stream(session_id: str, prompt: str) -> StreamingResponse:
    """SSE 端点：SDK 消息流转 SSE 帧；断连清理在生成器 finally 中完成."""

    async def event_generator():
        """产出 SSE 帧；错误走 error 帧；客户端断开时 interrupt 停掉当前轮."""
        client: ClaudeSDKClient | None = None
        finished = False
        try:
            client = await get_client(session_id)  # connect 异常也在此转 error 帧，不穿透生成器
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
                    yield f"data: {json.dumps({'content': text, 'done': False}, ensure_ascii=False)}\n\n"
                elif isinstance(message, ResultMessage):
                    finished = True
                    yield f"data: {json.dumps({'content': '', 'done': True}, ensure_ascii=False)}\n\n"  # 收尾帧：恒有 done=True
        except Exception as e:  # noqa: BLE001 -- SSE 帧协议须把任意异常转成 error 帧发给前端，不能中断连接
            yield f"data: {json.dumps({'content': f'error: {e}', 'done': True}, ensure_ascii=False)}\n\n"
        finally:
            if not finished and client is not None:
                try:
                    await client.interrupt()
                except ClaudeSDKError:
                    pass  # 客户端未连接或已断开：吞掉以免覆盖 error 帧语义

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
