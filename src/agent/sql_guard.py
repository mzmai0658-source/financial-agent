"""
SQL 只读防护：LLM 生成的 SQL 在执行前必须通过本模块校验。

规则：
  1. 单条语句，仅允许 SELECT / WITH 开头
  2. 禁止任何写操作与危险关键字
  3. FROM / JOIN 引用的表必须在白名单内
  4. 强制 LIMIT（缺失时追加，超限时收紧）
"""

import re
from typing import Optional, Tuple

from .domain import ALLOWED_TABLES

DEFAULT_LIMIT = 50
MAX_LIMIT = 500

_DANGEROUS_PATTERN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|merge|"
    r"call|grant|revoke|load|outfile|dumpfile|set|use|lock|unlock|"
    r"information_schema|performance_schema|mysql|sys)\b",
    re.IGNORECASE,
)

_TABLE_REF_PATTERN = re.compile(r"\b(?:from|join)\s+`?(\w+)`?", re.IGNORECASE)

_LIMIT_PATTERN = re.compile(r"\blimit\s+(\d+)(?:\s*,\s*\d+|\s+offset\s+\d+)?\s*$", re.IGNORECASE)


def normalize_readonly_sql(sql: str) -> Tuple[Optional[str], str]:
    """
    校验并规整 SQL。

    返回 (clean_sql, reason)：通过时 clean_sql 非空、reason 为 ""；
    不通过时 clean_sql 为 None、reason 描述拒绝原因（可回传给 LLM 自纠）。
    """
    cleaned = str(sql or "").strip()
    if not cleaned:
        return None, "SQL 为空"

    cleaned = re.sub(r"/\*.*?\*/", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"--.*?$", " ", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"```\w*", " ", cleaned)
    cleaned = cleaned.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if ";" in cleaned:
        return None, "只允许单条 SQL 语句"

    lowered = re.sub(r"\s+", " ", cleaned).lower()
    if not (lowered.startswith("select ") or lowered.startswith("with ")):
        return None, "只允许 SELECT / WITH 查询"

    if _DANGEROUS_PATTERN.search(lowered):
        return None, "包含被禁止的关键字（仅允许只读查询业务表）"

    tables = {match.lower() for match in _TABLE_REF_PATTERN.findall(cleaned)}
    # WITH 子句定义的 CTE 名允许被 FROM 引用
    cte_names = {
        m.group(1).lower()
        for m in re.finditer(r"(?:\bwith\s+|,\s*)`?(\w+)`?\s+as\s*\(", cleaned, re.IGNORECASE)
    }
    unknown = tables - set(ALLOWED_TABLES) - cte_names
    if unknown:
        return None, f"引用了不存在的表: {sorted(unknown)}。可用表: {sorted(ALLOWED_TABLES)}"

    limit_match = _LIMIT_PATTERN.search(cleaned)
    if limit_match:
        if int(limit_match.group(1)) > MAX_LIMIT:
            cleaned = _LIMIT_PATTERN.sub(f"LIMIT {MAX_LIMIT}", cleaned)
    else:
        cleaned = f"{cleaned} LIMIT {DEFAULT_LIMIT}"

    return cleaned, ""
