"""Sheet 存储实现 - 使用 Excel 文件模拟数据库

后续替换为 MySQL 时，只需实现 MySQLDataStore 子类。
"""
import os
import pandas as pd
from store.base_store import DataStore


class SheetDataStore(DataStore):
    """基于 Excel 文件的数据存储实现"""

    def __init__(self, data_sources: dict, mapping_manager):
        """
        Args:
            data_sources: {source_id: 目录路径} 映射
            mapping_manager: MappingManager 实例
        """
        self.data_sources = data_sources
        self.mapping = mapping_manager
        self._cache = {}  # 缓存已加载的表

    def load_table(self, source_id: str, entity_name: str) -> pd.DataFrame:
        """加载 Excel 表并用本体字段名重命名列"""
        cache_key = f"{source_id}:{entity_name}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        file_name = self.mapping.get_file_name(source_id, entity_name)
        if not file_name:
            raise ValueError(f"未找到 {source_id} 中 {entity_name} 的文件映射")

        file_path = os.path.join(self.data_sources[source_id], file_name)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"数据文件不存在: {file_path}")

        df = pd.read_excel(file_path)

        # 将实际字段名替换为本体属性名
        reverse_mapping = self.mapping.get_reverse_field_mapping(source_id, entity_name)
        df = df.rename(columns=reverse_mapping)

        self._cache[cache_key] = df
        return df.copy()

    def query(self, source_id: str, entity_name: str,
              conditions: list = None, fields: list = None) -> pd.DataFrame:
        """带条件查询
        
        Args:
            source_id: 数据源ID
            entity_name: 本体实体名
            conditions: [{"field": str, "op": str, "value": any}, ...]
                op 支持: "=", "!=", ">", "<", ">=", "<=", "contains", "in"
            fields: 要返回的本体属性名列表
        """
        df = self.load_table(source_id, entity_name)

        # 应用条件
        if conditions:
            for cond in conditions:
                field = cond["field"]
                op = cond["op"]
                value = cond["value"]

                if field not in df.columns:
                    continue

                if op == "=":
                    df = df[df[field] == value]
                elif op == "!=":
                    df = df[df[field] != value]
                elif op == ">":
                    df = df[df[field] > value]
                elif op == "<":
                    df = df[df[field] < value]
                elif op == ">=":
                    df = df[df[field] >= value]
                elif op == "<=":
                    df = df[df[field] <= value]
                elif op == "contains":
                    df = df[df[field].astype(str).str.contains(str(value), na=False)]
                elif op == "in":
                    df = df[df[field].isin(value)]

        # 选择字段
        if fields:
            valid_fields = [f for f in fields if f in df.columns]
            if valid_fields:
                df = df[valid_fields]

        return df.reset_index(drop=True)

    def execute_join(self, source_id: str, join_spec: dict) -> pd.DataFrame:
        """执行多表关联查询
        
        Args:
            join_spec: {
                "base_entity": str,
                "joins": [{"entity": str, "left_on": str, "right_on": str}, ...],
                "conditions": [...],  # 可选
                "fields": [...]       # 可选
            }
        """
        base_entity = join_spec["base_entity"]
        df = self.load_table(source_id, base_entity)

        for join in join_spec.get("joins", []):
            join_df = self.load_table(source_id, join["entity"])
            df = df.merge(join_df, left_on=join["left_on"], right_on=join["right_on"],
                         how="left", suffixes=("", f"_{join['entity']}"))

        # 应用条件
        conditions = join_spec.get("conditions", [])
        if conditions:
            for cond in conditions:
                field = cond["field"]
                op = cond["op"]
                value = cond["value"]
                if field in df.columns:
                    if op == "=":
                        df = df[df[field] == value]
                    elif op == ">":
                        df = df[df[field] > value]
                    elif op == "<":
                        df = df[df[field] < value]
                    elif op == "contains":
                        df = df[df[field].astype(str).str.contains(str(value), na=False)]

        # 选择字段
        fields = join_spec.get("fields")
        if fields:
            valid_fields = [f for f in fields if f in df.columns]
            if valid_fields:
                df = df[valid_fields]

        return df.reset_index(drop=True)

    def aggregate(self, source_id: str, entity_name: str,
                  group_by: list = None, agg_specs: list = None,
                  conditions: list = None) -> pd.DataFrame:
        """聚合查询
        
        Args:
            group_by: 分组字段列表
            agg_specs: [{"field": str, "func": "count"|"sum"|"avg"|"max"|"min"}, ...]
            conditions: 前置过滤条件
        """
        df = self.query(source_id, entity_name, conditions=conditions)

        if not agg_specs:
            return df

        agg_dict = {}
        for spec in agg_specs:
            field = spec["field"]
            func = spec["func"]
            func_map = {"count": "count", "sum": "sum", "avg": "mean", "max": "max", "min": "min"}
            if field in df.columns and func in func_map:
                agg_dict[field] = func_map[func]

        if group_by:
            valid_group = [g for g in group_by if g in df.columns]
            if valid_group and agg_dict:
                result = df.groupby(valid_group).agg(agg_dict).reset_index()
                return result
        elif agg_dict:
            result = {}
            for field, func in agg_dict.items():
                if func == "count":
                    result[f"{field}_count"] = [df[field].count()]
                elif func == "sum":
                    result[f"{field}_sum"] = [df[field].sum()]
                elif func == "mean":
                    result[f"{field}_avg"] = [df[field].mean()]
                elif func == "max":
                    result[f"{field}_max"] = [df[field].max()]
                elif func == "min":
                    result[f"{field}_min"] = [df[field].min()]
            return pd.DataFrame(result)

        return df
