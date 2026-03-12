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
    def load_table(self, source_id: str, entity_name: str) -> pd.DataFrame:
        """加载指定数据源的指定实体对应的表数据"""
        pass

    @abstractmethod
    def query(self, source_id: str, entity_name: str,
              conditions: list = None, fields: list = None) -> pd.DataFrame:
        """带条件查询"""
        pass

    @abstractmethod
    def execute_join(self, source_id: str, join_spec: dict) -> pd.DataFrame:
        """执行多表关联查询"""
        pass
