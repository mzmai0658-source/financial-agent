"""
财报 Agent 编排器：LLM 工具调用主循环，产出事件流。

事件类型：
  plan          规划/思考状态更新
  tool_call     工具调用开始 {tool, label, detail}
  tool_result   工具调用结束 {tool, status, summary, ...}
  answer_delta  最终回答增量 {text}
  chart         图表生成 {path, chart_data}
  references    引用列表 {items}
  clarify       需要澄清 {question, options}
  done          本轮结束 {result: 完整聚合结果}
  error         不可恢复错误 {message}

LLM 不可用时自动降级到规则兜底（fallback 模块）。
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from loguru import logger

from .fallback import build_display_context, build_fallback_sql, extract_slots, format_fallback_answer
from .llm_client import LLMClient
from .prompts import SYNTHESIS_INSTRUCTION, TOOL_SCHEMAS, build_system_prompt
from .tools import ChartTool, RAGTool, SQLTool

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

MAX_TOOL_ROUNDS = 6
MAX_HISTORY_MESSAGES = 16
RAG_TEXT_PROMPT_LIMIT = 700
REFERENCE_EXCERPT_LIMIT = 320


@dataclass
class AgentEvent:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


def _to_relative_reference_path(path_value: Any) -> str:
    raw = str(path_value or "").replace("\\", "/")
    if not raw:
        return ""
    try:
        rel = os.path.relpath(raw, ROOT_DIR).replace("\\", "/")
        if not rel.startswith("."):
            rel = f"./{rel}"
    except Exception:
        rel = raw
    m = re.search(r"(附件\d+[：:])", rel)
    if m:
        rel = "./" + rel[m.start():]
    return rel


def _excerpt(text: str, limit: int = REFERENCE_EXCERPT_LIMIT) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


class FinancialReportAgent:
    """LLM 工具调用为核心的财报问答 Agent。"""

    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()
        self.sql_tool = SQLTool()
        self.rag_tool = RAGTool()

    # ── 主入口 ────────────────────────────────────────────────────────────────

    def run(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        chart_prefix: str = "chat",
        chart_index: int = 1,
    ) -> Iterator[AgentEvent]:
        question = str(question or "").strip()
        if not question:
            yield AgentEvent("error", {"message": "问题为空"})
            return

        state = _TurnState(chart_prefix=chart_prefix, chart_index=chart_index)
        messages: List[Dict[str, Any]] = [{"role": "system", "content": build_system_prompt()}]
        for msg in (history or [])[-MAX_HISTORY_MESSAGES:]:
            role = msg.get("role")
            content = str(msg.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": question})

        yield AgentEvent("plan", {"label": "理解问题", "detail": "正在分析意图与所需数据"})

        llm_available = True
        for round_idx in range(MAX_TOOL_ROUNDS):
            state.rounds = round_idx + 1
            response = self.llm.chat_with_tools(messages, TOOL_SCHEMAS)
            if response is None:
                llm_available = False
                break

            tool_calls = response.get("tool_calls") or []
            content = str(response.get("content") or "").strip()

            if not tool_calls:
                # 无工具调用：闲聊直接回复，或工具阶段结束信号（DONE）
                if state.tools_used and (not content or _looks_like_done(content)):
                    messages.append({"role": "assistant", "content": content or "DONE"})
                    yield from self._synthesize(messages, state)
                elif content:
                    # 数据溯源防护：没调用任何工具却直接给出财务数字的回答不可接受，
                    # 退回去强制它查库核实（凭训练记忆/对话历史"背"数字是无证据回答）。
                    if not state.tools_used and not state.nudged and _contains_financial_figures(content):
                        logger.warning("LLM 未调用工具即输出财务数字，已退回要求查库核实")
                        state.nudged = True
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": (
                            "（系统提示）你刚才的回答包含财务数字，但本轮没有调用任何工具。"
                            "数字必须来自 query_database 的查询结果，不得凭记忆或对话历史直接给出。"
                            "请现在调用工具查询核实后再回答；如果这其实是闲聊或能力咨询，请重新简短回复且不要包含具体数字。"
                        )})
                        continue
                    state.answer_parts.append(content)
                    yield AgentEvent("answer_delta", {"text": content})
                else:
                    yield from self._synthesize(messages, state)
                yield self._done(question, state)
                return

            messages.append({
                "role": "assistant",
                "content": response.get("content") or "",
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                tc_id = tc.get("id") or f"call_{round_idx}"
                fn = (tc.get("function") or {})
                name = str(fn.get("name") or "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "ask_clarification":
                    clarify_question = str(args.get("question") or "请补充更多信息后再试。")
                    options = [str(o) for o in (args.get("options") or []) if str(o).strip()]
                    state.clarification = clarify_question
                    state.clarify_options = options
                    yield AgentEvent("clarify", {"question": clarify_question, "options": options})
                    yield self._done(question, state)
                    return

                handler = {
                    "query_database": self._handle_query_database,
                    "search_documents": self._handle_search_documents,
                    "render_chart": self._handle_render_chart,
                }.get(name)
                if handler is None:
                    tool_payload = {"status": "error", "message": f"未知工具: {name}"}
                    yield AgentEvent("tool_result", {"tool": name, "status": "error", "summary": "未知工具"})
                else:
                    try:
                        events, tool_payload = handler(args, state)
                    except Exception as exc:
                        # 参数或执行异常不终止整轮，转成错误结果回给 LLM 自纠（与 SQL 报错同等待遇）
                        logger.warning(f"工具 {name} 执行异常: {exc}")
                        state.tools_used = True
                        events = [
                            AgentEvent("tool_call", {"tool": name, "label": f"调用 {name}", "detail": ""}),
                            AgentEvent("tool_result", {
                                "tool": name, "status": "error",
                                "summary": f"执行异常: {exc}"[:120],
                            }),
                        ]
                        tool_payload = {"status": "error", "message": f"工具执行异常：{exc}。请修正参数后重试。"}
                    for ev in events:
                        yield ev

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(tool_payload, ensure_ascii=False),
                })

        if not llm_available:
            yield from self._run_fallback(question, history, state)
            yield self._done(question, state)
            return

        # 工具轮次用尽，强制合成
        yield from self._synthesize(messages, state)
        yield self._done(question, state)

    # ── 工具处理 ──────────────────────────────────────────────────────────────

    def _handle_query_database(self, args: Dict[str, Any], state: "_TurnState"):
        sql = str(args.get("sql") or "")
        purpose = str(args.get("purpose") or "查询财报数据")
        events = [AgentEvent("tool_call", {"tool": "query_database", "label": purpose, "detail": sql})]

        result = self.sql_tool.run(sql)
        status = result.get("status")
        executed_sql = str(result.get("sql") or sql)
        if status == "success":
            rows = result.get("rows") or []
            state.executed_sql.append(executed_sql)
            state.sql_rows.extend(rows)
            state.sql_events.append({"sql": executed_sql, "status": "success", "row_count": result.get("row_count")})
            summary = f"返回 {result.get('row_count')} 行"
            payload = {
                "status": "success",
                "row_count": result.get("row_count"),
                "columns": result.get("columns"),
                "rows": rows[:50],
            }
        elif status == "empty":
            state.executed_sql.append(executed_sql)
            state.sql_events.append({"sql": executed_sql, "status": "empty"})
            summary = "结果为空"
            payload = {"status": "empty", "message": result.get("message")}
        else:
            state.sql_events.append({"sql": executed_sql, "status": str(status)})
            summary = str(result.get("message") or "执行失败")[:120]
            payload = {"status": status, "message": result.get("message")}

        state.tools_used = True
        events.append(AgentEvent("tool_result", {
            "tool": "query_database",
            "status": str(status),
            "summary": summary,
            "sql": executed_sql,
            "row_count": result.get("row_count"),
            "rows": (result.get("rows") or [])[:20],
            "columns": result.get("columns") or [],
        }))
        return events, payload

    def _handle_search_documents(self, args: Dict[str, Any], state: "_TurnState"):
        query = str(args.get("query") or "")
        stock_code = args.get("stock_code") or None
        report_year = args.get("report_year") or None
        try:
            top_k = int(args.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        events = [AgentEvent("tool_call", {"tool": "search_documents", "label": "检索研报/年报", "detail": query})]

        result = self.rag_tool.run(query, top_k=top_k, stock_code=stock_code, report_year=report_year)
        items = result.get("results") or []
        state.rag_results.extend(items)
        state.tools_used = True

        prompt_items = [
            {
                "source": item.get("source_title") or item.get("paper_path"),
                "paper_path": item.get("paper_path"),
                "section": item.get("section_title"),
                "text": _excerpt(item.get("text"), RAG_TEXT_PROMPT_LIMIT),
            }
            for item in items[:6]
        ]
        payload = {"status": result.get("status"), "results": prompt_items}
        events.append(AgentEvent("tool_result", {
            "tool": "search_documents",
            "status": str(result.get("status")),
            "summary": f"命中 {len(items)} 个片段",
            "items": [
                {
                    "source_title": item.get("source_title"),
                    "paper_path": _to_relative_reference_path(item.get("paper_path")),
                    "text": _excerpt(item.get("text")),
                    "score": item.get("score"),
                }
                for item in items[:6]
            ],
        }))
        return events, payload

    def _handle_render_chart(self, args: Dict[str, Any], state: "_TurnState"):
        chart_type = str(args.get("chart_type") or "bar")
        title = str(args.get("title") or "财务数据图表")
        events = [AgentEvent("tool_call", {"tool": "render_chart", "label": "生成图表", "detail": title})]

        y_data: List[float] = []
        for value in args.get("y_data") or []:
            try:
                y_data.append(float(value))
            except (TypeError, ValueError):
                raise ValueError(
                    f"y_data 含非数值项 {value!r}。缺失数据请剔除对应期数，保持 x_data 与 y_data 一一对应。"
                )

        filename = f"{state.chart_prefix}_{state.chart_index}.png"
        chart_tool = ChartTool()
        result = chart_tool.run(
            chart_type=chart_type,
            title=title,
            x_data=[str(x) for x in (args.get("x_data") or [])],
            y_data=y_data,
            x_label=str(args.get("x_label") or ""),
            y_label=str(args.get("y_label") or ""),
            series_name=str(args.get("series_name") or ""),
            filename=filename,
        )
        state.tools_used = True
        if result.get("status") == "success":
            state.chart_index += 1
            state.images.append(str(result.get("path")))
            state.chart_data.append(result.get("chart_data") or {})
            state.chart_formats.append({"line": "折线图", "bar": "柱状图", "pie": "饼图"}.get(chart_type, chart_type))
            events.append(AgentEvent("chart", {
                "path": str(result.get("path")),
                "chart_data": result.get("chart_data"),
                "title": title,
            }))
            events.append(AgentEvent("tool_result", {
                "tool": "render_chart", "status": "success", "summary": title,
            }))
            payload = {"status": "success", "message": f"图表已生成并展示给用户：{title}"}
        else:
            events.append(AgentEvent("tool_result", {
                "tool": "render_chart", "status": "error",
                "summary": str(result.get("message") or "生成失败")[:120],
            }))
            payload = {"status": "error", "message": result.get("message")}
        return events, payload

    # ── 最终回答合成（流式）────────────────────────────────────────────────────

    def _synthesize(self, messages: List[Dict[str, Any]], state: "_TurnState") -> Iterator[AgentEvent]:
        yield AgentEvent("plan", {"label": "合成回答", "detail": "正在整理结论"})
        synthesis_messages = messages + [{"role": "user", "content": SYNTHESIS_INSTRUCTION}]
        streamed_any = False
        for delta in self.llm.chat_stream(synthesis_messages):
            streamed_any = True
            state.answer_parts.append(delta)
            yield AgentEvent("answer_delta", {"text": delta})
        if not streamed_any:
            # 流式失败时退回非流式一次
            content = self.llm.chat(synthesis_messages) or ""
            if content:
                state.answer_parts.append(content)
                yield AgentEvent("answer_delta", {"text": content})
            else:
                fallback_text = self._fallback_summary(state)
                state.answer_parts.append(fallback_text)
                yield AgentEvent("answer_delta", {"text": fallback_text})

    def _fallback_summary(self, state: "_TurnState") -> str:
        if state.sql_rows:
            return "智能合成暂不可用，但已查到数据，请在右侧证据栏查看 SQL 查询结果。"
        return "抱歉，当前无法生成回答，请稍后重试。"

    # ── 规则兜底（LLM 完全不可用）──────────────────────────────────────────────

    def _run_fallback(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]],
        state: "_TurnState",
    ) -> Iterator[AgentEvent]:
        logger.warning("LLM 不可用，进入规则兜底查询")
        yield AgentEvent("plan", {"label": "降级查询", "detail": "智能分析服务不可用，尝试数据库直查"})
        previous_context = _context_from_history(history)
        slots = extract_slots(question, previous_context)
        sql = build_fallback_sql(slots)
        rows: List[Dict[str, Any]] = []
        if sql:
            yield AgentEvent("tool_call", {"tool": "query_database", "label": "数据库直查", "detail": sql})
            result = self.sql_tool.run(sql)
            executed_sql = str(result.get("sql") or sql)
            if result.get("status") == "success":
                rows = result.get("rows") or []
                state.executed_sql.append(executed_sql)
                state.sql_rows.extend(rows)
                state.sql_events.append({"sql": executed_sql, "status": "success", "row_count": result.get("row_count")})
            else:
                state.sql_events.append({"sql": executed_sql, "status": str(result.get("status"))})
            yield AgentEvent("tool_result", {
                "tool": "query_database",
                "status": str(result.get("status")),
                "summary": f"返回 {len(rows)} 行" if rows else str(result.get("message") or "")[:120],
                "sql": executed_sql,
                "rows": rows[:20],
            })
        answer = format_fallback_answer(slots, rows)
        state.answer_parts.append(answer)
        state.degraded = True
        yield AgentEvent("answer_delta", {"text": answer})

    # ── 聚合结果 ──────────────────────────────────────────────────────────────

    def _done(self, question: str, state: "_TurnState") -> AgentEvent:
        references = []
        seen_keys = set()
        for item in state.rag_results:
            paper_path = item.get("paper_path")
            text = item.get("text")
            if not paper_path or not text:
                continue
            excerpt = _excerpt(text)
            key = (str(paper_path), excerpt)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            references.append({
                "paper_path": _to_relative_reference_path(paper_path),
                "source_title": item.get("source_title"),
                "text": excerpt,
                "paper_image": item.get("paper_image"),
                "score": item.get("score"),
            })
            if len(references) >= 5:
                break

        content = "".join(state.answer_parts).strip()
        needs_clarification = bool(state.clarification)
        display_context = build_display_context(question, state.executed_sql)

        status = "waiting" if needs_clarification else (
            "warn" if (state.degraded or any(e.get("status") not in {"success"} for e in state.sql_events)) else "pass"
        )

        result = {
            "question": question,
            "sql": ";\n".join(state.executed_sql) if state.executed_sql else "-",
            "answer": {
                "content": content or state.clarification,
                "image": list(state.images),
                "references": references,
            },
            "context": display_context,
            "chart_format": state.chart_formats[0] if state.chart_formats else "无",
            "chart_data": state.chart_data[0] if state.chart_data else None,
            "chart_data_list": list(state.chart_data),
            "needs_clarification": needs_clarification,
            "clarify_options": list(state.clarify_options),
            "validation": {
                "status": status,
                "mode": "chat",
                "degraded": state.degraded,
                "sql_events": state.sql_events,
                "quality_warnings": [],
            },
            "execution_plan": state.execution_plan(),
        }
        elapsed = time.monotonic() - state.started_at
        logger.info(
            "[turn] q={q!r} rounds={rounds} sql={sql} rag={rag} charts={charts} "
            "clarify={clarify} degraded={degraded} nudged={nudged} elapsed={elapsed:.1f}s",
            q=question[:60],
            rounds=state.rounds,
            sql=len(state.sql_events),
            rag=len(state.rag_results),
            charts=len(state.images),
            clarify=needs_clarification,
            degraded=state.degraded,
            nudged=state.nudged,
            elapsed=elapsed,
        )
        return AgentEvent("done", {"result": result})

    # ── 兼容入口：非流式一次性返回 ──────────────────────────────────────────────

    def process_chat(
        self,
        question: str,
        history: Optional[List[Dict[str, str]]] = None,
        session_id: str = "chat",
        chart_index: int = 1,
        **_compat_kwargs: Any,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for event in self.run(
            question,
            history=history,
            chart_prefix=f"chat_{re.sub(r'[^A-Za-z0-9_-]', '', str(session_id))[:24] or 'chat'}",
            chart_index=chart_index,
        ):
            if event.type == "done":
                result = event.data.get("result") or {}
            elif event.type == "error":
                result = {
                    "question": question,
                    "sql": "-",
                    "answer": {"content": str(event.data.get("message") or "处理失败"), "image": [], "references": []},
                    "context": {},
                    "chart_format": "无",
                    "needs_clarification": False,
                    "validation": {"status": "fail", "mode": "chat", "sql_events": [], "quality_warnings": []},
                    "execution_plan": [],
                }
        return result


class _TurnState:
    """单轮执行的累积状态。"""

    def __init__(self, chart_prefix: str, chart_index: int):
        self.chart_prefix = chart_prefix
        self.chart_index = chart_index
        self.started_at = time.monotonic()
        self.rounds = 0
        self.tools_used = False
        self.nudged = False
        self.degraded = False
        self.executed_sql: List[str] = []
        self.sql_rows: List[Dict[str, Any]] = []
        self.sql_events: List[Dict[str, Any]] = []
        self.rag_results: List[Dict[str, Any]] = []
        self.images: List[str] = []
        self.chart_data: List[Dict[str, Any]] = []
        self.chart_formats: List[str] = []
        self.answer_parts: List[str] = []
        self.clarification: str = ""
        self.clarify_options: List[str] = []
        self._steps: List[Dict[str, Any]] = []

    def execution_plan(self) -> List[Dict[str, Any]]:
        steps: List[Dict[str, Any]] = [
            {"step": 1, "label": "理解问题", "detail": "解析意图与所需数据", "status": "done"},
        ]
        idx = 2
        for event in self.sql_events:
            steps.append({
                "step": idx,
                "label": "SQL 查询",
                "detail": str(event.get("sql") or "")[:160],
                "status": "done" if event.get("status") == "success" else str(event.get("status") or "done"),
            })
            idx += 1
        if self.rag_results:
            steps.append({
                "step": idx,
                "label": "研报/年报检索",
                "detail": f"命中 {len(self.rag_results)} 个片段",
                "status": "done",
            })
            idx += 1
        for chart in self.chart_formats:
            steps.append({"step": idx, "label": "图表生成", "detail": chart, "status": "done"})
            idx += 1
        if self.clarification:
            steps.append({"step": idx, "label": "等待澄清", "detail": self.clarification[:120], "status": "waiting"})
        else:
            steps.append({"step": idx, "label": "回答合成", "detail": "生成最终结论", "status": "done"})
        return steps


def _looks_like_done(content: str) -> bool:
    compact = re.sub(r"[\s。.!！]", "", str(content or "")).upper()
    return compact in {"DONE", "OK", "完成", "READY"} or len(compact) <= 6


_FINANCIAL_FIGURE_PATTERN = re.compile(
    r"\d+(?:[,，]\d{3})*(?:\.\d+)?\s*(?:亿元|亿|万元|万|百万|元|%|％)"
)


def _contains_financial_figures(content: str) -> bool:
    """粗判回答是否携带财务数字（金额/百分比），用于数据溯源防护。"""
    return bool(_FINANCIAL_FIGURE_PATTERN.search(str(content or "")))


def _context_from_history(history: Optional[List[Dict[str, str]]]) -> Dict[str, Any]:
    """从最近的用户消息里粗提上下文，仅用于兜底路径。"""
    context: Dict[str, Any] = {}
    for msg in reversed(history or []):
        if msg.get("role") != "user":
            continue
        slots = extract_slots(str(msg.get("content") or ""))
        for key, ctx_key in [("company", "company"), ("year", "report_year"), ("period", "report_period"), ("metric_field", "metric_field")]:
            if slots.get(key) and ctx_key not in context:
                context[ctx_key] = slots[key]
        if {"company", "report_year"}.issubset(context.keys()):
            break
    return context
