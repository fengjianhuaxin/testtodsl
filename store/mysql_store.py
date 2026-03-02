"""MySQL data store implementation."""
import pandas as pd
import pymysql
from pymysql.cursors import DictCursor

from store.base_store import DataStore


class MySQLDataStore(DataStore):
    def __init__(self, mysql_config: dict, mapping_manager):
        self.mysql_config = dict(mysql_config or {})
        self.mapping = mapping_manager
        self._conn = None

    def _get_connection(self):
        if self._conn is None:
            self._conn = pymysql.connect(
                host=self.mysql_config.get("host", "localhost"),
                port=int(self.mysql_config.get("port", 3306)),
                user=self.mysql_config.get("user", ""),
                password=self.mysql_config.get("password", ""),
                database=self.mysql_config.get("database", ""),
                charset="utf8mb4",
                cursorclass=DictCursor,
                autocommit=True,
            )
        else:
            self._conn.ping(reconnect=True)
        return self._conn

    def _execute(self, sql: str, params=None) -> pd.DataFrame:
        connection = self._get_connection()
        with connection.cursor() as cursor:
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            rows = cursor.fetchall()
        return pd.DataFrame(rows)

    def _table_name(self, source_id: str, entity_name: str) -> str:
        table_name = self.mapping.get_table_name(source_id, entity_name)
        return table_name or entity_name

    def _actual_field(self, source_id: str, entity_name: str, ontology_field: str) -> str:
        actual = self.mapping.ontology_field_to_actual(source_id, entity_name, ontology_field)
        return actual or ontology_field

    @staticmethod
    def _quote(identifier: str) -> str:
        return f"`{str(identifier).replace('`', '``')}`"

    def _build_where(self, source_id: str, entity_name: str, conditions: list):
        if not conditions:
            return "", []

        clauses = []
        params = []
        for condition in conditions:
            ontology_field = condition.get("field", "")
            op = str(condition.get("op", "=")).strip().lower()
            value = condition.get("value")
            actual_field = self._actual_field(source_id, entity_name, ontology_field)
            field_sql = self._quote(actual_field)

            if op == "contains":
                clauses.append(f"{field_sql} LIKE %s")
                params.append(f"%{value}%")
            elif op == "in":
                if not isinstance(value, (list, tuple, set)) or not value:
                    clauses.append("1=0")
                else:
                    holders = ", ".join(["%s"] * len(value))
                    clauses.append(f"{field_sql} IN ({holders})")
                    params.extend(list(value))
            else:
                allowed = {"=", "!=", ">", "<", ">=", "<="}
                sql_op = op if op in allowed else "="
                clauses.append(f"{field_sql} {sql_op} %s")
                params.append(value)

        if not clauses:
            return "", []
        return " WHERE " + " AND ".join(clauses), params

    def execute_sql(self, source_id: str, sql: str) -> pd.DataFrame:
        return self._execute(sql)

    def load_table(self, source_id: str, entity_name: str) -> pd.DataFrame:
        table_name = self._table_name(source_id, entity_name)
        sql = f"SELECT * FROM {self._quote(table_name)}"
        df = self._execute(sql)
        reverse_mapping = self.mapping.get_reverse_field_mapping(source_id, entity_name)
        if not df.empty and reverse_mapping:
            df = df.rename(columns=reverse_mapping)
        return df

    def query(self, source_id: str, entity_name: str, conditions: list = None, fields: list = None) -> pd.DataFrame:
        table_name = self._table_name(source_id, entity_name)

        if fields:
            select_parts = []
            for field_name in fields:
                actual_field = self._actual_field(source_id, entity_name, field_name)
                select_parts.append(f"{self._quote(actual_field)} AS {self._quote(field_name)}")
            select_sql = ", ".join(select_parts)
        else:
            select_sql = "*"

        where_sql, params = self._build_where(source_id, entity_name, conditions or [])
        sql = f"SELECT {select_sql} FROM {self._quote(table_name)}{where_sql}"
        df = self._execute(sql, params)

        if not fields:
            reverse_mapping = self.mapping.get_reverse_field_mapping(source_id, entity_name)
            if reverse_mapping:
                df = df.rename(columns=reverse_mapping)

        return df.reset_index(drop=True)

    def execute_join(self, source_id: str, join_spec: dict) -> pd.DataFrame:
        base_entity = join_spec["base_entity"]
        dataframe = self.load_table(source_id, base_entity)

        for join in join_spec.get("joins", []):
            join_df = self.load_table(source_id, join["entity"])
            dataframe = dataframe.merge(
                join_df,
                left_on=join["left_on"],
                right_on=join["right_on"],
                how="left",
                suffixes=("", f"_{join['entity']}"),
            )

        conditions = join_spec.get("conditions", [])
        if conditions:
            for condition in conditions:
                field = condition["field"]
                op = condition["op"]
                value = condition["value"]
                if field not in dataframe.columns:
                    continue
                if op == "=":
                    dataframe = dataframe[dataframe[field] == value]
                elif op == ">":
                    dataframe = dataframe[dataframe[field] > value]
                elif op == "<":
                    dataframe = dataframe[dataframe[field] < value]
                elif op == "contains":
                    dataframe = dataframe[dataframe[field].astype(str).str.contains(str(value), na=False)]

        fields = join_spec.get("fields")
        if fields:
            valid_fields = [field for field in fields if field in dataframe.columns]
            if valid_fields:
                dataframe = dataframe[valid_fields]

        return dataframe.reset_index(drop=True)

    def aggregate(self, source_id: str, entity_name: str, group_by: list = None, agg_specs: list = None, conditions: list = None) -> pd.DataFrame:
        dataframe = self.query(source_id, entity_name, conditions=conditions)
        if not agg_specs:
            return dataframe

        agg_dict = {}
        for spec in agg_specs:
            field_name = spec["field"]
            func_name = spec["func"]
            func_map = {"count": "count", "sum": "sum", "avg": "mean", "max": "max", "min": "min"}
            if field_name in dataframe.columns and func_name in func_map:
                agg_dict[field_name] = func_map[func_name]

        if group_by:
            valid_group = [field for field in group_by if field in dataframe.columns]
            if valid_group and agg_dict:
                return dataframe.groupby(valid_group).agg(agg_dict).reset_index()
        elif agg_dict:
            result = {}
            for field_name, func in agg_dict.items():
                if func == "count":
                    result[f"{field_name}_count"] = [dataframe[field_name].count()]
                elif func == "sum":
                    result[f"{field_name}_sum"] = [dataframe[field_name].sum()]
                elif func == "mean":
                    result[f"{field_name}_avg"] = [dataframe[field_name].mean()]
                elif func == "max":
                    result[f"{field_name}_max"] = [dataframe[field_name].max()]
                elif func == "min":
                    result[f"{field_name}_min"] = [dataframe[field_name].min()]
            return pd.DataFrame(result)

        return dataframe
