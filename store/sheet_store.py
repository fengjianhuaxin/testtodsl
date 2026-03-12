"""Sheet 存储实现 - 使用 Excel 文件模拟数据库

后续替换为 MySQL 时，只需实现 MySQLDataStore 子类。
"""
import os
import sqlite3
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

    def execute_sql(self, source_id: str, sql: str) -> pd.DataFrame:
        """Execute SQL in sheet mode by loading mapped sheets into SQLite."""
        source_dir = self.data_sources.get(source_id)
        if not source_dir:
            raise ValueError(f"未找到数据源目录: {source_id}")

        connection = sqlite3.connect(":memory:")
        try:
            self._load_source_tables_to_sqlite(source_id, source_dir, connection)
            return pd.read_sql_query(sql, connection)
        finally:
            connection.close()

    def _load_source_tables_to_sqlite(self, source_id: str, source_dir: str, connection):
        mapping_payload = getattr(self.mapping, "mappings", {}).get(source_id, {})
        table_mappings = mapping_payload.get("table_mappings", {}) if isinstance(mapping_payload, dict) else {}
        if not isinstance(table_mappings, dict):
            table_mappings = {}
        table_relations = mapping_payload.get("table_relations", []) if isinstance(mapping_payload, dict) else []
        if isinstance(table_relations, dict):
            table_relations = list(table_relations.values())
        if not isinstance(table_relations, list):
            table_relations = []

        loaded = set()
        file_index_by_table = {}

        def _index_file(table_name: str, file_name: str):
            table_text = str(table_name or "").strip().lower()
            file_text = str(file_name or "").strip()
            if table_text and file_text and table_text not in file_index_by_table:
                file_index_by_table[table_text] = file_text

        def _guess_file_name(table_name: str, explicit_file: str = "") -> str:
            explicit_text = str(explicit_file or "").strip()
            if explicit_text:
                return explicit_text
            table_text = str(table_name or "").strip()
            if not table_text:
                return ""
            cached = file_index_by_table.get(table_text.lower(), "")
            if cached:
                return cached
            candidate = f"{table_text}.xlsx"
            candidate_path = os.path.join(source_dir, candidate)
            if os.path.exists(candidate_path):
                return candidate
            return ""

        def _load_one(table_name: str, file_name: str):
            table_text = str(table_name or "").strip()
            file_text = _guess_file_name(table_text, file_name)
            if not table_text or not file_text:
                return
            if table_text.lower() in loaded:
                return
            file_path = os.path.join(source_dir, file_text)
            if not os.path.exists(file_path):
                return
            dataframe = pd.read_excel(file_path)
            dataframe.to_sql(table_text, connection, if_exists="replace", index=False)
            loaded.add(table_text.lower())

        for table_mapping in table_mappings.values():
            if not isinstance(table_mapping, dict):
                continue

            primary = table_mapping.get("primary_table", {})
            if not isinstance(primary, dict):
                primary = {}
            primary_table = str(primary.get("table_name", "")).strip() or str(table_mapping.get("table_name", "")).strip()
            primary_file = str(primary.get("file_name", "")).strip() or str(table_mapping.get("file_name", "")).strip()
            _index_file(primary_table, primary_file)
            _load_one(primary_table, primary_file)

            secondary = table_mapping.get("secondary_tables", [])
            if isinstance(secondary, dict):
                secondary = [item for item in secondary.values() if isinstance(item, dict)]
            if not isinstance(secondary, list):
                secondary = []
            for item in secondary:
                table_name = str(item.get("table_name", "") or item.get("table", "")).strip()
                file_name = str(item.get("file_name", "")).strip()
                _index_file(table_name, file_name)
                _load_one(table_name, file_name)

            field_sources = table_mapping.get("field_sources", table_mapping.get("field_source_mappings", {}))
            if not isinstance(field_sources, dict):
                field_sources = {}
            for source_item in field_sources.values():
                if not isinstance(source_item, dict):
                    continue
                table_name = str(source_item.get("table_name", "") or source_item.get("table", "")).strip()
                file_name = str(source_item.get("file_name", "")).strip()
                _index_file(table_name, file_name)
                _load_one(table_name, file_name)

        for relation in table_relations:
            if not isinstance(relation, dict):
                continue
            left_table = str(relation.get("left_table", "") or relation.get("from_table", "")).strip()
            right_table = str(relation.get("right_table", "") or relation.get("to_table", "")).strip()
            _load_one(left_table, "")
            _load_one(right_table, "")

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
