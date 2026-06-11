"""
财报领域常量与 schema 单一事实来源。

- 表/字段白名单与 schema 描述均从 src/init_db.py 的 SQLAlchemy 模型派生，
  避免手写文案与真实表结构漂移。
- 公司注册表来自附件1 CSV（src/utils/company_registry.py）。
"""

from functools import lru_cache
from typing import Dict, List

from src.init_db import (
    BalanceSheet,
    CashFlowSheet,
    CorePerformanceIndicatorsSheet,
    IncomeSheet,
)
from src.utils.company_registry import (
    build_company_list_for_prompt,
    get_code_to_name,
    get_name_to_code,
)

REPORT_PERIODS: tuple = ("FY", "Q1", "HY", "Q3")
PERIOD_LABELS: Dict[str, str] = {"FY": "全年年报", "Q1": "一季度", "HY": "半年报", "Q3": "三季度"}

_TABLE_MODELS = {
    "income_sheet": (IncomeSheet, "利润表"),
    "balance_sheet": (BalanceSheet, "资产负债表"),
    "cash_flow_sheet": (CashFlowSheet, "现金流量表"),
    "core_performance_indicators_sheet": (CorePerformanceIndicatorsSheet, "核心业绩指标表"),
}

# 不向 LLM 暴露、也不参与指标查询的管理字段
_META_FIELDS = {"serial_number", "created_at", "updated_at"}
_KEY_FIELDS = ("stock_code", "stock_abbr", "report_period", "report_year")


@lru_cache(maxsize=1)
def get_table_fields() -> Dict[str, Dict[str, str]]:
    """table -> {field_name: 中文注释}，含主键字段，不含管理字段。"""
    result: Dict[str, Dict[str, str]] = {}
    for table, (model, _label) in _TABLE_MODELS.items():
        fields: Dict[str, str] = {}
        for column in model.__table__.columns:
            if column.name in _META_FIELDS:
                continue
            fields[column.name] = str(column.comment or column.name)
        result[table] = fields
    return result


@lru_cache(maxsize=1)
def get_allowed_fields() -> Dict[str, frozenset]:
    """SQL 校验白名单：table -> 全部合法字段（含管理字段，防止误杀）。"""
    return {
        table: frozenset(column.name for column in model.__table__.columns)
        for table, (model, _label) in _TABLE_MODELS.items()
    }


ALLOWED_TABLES: frozenset = frozenset(_TABLE_MODELS.keys())


def get_schema_description() -> str:
    """生成注入 LLM 提示词的数据库 schema 描述（由模型注释自动生成）。"""
    lines: List[str] = ["数据库表结构说明（除注释中另有标注外，金额字段单位：万元）：", ""]
    for idx, (table, (_model, label)) in enumerate(_TABLE_MODELS.items(), start=1):
        fields = get_table_fields()[table]
        lines.append(f"{idx}. {table} ({label})")
        lines.append(
            "   主键字段：stock_code(股票代码 VARCHAR), stock_abbr(股票简称), "
            "report_period(报告期: FY/Q1/HY/Q3), report_year(年份 INT)"
        )
        metric_parts = [
            f"{name}({comment})" for name, comment in fields.items() if name not in _KEY_FIELDS
        ]
        lines.append("   财务字段：" + ", ".join(metric_parts))
        lines.append("")
    lines.append(build_company_list_for_prompt())
    lines.append("report_period 枚举：FY(全年年报), Q1(一季度), HY(半年报), Q3(三季度)")
    return "\n".join(lines)


# ── 公司注册表 ────────────────────────────────────────────────────────────────

COMPANY_CODE_MAP: Dict[str, str] = get_name_to_code()
CODE_TO_NAME_MAP: Dict[str, str] = get_code_to_name()


# ── 指标关键词 → 字段映射（规则兜底与上下文标签使用）─────────────────────────

KEYWORD_FIELD_MAP: Dict[str, str] = {
    '利润总额': 'total_profit', '净利润': 'net_profit', '营业利润': 'operating_profit',
    '归属于上市公司股东的净利润': 'net_profit', '归属于母公司所有者的净利润': 'net_profit',
    '归母净利润': 'net_profit', '归属母公司净利润': 'net_profit',
    '扣非净利润': 'net_profit_excl_non_recurring',
    '扣除非经常性损益后的净利润': 'net_profit_excl_non_recurring',
    '归属于上市公司股东的扣除非经常性损益的净利润': 'net_profit_excl_non_recurring',
    '营业收入': 'total_operating_revenue', '主营业务收入': 'total_operating_revenue',
    '销售额': 'total_operating_revenue', '营收': 'total_operating_revenue',
    '营业成本': 'operating_expense_cost_of_sales',
    '销售费用': 'operating_expense_selling_expenses',
    '管理费用': 'operating_expense_administrative_expenses',
    '财务费用': 'operating_expense_financial_expenses',
    '研发费用': 'operating_expense_rnd_expenses',
    '货币资金': 'asset_cash_and_cash_equivalents',
    '应收账款': 'asset_accounts_receivable',
    '存货': 'asset_inventory', '总资产': 'asset_total_assets',
    '总负债': 'liability_total_liabilities', '股东权益': 'equity_total_equity',
    '经营活动现金流': 'operating_cf_net_amount',
    '经营现金流': 'operating_cf_net_amount',
    '投资活动现金流': 'investing_cf_net_amount',
    '投资现金流': 'investing_cf_net_amount',
    '筹资活动现金流': 'financing_cf_net_amount',
    '筹资现金流': 'financing_cf_net_amount',
    '净现金流': 'net_cash_flow',
    '现金流': 'net_cash_flow',
    '每股收益': 'eps', '净资产收益率': 'roe', '收益率': 'roe',
    '资产负债率': 'asset_liability_ratio',
    '销售毛利率': 'gross_profit_margin', '毛利率': 'gross_profit_margin',
    '销售净利率': 'net_profit_margin', '净利率': 'net_profit_margin',
}

# 字段 → 默认中文标签（取 KEYWORD_FIELD_MAP 中第一个映射到该字段的关键词）
FIELD_LABEL_MAP: Dict[str, str] = {}
for _keyword, _field in KEYWORD_FIELD_MAP.items():
    FIELD_LABEL_MAP.setdefault(_field, _keyword)


def field_table(field: str) -> str:
    """返回包含该字段的优先表（core 指标表优先级最低，作为跨表汇总）。"""
    priority = ["income_sheet", "balance_sheet", "cash_flow_sheet", "core_performance_indicators_sheet"]
    for table in priority:
        if field in get_allowed_fields()[table]:
            return table
    return "core_performance_indicators_sheet"
