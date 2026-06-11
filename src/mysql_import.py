
"""
MySQL 数据导入脚本
将提取的数据导入到 MySQL 数据库
"""

import os
import sys
import pandas as pd
from loguru import logger
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.db_config import get_db_config
from src.init_db import Base, IncomeSheet, BalanceSheet, CashFlowSheet, CorePerformanceIndicatorsSheet

def create_database():
    """创建数据库"""
    config = get_db_config()
    engine = create_engine(config.connection_string_no_db)
    with engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {config.database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        conn.commit()
        logger.info(f"数据库 '{config.database}' 创建成功")

def create_tables():
    """创建表结构"""
    config = get_db_config()
    engine = create_engine(config.connection_string)
    Base.metadata.create_all(engine)
    logger.info("所有表创建成功")

def get_table_columns(model_class):
    """获取模型类的所有列名"""
    return [c.name for c in model_class.__table__.columns]


def _count_non_empty_fields(row_dict: dict, excluded_fields: set) -> int:
    count = 0
    for key, value in row_dict.items():
        if key in excluded_fields:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        try:
            if pd.isna(value):
                continue
        except Exception:
            pass
        count += 1
    return count


def _count_non_zero_numeric_fields(row_dict: dict, excluded_fields: set) -> int:
    count = 0
    for key, value in row_dict.items():
        if key in excluded_fields:
            continue
        if not _is_meaningful_value(value):
            continue
        if isinstance(value, (int, float)) and not pd.isna(value) and abs(float(value)) > 1e-8:
            count += 1
    return count


def _row_quality_score(row_dict: dict, excluded_fields: set):
    return (
        _count_non_empty_fields(row_dict, excluded_fields),
        _count_non_zero_numeric_fields(row_dict, excluded_fields),
    )


def _dedupe_rows_prefer_richer(df: pd.DataFrame, key_columns: list) -> pd.DataFrame:
    if df.empty or not all(c in df.columns for c in key_columns):
        return df

    excluded_fields = set(key_columns) | {"serial_number", "created_at", "updated_at"}
    best_by_key = {}
    order = []

    for _, row in df.iterrows():
        row_dict = row.to_dict()
        key = tuple(row_dict.get(col) for col in key_columns)
        score = _row_quality_score(row_dict, excluded_fields)
        current = best_by_key.get(key)
        if current is None or score > current[0]:
            best_by_key[key] = (score, row_dict)
            if key not in order:
                order.append(key)

    deduped_rows = [best_by_key[key][1] for key in order if key in best_by_key]
    return pd.DataFrame(deduped_rows)


def _is_meaningful_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    return True


def _merge_row_dict(existing_row_dict: dict, new_row_dict: dict, excluded_fields: set, fill_only: bool = False) -> dict:
    merged = {}
    data_keys = set(existing_row_dict) | set(new_row_dict)
    data_keys -= {"serial_number", "created_at", "updated_at"}

    for key in data_keys:
        if key in excluded_fields:
            continue
        existing_value = existing_row_dict.get(key)
        new_value = new_row_dict.get(key)

        if fill_only:
            if _is_meaningful_value(existing_value):
                merged[key] = existing_value
            elif _is_meaningful_value(new_value):
                merged[key] = new_value
        else:
            if _is_meaningful_value(new_value):
                merged[key] = new_value
            elif _is_meaningful_value(existing_value):
                merged[key] = existing_value

    for key in ("stock_code", "report_year", "report_period"):
        if key in new_row_dict and _is_meaningful_value(new_row_dict.get(key)):
            merged[key] = new_row_dict[key]
        elif key in existing_row_dict and _is_meaningful_value(existing_row_dict.get(key)):
            merged[key] = existing_row_dict[key]
    return merged

def import_sheet(
    engine,
    df: pd.DataFrame,
    model_class,
    table_name: str,
    conn=None,
    fill_only: bool = False,
    replace_existing: bool = False,
):
    """通用导入函数"""
    if df.empty:
        return

    # 获取数据库表的有效列
    valid_columns = get_table_columns(model_class)
    
    # 筛选DataFrame中存在的列
    df_columns = [c for c in df.columns if c in valid_columns]
    df_filtered = df[df_columns]
    
    key_columns = ['stock_code', 'report_year', 'report_period']
    for col in key_columns:
        if col in df_filtered.columns:
            df_filtered = df_filtered[df_filtered[col].notna()]
    if all(c in df_filtered.columns for c in key_columns):
        df_filtered = _dedupe_rows_prefer_richer(df_filtered, key_columns)

    try:
        managed_tx = conn is None
        tx = engine.begin() if managed_tx else None
        active_conn = tx.__enter__() if managed_tx else conn
        try:
            rows_to_insert = []
            excluded_fields = set(key_columns) | {"serial_number", "created_at", "updated_at"}

            if all(c in df_filtered.columns for c in key_columns):
                for _, row in df_filtered.iterrows():
                    key_payload = {
                        "stock_code": str(row["stock_code"]),
                        "report_year": int(row["report_year"]),
                        "report_period": str(row["report_period"]),
                    }
                    existing_df = pd.read_sql_query(
                        text(
                            f"SELECT * FROM {table_name} "
                            "WHERE stock_code=:stock_code AND report_year=:report_year AND report_period=:report_period "
                            "LIMIT 1"
                        ),
                        active_conn,
                        params=key_payload,
                    )

                    new_row_dict = row.to_dict()
                    new_score = _count_non_empty_fields(new_row_dict, excluded_fields)

                    if not existing_df.empty:
                        existing_row_dict = existing_df.iloc[0].to_dict()
                        existing_score = _count_non_empty_fields(existing_row_dict, excluded_fields)
                        active_conn.execute(
                            text(
                                f"DELETE FROM {table_name} "
                                "WHERE stock_code=:stock_code AND report_year=:report_year AND report_period=:report_period"
                            ),
                            key_payload,
                        )

                        if replace_existing and not fill_only:
                            existing_quality = _row_quality_score(existing_row_dict, excluded_fields)
                            new_quality = _row_quality_score(new_row_dict, excluded_fields)
                            if new_quality < existing_quality:
                                merged_row_dict = _merge_row_dict(
                                    existing_row_dict,
                                    new_row_dict,
                                    excluded_fields,
                                    fill_only=True,
                                )
                                merged_score = _count_non_empty_fields(merged_row_dict, excluded_fields)
                                logger.info(
                                    f"保留更完整记录 {table_name}: {key_payload['stock_code']} "
                                    f"{key_payload['report_year']} {key_payload['report_period']} "
                                    f"(existing={existing_score}, new={new_score}, merged={merged_score})"
                                )
                                rows_to_insert.append(merged_row_dict)
                                continue
                            logger.info(
                                f"覆盖更新记录 {table_name}: {key_payload['stock_code']} "
                                f"{key_payload['report_year']} {key_payload['report_period']} "
                                f"(existing={existing_score}, new={new_score}, fill_only={fill_only})"
                            )
                            rows_to_insert.append(new_row_dict)
                            continue

                        merged_row_dict = _merge_row_dict(
                            existing_row_dict,
                            new_row_dict,
                            excluded_fields,
                            fill_only=fill_only,
                        )
                        merged_score = _count_non_empty_fields(merged_row_dict, excluded_fields)
                        logger.info(
                            f"合并更新记录 {table_name}: {key_payload['stock_code']} "
                            f"{key_payload['report_year']} {key_payload['report_period']} "
                            f"(existing={existing_score}, new={new_score}, merged={merged_score}, fill_only={fill_only})"
                        )
                        rows_to_insert.append(merged_row_dict)
                        continue

                    if fill_only:
                        logger.info(
                            f"跳过摘要新建记录 {table_name}: {key_payload['stock_code']} "
                            f"{key_payload['report_year']} {key_payload['report_period']}"
                        )
                        continue

                    rows_to_insert.append(new_row_dict)
            else:
                rows_to_insert = df_filtered.to_dict(orient="records")

            if rows_to_insert:
                cleaned_rows = []
                for row_dict in rows_to_insert:
                    cleaned_rows.append({
                        k: v for k, v in row_dict.items()
                        if k not in {"serial_number", "created_at", "updated_at"}
                    })
                pd.DataFrame(cleaned_rows).to_sql(table_name, active_conn, if_exists='append', index=False)
            if managed_tx:
                tx.__exit__(None, None, None)
        except Exception as inner_e:
            if managed_tx:
                tx.__exit__(type(inner_e), inner_e, inner_e.__traceback__)
            raise
        logger.info(f"导入 {table_name}: {len(rows_to_insert)} 条记录")
    except Exception as e:
        logger.error(f"导入 {table_name} 失败: {e}")
        raise

def import_data(excel_path: str):
    """导入数据到 MySQL"""
    config = get_db_config()
    engine = create_engine(config.connection_string)
    
    xlsx = pd.ExcelFile(excel_path)
    
    # 导入利润表
    if '利润表' in xlsx.sheet_names:
        df = pd.read_excel(xlsx, sheet_name='利润表')
        import_sheet(engine, df, IncomeSheet, 'income_sheet')
    
    # 导入资产负债表
    if '资产负债表' in xlsx.sheet_names:
        df = pd.read_excel(xlsx, sheet_name='资产负债表')
        import_sheet(engine, df, BalanceSheet, 'balance_sheet')
    
    # 导入现金流量表
    if '现金流量表' in xlsx.sheet_names:
        df = pd.read_excel(xlsx, sheet_name='现金流量表')
        import_sheet(engine, df, CashFlowSheet, 'cash_flow_sheet')
        
    # 导入核心业绩指标表
    if '核心业绩指标表' in xlsx.sheet_names:
        df = pd.read_excel(xlsx, sheet_name='核心业绩指标表')
        import_sheet(engine, df, CorePerformanceIndicatorsSheet, 'core_performance_indicators_sheet')

def init_mysql_database():
    """初始化 MySQL 数据库"""
    logger.info("开始初始化 MySQL 数据库...")
    
    # 创建数据库
    create_database()
    
    # 创建表
    create_tables()
    
    # 导入数据
    excel_path = "result/extracted_data.xlsx"
    if os.path.exists(excel_path):
        import_data(excel_path)
    else:
        logger.warning(f"数据文件不存在: {excel_path}")
    
    logger.info("MySQL 数据库初始化完成！")

if __name__ == "__main__":
    init_mysql_database()
