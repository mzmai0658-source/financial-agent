# -*- coding: utf-8 -*-
"""
真实 LLM 端到端冒烟脚本（需要 DEEPSEEK_API_KEY，以及可用的 MySQL/Chroma）。

用法：
  python scripts/smoke_chat.py "药明康德2024年净利润是多少"
  python scripts/smoke_chat.py            # 跑默认问题集
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from src.agent.orchestrator import FinancialReportAgent

DEFAULT_QUESTIONS = [
    "药明康德2024年净利润是多少",
    "凯莱英近三年营业收入趋势如何？请画图",
    "这个公司的利润怎么样",  # 应触发澄清
    "你好",  # 闲聊，不应调用工具
]


def run_one(agent: FinancialReportAgent, question: str) -> None:
    print("=" * 72)
    print(f"Q: {question}")
    print("-" * 72)
    answer_started = False
    for event in agent.run(question):
        if event.type == "plan":
            print(f"  [plan] {event.data.get('label')}: {event.data.get('detail', '')}")
        elif event.type == "tool_call":
            print(f"  [tool→] {event.data.get('tool')} | {event.data.get('label')} | {str(event.data.get('detail', ''))[:100]}")
        elif event.type == "tool_result":
            print(f"  [tool✓] {event.data.get('tool')} | {event.data.get('status')} | {event.data.get('summary', '')}")
        elif event.type == "answer_delta":
            if not answer_started:
                print("  [answer] ", end="", flush=True)
                answer_started = True
            print(event.data.get("text", ""), end="", flush=True)
        elif event.type == "chart":
            print(f"\n  [chart] {event.data.get('path')}")
        elif event.type == "clarify":
            print(f"  [clarify] {event.data.get('question')} 选项: {event.data.get('options')}")
        elif event.type == "error":
            print(f"  [error] {event.data.get('message')}")
        elif event.type == "done":
            result = event.data.get("result") or {}
            print()
            print(f"  [done] status={result.get('validation', {}).get('status')} "
                  f"sql={'有' if result.get('sql') != '-' else '无'} "
                  f"图表={len(result.get('answer', {}).get('image') or [])} "
                  f"引用={len(result.get('answer', {}).get('references') or [])}")


def main() -> None:
    questions = sys.argv[1:] or DEFAULT_QUESTIONS
    agent = FinancialReportAgent()
    for question in questions:
        run_one(agent, question)


if __name__ == "__main__":
    main()
