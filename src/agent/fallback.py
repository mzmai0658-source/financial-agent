"""
规则兜底：LLM 不可用时的降级查询路径。

仅覆盖最常见的"公司 + 年份/期间 + 指标"型查询；
同时提供轻量槽位抽取，供上下文标签（前端展示）复用。
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .domain import (
    COMPANY_CODE_MAP,
    CODE_TO_NAME_MAP,
    FIELD_LABEL_MAP,
    KEYWORD_FIELD_MAP,
    field_table,
)

_TREND_MARKERS = ["趋势", "走势", "变化", "历年", "逐年", "近几年", "最近几年", "近三年", "近5年", "近五年"]
_RANKING_MARKERS = ["排名", "最高", "最低", "前十", "前10", "前五", "前5", "top", "TOP"]


def extract_slots(question: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """轻量槽位抽取：公司、年份、报告期、指标字段。"""
    q = str(question or "")
    context = context or {}

    company = None
    for code in re.findall(r"(?<!\d)(\d{6})(?!\d)", q):
        if code in CODE_TO_NAME_MAP:
            company = CODE_TO_NAME_MAP[code]
            break
    if company is None:
        for name in sorted(COMPANY_CODE_MAP, key=len, reverse=True):
            if name in q:
                company = name
                break
    company = company or context.get("company")

    year = None
    m_year = re.search(r"(?<!\d)(20\d{2})(?!\d)", q)
    if m_year:
        year = int(m_year.group(1))
    else:
        current_year = datetime.now().year
        if "今年" in q:
            year = current_year
        elif "去年" in q:
            year = current_year - 1
        elif "前年" in q:
            year = current_year - 2
    year = year if year is not None else context.get("report_year")

    period = None
    if any(k in q for k in ["一季度", "第一季度"]) or re.search(r"\bQ1\b", q, re.IGNORECASE):
        period = "Q1"
    elif any(k in q for k in ["半年度", "中期", "上半年", "半年报", "中报"]):
        period = "HY"
    elif any(k in q for k in ["三季度", "第三季度", "前三季度"]) or re.search(r"\bQ3\b", q, re.IGNORECASE):
        period = "Q3"
    elif any(k in q for k in ["年度", "年报", "全年"]) or re.search(r"\bFY\b", q, re.IGNORECASE):
        period = "FY"
    period = period or context.get("report_period")

    metric_field = None
    metric_keyword = None
    for keyword, field in sorted(KEYWORD_FIELD_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        if keyword in q:
            metric_field = field
            metric_keyword = keyword
            break
    metric_field = metric_field or context.get("metric_field")
    metric_keyword = metric_keyword or context.get("metric_keyword") or FIELD_LABEL_MAP.get(metric_field or "")

    return {
        "company": company,
        "stock_code": COMPANY_CODE_MAP.get(company or ""),
        "year": year,
        "period": period,
        "metric_field": metric_field,
        "metric_keyword": metric_keyword,
        "trend": any(k in q for k in _TREND_MARKERS),
        "ranking": any(k in q for k in _RANKING_MARKERS),
    }


def build_display_context(question: str, executed_sql: List[str], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成仅供前端展示的上下文标签（公司/年份/期间/指标）。"""
    slots = extract_slots(question, previous)
    sql_text = "\n".join(executed_sql or [])

    if not slots.get("company"):
        for code_match in re.findall(r"stock_code\s*(?:=|IN\s*\()\s*'?(\d{6})", sql_text):
            name = CODE_TO_NAME_MAP.get(code_match)
            if name:
                slots["company"] = name
                slots["stock_code"] = code_match
                break
    if not slots.get("year"):
        m = re.search(r"report_year\s*=\s*(20\d{2})", sql_text)
        if m:
            slots["year"] = int(m.group(1))
    if not slots.get("period"):
        m = re.search(r"report_period\s*=\s*'(FY|Q1|HY|Q3)'", sql_text)
        if m:
            slots["period"] = m.group(1)
    if not slots.get("metric_field"):
        for field in FIELD_LABEL_MAP:
            if re.search(rf"\b{field}\b", sql_text):
                slots["metric_field"] = field
                slots["metric_keyword"] = FIELD_LABEL_MAP[field]
                break

    context: Dict[str, Any] = {}
    if slots.get("company"):
        context["company"] = slots["company"]
    if slots.get("year"):
        context["report_year"] = slots["year"]
    if slots.get("period"):
        context["report_period"] = slots["period"]
    if slots.get("metric_field"):
        context["metric_field"] = slots["metric_field"]
    if slots.get("metric_keyword"):
        context["metric_keyword"] = slots["metric_keyword"]
    return context


def build_fallback_sql(slots: Dict[str, Any]) -> Optional[str]:
    """根据槽位构建模板 SQL；信息不足时返回 None。"""
    metric_field = slots.get("metric_field")
    if not metric_field:
        return None
    table = field_table(metric_field)
    conditions = ["1=1"]
    if slots.get("stock_code"):
        conditions.append(f"stock_code='{slots['stock_code']}'")
    if slots.get("year"):
        conditions.append(f"report_year={int(slots['year'])}")
    if slots.get("period"):
        conditions.append(f"report_period='{slots['period']}'")
    elif slots.get("year") and not slots.get("trend"):
        conditions.append("report_period='FY'")

    if not slots.get("stock_code") and not slots.get("ranking"):
        return None

    order = (
        f"ORDER BY {metric_field} DESC LIMIT 10"
        if slots.get("ranking")
        else "ORDER BY report_year, FIELD(report_period,'Q1','HY','Q3','FY') LIMIT 50"
    )
    return (
        f"SELECT stock_code, stock_abbr, report_year, report_period, {metric_field} "
        f"FROM {table} WHERE {' AND '.join(conditions)} {order}"
    )


def format_fallback_answer(slots: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    """模板化兜底回答。"""
    label = slots.get("metric_keyword") or FIELD_LABEL_MAP.get(str(slots.get("metric_field") or ""), "该指标")
    if not rows:
        return (
            "当前智能分析服务暂不可用，且数据库中未查到匹配记录。"
            "请确认公司名称、年份与报告期后重试。"
        )
    lines = [f"（智能分析服务暂不可用，以下为数据库直查结果）"]
    field = str(slots.get("metric_field") or "")
    for row in rows[:10]:
        value = row.get(field)
        try:
            value_text = f"{float(value):,.2f}"
        except (TypeError, ValueError):
            value_text = str(value)
        period = str(row.get("report_period") or "")
        lines.append(
            f"{row.get('stock_abbr') or row.get('stock_code')} {row.get('report_year')}年{period}：{label} {value_text}"
        )
    return "\n".join(lines)
