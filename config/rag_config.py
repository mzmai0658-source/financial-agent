from pathlib import Path
from typing import Any, List


# Semantic chunking
RAG_SEMANTIC_CHUNK_SIZE = 900
RAG_SEMANTIC_CHUNK_OVERLAP = 120

# Retrieval sizing
RAG_TOP_K_EXPLANATORY = 10
RAG_TOP_K_LIST = 8
RAG_TOP_K_DEFAULT = 5
RAG_RESULT_LIMIT_EXPLANATORY = 8
RAG_VECTOR_TOPK_MULTIPLIER = 2
RAG_MAX_CONTEXT_CHUNKS = 8

# Attribution / references
RAG_MAX_REASON_SENTENCE_SOURCES = 8
RAG_MAX_REFERENCE_SOURCE_ITEMS = 5
RAG_MAX_SUBTASK_REFERENCES = 4
RAG_MAX_FINAL_REFERENCES = 5

# Supplemental retrieval for explanatory questions
RAG_SECOND_PASS_TOP_K = 6
RAG_SECOND_PASS_SCORE_RATIO = 0.8
RAG_ANNUAL_SUPPLEMENT_TOP_K = 8
RAG_SUPPLEMENTAL_REASON_HINTS: List[str] = [
    "原因",
    "变动原因",
    "经营情况",
    "讨论与分析",
]
RAG_SUPPLEMENTAL_BUSINESS_HINTS: List[str] = [
    "业务",
    "板块",
    "产品",
    "品类",
    "渠道",
    "并表",
    "协同",
]


def build_source_title(path_value: Any) -> str:
    raw = str(path_value or "").strip()
    if not raw:
        return "来源"
    try:
        return Path(raw).stem or "来源"
    except Exception:
        return raw
