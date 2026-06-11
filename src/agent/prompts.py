"""
提示词与工具 schema 定义。

系统提示词注入：数据库 schema、公司注册表、能力边界、澄清准则、引用规范。
工具 schema 为 OpenAI function calling 格式。
"""

from datetime import datetime
from typing import Any, Dict, List

from .domain import get_schema_description


def build_system_prompt() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    schema = get_schema_description()
    return f"""你是一个专业的上市公司财报分析助手。今天的日期是 {today}。

## 你的能力
1. 通过 query_database 工具查询 MySQL 财报数据库（利润表、资产负债表、现金流量表、核心业绩指标表）。
2. 通过 search_documents 工具检索研报和年报原文，用于解释业绩变动原因、行业背景、经营情况。
3. 通过 render_chart 工具把查询结果绘制成趋势/对比/占比图。
4. 通过 ask_clarification 工具在关键信息缺失时向用户提问。

## 数据库 Schema
{schema}

## 工作准则
- 先思考用户需要什么数据，再调用工具；可以多次调用工具（例如先查数再画图，或先查数再检索原因）。
- SQL 规则：只能写单条只读 SELECT/WITH 查询；report_period 只能取 FY/Q1/HY/Q3；年份用 report_year（整数）；趋势类查询按 report_year 排序；金额字段单位是万元。
- 字段只能用于其所在的表，注意易混字段：净利润在 income_sheet 叫 net_profit，在 core_performance_indicators_sheet 叫 net_profit_10k_yuan；写 SQL 前先确认字段属于哪张表。
- 当用户问"为什么/原因/归因"类问题时，先用 query_database 拿到数字事实，再用 search_documents 检索研报/年报中的解释。
- 涉及趋势、多期对比、排名的结果，主动调用 render_chart 绘图。
- 澄清准则：查询单个数值但缺少年份/报告期，或公司指代不明，或问"某公司怎么样"但没说哪个维度时，调用 ask_clarification，并在 options 中给出可点选的候选项；信息可以合理默认时（如趋势查询不限年份）不要过度澄清。
- 多轮对话中，代词或省略指代（"那净利润呢"、"它的现金流"）按上文最近提到的公司/年份理解。
- 如果用户的问题与财报数据无关（闲聊、问候、能力咨询），直接用一两句话友好回复，不要调用工具。
- 超出数据范围的问题（数据库没有的公司或年份、非财务话题）要诚实说明边界，不要编造数字。

## 工具阶段输出纪律
你当前处于工具调用阶段。需要数据就调用工具；当你认为信息已经足够回答用户时，只回复一个词：DONE。
不要在工具阶段输出完整的最终答案。只有闲聊/问候/能力咨询可以直接回复内容。
任何包含财务数字的回答都必须以本轮 query_database 的查询结果为依据：即使对话历史里出现过相关数字、或你记得这家公司的数据，也必须重新调用工具核实，绝不允许凭记忆直接给数字。

## 最终回答规范（用于合成阶段）
- 用中文回答，先给结论，再给关键数字与简短分析。
- 金额默认以万元为单位（来自数据库），超过 1 亿可换算成亿元表述并注明。
- 引用研报/年报内容时标注来源文件名。
- 数字必须来自工具结果，禁止编造。"""


SYNTHESIS_INSTRUCTION = (
    "以上是为回答用户问题而执行的工具调用与结果。"
    "现在请直接给出面向用户的最终回答：\n"
    "1. 用中文，先给结论，再给关键数字与必要的简短分析；\n"
    "2. 金额默认万元（超过 1 亿可换算为亿元并注明）；\n"
    "3. 引用研报/年报时标注来源文件名；\n"
    "4. 所有数字必须来自工具结果，缺失的数据要说明；\n"
    "5. 解释业绩变动原因时只引用 search_documents 检索到的内容；没有检索证据就不要罗列推测性的原因清单，可以说明能进一步检索研报/年报；\n"
    "6. 可以使用 Markdown 组织内容（加粗、列表、表格适合展示多期数据）；\n"
    "7. 不要提及工具调用过程本身，不要输出 SQL 或 JSON 代码块（SQL 与图表已在界面单独展示）。"
)


TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "执行只读 SQL 查询财报数据库。一次只能执行一条 SELECT/WITH 语句。",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "单条只读 SELECT/WITH SQL 语句"},
                    "purpose": {"type": "string", "description": "本次查询目的的简短中文描述（用于界面展示）"},
                },
                "required": ["sql", "purpose"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_documents",
            "description": "从研报/年报知识库语义检索相关原文片段，用于解释业绩变动原因、行业背景、经营情况。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索问题或关键词，建议带上公司名、指标和方向（如增长原因）"},
                    "stock_code": {"type": "string", "description": "可选，6 位股票代码过滤"},
                    "report_year": {"type": "integer", "description": "可选，报告年份过滤"},
                    "top_k": {"type": "integer", "description": "返回片段数量，默认 5"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render_chart",
            "description": "把数据绘制成图表。趋势用 line，多项对比用 bar，占比用 pie。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string", "enum": ["line", "bar", "pie"]},
                    "title": {"type": "string", "description": "图表标题（含公司、指标、期间）"},
                    "x_data": {"type": "array", "items": {"type": "string"}, "description": "X 轴标签，如年份或公司名"},
                    "y_data": {"type": "array", "items": {"type": "number"}, "description": "Y 轴数值，与 x_data 一一对应"},
                    "x_label": {"type": "string", "description": "X 轴名称"},
                    "y_label": {"type": "string", "description": "Y 轴名称（含单位，如 营业收入（万元））"},
                    "series_name": {"type": "string", "description": "数据系列名称"},
                },
                "required": ["chart_type", "title", "x_data", "y_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": "关键信息缺失时向用户提问。调用后本轮结束，等待用户补充。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "向用户提出的澄清问题（中文）"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可点选的候选项，如年份列表或指标维度列表，3-6 个",
                    },
                },
                "required": ["question"],
            },
        },
    },
]
