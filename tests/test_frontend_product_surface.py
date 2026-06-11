# -*- coding: utf-8 -*-
"""
前端产品形态检查：chat-first 布局、流式接入、设计系统与证据栏的结构性约定。
（静态检查源码结构，不运行浏览器。）
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

FRONTEND = ROOT_DIR / "frontend" / "src"


def read(relative_path: str) -> str:
    return (FRONTEND / relative_path).read_text(encoding="utf-8")


class TestDesignSystem:
    def test_design_tokens_exist(self):
        tokens = read("design/tokens.css")
        assert "--c-primary" in tokens
        assert "--rail-right-expanded" in tokens
        assert "--chat-max-w" in tokens

    def test_base_ui_components_exist(self):
        for name in ("BaseButton.vue", "BaseTag.vue", "BaseSpinner.vue", "BaseEmpty.vue", "icons.ts"):
            assert (FRONTEND / "components" / "ui" / name).exists(), f"components/ui/{name} 缺失"

    def test_styles_import_tokens(self):
        assert "design/tokens.css" in read("styles.css")


class TestChatFirstLayout:
    def test_workspace_uses_chat_components(self):
        workspace = read("pages/WorkspacePage.vue")
        assert "ChatMessage" in workspace
        assert "ChatInput" in workspace
        assert "EvidenceRail" in workspace, "右侧常驻证据栏必须存在"

    def test_message_inline_evidence(self):
        message = read("components/chat/ChatMessage.vue")
        assert "SqlCard" in message, "SQL 卡片应内联在消息流中"
        assert "ChartCard" in message
        assert "ReferenceCards" in message
        assert "ThinkingTrail" in message
        assert "ClarifyOptions" in message

    def test_evidence_rail_collapsible_with_tabs(self):
        rail = read("components/EvidenceRail.vue")
        assert "railExpanded" in rail, "证据栏必须支持收起/展开"
        for tab in ("sql", "execution", "chart", "refs"):
            assert f"'{tab}'" in rail or f'"{tab}"' in rail, f"证据栏缺少 {tab} 标签页"

    def test_home_page_is_chat_entry(self):
        home = read("pages/HomePage.vue")
        assert "ChatInput" in home
        assert "/workspace" in home


class TestStreaming:
    def test_api_service_has_stream_chat(self):
        api = read("services/api.ts")
        assert "streamChat" in api
        assert "/api/chat/stream" in api
        assert "text/event-stream" in api or "ReadableStream" in api or "getReader" in api

    def test_session_store_consumes_stream(self):
        store = read("stores/session.ts")
        assert "streamChat" in store
        assert "onAnswerDelta" in store
        assert "onToolCall" in store
        assert "onClarify" in store
        assert "AbortController" in store, "必须支持停止生成"

    def test_chat_input_supports_stop(self):
        chat_input = read("components/chat/ChatInput.vue")
        assert "stop" in chat_input
        assert "streaming" in chat_input


class TestProductCopy:
    def test_zh_finance_copy_present(self):
        home = read("pages/HomePage.vue")
        assert "财报" in home

    def test_no_demo_placeholder_copy(self):
        for page in ("pages/HomePage.vue", "pages/WorkspacePage.vue"):
            text = read(page)
            for banned in ("TODO", "lorem", "占位"):
                assert banned not in text, f"{page} 包含占位文案 {banned}"
