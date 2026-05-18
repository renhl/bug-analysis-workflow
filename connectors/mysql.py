"""
MySQL 数据库连接器（通用只读）

用于在 Bug 分析时查询相关表的实际数据，辅助定位根因。
支持多业务线：通过 BugAnalysisConfig.databases 按域名索引不同连接配置。
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

try:
    import pymysql
    import pymysql.cursors
    HAS_PYMYSQL = True
except ImportError:
    HAS_PYMYSQL = False


@dataclass
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"
    connect_timeout: int = 10


class MySQLConnector:
    """
    通用 MySQL 只读连接器

    设计原则：
    - 不包含任何业务域专用查询方法
    - 业务域专用查询请在 domains/<domain>/ 中通过 query()/query_one() 自行构造
    """

    def __init__(self, config: MySQLConfig):
        if not HAS_PYMYSQL:
            raise ImportError("pymysql is required: pip install pymysql")
        self.config = config
        self._conn = None

    def _get_conn(self):
        if self._conn is None or not self._check_conn():
            self._conn = pymysql.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                database=self.config.database,
                charset=self.config.charset,
                connect_timeout=self.config.connect_timeout,
                cursorclass=pymysql.cursors.DictCursor,
            )
        return self._conn

    def _check_conn(self) -> bool:
        try:
            self._conn.ping(reconnect=False)
            return True
        except Exception as e:
            logger.debug("Connection ping failed: %s", e)
            return False

    def query(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        """执行只读 SELECT 查询，返回字典列表"""
        sql_stripped = sql.strip().lower()
        if not sql_stripped.startswith(("select", "show", "describe", "desc ", "explain")):
            raise ValueError(f"MySQLConnector.query() 只允许 SELECT/SHOW/DESCRIBE/EXPLAIN 语句，拒绝: {sql[:50]}")
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()

    def query_one(self, sql: str, args: tuple = ()) -> Optional[Dict[str, Any]]:
        """执行只读 SELECT 查询，返回第一条记录"""
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception as e:
                logger.debug("MySQL connection close failed: %s", e)
            self._conn = None
