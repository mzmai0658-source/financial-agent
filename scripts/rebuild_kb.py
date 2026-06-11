"""
清空并重建 RAG 知识库（Chroma）。

用法（项目根目录执行）：
    .venv\\Scripts\\python.exe scripts\\rebuild_kb.py
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.etl.rag_builder import build_knowledge_base, reset_chroma_db

print("=" * 60)
print("清空知识库...")
reset_chroma_db()
print("知识库已清空")

print("=" * 60)
print("开始构建知识库...")
build_knowledge_base(include_research_reports=True, include_financial_mda=True)
print("完成")
