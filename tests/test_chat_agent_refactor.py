# -*- coding: utf-8 -*-
"""
Agent 行为契约测试（离线，FakeLLM 驱动，不依赖真实 LLM / MySQL / Chroma）。

覆盖：
  - sql_guard 只读防护
  - fallback 槽位抽取与模板 SQL
  - orchestrator 事件流：正常链路 / 澄清 / 闲聊 / 规则兜底 / 多轮历史
  - API 层：/api/chat/query、/api/chat/stream（SSE）、会话持久化
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.agent.fallback import build_fallback_sql, extract_slots
from src.agent.orchestrator import FinancialReportAgent
from src.agent.sql_guard import normalize_readonly_sql


# ── Fakes ─────────────────────────────────────────────────────────────────────

class FakeLLM:
    """先调用 query_database，再回复 DONE 进入合成阶段。"""

    def __init__(self, sql: str = "SELECT stock_code, stock_abbr, report_year, report_period, net_profit "
                                  "FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'"):
        self.sql = sql
        self.round = 0
        self.captured_messages: List[List[Dict[str, Any]]] = []

    def chat_with_tools(self, messages, tools, **kwargs):
        self.captured_messages.append(list(messages))
        self.round += 1
        if self.round == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "query_database",
                        "arguments": json.dumps({"sql": self.sql, "purpose": "查询净利润"}),
                    },
                }],
            }
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "最终回答："
        yield "净利润为 94.47 亿元。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class ChitchatLLM:
    def chat_with_tools(self, messages, tools, **kwargs):
        return {"content": "你好，我是财报分析助手，可以帮你查询财报数据。", "tool_calls": None}


class MemoryAnswerLLM:
    """模拟"凭记忆背数字"：首轮直接给含财务数字的回答；被纠正后改为查库。"""

    def __init__(self):
        self.round = 0
        self.saw_nudge = False

    def chat_with_tools(self, messages, tools, **kwargs):
        self.round += 1
        if any("（系统提示）" in str(m.get("content") or "") for m in messages if m.get("role") == "user"):
            self.saw_nudge = True
        if not self.saw_nudge:
            return {"content": "药明康德2024年净利润为94.50亿元，同比下降1.63%。", "tool_calls": None}
        if self.round <= 2:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "query_database",
                        "arguments": json.dumps({
                            "sql": "SELECT net_profit FROM income_sheet WHERE stock_code='603259' AND report_year=2024 AND report_period='FY'",
                            "purpose": "核实净利润",
                        }),
                    },
                }],
            }
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "经核实，净利润为 94.47 亿元。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class BadChartArgsLLM:
    """模拟图表参数错误（y_data 含 null）：收到工具报错后结束本轮。"""

    def __init__(self):
        self.round = 0
        self.error_feedback = ""

    def chat_with_tools(self, messages, tools, **kwargs):
        self.round += 1
        if self.round == 1:
            return {
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "function": {
                        "name": "render_chart",
                        "arguments": json.dumps({
                            "chart_type": "line",
                            "title": "净利润趋势",
                            "x_data": ["2022", "2023", "2024"],
                            "y_data": [881400.0, None, 945000.0],
                        }),
                    },
                }],
            }
        for m in messages:
            if m.get("role") == "tool":
                self.error_feedback = str(m.get("content") or "")
        return {"content": "DONE", "tool_calls": None}

    def chat_stream(self, messages, **kwargs) -> Iterator[str]:
        yield "图表数据存在缺失期。"

    def chat(self, messages, **kwargs) -> Optional[str]:
        return None


class ClarifyLLM:
    def chat_with_tools(self, messages, tools, **kwargs):
        return {
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "function": {
                    "name": "ask_clarification",
                    "arguments": json.dumps({"question": "请问你要查询哪一年的数据？", "options": ["2023年", "2024年", "2025年"]}),
                },
            }],
        }


class DeadLLM:
    """模拟 LLM 完全不可用。"""

    def chat_with_tools(self, messages, tools, **kwargs):
        return None


class FakeSQLTool:
    def __init__(self):
        self.executed: List[str] = []

    def run(self, sql: str) -> Dict[str, Any]:
        self.executed.append(sql)
        return {
            "status": "success",
            "sql": sql,
            "row_count": 1,
            "columns": ["stock_abbr", "report_year", "report_period", "net_profit"],
            "rows": [{"stock_abbr": "药明康德", "report_year": 2024, "report_period": "FY", "net_profit": 944718.0}],
        }


def collect_events(agent: FinancialReportAgent, question: str, history=None):
    events = list(agent.run(question, history=history))
    types = [event.type for event in events]
    done = next((event for event in events if event.type == "done"), None)
    assert done is not None, f"missing done event, got {types}"
    return events, types, done.data["result"]


# ── sql_guard ─────────────────────────────────────────────────────────────────

class TestSqlGuard:
    def test_select_passes_and_gets_limit(self):
        clean, reason = normalize_readonly_sql("SELECT * FROM income_sheet WHERE report_year=2024")
        assert clean is not None and reason == ""
        assert clean.endswith("LIMIT 50")

    def test_oversized_limit_is_tightened(self):
        clean, _ = normalize_readonly_sql("SELECT net_profit FROM income_sheet LIMIT 99999")
        assert clean is not None
        assert "LIMIT 500" in clean

    @pytest.mark.parametrize("bad_sql", [
        "DROP TABLE income_sheet",
        "UPDATE income_sheet SET net_profit=0",
        "DELETE FROM income_sheet",
        "SELECT 1; SELECT 2",
        "SELECT * FROM information_schema.tables",
        "INSERT INTO income_sheet VALUES (1)",
    ])
    def test_write_and_multi_statement_rejected(self, bad_sql):
        clean, reason = normalize_readonly_sql(bad_sql)
        assert clean is None
        assert reason

    def test_unknown_table_rejected(self):
        clean, reason = normalize_readonly_sql("SELECT * FROM users")
        assert clean is None
        assert "users" in reason

    def test_cte_allowed(self):
        clean, _ = normalize_readonly_sql(
            "WITH t AS (SELECT stock_code FROM income_sheet) SELECT * FROM t"
        )
        assert clean is not None


# ── fallback ──────────────────────────────────────────────────────────────────

class TestFallback:
    def test_extract_slots_company_year_metric(self):
        slots = extract_slots("药明康德2024年净利润是多少")
        assert slots["company"] == "药明康德"
        assert slots["stock_code"] == "603259"
        assert slots["year"] == 2024
        assert slots["metric_field"] == "net_profit"

    def test_extract_slots_period(self):
        assert extract_slots("药明康德2025年三季度营业收入")["period"] == "Q3"
        assert extract_slots("药明康德2025年上半年营业收入")["period"] == "HY"

    def test_build_sql_requires_metric(self):
        assert build_fallback_sql({"metric_field": None}) is None

    def test_build_sql_single_value(self):
        slots = extract_slots("药明康德2024年净利润是多少")
        sql = build_fallback_sql(slots)
        assert sql is not None
        assert "income_sheet" in sql
        assert "stock_code='603259'" in sql
        assert "report_year=2024" in sql
        clean, reason = normalize_readonly_sql(sql)
        assert clean is not None, f"fallback SQL must pass guard: {reason}"

    def test_context_inheritance(self):
        slots = extract_slots("那现金流呢", {"company": "药明康德", "report_year": 2024})
        assert slots["company"] == "药明康德"
        assert slots["year"] == 2024
        assert slots["metric_field"] == "net_cash_flow"


# ── orchestrator 行为 ─────────────────────────────────────────────────────────

class TestOrchestrator:
    def make_agent(self, llm) -> FinancialReportAgent:
        agent = FinancialReportAgent(llm=llm)
        agent.sql_tool = FakeSQLTool()
        return agent

    def test_normal_flow_emits_tool_and_answer_events(self):
        agent = self.make_agent(FakeLLM())
        events, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert "tool_call" in types
        assert "tool_result" in types
        assert "answer_delta" in types
        assert result["answer"]["content"] == "最终回答：净利润为 94.47 亿元。"
        assert result["sql"] != "-"
        assert result["needs_clarification"] is False
        assert result["validation"]["status"] == "pass"

    def test_execution_plan_recorded(self):
        agent = self.make_agent(FakeLLM())
        _, _, result = collect_events(agent, "药明康德2024年净利润是多少")
        labels = [step["label"] for step in result["execution_plan"]]
        assert "理解问题" in labels
        assert "SQL 查询" in labels
        assert "回答合成" in labels

    def test_display_context_extracted(self):
        agent = self.make_agent(FakeLLM())
        _, _, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert result["context"].get("company") == "药明康德"
        assert result["context"].get("report_year") == 2024

    def test_clarify_flow(self):
        agent = self.make_agent(ClarifyLLM())
        _, types, result = collect_events(agent, "净利润是多少")
        assert "clarify" in types
        assert result["needs_clarification"] is True
        assert result["clarify_options"] == ["2023年", "2024年", "2025年"]
        assert result["validation"]["status"] == "waiting"

    def test_chitchat_answers_without_tools(self):
        agent = self.make_agent(ChitchatLLM())
        _, types, result = collect_events(agent, "你好")
        assert "tool_call" not in types
        assert "财报" in result["answer"]["content"]
        assert result["sql"] == "-"

    def test_memory_answer_is_rejected_and_forced_to_query(self):
        """无工具调用却输出财务数字 → 必须被退回并强制查库。"""
        llm = MemoryAnswerLLM()
        agent = self.make_agent(llm)
        _, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert llm.saw_nudge, "应注入纠正消息强制查库"
        assert "tool_call" in types, "纠正后必须发生工具调用"
        assert result["sql"] != "-", "最终结果必须有 SQL 证据"
        assert agent.sql_tool.executed, "数据库必须被真实查询"
        assert "核实" in result["answer"]["content"]

    def test_tool_argument_error_does_not_abort_turn(self):
        """工具参数异常（如 y_data 含 null）应转为错误结果回给 LLM 自纠，而不是炸掉整轮。"""
        llm = BadChartArgsLLM()
        agent = self.make_agent(llm)
        events, types, result = collect_events(agent, "画一下药明康德近三年净利润趋势")
        assert "error" not in types, "工具参数异常不应升级为整轮 error"
        error_results = [
            e for e in events
            if e.type == "tool_result" and e.data.get("status") == "error"
        ]
        assert error_results, "应产生 error 状态的 tool_result 事件"
        assert "y_data" in llm.error_feedback, "LLM 应收到可自纠的报错信息"
        assert result["answer"]["content"] == "图表数据存在缺失期。"

    def test_llm_unavailable_falls_back_to_rules(self):
        agent = self.make_agent(DeadLLM())
        _, types, result = collect_events(agent, "药明康德2024年净利润是多少")
        assert "answer_delta" in types
        assert result["validation"]["degraded"] is True
        assert "药明康德" in result["answer"]["content"]
        assert agent.sql_tool.executed, "fallback should query database directly"

    def test_history_is_passed_to_llm(self):
        llm = FakeLLM()
        agent = self.make_agent(llm)
        history = [
            {"role": "user", "content": "药明康德2024年净利润是多少"},
            {"role": "assistant", "content": "净利润为 94.47 亿元。"},
        ]
        collect_events(agent, "那营业收入呢", history=history)
        first_call_messages = llm.captured_messages[0]
        contents = [str(m.get("content")) for m in first_call_messages]
        assert any("药明康德2024年净利润是多少" in c for c in contents), "历史用户消息必须传给 LLM"
        assert any("94.47" in c for c in contents), "历史助手消息必须传给 LLM"
        assert first_call_messages[0]["role"] == "system"
        assert first_call_messages[-1]["content"] == "那营业收入呢"

    def test_empty_question_yields_error(self):
        agent = self.make_agent(FakeLLM())
        events = list(agent.run("  "))
        assert events[0].type == "error"


# ── API 层 ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def api_client(monkeypatch):
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    store_path = Path(tempfile.gettempdir()) / f"chat_sessions_test_{uuid.uuid4().hex}.json"
    monkeypatch.setenv("CHAT_SESSION_STORE_PATH", str(store_path))

    import src.api.main as api_main
    from src.api.session_store import SessionStore

    api_main._session_store = SessionStore(persist_path=store_path)
    agent = FinancialReportAgent(llm=FakeLLM())
    agent.sql_tool = FakeSQLTool()
    api_main._agent_instance = agent

    client = fastapi_testclient.TestClient(api_main.app)
    yield client
    if store_path.exists():
        os.remove(store_path)


class TestApi:
    def test_chat_query_contract(self, api_client):
        resp = api_client.post("/api/chat/query", json={"question": "药明康德2024年净利润是多少"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["answer"]["content"]
        assert body["sql"] != "-"
        assert body["session_id"]
        assert body["needs_clarification"] is False
        assert isinstance(body["execution_plan"], list)
        assert len(body["messages"]) == 2

    def test_chat_stream_sse_event_order(self, api_client):
        with api_client.stream("POST", "/api/chat/stream", json={"question": "药明康德2024年净利润是多少"}) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            raw = "".join(resp.iter_text())

        events = []
        for block in raw.strip().split("\n\n"):
            lines = block.split("\n")
            etype = next((l[len("event: "):] for l in lines if l.startswith("event: ")), "")
            data = next((l[len("data: "):] for l in lines if l.startswith("data: ")), "{}")
            events.append((etype, json.loads(data)))

        types = [item[0] for item in events]
        assert types[0] == "session"
        assert "tool_call" in types
        assert "tool_result" in types
        assert "answer_delta" in types
        assert types[-1] == "done"

        done_payload = events[-1][1]
        assert done_payload["answer"]["content"]
        assert done_payload["session_id"]

    def test_multi_turn_session_history(self, api_client):
        first = api_client.post("/api/chat/query", json={"question": "药明康德2024年净利润是多少"}).json()
        session_id = first["session_id"]
        second = api_client.post(
            "/api/chat/query", json={"session_id": session_id, "question": "那营业收入呢"}
        ).json()
        assert second["session_id"] == session_id
        assert len(second["messages"]) == 4

        session = api_client.get(f"/api/sessions/{session_id}").json()
        assert len(session["messages"]) == 4
        assert session["messages"][0]["content"] == "药明康德2024年净利润是多少"

    def test_session_lifecycle(self, api_client):
        created = api_client.post("/api/sessions", json={}).json()
        sid = created["session_id"]
        assert api_client.get(f"/api/sessions/{sid}").status_code == 200
        assert api_client.delete(f"/api/sessions/{sid}").json() == {"ok": True}
        assert api_client.get(f"/api/sessions/{sid}").status_code == 404

    def test_examples_endpoint(self, api_client):
        body = api_client.get("/api/examples").json()
        assert len(body["examples"]) >= 4
