from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Any, AsyncIterator, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import create_engine, text

from config.db_config import get_db_config
from src.agent.llm_client import LLMClient
from src.agent.orchestrator import AgentEvent, FinancialReportAgent
from src.api.schemas import (
    AssistantMetadata,
    ChatQueryRequest,
    ChatQueryResponse,
    ExampleItem,
    ExamplesResponse,
    HealthResponse,
    SessionCreateRequest,
    SessionMessage,
    SessionResponse,
    StatusItem,
)
from src.api.session_store import ChatSessionRecord, SessionStore


ROOT_DIR = Path(__file__).resolve().parents[2]
RESULT_DIR = ROOT_DIR / "result"
CHROMA_DIR = ROOT_DIR / "data" / "chroma_db"

_CHART_FILE_PATTERN = re.compile(r"^S([0-9A-F]{8})_\d+\.png$")


def _session_chart_prefix(session_id: str) -> str:
    return f"S{session_id[:8].upper()}"


def _delete_session_charts(session_id: str) -> None:
    prefix = _session_chart_prefix(session_id)
    try:
        for path in RESULT_DIR.glob(f"{prefix}_*.png"):
            path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(f"清理会话图表失败 {session_id}: {exc}")


def _cleanup_orphan_charts() -> None:
    """清理不属于任何现存会话的图表文件，防止 result/ 无限膨胀。"""
    try:
        active = {_session_chart_prefix(sid) for sid in _session_store.session_ids()}
        removed = 0
        for path in RESULT_DIR.glob("S*_*.png"):
            match = _CHART_FILE_PATTERN.match(path.name)
            if match and f"S{match.group(1)}" not in active:
                path.unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info(f"已清理 {removed} 个孤儿图表文件")
    except Exception as exc:
        logger.warning(f"孤儿图表清理失败: {exc}")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _cleanup_orphan_charts()
    yield


app = FastAPI(
    title="上市公司财报分析 Chat Agent API",
    description="面向自然语言财报分析对话的 FastAPI 服务（支持 SSE 流式）",
    version="0.2.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

RESULT_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/results", StaticFiles(directory=str(RESULT_DIR)), name="results")

_session_store = SessionStore()
_agent_instance: Optional[FinancialReportAgent] = None
_agent_lock = Lock()
_health_engine = None
_health_engine_lock = Lock()


def _get_health_engine():
    """健康检查复用同一个 engine，避免每次请求重建连接池。"""
    global _health_engine
    if _health_engine is None:
        with _health_engine_lock:
            if _health_engine is None:
                _health_engine = create_engine(get_db_config().connection_string, pool_pre_ping=True)
    return _health_engine


def _get_agent() -> FinancialReportAgent:
    global _agent_instance
    if _agent_instance is None:
        with _agent_lock:
            if _agent_instance is None:
                _agent_instance = FinancialReportAgent(llm=LLMClient())
    return _agent_instance


def _health_item(ok: bool, detail: str) -> StatusItem:
    return StatusItem(ok=ok, detail=detail)


def _load_examples(limit: int = 8) -> List[ExampleItem]:
    curated = [
        ("ex-1", "结构化查询", "药明康德 2024 年营业收入是多少？"),
        ("ex-2", "趋势分析", "凯莱英近三年营业收入和净利润的变化趋势是什么？请画图。"),
        ("ex-3", "排名查询", "2024 年净利润最高的前十家公司有哪些？"),
        ("ex-4", "对比分析", "药明康德和泰格医药 2025 年三季度的毛利率和净利率对比如何？"),
        ("ex-5", "现金流", "迪安诊断 2024 年净现金流怎么样？"),
        ("ex-6", "多轮追问", "先查百克生物 2025 年三季度净利润，然后再看现金流怎么样。"),
        ("ex-7", "可视化", "请画出成都先导 2022 到 2025 年净利润趋势图。"),
        ("ex-8", "澄清示例", "这个公司的利润怎么样？"),
    ]
    return [
        ExampleItem(id=item_id, type=item_type, question=question)
        for item_id, item_type, question in curated[:limit]
    ]


def _session_to_response(session: ChatSessionRecord) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        messages=[
            SessionMessage(
                role=message.role,  # type: ignore[arg-type]
                content=message.content,
                ts=message.ts,
                metadata=AssistantMetadata(**message.metadata) if message.metadata else None,
            )
            for message in session.messages
        ],
        latest_context=dict(session.latest_context),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _ensure_workspace_path(path_value: str) -> Path:
    candidate = Path(path_value)
    resolved = candidate.resolve() if candidate.is_absolute() else (ROOT_DIR / candidate).resolve()
    root_resolved = ROOT_DIR.resolve()
    if root_resolved not in resolved.parents and resolved != root_resolved:
        raise HTTPException(status_code=403, detail="Path is outside workspace")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


def _local_asset_url(path_value: Optional[str]) -> Optional[str]:
    if not path_value:
        return None
    raw = str(path_value)
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/results/") or raw.startswith("/api/assets"):
        return raw
    path = Path(raw)
    if path.exists():
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if RESULT_DIR.exists() and (resolved == RESULT_DIR.resolve() or RESULT_DIR.resolve() in resolved.parents):
            return f"/results/{quote(resolved.name)}"
        return f"/api/assets?path={quote(str(resolved))}"
    return raw


def _normalize_answer_payload(answer: Dict[str, Any]) -> Dict[str, Any]:
    images = [_local_asset_url(path) for path in (answer.get("image") or [])]
    references = []
    for item in answer.get("references") or []:
        references.append(
            {
                "paper_path": str(item.get("paper_path") or ""),
                "source_title": str(item.get("source_title") or ""),
                "text": str(item.get("text") or ""),
                "score": item.get("score"),
                "paper_image": _local_asset_url(item.get("paper_image")),
            }
        )
    return {
        "content": str(answer.get("content") or ""),
        "image": [img for img in images if img],
        "references": references,
    }


# ── 基础接口 ──────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse)
async def get_health() -> HealthResponse:
    service = _health_item(True, "FastAPI service ready")

    try:
        engine = _get_health_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = _health_item(True, "MySQL connection available")
    except Exception as exc:
        database = _health_item(False, f"MySQL unavailable: {exc}")

    chroma_ok = CHROMA_DIR.exists() and any(CHROMA_DIR.iterdir())
    chroma_detail = "Chroma knowledge base detected" if chroma_ok else "Chroma directory is empty or missing"
    knowledge_base = _health_item(chroma_ok, chroma_detail)

    llm_client = LLMClient()
    llm = _health_item(bool(llm_client.api_key), "LLM API key configured" if llm_client.api_key else "DEEPSEEK_API_KEY missing")

    examples = _health_item(True, "Curated chat examples available")

    return HealthResponse(
        service=service,
        database=database,
        knowledge_base=knowledge_base,
        llm=llm,
        examples=examples,
    )


@app.get("/api/examples", response_model=ExamplesResponse)
async def get_examples() -> ExamplesResponse:
    return ExamplesResponse(examples=_load_examples())


@app.post("/api/sessions", response_model=SessionResponse)
async def create_session(payload: Optional[SessionCreateRequest] = None) -> SessionResponse:
    session = _session_store.create_session()
    return _session_to_response(session)


@app.get("/api/sessions/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str) -> SessionResponse:
    session = _session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _session_to_response(session)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> Dict[str, Any]:
    deleted = _session_store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    _delete_session_charts(session_id)
    return {"ok": True}


@app.get("/api/assets")
async def get_asset(path: str = Query(..., description="Absolute or workspace-relative file path")) -> FileResponse:
    resolved = _ensure_workspace_path(path)
    return FileResponse(resolved)


# ── 对话执行 ──────────────────────────────────────────────────────────────────

HISTORY_ASSISTANT_CONTENT_LIMIT = 600


def _build_history(session: ChatSessionRecord) -> List[Dict[str, str]]:
    """会话消息历史（仅 role/content），交给 LLM 作为多轮上下文。

    助手长回答截断，控制多轮对话的 token 成本；用户消息保留全文。
    """
    history: List[Dict[str, str]] = []
    for message in session.messages:
        content = str(message.content or "").strip()
        if message.role not in {"user", "assistant"} or not content:
            continue
        if message.role == "assistant" and len(content) > HISTORY_ASSISTANT_CONTENT_LIMIT:
            content = content[:HISTORY_ASSISTANT_CONTENT_LIMIT] + "…（已截断）"
        history.append({"role": message.role, "content": content})
    return history


def _resolve_session(session_id: Optional[str]) -> ChatSessionRecord:
    sid = str(session_id or "").strip()
    session = _session_store.get_session(sid) if sid else None
    if session is None:
        session = _session_store.create_session()
    return session


async def _turn_events(session: ChatSessionRecord, question: str) -> AsyncIterator[AgentEvent]:
    """
    运行一轮对话，把同步事件生成器桥接为异步迭代。

    落库（_finalize_turn）在生产者线程内完成，因此即使客户端中途断连
    （用户点了"停止生成"或刷新页面），本轮结果依然会写入会话存储。
    最终的 done 事件携带完整的 ChatQueryResponse。
    """
    loop = asyncio.get_running_loop()
    event_queue: "queue.Queue[Optional[AgentEvent]]" = queue.Queue()
    history = _build_history(session)
    chart_prefix = f"S{session.session_id[:8].upper()}"
    chart_index = session.next_chart_index

    def _produce() -> None:
        final_result: Optional[Dict[str, Any]] = None
        error_message: Optional[str] = None
        try:
            agent = _get_agent()
            for event in agent.run(question, history=history, chart_prefix=chart_prefix, chart_index=chart_index):
                if event.type == "done":
                    final_result = event.data.get("result") or {}
                    continue
                if event.type == "error":
                    error_message = str(event.data.get("message") or "处理失败")
                event_queue.put(event)
        except Exception as exc:
            logger.exception(f"[API] agent run failed: {exc}")
            error_message = "本轮问题执行失败，请重试一次，或换成更明确的公司/年份/指标问法。"
            event_queue.put(AgentEvent("error", {"message": error_message}))

        try:
            if final_result is None:
                final_result = _error_result(question, error_message or "处理失败，请重试。")
            response = _finalize_turn(session, question, final_result)
            event_queue.put(AgentEvent("done", {"response": response}))
        except Exception as exc:
            logger.exception(f"[API] finalize turn failed: {exc}")
            event_queue.put(AgentEvent("error", {"message": "结果保存失败，请重试。"}))
        finally:
            event_queue.put(None)

    threading.Thread(target=_produce, daemon=True).start()

    while True:
        event = await loop.run_in_executor(None, event_queue.get)
        if event is None:
            break
        yield event


def _finalize_turn(
    session: ChatSessionRecord,
    question: str,
    result: Dict[str, Any],
) -> ChatQueryResponse:
    """归一化结果、写入会话存储，并构造响应。"""
    normalized_answer = _normalize_answer_payload(result.get("answer") or {})
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    execution_plan = result.get("execution_plan") if isinstance(result.get("execution_plan"), list) else []
    sql = str(result.get("sql") or "-")
    needs_clarification = bool(result.get("needs_clarification"))
    clarify_options = [str(o) for o in (result.get("clarify_options") or [])]
    chart_data = result.get("chart_data") if isinstance(result.get("chart_data"), dict) else None
    chart_data_list = [item for item in (result.get("chart_data_list") or []) if isinstance(item, dict)]

    result_context = result.get("context") if isinstance(result.get("context"), dict) else {}
    if sql != "-" and result_context:
        latest_context = dict(result_context)
    else:
        latest_context = dict(session.latest_context or {})

    assistant_metadata = AssistantMetadata(
        sql=sql,
        chart_format=str(result.get("chart_format") or "无"),
        chart_data=chart_data,
        chart_data_list=chart_data_list,
        images=list(normalized_answer.get("image") or []),
        references=list(normalized_answer.get("references") or []),
        validation=validation,
        execution_plan=execution_plan,
        needs_clarification=needs_clarification,
        clarify_options=clarify_options,
        context=latest_context,
    )

    session = _session_store.append_turn(
        session_id=session.session_id,
        user_content=question,
        assistant_content=str(normalized_answer.get("content") or ""),
        assistant_metadata=assistant_metadata.model_dump(),
        latest_context=latest_context,
        image_count=len(normalized_answer.get("image") or []),
    )

    return ChatQueryResponse(
        session_id=session.session_id,
        answer=normalized_answer,
        sql=sql,
        chart_format=str(result.get("chart_format") or "无"),
        chart_data=chart_data,
        chart_data_list=chart_data_list,
        execution_plan=execution_plan,
        validation=validation,
        context=latest_context,
        needs_clarification=needs_clarification,
        clarify_options=clarify_options,
        messages=_session_to_response(session).messages,
    )


def _error_result(question: str, message: str) -> Dict[str, Any]:
    return {
        "question": question,
        "sql": "-",
        "answer": {"content": message, "image": [], "references": []},
        "context": {},
        "chart_format": "无",
        "needs_clarification": False,
        "validation": {"status": "fail", "mode": "chat", "sql_events": [], "quality_warnings": []},
        "execution_plan": [],
    }


def _sse_format(event_type: str, data: Dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(payload: ChatQueryRequest) -> StreamingResponse:
    """SSE 流式问答：plan / tool_call / tool_result / answer_delta / chart / clarify / error / done"""
    session = _resolve_session(payload.session_id)
    question = str(payload.question or "").strip()
    lock = _session_store.get_session_lock(session.session_id)

    async def _event_stream() -> AsyncIterator[str]:
        async with lock:
            yield _sse_format("session", {"session_id": session.session_id})
            async for event in _turn_events(session, question):
                if event.type == "done":
                    response: ChatQueryResponse = event.data["response"]
                    yield _sse_format("done", response.model_dump())
                    continue
                data = dict(event.data)
                if event.type == "chart":
                    data["url"] = _local_asset_url(data.get("path"))
                yield _sse_format(event.type, data)

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/query", response_model=ChatQueryResponse)
async def chat_query(payload: ChatQueryRequest) -> ChatQueryResponse:
    """非流式问答（聚合 SSE 事件后一次性返回），作为降级与测试入口。"""
    session = _resolve_session(payload.session_id)
    question = str(payload.question or "").strip()
    lock = _session_store.get_session_lock(session.session_id)

    async with lock:
        response: Optional[ChatQueryResponse] = None
        async for event in _turn_events(session, question):
            if event.type == "done":
                response = event.data["response"]
        if response is None:
            raise HTTPException(status_code=500, detail="处理失败，请重试")
        return response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.api.main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
