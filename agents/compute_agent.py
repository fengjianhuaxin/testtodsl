"""Compute agent: execute query plan on configured data store."""
import pandas as pd

from agents.base_agent import BaseAgent


class ComputeAgent(BaseAgent):
    def __init__(self, data_store, mapping_manager, ontology_manager):
        super().__init__("计算执行智能体", "执行查询并从数据存储获取结果")
        self.store = data_store
        self.mapping = mapping_manager
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        query_plan = input_data.get("query_plan", {})
        conditions = input_data.get("conditions", [])
        extracted_fields = input_data.get("extracted_fields", [])
        calc_rule = input_data.get("calc_rule", {})
        query_spec = input_data.get("query_spec", {})
        dispatch = input_data.get("dispatch", {})
        dsl_query = input_data.get("dsl_query", {})

        self.log("开始执行计算...")
        source_id = str(dispatch.get("source_id", "")).strip() or str(input_data.get("source_id", "")).strip()

        try:
            sql_text = dsl_query.get(source_id, {}).get("sql")
            if sql_text and hasattr(self.store, "execute_sql"):
                self.log(f"  数据源 {source_id}: 使用 SQL 执行")
                result = self.store.execute_sql(source_id, sql_text)
            else:
                result = self._execute_for_source(
                    source_id, query_plan, conditions, extracted_fields, calc_rule, query_spec
                )

            if not isinstance(result, pd.DataFrame):
                result = pd.DataFrame(result)
            final_df = result
            self.log(f"  数据源 {source_id}: 获得 {len(final_df)} 条结果")
        except Exception as error:
            self.log(f"  数据源 {source_id} 执行失败: {error}")
            final_df = pd.DataFrame()

        self.log(f"计算执行完成: 共{len(final_df)}条结果")
        return {
            **input_data,
            "compute_result": final_df.to_dict(orient="records"),
            "compute_result_df": final_df,
        }

    def _execute_for_source(self, source_id, query_plan, conditions, fields, calc_rule, query_spec=None):
        entities = query_plan.get("entities", [])
        if not entities:
            return pd.DataFrame()

        calc_type, calc_params = self._resolve_calc_rule(calc_rule, query_spec)

        if len(entities) == 1:
            entity_name = entities[0]["name"]
            return self._simple_query(source_id, entity_name, conditions, fields, calc_type, calc_params)
        return self._join_query(source_id, query_plan, conditions, fields, calc_type, calc_params)

    def _resolve_calc_rule(self, calc_rule: dict, query_spec: dict | None) -> tuple[str, dict]:
        calc_type = str((calc_rule or {}).get("type", "detail")).strip().lower() or "detail"
        calc_params = dict((calc_rule or {}).get("params", {})) if isinstance(calc_rule, dict) else {}
        if not isinstance(query_spec, dict) or not query_spec:
            return calc_type, calc_params

        mode = str(query_spec.get("query_mode", "")).strip().lower()
        if mode == "detail":
            return ("topn", calc_params) if calc_params.get("order_by") and calc_params.get("limit") else ("detail", calc_params)
        if mode == "custom_sql":
            return "custom_sql", calc_params
        if mode != "aggregate":
            return calc_type, calc_params

        dimensions = query_spec.get("dimensions", [])
        if isinstance(dimensions, str):
            dimensions = [dimensions]
        if isinstance(dimensions, list) and dimensions:
            calc_params["group_by"] = [str(item).strip() for item in dimensions if str(item).strip()]

        sort_items = query_spec.get("sort", [])
        if isinstance(sort_items, dict):
            sort_items = [sort_items]
        if isinstance(sort_items, list) and sort_items:
            sort_item = sort_items[0] if isinstance(sort_items[0], dict) else {}
            order_by = str(sort_item.get("by", "")).strip()
            order_dir = str(sort_item.get("dir", "desc")).strip().lower()
            if order_by:
                calc_params["order_by"] = order_by
            if order_dir in ("asc", "desc"):
                calc_params["order_dir"] = order_dir

        try:
            limit = query_spec.get("limit", None)
            if limit is not None:
                limit_num = int(limit)
                if limit_num > 0:
                    calc_params["limit"] = limit_num
        except Exception:
            pass

        measures = query_spec.get("measures", [])
        if isinstance(measures, dict):
            measures = [measures]
        if not isinstance(measures, list) or not measures:
            return calc_type, calc_params
        measure = measures[0] if isinstance(measures[0], dict) else {}
        agg = str(measure.get("agg", "")).strip().lower()

        if agg == "count":
            return ("group_count", calc_params) if calc_params.get("group_by") else ("count", calc_params)
        if agg in ("sum", "avg", "max", "min"):
            return agg, calc_params
        if agg == "rate":
            rate_field = str(measure.get("field", "")).strip()
            if rate_field:
                calc_params["rate_field"] = self._normalize_field_ref(rate_field)
            options = measure.get("options", {}) if isinstance(measure, dict) else {}
            if isinstance(options, dict):
                true_values = options.get("true_values", [])
                if isinstance(true_values, list) and true_values:
                    calc_params["rate_true_values"] = true_values
            alias = str(measure.get("alias", "")).strip()
            if alias:
                calc_params["metric_alias"] = alias
            return "rate", calc_params

        return calc_type, calc_params

    def _simple_query(self, source_id, entity_name, conditions, fields, calc_type, calc_params):
        store_conditions = [
            {"field": cond["field"], "op": cond["op"], "value": cond["value"]}
            for cond in conditions
            if cond.get("entity") == entity_name
        ]
        field_names = [field["field"] for field in fields if field.get("entity") == entity_name]

        if calc_type == "count":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            group_by = [self._normalize_field_ref(group) for group in group_by]
            dataframe = self.store.query(source_id, entity_name, conditions=store_conditions)
            if group_by:
                valid_groups = [group for group in group_by if group in dataframe.columns]
                if valid_groups:
                    return dataframe.groupby(valid_groups).size().reset_index(name="count")
            return pd.DataFrame([{"total_count": int(len(dataframe))}])

        if calc_type in ("avg", "sum", "max", "min"):
            agg_field = None
            for field in fields:
                if field.get("entity") == entity_name and field.get("type") == "number":
                    agg_field = field["field"]
                    break
            if agg_field:
                agg_specs = [{"field": agg_field, "func": calc_type}]
                group_by = calc_params.get("group_by")
                if group_by:
                    group_by = group_by if isinstance(group_by, list) else [group_by]
                    group_by = [self._normalize_field_ref(group) for group in group_by]
                return self.store.aggregate(
                    source_id,
                    entity_name,
                    group_by=group_by,
                    agg_specs=agg_specs,
                    conditions=store_conditions,
                )

        if calc_type == "rate":
            dataframe = self.store.query(source_id, entity_name, conditions=store_conditions)
            return self._calc_rate(dataframe, calc_params)

        if calc_type == "group_count":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            group_by = [self._normalize_field_ref(group) for group in group_by]
            dataframe = self.store.query(source_id, entity_name, conditions=store_conditions)
            if group_by:
                valid_groups = [group for group in group_by if group in dataframe.columns]
                if valid_groups:
                    result_df = dataframe.groupby(valid_groups).size().reset_index(name="count")
                    return self._apply_order_limit(result_df, calc_params, metric_cols=("count", "count_value", "total_count"))
            return dataframe

        dataframe = self.store.query(
            source_id,
            entity_name,
            conditions=store_conditions,
            fields=field_names if field_names else None,
        )

        if calc_type == "topn":
            order_by = calc_params.get("order_by", "")
            order_by = self._normalize_field_ref(order_by)
            limit = calc_params.get("limit", 10)
            if order_by and order_by in dataframe.columns:
                dataframe = dataframe.sort_values(order_by, ascending=False).head(limit)

        return dataframe

    def _join_query(self, source_id, query_plan, conditions, fields, calc_type, calc_params):
        entities = query_plan.get("entities", [])
        join_type_map = self._build_join_type_map(query_plan)
        base_entity = entities[0]["name"]
        dataframe = self.store.load_table(source_id, base_entity)

        for index, entity in enumerate(entities[1:], start=1):
            entity_name = entity["name"]
            previous_entity = entities[index - 1]["name"]
            join_df = self.store.load_table(source_id, entity_name)

            join_key_pairs = self.ontology.resolve_join_field_pairs(previous_entity, entity_name)
            if join_key_pairs:
                left_on = [left for left, _ in join_key_pairs]
                right_on = [right for _, right in join_key_pairs]
                if len(left_on) == 1:
                    left_on = left_on[0]
                    right_on = right_on[0]
            else:
                fallback_join_key = self._find_join_field(dataframe, join_df)
                if fallback_join_key:
                    left_on, right_on = fallback_join_key
                else:
                    left_on, right_on = None, None

            if left_on and right_on:
                join_type = join_type_map.get((str(previous_entity).upper(), str(entity_name).upper()), "inner")
                how_type = "left" if join_type == "left" else "inner"
                dataframe = dataframe.merge(
                    join_df,
                    left_on=left_on,
                    right_on=right_on,
                    how=how_type,
                    suffixes=("", f"_{entity_name}"),
                )

        for condition in conditions:
            field = condition.get("field", "")
            op = condition.get("op", "=")
            value = condition.get("value", "")
            if field not in dataframe.columns:
                continue
            if op == "=":
                dataframe = dataframe[dataframe[field] == value]
            elif op == "!=":
                dataframe = dataframe[dataframe[field] != value]
            elif op == ">":
                dataframe = dataframe[dataframe[field] > float(value)]
            elif op == "<":
                dataframe = dataframe[dataframe[field] < float(value)]
            elif op == ">=":
                dataframe = dataframe[dataframe[field] >= float(value)]
            elif op == "<=":
                dataframe = dataframe[dataframe[field] <= float(value)]
            elif op == "contains":
                dataframe = dataframe[dataframe[field].astype(str).str.contains(str(value), na=False)]
            elif op == "in" and isinstance(value, list):
                dataframe = dataframe[dataframe[field].isin(value)]

        if calc_type == "count":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            group_by = [self._normalize_field_ref(group) for group in group_by]
            valid_groups = [group for group in group_by if group in dataframe.columns]
            if valid_groups:
                return dataframe.groupby(valid_groups).size().reset_index(name="count")
            return pd.DataFrame([{"total_count": int(len(dataframe))}])

        field_names = [field["field"] for field in fields]
        valid_fields = [field for field in field_names if field in dataframe.columns]
        if valid_fields:
            dataframe = dataframe[valid_fields]

        if calc_type in ("avg", "sum", "max", "min"):
            numeric_columns = dataframe.select_dtypes(include="number").columns.tolist()
            if numeric_columns:
                group_by = calc_params.get("group_by")
                if group_by:
                    group_by = group_by if isinstance(group_by, list) else [group_by]
                    group_by = [self._normalize_field_ref(group) for group in group_by]
                    valid_groups = [group for group in group_by if group in dataframe.columns]
                    if valid_groups:
                        func_map = {"avg": "mean", "sum": "sum", "max": "max", "min": "min"}
                        return dataframe.groupby(valid_groups)[numeric_columns].agg(func_map[calc_type]).reset_index()
                func_map = {"avg": "mean", "sum": "sum", "max": "max", "min": "min"}
                return pd.DataFrame([dataframe[numeric_columns].agg(func_map[calc_type])])

        if calc_type == "group_count":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            group_by = [self._normalize_field_ref(group) for group in group_by]
            valid_groups = [group for group in group_by if group in dataframe.columns]
            if valid_groups:
                result_df = dataframe.groupby(valid_groups).size().reset_index(name="count")
                return self._apply_order_limit(result_df, calc_params, metric_cols=("count", "count_value", "total_count"))

        if calc_type == "rate":
            return self._calc_rate(dataframe, calc_params)

        if calc_type == "topn":
            order_by = calc_params.get("order_by", "")
            order_by = self._normalize_field_ref(order_by)
            limit = calc_params.get("limit", 10)
            if order_by and order_by in dataframe.columns:
                dataframe = dataframe.sort_values(order_by, ascending=False).head(limit)

        return dataframe.reset_index(drop=True)

    def _calc_rate(self, dataframe: pd.DataFrame, calc_params: dict) -> pd.DataFrame:
        if dataframe is None or dataframe.empty:
            return pd.DataFrame([{"rate_value": None}])

        rate_field = self._normalize_field_ref(calc_params.get("rate_field", ""))
        if not rate_field or rate_field not in dataframe.columns:
            for col in dataframe.columns:
                col_text = str(col).upper()
                if col_text.startswith("IS_") or col_text.startswith("IS"):
                    rate_field = col
                    break
        if not rate_field or rate_field not in dataframe.columns:
            return pd.DataFrame([{"rate_value": None}])

        true_values = calc_params.get("rate_true_values", ["是", "1", "true", "TRUE", "Y", "y", "yes", "YES"])
        if isinstance(true_values, str):
            true_values = [x.strip() for x in true_values.split(",") if x.strip()]
        if not isinstance(true_values, list) or not true_values:
            true_values = ["是", "1", "true", "TRUE", "Y", "y", "yes", "YES"]
        true_set = {str(x) for x in true_values}

        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []
        group_by = [self._normalize_field_ref(group) for group in group_by]
        group_by = [g for g in group_by if g in dataframe.columns]

        def _series_rate(series: pd.Series):
            total = len(series)
            if total <= 0:
                return None
            hit = series.astype(str).isin(true_set).sum()
            return round(hit * 100.0 / total, 2)

        metric_alias = str(calc_params.get("metric_alias", "rate_value")).strip() or "rate_value"
        if group_by:
            result = dataframe.groupby(group_by)[rate_field].apply(_series_rate).reset_index(name=metric_alias)
            return result
        return pd.DataFrame([{metric_alias: _series_rate(dataframe[rate_field])}])

    @staticmethod
    def _normalize_join_type(value) -> str:
        text = str(value or "").strip().lower()
        if text in {"left", "left_join", "left join"}:
            return "left"
        return "inner"

    def _build_join_type_map(self, query_plan: dict) -> dict:
        result = {}
        joins = query_plan.get("joins", []) if isinstance(query_plan, dict) else []
        if not isinstance(joins, list):
            return result
        for item in joins:
            if not isinstance(item, dict):
                continue
            left_entity = str(item.get("left_entity", "")).strip().upper()
            right_entity = str(item.get("right_entity", "")).strip().upper()
            if not left_entity or not right_entity:
                continue
            result[(left_entity, right_entity)] = self._normalize_join_type(item.get("join_type", "inner"))
        return result

    @staticmethod
    def _find_join_field(df1, df2):
        common_cols = set(df1.columns) & set(df2.columns)
        id_cols = [
            col for col in common_cols
            if col.endswith("_id") or col in ("student_id", "course_id", "class_id", "teacher_id")
        ]
        if id_cols:
            return id_cols[0], id_cols[0]
        if common_cols:
            field = list(common_cols)[0]
            return field, field
        return None

    @staticmethod
    def _normalize_field_ref(field_ref) -> str:
        text = str(field_ref or "").strip()
        if "." in text:
            return text.rsplit(".", 1)[1].strip()
        return text

    def _apply_order_limit(self, dataframe: pd.DataFrame, calc_params: dict, metric_cols=("metric_value",)) -> pd.DataFrame:
        if dataframe is None or dataframe.empty or not isinstance(calc_params, dict):
            return dataframe

        result = dataframe
        order_by_raw = str(calc_params.get("order_by", "")).strip()
        order_dir = str(calc_params.get("order_dir", "desc")).strip().lower()
        ascending = order_dir == "asc"

        target_col = ""
        if order_by_raw in {"__metric__", "metric"}:
            for col in metric_cols:
                if str(col) in result.columns:
                    target_col = str(col)
                    break
        elif order_by_raw:
            order_field = self._normalize_field_ref(order_by_raw)
            if order_field in result.columns:
                target_col = order_field

        if target_col:
            result = result.sort_values(target_col, ascending=ascending)

        limit_value = calc_params.get("limit")
        if limit_value is not None:
            try:
                limit_num = int(limit_value)
                if limit_num > 0:
                    result = result.head(limit_num)
            except Exception:
                pass

        return result
