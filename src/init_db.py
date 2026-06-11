"""
数据库初始化脚本
创建财务报表数据库和表结构（对齐附件3字段说明）
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text, Column, String, Float, Integer, Date, DateTime, Text, Index, DECIMAL, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

from config.db_config import get_db_config, DatabaseConfig

Base = declarative_base()


class CorePerformanceIndicatorsSheet(Base):
    """核心业绩指标表"""
    __tablename__ = 'core_performance_indicators_sheet'

    serial_number = Column(Integer, primary_key=True, autoincrement=True, comment='序号')
    stock_code = Column(String(20), nullable=False, comment='股票代码')
    stock_abbr = Column(String(50), comment='股票简称')
    
    # 主要指标
    eps = Column(DECIMAL(10, 4), comment='每股收益(元)')
    total_operating_revenue = Column(DECIMAL(20, 2), comment='营业总收入(万元)')
    operating_revenue_yoy_growth = Column(DECIMAL(10, 4), comment='营业总收入-同比增长(%)')
    operating_revenue_qoq_growth = Column(DECIMAL(10, 4), comment='营业总收入-季度环比增长(%)')
    net_profit_10k_yuan = Column(DECIMAL(20, 2), comment='净利润(万元)')
    net_profit_yoy_growth = Column(DECIMAL(10, 4), comment='净利润-同比增长(%)')
    net_profit_qoq_growth = Column(DECIMAL(10, 4), comment='净利润-季度环比增长(%)')
    net_asset_per_share = Column(DECIMAL(10, 4), comment='每股净资产(元)')
    roe = Column(DECIMAL(10, 4), comment='净资产收益率(%)')
    operating_cf_per_share = Column(DECIMAL(10, 4), comment='每股经营现金流量(元)')
    net_profit_excl_non_recurring = Column(DECIMAL(20, 2), comment='扣非净利润（万元）')
    net_profit_excl_non_recurring_yoy = Column(DECIMAL(10, 4), comment='扣非净利润同比增长（%）')
    gross_profit_margin = Column(DECIMAL(10, 4), comment='销售毛利率(%)')
    net_profit_margin = Column(DECIMAL(10, 4), comment='销售净利率（%）')
    roe_weighted_excl_non_recurring = Column(DECIMAL(10, 4), comment='加权平均净资产收益率（扣非）（%）')
    
    report_period = Column(String(20), comment='报告期')
    report_year = Column(Integer, comment='报告期-年份')

    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    __table_args__ = (
        Index('idx_core_stock_period', 'stock_code', 'report_period'),
        UniqueConstraint('stock_code', 'report_year', 'report_period', name='uq_core_stock_year_period'),
        {'comment': '核心业绩指标表'}
    )


class BalanceSheet(Base):
    """资产负债表"""
    __tablename__ = 'balance_sheet'

    serial_number = Column(Integer, primary_key=True, autoincrement=True, comment='序号')
    stock_code = Column(String(20), nullable=False, comment='股票代码')
    stock_abbr = Column(String(50), comment='股票简称')

    # 资产类
    asset_cash_and_cash_equivalents = Column(DECIMAL(20, 2), comment='资产-货币资金(万元)')
    asset_accounts_receivable = Column(DECIMAL(20, 2), comment='资产-应收账款(万元)')
    asset_inventory = Column(DECIMAL(20, 2), comment='资产-存货(万元)')
    asset_trading_financial_assets = Column(DECIMAL(20, 2), comment='资产-交易性金融资产（万元）')
    asset_construction_in_progress = Column(DECIMAL(20, 2), comment='资产-在建工程（万元）')
    asset_total_assets = Column(DECIMAL(20, 2), comment='资产-总资产(万元)')
    asset_total_assets_yoy_growth = Column(DECIMAL(10, 4), comment='资产-总资产同比(%)')

    # 负债类
    liability_accounts_payable = Column(DECIMAL(20, 2), comment='负债-应付账款(万元)')
    liability_advance_from_customers = Column(DECIMAL(20, 2), comment='负债-预收账款(万元)')
    liability_total_liabilities = Column(DECIMAL(20, 2), comment='负债-总负债(万元)')
    liability_total_liabilities_yoy_growth = Column(DECIMAL(10, 4), comment='负债-总负债同比(%)')
    liability_contract_liabilities = Column(DECIMAL(20, 2), comment='负债-合同负债（万元）')
    liability_short_term_loans = Column(DECIMAL(20, 2), comment='负债-短期借款（万元）')

    # 其他
    asset_liability_ratio = Column(DECIMAL(10, 4), comment='资产负债率(%)')

    # 股东权益类
    equity_unappropriated_profit = Column(DECIMAL(20, 2), comment='股东权益-未分配利润（万元）')
    equity_total_equity = Column(DECIMAL(20, 2), comment='股东权益合计(万元)')

    report_period = Column(String(20), comment='报告期')
    report_year = Column(Integer, comment='报告期-年份')

    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    __table_args__ = (
        Index('idx_balance_stock_period', 'stock_code', 'report_period'),
        UniqueConstraint('stock_code', 'report_year', 'report_period', name='uq_balance_stock_year_period'),
        {'comment': '资产负债表'}
    )


class IncomeSheet(Base):
    """利润表"""
    __tablename__ = 'income_sheet'

    serial_number = Column(Integer, primary_key=True, autoincrement=True, comment='序号')
    stock_code = Column(String(20), nullable=False, comment='股票代码')
    stock_abbr = Column(String(50), comment='股票简称')

    # 利润指标
    net_profit = Column(DECIMAL(20, 2), comment='净利润(万元)')
    net_profit_yoy_growth = Column(DECIMAL(10, 4), comment='净利润同比(%)')
    other_income = Column(DECIMAL(20, 2), comment='其他收益（万元）')
    total_operating_revenue = Column(DECIMAL(20, 2), comment='营业总收入(万元)')
    operating_revenue_yoy_growth = Column(DECIMAL(10, 4), comment='营业总收入同比(%)')

    # 营业支出
    operating_expense_cost_of_sales = Column(DECIMAL(20, 2), comment='营业总支出-营业支出(万元)')
    operating_expense_selling_expenses = Column(DECIMAL(20, 2), comment='营业总支出-销售费用(万元)')
    operating_expense_administrative_expenses = Column(DECIMAL(20, 2), comment='营业总支出-管理费用(万元)')
    operating_expense_financial_expenses = Column(DECIMAL(20, 2), comment='营业总支出-财务费用(万元)')
    operating_expense_rnd_expenses = Column(DECIMAL(20, 2), comment='营业总支出-研发费用（万元）')
    operating_expense_taxes_and_surcharges = Column(DECIMAL(20, 2), comment='营业总支出-税金及附加（万元）')
    total_operating_expenses = Column(DECIMAL(20, 2), comment='营业总支出(万元)')

    # 利润
    operating_profit = Column(DECIMAL(20, 2), comment='营业利润(万元)')
    total_profit = Column(DECIMAL(20, 2), comment='利润总额(万元)')

    # 减值损失
    asset_impairment_loss = Column(DECIMAL(20, 2), comment='资产减值损失（万元）')
    credit_impairment_loss = Column(DECIMAL(20, 2), comment='信用减值损失（万元）')

    report_period = Column(String(20), comment='报告期')
    report_year = Column(Integer, comment='报告期-年份')

    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    __table_args__ = (
        Index('idx_income_stock_period', 'stock_code', 'report_period'),
        UniqueConstraint('stock_code', 'report_year', 'report_period', name='uq_income_stock_year_period'),
        {'comment': '利润表'}
    )


class CashFlowSheet(Base):
    """现金流量表"""
    __tablename__ = 'cash_flow_sheet'

    serial_number = Column(Integer, primary_key=True, autoincrement=True, comment='序号')
    stock_code = Column(String(20), nullable=False, comment='股票代码')
    stock_abbr = Column(String(50), comment='股票简称')

    # 净现金流
    net_cash_flow = Column(DECIMAL(20, 2), comment='净现金流(元)')
    net_cash_flow_yoy_growth = Column(DECIMAL(10, 4), comment='净现金流-同比增长(%)')

    # 经营性现金流
    operating_cf_net_amount = Column(DECIMAL(20, 2), comment='经营性现金流-现金流量净额(万元)')
    operating_cf_ratio_of_net_cf = Column(DECIMAL(10, 4), comment='经营性现金流-净现金流占比(%)')
    operating_cf_cash_from_sales = Column(DECIMAL(20, 2), comment='经营性现金流-销售商品收到的现金（万元）')

    # 投资性现金流
    investing_cf_net_amount = Column(DECIMAL(20, 2), comment='投资性现金流-现金流量净额(万元)')
    investing_cf_ratio_of_net_cf = Column(DECIMAL(10, 4), comment='投资性现金流-净现金流占比(%)')
    investing_cf_cash_for_investments = Column(DECIMAL(20, 2), comment='投资性现金流-投资支付的现金（万元）')
    investing_cf_cash_from_investment_recovery = Column(DECIMAL(20, 2), comment='投资性现金流-收回投资收到的现金（万元）')

    # 融资性现金流
    financing_cf_cash_from_borrowing = Column(DECIMAL(20, 2), comment='融资性现金流-取得借款收到的现金（万元）')
    financing_cf_cash_for_debt_repayment = Column(DECIMAL(20, 2), comment='融资性现金流-偿还债务支付的现金（万元）')
    financing_cf_net_amount = Column(DECIMAL(20, 2), comment='融资性现金流-现金流量净额(万元)')
    financing_cf_ratio_of_net_cf = Column(DECIMAL(10, 4), comment='融资性现金流-净现金流占比(%)')

    report_period = Column(String(20), comment='报告期')
    report_year = Column(Integer, comment='报告期-年份')

    created_at = Column(DateTime, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, comment='更新时间')

    __table_args__ = (
        Index('idx_cashflow_stock_period', 'stock_code', 'report_period'),
        UniqueConstraint('stock_code', 'report_year', 'report_period', name='uq_cashflow_stock_year_period'),
        {'comment': '现金流量表'}
    )


def create_database(config: DatabaseConfig = None):
    """
    创建数据库

    Args:
        config: 数据库配置
    """
    if config is None:
        config = get_db_config()

    # 连接到 MySQL 服务器（不指定数据库）
    engine = create_engine(config.connection_string_no_db)

    with engine.connect() as conn:
        # 创建数据库
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {config.database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        conn.commit()
        print(f"数据库 '{config.database}' 创建成功")


def create_tables(config: DatabaseConfig = None):
    """
    创建所有表

    Args:
        config: 数据库配置
    """
    if config is None:
        config = get_db_config()

    engine = create_engine(config.connection_string)

    # 创建所有表
    Base.metadata.create_all(engine)
    print("所有表创建成功")

    # 打印创建的表
    for table in Base.metadata.tables.values():
        print(f"  - {table.name}")


def init_database(config: DatabaseConfig = None):
    """
    初始化数据库（创建数据库和表）

    Args:
        config: 数据库配置
    """
    if config is None:
        config = get_db_config()

    print(f"正在初始化数据库...")
    print(f"主机: {config.host}:{config.port}")
    print(f"数据库: {config.database}")

    create_database(config)
    create_tables(config)

    print("\n数据库初始化完成！")


def get_table_ddl():
    """获取所有表的DDL语句（用于Schema Linking）"""
    ddl_statements = {
        'core_performance_indicators_sheet': """
CREATE TABLE core_performance_indicators_sheet (
    serial_number INT PRIMARY KEY AUTO_INCREMENT COMMENT '序号',
    stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
    stock_abbr VARCHAR(50) COMMENT '股票简称',
    eps DECIMAL(10,4) COMMENT '每股收益(元)',
    total_operating_revenue DECIMAL(20,2) COMMENT '营业总收入(万元)',
    operating_revenue_yoy_growth DECIMAL(10,4) COMMENT '营业总收入-同比增长(%)',
    operating_revenue_qoq_growth DECIMAL(10,4) COMMENT '营业总收入-季度环比增长(%)',
    net_profit_10k_yuan DECIMAL(20,2) COMMENT '净利润(万元)',
    net_profit_yoy_growth DECIMAL(10,4) COMMENT '净利润-同比增长(%)',
    net_profit_qoq_growth DECIMAL(10,4) COMMENT '净利润-季度环比增长(%)',
    net_asset_per_share DECIMAL(10,4) COMMENT '每股净资产(元)',
    roe DECIMAL(10,4) COMMENT '净资产收益率(%)',
    operating_cf_per_share DECIMAL(10,4) COMMENT '每股经营现金流量(元)',
    net_profit_excl_non_recurring DECIMAL(20,2) COMMENT '扣非净利润（万元）',
    net_profit_excl_non_recurring_yoy DECIMAL(10,4) COMMENT '扣非净利润同比增长（%）',
    gross_profit_margin DECIMAL(10,4) COMMENT '销售毛利率(%)',
    net_profit_margin DECIMAL(10,4) COMMENT '销售净利率（%）',
    roe_weighted_excl_non_recurring DECIMAL(10,4) COMMENT '加权平均净资产收益率（扣非）（%）',
    report_period VARCHAR(20) COMMENT '报告期',
    report_year INT COMMENT '报告期-年份'
) COMMENT '核心业绩指标表';
""",
        'balance_sheet': """
CREATE TABLE balance_sheet (
    serial_number INT PRIMARY KEY AUTO_INCREMENT COMMENT '序号',
    stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
    stock_abbr VARCHAR(50) COMMENT '股票简称',
    asset_cash_and_cash_equivalents DECIMAL(20,2) COMMENT '资产-货币资金(万元)',
    asset_accounts_receivable DECIMAL(20,2) COMMENT '资产-应收账款(万元)',
    asset_inventory DECIMAL(20,2) COMMENT '资产-存货(万元)',
    asset_trading_financial_assets DECIMAL(20,2) COMMENT '资产-交易性金融资产（万元）',
    asset_construction_in_progress DECIMAL(20,2) COMMENT '资产-在建工程（万元）',
    asset_total_assets DECIMAL(20,2) COMMENT '资产-总资产(万元)',
    asset_total_assets_yoy_growth DECIMAL(10,4) COMMENT '资产-总资产同比(%)',
    liability_accounts_payable DECIMAL(20,2) COMMENT '负债-应付账款(万元)',
    liability_advance_from_customers DECIMAL(20,2) COMMENT '负债-预收账款(万元)',
    liability_total_liabilities DECIMAL(20,2) COMMENT '负债-总负债(万元)',
    liability_total_liabilities_yoy_growth DECIMAL(10,4) COMMENT '负债-总负债同比(%)',
    liability_contract_liabilities DECIMAL(20,2) COMMENT '负债-合同负债（万元）',
    liability_short_term_loans DECIMAL(20,2) COMMENT '负债-短期借款（万元）',
    asset_liability_ratio DECIMAL(10,4) COMMENT '资产负债率(%)',
    equity_unappropriated_profit DECIMAL(20,2) COMMENT '股东权益-未分配利润（万元）',
    equity_total_equity DECIMAL(20,2) COMMENT '股东权益合计(万元)',
    report_period VARCHAR(20) COMMENT '报告期',
    report_year INT COMMENT '报告期-年份'
) COMMENT '资产负债表';
""",
        'income_sheet': """
CREATE TABLE income_sheet (
    serial_number INT PRIMARY KEY AUTO_INCREMENT COMMENT '序号',
    stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
    stock_abbr VARCHAR(50) COMMENT '股票简称',
    net_profit DECIMAL(20,2) COMMENT '净利润(万元)',
    net_profit_yoy_growth DECIMAL(10,4) COMMENT '净利润同比(%)',
    other_income DECIMAL(20,2) COMMENT '其他收益（万元）',
    total_operating_revenue DECIMAL(20,2) COMMENT '营业总收入(万元)',
    operating_revenue_yoy_growth DECIMAL(10,4) COMMENT '营业总收入同比(%)',
    operating_expense_cost_of_sales DECIMAL(20,2) COMMENT '营业总支出-营业支出(万元)',
    operating_expense_selling_expenses DECIMAL(20,2) COMMENT '营业总支出-销售费用(万元)',
    operating_expense_administrative_expenses DECIMAL(20,2) COMMENT '营业总支出-管理费用(万元)',
    operating_expense_financial_expenses DECIMAL(20,2) COMMENT '营业总支出-财务费用(万元)',
    operating_expense_rnd_expenses DECIMAL(20,2) COMMENT '营业总支出-研发费用（万元）',
    operating_expense_taxes_and_surcharges DECIMAL(20,2) COMMENT '营业总支出-税金及附加（万元）',
    total_operating_expenses DECIMAL(20,2) COMMENT '营业总支出(万元)',
    operating_profit DECIMAL(20,2) COMMENT '营业利润(万元)',
    total_profit DECIMAL(20,2) COMMENT '利润总额(万元)',
    asset_impairment_loss DECIMAL(20,2) COMMENT '资产减值损失（万元）',
    credit_impairment_loss DECIMAL(20,2) COMMENT '信用减值损失（万元）',
    report_period VARCHAR(20) COMMENT '报告期',
    report_year INT COMMENT '报告期-年份'
) COMMENT '利润表';
""",
        'cash_flow_sheet': """
CREATE TABLE cash_flow_sheet (
    serial_number INT PRIMARY KEY AUTO_INCREMENT COMMENT '序号',
    stock_code VARCHAR(20) NOT NULL COMMENT '股票代码',
    stock_abbr VARCHAR(50) COMMENT '股票简称',
    net_cash_flow DECIMAL(20,2) COMMENT '净现金流(元)',
    net_cash_flow_yoy_growth DECIMAL(10,4) COMMENT '净现金流-同比增长(%)',
    operating_cf_net_amount DECIMAL(20,2) COMMENT '经营性现金流-现金流量净额(万元)',
    operating_cf_ratio_of_net_cf DECIMAL(10,4) COMMENT '经营性现金流-净现金流占比(%)',
    operating_cf_cash_from_sales DECIMAL(20,2) COMMENT '经营性现金流-销售商品收到的现金（万元）',
    investing_cf_net_amount DECIMAL(20,2) COMMENT '投资性现金流-现金流量净额(万元)',
    investing_cf_ratio_of_net_cf DECIMAL(10,4) COMMENT '投资性现金流-净现金流占比(%)',
    investing_cf_cash_for_investments DECIMAL(20,2) COMMENT '投资性现金流-投资支付的现金（万元）',
    investing_cf_cash_from_investment_recovery DECIMAL(20,2) COMMENT '投资性现金流-收回投资收到的现金（万元）',
    financing_cf_cash_from_borrowing DECIMAL(20,2) COMMENT '融资性现金流-取得借款收到的现金（万元）',
    financing_cf_cash_for_debt_repayment DECIMAL(20,2) COMMENT '融资性现金流-偿还债务支付的现金（万元）',
    financing_cf_net_amount DECIMAL(20,2) COMMENT '融资性现金流-现金流量净额(万元)',
    financing_cf_ratio_of_net_cf DECIMAL(10,4) COMMENT '融资性现金流-净现金流占比(%)',
    report_period VARCHAR(20) COMMENT '报告期',
    report_year INT COMMENT '报告期-年份'
) COMMENT '现金流量表';
"""
    }
    return ddl_statements


if __name__ == "__main__":
    init_database()
