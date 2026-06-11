"""
同比/环比增长率自动计算模块
在全量 ETL 入库后运行，批量计算并回填增长率字段
公式：同比 = (本期 - 上期同期) / abs(上期同期) * 100
"""

from loguru import logger
from sqlalchemy import create_engine, text

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config.db_config import get_db_config

PERIOD_ORDER = {"Q1": 1, "HY": 2, "Q3": 3, "FY": 4}

# (表名, 增长率字段, 基础数值字段, 计算类型)
YOY_RULES = [
    # core_performance_indicators_sheet
    ("core_performance_indicators_sheet", "net_profit_excl_non_recurring_yoy", "net_profit_excl_non_recurring", "yoy"),
]

GROWTH_LIMITS = {
    "operating_revenue_yoy_growth": 1000,
    "net_profit_yoy_growth": 1000,
    "net_profit_excl_non_recurring_yoy": 1000,
    "operating_revenue_qoq_growth": 500,
    "net_profit_qoq_growth": 1000,
}

RATIO_RULES = [
]

RATIO_LIMITS = {
    "asset_liability_ratio": 100.0,
    "operating_cf_ratio_of_net_cf": 1000.0,
    "investing_cf_ratio_of_net_cf": 1000.0,
    "financing_cf_ratio_of_net_cf": 1000.0,
}


def calculate_yoy_growth(engine=None):
    if engine is None:
        config = get_db_config()
        engine = create_engine(config.connection_string)

    updated = 0

    for table, growth_field, value_field, _ in YOY_RULES:
        limit = GROWTH_LIMITS.get(growth_field, 1000)
        sql = f"""
            UPDATE {table} AS cur
            JOIN {table} AS prev
              ON cur.stock_code = prev.stock_code
              AND cur.report_period = prev.report_period
              AND cur.report_year = prev.report_year + 1
            SET cur.{growth_field} = CASE
                WHEN ABS((cur.{value_field} - prev.{value_field}) / ABS(prev.{value_field}) * 100) <= {limit}
                THEN ROUND((cur.{value_field} - prev.{value_field}) / ABS(prev.{value_field}) * 100, 4)
                ELSE NULL
            END
            WHERE cur.{growth_field} IS NULL
              AND cur.{value_field} IS NOT NULL
              AND prev.{value_field} IS NOT NULL
              AND ABS(prev.{value_field}) > 0.01
        """
        try:
            with engine.begin() as conn:
                result = conn.execute(text(sql))
                cnt = result.rowcount
                if cnt > 0:
                    logger.info(f"[YOY] {table}.{growth_field}: 更新 {cnt} 行")
                    updated += cnt
        except Exception as e:
            logger.error(f"[YOY] {table}.{growth_field} 计算失败: {e}")

    return updated


def calculate_qoq_growth(engine=None):
    """
    qoq 现已在 ETL 全量批处理中优先由“年报分季度主要财务数据”回填。
    这里不再用累计值逆推单季度做数据库层补算，避免和财报原始披露口径混淆。
    """
    logger.info("[QOQ] skip database-level cumulative backfill; handled in ETL batch pipeline")
    return 0


def calculate_ratios(engine=None):
    if engine is None:
        config = get_db_config()
        engine = create_engine(config.connection_string)

    updated = 0

    for table, ratio_field, numerator, denominator in RATIO_RULES:
        limit = RATIO_LIMITS.get(ratio_field, 1000.0)
        if table == "cash_flow_sheet":
            raw_ratio_expr = f"({numerator} / ({denominator} / 10000.0) * 100)"
            rounded_ratio_expr = f"ROUND({raw_ratio_expr}, 4)"
            denominator_guard = f"ABS({denominator}) > 100"
        else:
            raw_ratio_expr = f"({numerator} / {denominator} * 100)"
            rounded_ratio_expr = f"ROUND({raw_ratio_expr}, 4)"
            denominator_guard = f"ABS({denominator}) > 0.01"
        sql = f"""
            UPDATE {table}
            SET {ratio_field} = CASE
                WHEN ABS({raw_ratio_expr}) <= {limit}
                THEN {rounded_ratio_expr}
                ELSE NULL
            END
            WHERE ({ratio_field} IS NULL OR ABS({ratio_field}) > {limit})
              AND {numerator} IS NOT NULL
              AND {denominator} IS NOT NULL
              AND {denominator_guard}
        """
        try:
            with engine.begin() as conn:
                result = conn.execute(text(sql))
                cnt = result.rowcount
                if cnt > 0:
                    logger.info(f"[RATIO] {table}.{ratio_field}: 更新 {cnt} 行")
                    updated += cnt
        except Exception as e:
            logger.error(f"[RATIO] {table}.{ratio_field} 失败: {e}")

    return updated


def run_all_calculations(engine=None):
    if engine is None:
        config = get_db_config()
        engine = create_engine(config.connection_string)

    logger.info("[计算] 开始批量计算同比/环比/比率...")
    total = 0
    total += calculate_yoy_growth(engine)
    total += calculate_ratios(engine)
    logger.info(f"[计算] 完成，共更新 {total} 个字段值")
    return total


if __name__ == "__main__":
    run_all_calculations()
