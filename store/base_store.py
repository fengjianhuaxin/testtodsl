"""存储抽象基类 - 定义数据存储和本体存储的接口

后续接入真实数据库时，只需：
1. 实现对应子类（MySQLDataStore / Neo4jOntologyStore）
2. 在 config.py 中修改 STORE_TYPE
"""
from abc import ABC, abstractmethod
import pandas as pd


class DataStore(ABC):
    """数据存储抽象基类（当前 Sheet 实现，后续 MySQL）"""

    @abstractmethod
    def execute_sql(self, source_id: str, sql: str) -> pd.DataFrame:
        """Execute SQL and return a DataFrame."""
        pass
