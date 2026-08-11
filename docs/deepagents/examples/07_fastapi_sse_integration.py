"""07 · FastAPI SSE 集成骨架：deepagents v3 流式投影 → SSE 帧.

依赖包：fastapi、uvicorn、pydantic、deepagents>=0.7.5、langchain-anthropic
所需环境变量：ANTHROPIC_API_KEY=<your-anthropic-api-key>
说明：示例按 deepagents 0.7.5 API 编写，未在本仓运行；骨架级演示，未包含
鉴权/限流/metrics 横切逻辑（落地时对齐 app/api/v1/chatbot.py：@limiter.limit、
llm_stream_duration_seconds 计时、Langfuse callbacks 透传）。
"""

import json
import logging
import uvicorn
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from deepagents import create_deep_agent
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

logger = logging.getLogger(__name__)
agent: Any = None  # lifespan 内装配的 CompiledStateGraph 单例


class ChatRequest(BaseModel):
    """聊天请求体."""

    message: str
    thread_id: str


def sse_frame(payload: dict) -> str:
    """把事件载荷编码为一条 SSE data 帧."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动期构建单例 agent（生产换 AsyncPostgresSaver，见 09 章第 4 节）."""
    global agent
    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-5",
        checkpointer=MemorySaver(),
        system_prompt="You are a helpful assistant.",
    )
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/chat/stream")
async def chat_stream(chat_request: ChatRequest) -> StreamingResponse:
    """把 deepagents v3 投影转换为 SSE 事件流（含子代理来源标注）."""

    async def event_generator() -> AsyncIterator[str]:
        """逐帧产出 coordinator / subagent 消息，异常也序列化为 error 帧."""
        stream = await agent.astream_events(
            {"messages": [{"role": "user", "content": chat_request.message}]},
            version="v3",
            config={"configurable": {"thread_id": chat_request.thread_id}},
        )
        try:
            async for message in stream.messages:
                yield sse_frame({"source": "coordinator", "content": await message.text, "done": False})
            async for subagent in stream.subagents:
                async for message in subagent.messages:
                    yield sse_frame({"source": subagent.name, "content": await message.text, "done": False})
            yield sse_frame({"content": "", "done": True})
        except Exception as e:  # noqa: BLE001  SSE 出口必须把任意异常收敛为 error 帧
            logger.exception("stream_chat_request_failed", extra={"thread_id": chat_request.thread_id})
            yield sse_frame({"content": str(e), "done": True})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
