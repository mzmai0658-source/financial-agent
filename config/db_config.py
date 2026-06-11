"""
数据库配置模块
"""

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - dotenv is optional at import time
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


@dataclass
class DatabaseConfig:
    """数据库配置"""
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "financial_report"
    charset: str = "utf8mb4"
    
    @property
    def connection_string(self) -> str:
        """获取 SQLAlchemy 连接字符串"""
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"mysql+pymysql://{user}:{password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"
    
    @property
    def connection_string_no_db(self) -> str:
        """获取不包含数据库名的连接字符串（用于创建数据库）"""
        user = quote_plus(self.user)
        password = quote_plus(self.password)
        return f"mysql+pymysql://{user}:{password}@{self.host}:{self.port}?charset={self.charset}"


def get_db_config() -> DatabaseConfig:
    """
    从环境变量获取数据库配置

    Returns:
        DatabaseConfig 对象
    """
    return DatabaseConfig(
        host=os.getenv("DB_HOST") or os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("DB_PORT") or os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("DB_USER") or os.getenv("MYSQL_USER", "root"),
        password=os.getenv("DB_PASSWORD") or os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("DB_NAME") or os.getenv("MYSQL_DATABASE", "financial_report"),
        charset=os.getenv("DB_CHARSET") or os.getenv("MYSQL_CHARSET", "utf8mb4")
    )


# 默认配置
DEFAULT_CONFIG = DatabaseConfig()
