"""DSL query agent: generate SPARQL and SQL."""
import re

from agents.base_agent import BaseAgent


class DSLQueryAgent(BaseAgent):
    def __init__(self, llm_client, mapping_manager, ontology_manager):
        super().__init__("DSL查询智能体", "将分析结果转为 SPARQL 和 SQL 查询语句")
        self.llm = llm_client
        self.mapping = mapping_manager
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        query_plan = input_data.get("query_plan", {})
        conditions = input_data.get("conditions", [])
        extracted_fields = input_data.get("extracted_fields", [])
        calc_rule = input_data.get("calc_rule", {})
        query_spec = input_data.get("query_spec", {})
        dispatch = input_data.get("dispatch", {})
        self.log("生成 DSL 查询语句...")

        data_sources = dispatch.get("data_sources", ["xksx"])
        custom_sql = str(intent.get("custom_sql", "")).strip()
        dsl_results = {}

        if custom_sql:
            rule_id = str(intent.get("custom_sql_rule_id", "")).strip()
            rule_name = str(intent.get("custom_sql_rule_name", "")).strip()
            if rule_id or rule_name:
                self.log(f"命中预定义SQL规则: id={rule_id or '-'} name={rule_name or '-'}")
            for source_id in data_sources:
                resolved_sql = self._build_custom_sql(
                    source_id=source_id,
                    intent=intent,
                    query_plan=query_plan,
                    conditions=conditions,
                    extracted_fields=extracted_fields,
                    calc_rule=calc_rule,
                    custom_sql=custom_sql,
                )
                dsl_results[source_id] = {
                    "sparql": "# 使用垂直指标预定义SQL，跳过SPARQL生成",
                    "sql": resolved_sql,
                }
                self.log(f"[{source_id}] SQL(knowledge):\n{resolved_sql}")
            self.log(f"DSL 生成完成: {len(dsl_results)}个数据源")
            return {**input_data, "dsl_query": dsl_results}

        for source_id in data_sources:
            effective_fields = extracted_fields
            effective_calc_rule = calc_rule
            if isinstance(query_spec, dict) and query_spec:
                effective_fields = self._merge_fields_for_query_spec(query_plan, extracted_fields, query_spec)
                effective_calc_rule = self._calc_rule_from_query_spec(query_spec, calc_rule)

            sparql = self._generate_sparql(intent, query_plan, conditions, effective_fields, effective_calc_rule)
            sql = self._generate_sql(source_id, query_plan, conditions, effective_fields, effective_calc_rule)
            dsl_results[source_id] = {"sparql": sparql, "sql": sql}
            self.log(f"[{source_id}] SQL:\n{sql}")

        self.log(f"DSL 生成完成: {len(dsl_results)}个数据源")
        return {**input_data, "dsl_query": dsl_results}

    def _generate_sparql(self, intent, query_plan, conditions, fields, calc_rule):
        entities = query_plan.get("entities", [])
        if not entities:
            return "# 无法生成 SPARQL：缺少实体信息"

        lines = [
            "PREFIX onto: <http://student-mgmt.example.org/ontology#>",
            "PREFIX data: <http://student-mgmt.example.org/data#>",
            "",
        ]

        select_vars = [f"?{field['entity']}_{field['field']}" for field in fields]
        calc_type = calc_rule.get("type", "detail")

        if calc_type == "count":
            lines.append("SELECT (COUNT(*) AS ?total_count)")
        elif calc_type in ("sum", "avg", "max", "min"):
            target_var = select_vars[0] if select_vars else "?value"
            func_map = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}
            alias_map = {"sum": "sum_value", "avg": "average_value", "max": "max_value", "min": "min_value"}
            lines.append(f"SELECT ({func_map[calc_type]}({target_var}) AS ?{alias_map[calc_type]})")
        elif calc_type == "group_count":
            group_by = calc_rule.get("params", {}).get("group_by", [])
            group_vars = [f"?{field}" for field in group_by] if group_by else select_vars[:1]
            lines.append(f"SELECT {' '.join(group_vars)} (COUNT(*) AS ?count_value)")
        else:
            lines.append(f"SELECT {' '.join(select_vars) if select_vars else '*'}")

        lines.append("WHERE {")
        for entity in entities:
            entity_name = entity["name"]
            lines.append(f"  ?{entity_name.lower()} a onto:{entity_name} .")
            for prop in entity.get("properties", []):
                if any(field["field"] == prop and field["entity"] == entity_name for field in fields):
                    lines.append(f"  ?{entity_name.lower()} onto:{prop} ?{entity_name}_{prop} .")

        for condition in conditions:
            entity = condition.get("entity", "")
            field = condition.get("field", "")
            op = str(condition.get("op", "=")).lower()
            value = condition.get("value", "")
            sparql_op = {"=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<="}.get(op, "=")
            if op == "contains":
                lines.append(f'  FILTER (CONTAINS(STR(?{entity}_{field}), "{value}"))')
            else:
                lines.append(f'  FILTER (?{entity}_{field} {sparql_op} "{value}")')

        lines.append("}")

        if calc_type == "group_count":
            group_by = calc_rule.get("params", {}).get("group_by", [])
            if group_by:
                lines.append(f"GROUP BY {' '.join(f'?{field}' for field in group_by)}")

        if calc_type == "topn":
            limit = calc_rule.get("params", {}).get("limit", 10)
            order_by = calc_rule.get("params", {}).get("order_by", "")
            if order_by:
                lines.append(f"ORDER BY DESC(?{order_by})")
            lines.append(f"LIMIT {limit}")

        return "\n".join(lines)

    def _generate_sql(self, source_id, query_plan, conditions, fields, calc_rule):
        entities = query_plan.get("entities", [])
        if not entities:
            return "-- ???? SQL???????"

        join_type_map = self._build_join_type_map(query_plan)
        table_aliases = {}
        from_parts = []
        join_parts = []
        select_infos = []
        pushed_to_on = set()
        join_pairs = []

        for index, entity in enumerate(entities):
            entity_name = entity["name"]
            table_name = self.mapping.get_table_name(source_id, entity_name) or entity_name.lower()
            alias = f"t{index}"
            table_aliases[entity_name] = alias

            if index == 0:
                from_parts.append(f"{table_name} {alias}")
                continue

            prev_entity = entities[index - 1]["name"]
            prev_alias = table_aliases[prev_entity]
            join_on, join_meta = self._find_join_key(source_id, prev_entity, entity_name)
            join_type = join_type_map.get((str(prev_entity).upper(), str(entity_name).upper()), "inner")
            join_keyword = "LEFT JOIN" if join_type == "left" else "JOIN"
            if join_on:
                join_pairs.append(
                    {
                        "left_entity": str(prev_entity).upper(),
                        "right_entity": str(entity_name).upper(),
                        "right_alias": alias,
                        "left_actual": str(join_on[0]),
                        "right_actual": str(join_on[1]),
                    }
                )
                on_parts = [f"{prev_alias}.{join_on[0]} = {alias}.{join_on[1]}"]
                if join_type == "left":
                    for cond_index, condition in enumerate(conditions):
                        if cond_index in pushed_to_on:
                            continue
                        cond_entity = str(condition.get("entity", "")).strip().upper()
                        if cond_entity != str(entity_name).upper():
                            continue
                        field = condition.get("field", "")
                        op = condition.get("op", "=")
                        value = condition.get("value", "")
                        actual_field = self.mapping.ontology_field_to_actual(source_id, entity_name, field) or field
                        sql_condition = self._build_where_condition(alias, actual_field, op, value)
                        if sql_condition:
                            on_parts.append(sql_condition)
                            pushed_to_on.add(cond_index)
                            self.log(
                                f"[{source_id}] LEFT JOIN ON下沉条件: "
                                f"{entity_name}.{field} {op} {value}"
                            )
                join_parts.append(f"{join_keyword} {table_name} {alias} ON {' AND '.join(on_parts)}")
                if join_meta:
                    self.log(
                        f"[{source_id}] JOIN??: "
                        f"{prev_entity}.{join_meta['left_onto']}({join_meta['left_actual']}) = "
                        f"{entity_name}.{join_meta['right_onto']}({join_meta['right_actual']}) "
                        f"??={join_meta['source']}"
                        )
                self.log(f"[{source_id}] JOIN类型: {prev_entity} -> {entity_name} = {join_type.upper()}")
            else:
                self.log(f"[{source_id}] JOIN??: {prev_entity} -> {entity_name} ?????????????")
                from_parts.append(f"{table_name} {alias}")

        for field in fields:
            entity_name = field["entity"]
            ontology_field = field["field"]
            alias = table_aliases.get(entity_name, "t0")
            actual_field = self.mapping.ontology_field_to_actual(source_id, entity_name, ontology_field) or ontology_field
            display_alias = self._normalize_output_alias(field.get("label", ontology_field), fallback=ontology_field)
            select_infos.append({
                "entity": entity_name,
                "ontology_field": ontology_field,
                "actual_field": actual_field,
                "sql_expr": f"{alias}.{actual_field}",
                "alias": display_alias,
                "type": field.get("type", "string"),
            })

        calc_type = calc_rule.get("type", "detail")
        calc_params = dict(calc_rule.get("params", {})) if isinstance(calc_rule, dict) else {}
        if calc_type == "group_count":
            self._prepare_group_count_params(
                source_id=source_id,
                entities=entities,
                table_aliases=table_aliases,
                select_infos=select_infos,
                calc_params=calc_params,
                join_pairs=join_pairs,
            )
        select_sql = self._build_select_sql(calc_type, calc_params, fields, select_infos)

        where_parts = []
        for cond_index, condition in enumerate(conditions):
            if cond_index in pushed_to_on:
                continue
            entity_name = condition.get("entity", "")
            field = condition.get("field", "")
            op = condition.get("op", "=")
            value = condition.get("value", "")
            alias = table_aliases.get(entity_name, "t0")
            actual_field = self.mapping.ontology_field_to_actual(source_id, entity_name, field) or field
            sql_condition = self._build_where_condition(alias, actual_field, op, value)
            if sql_condition:
                where_parts.append(sql_condition)

        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        from_sql = f"FROM {', '.join(from_parts)}"
        join_sql = "\n".join(join_parts)

        extras = []
        grouped_calc_types = {"group_count", "rate", "sum", "avg", "max", "min", "topn"}
        group_info_keys = set()
        if calc_type in grouped_calc_types:
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            group_actual = []
            for group_field in group_by:
                info = self._resolve_select_info(select_infos, group_field)
                if info:
                    group_actual.append(info["sql_expr"])
                    group_info_keys.add((
                        str(info.get("entity", "")),
                        str(info.get("ontology_field", "")),
                        str(info.get("actual_field", "")),
                    ))
            if group_actual:
                extras.append(f"GROUP BY {', '.join(group_actual)}")

        if calc_type == "topn":
            order_by = calc_params.get("order_by", "")
            order_dir = str(calc_params.get("order_dir", "desc")).strip().lower()
            if order_dir not in ("asc", "desc"):
                order_dir = "desc"
            limit = calc_params.get("limit", 10)
            metric_alias = str(calc_params.get("metric_alias", "metric_value")).strip() or "metric_value"
            if order_by:
                if order_by in ("__metric__", "metric", metric_alias):
                    extras.append(f"ORDER BY {self._format_alias(metric_alias)} {order_dir.upper()}")
                else:
                    info = self._resolve_select_info(select_infos, order_by)
                    if info:
                        extras.append(f"ORDER BY {info['sql_expr']} {order_dir.upper()}")
            extras.append(f"LIMIT {limit}")

        if calc_type == "rate":
            order_by = str(calc_params.get("order_by", "")).strip()
            order_dir = str(calc_params.get("order_dir", "desc")).strip().lower()
            if order_dir not in ("asc", "desc"):
                order_dir = "desc"
            metric_alias = str(calc_params.get("metric_alias", "rate_value")).strip() or "rate_value"

            if order_by:
                if order_by in ("__metric__", "rate", "rate_value", metric_alias):
                    extras.append(f"ORDER BY {self._format_alias(metric_alias)} {order_dir.upper()}")
                else:
                    info = self._resolve_select_info(select_infos, order_by)
                    if info:
                        extras.append(f"ORDER BY {info['sql_expr']} {order_dir.upper()}")

            limit = calc_params.get("limit")
            if limit is not None:
                try:
                    limit_num = int(limit)
                    if limit_num > 0:
                        extras.append(f"LIMIT {limit_num}")
                except Exception:
                    pass

        if calc_type in {"sum", "avg", "max", "min"} and group_info_keys:
            metric_alias_map = {"sum": "sum_value", "avg": "average_value", "max": "max_value", "min": "min_value"}
            metric_alias = self._extract_last_select_alias(select_sql) or metric_alias_map.get(calc_type, "metric_value")

            order_by = str(calc_params.get("order_by", "")).strip()
            order_dir = str(calc_params.get("order_dir", "desc")).strip().lower()
            if order_dir not in ("asc", "desc"):
                order_dir = "desc"
            if order_by:
                if order_by in ("__metric__", metric_alias):
                    extras.append(f"ORDER BY {self._format_alias(metric_alias)} {order_dir.upper()}")
                else:
                    info = self._resolve_select_info(select_infos, order_by)
                    if info:
                        info_key = (
                            str(info.get("entity", "")),
                            str(info.get("ontology_field", "")),
                            str(info.get("actual_field", "")),
                        )
                        if info_key in group_info_keys:
                            extras.append(f"ORDER BY {info['sql_expr']} {order_dir.upper()}")
                        else:
                            extras.append(f"ORDER BY {self._format_alias(metric_alias)} {order_dir.upper()}")

            limit = calc_params.get("limit")
            if limit is not None:
                try:
                    limit_num = int(limit)
                    if limit_num > 0:
                        extras.append(f"LIMIT {limit_num}")
                except Exception:
                    pass

        parts = [select_sql, from_sql]
        if join_sql:
            parts.append(join_sql)
        if where_sql:
            parts.append(where_sql)
        if extras:
            parts.append("\n".join(extras))
        return "\n".join(parts)

    @staticmethod
    def _escape_sql_str(value) -> str:
        return str(value).replace("\\", "\\\\").replace("'", "''")

    @staticmethod
    def _normalize_output_alias(alias: str, fallback: str = "value") -> str:
        text = str(alias or "").strip()
        if text:
            return text
        backup = str(fallback or "").strip()
        return backup or "value"

    @staticmethod
    def _escape_identifier(identifier: str) -> str:
        return str(identifier or "").replace("`", "``")

    def _format_alias(self, alias: str) -> str:
        normalized = self._normalize_output_alias(alias)
        return f"`{self._escape_identifier(normalized)}`"

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
            join_type = self._normalize_join_type(item.get("join_type", "inner"))
            result[(left_entity, right_entity)] = join_type
        return result

    @staticmethod
    def _normalize_join_type(value) -> str:
        text = str(value or "").strip().lower()
        if text in {"left", "left_join", "left join"}:
            return "left"
        return "inner"

    def _prepare_group_count_params(
        self,
        source_id: str,
        entities: list,
        table_aliases: dict,
        select_infos: list,
        calc_params: dict,
        join_pairs: list,
    ):
        target_sql = str(calc_params.get("group_count_target_sql", "")).strip()
        if target_sql:
            return

        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []

        group_infos = []
        for group_field in group_by:
            info = self._resolve_select_info(select_infos, group_field)
            if info:
                group_infos.append(info)

        metric_candidates = [info for info in select_infos if info not in group_infos]
        if metric_candidates:
            return

        grouped_entities = {
            str(info.get("entity", "")).strip().upper()
            for info in group_infos
            if str(info.get("entity", "")).strip()
        }

        if not grouped_entities and entities:
            grouped_entities.add(str(entities[0].get("name", "")).strip().upper())

        selected_entity = ""
        selected_alias = ""
        join_right_actual = ""

        for pair in join_pairs:
            right_entity = str(pair.get("right_entity", "")).strip().upper()
            if not right_entity or right_entity in grouped_entities:
                continue
            selected_entity = right_entity
            selected_alias = str(pair.get("right_alias", "")).strip()
            join_right_actual = str(pair.get("right_actual", "")).strip()
            break

        if not selected_entity:
            for entity in entities:
                name = str(entity.get("name", "")).strip().upper()
                if name and name not in grouped_entities:
                    selected_entity = name
                    selected_alias = table_aliases.get(name) or table_aliases.get(name.upper(), "")
                    break

        if not selected_entity:
            return

        inferred_actual = self._infer_count_field_for_entity(
            source_id=source_id,
            entity_name=selected_entity,
            exclude_actual={join_right_actual} if join_right_actual else set(),
        )
        if not inferred_actual:
            return

        alias = selected_alias or table_aliases.get(selected_entity) or ""
        if not alias:
            return

        calc_params["group_count_target_sql"] = f"{alias}.{inferred_actual}"
        calc_params["group_count_distinct"] = True
        self.log(
            f"[{source_id}] group_count计数目标推断: "
            f"{selected_entity}.{inferred_actual} (distinct)"
        )

    def _infer_count_field_for_entity(self, source_id: str, entity_name: str, exclude_actual: set | None = None) -> str:
        exclude = {str(item).strip().upper() for item in (exclude_actual or set()) if str(item).strip()}
        field_mapping = self.mapping.get_field_mapping(source_id, entity_name)
        if not isinstance(field_mapping, dict) or not field_mapping:
            return ""

        candidates = []
        for onto_field, actual_field in field_mapping.items():
            onto_text = str(onto_field or "").strip().upper()
            actual_text = str(actual_field or "").strip()
            if not onto_text or not actual_text:
                continue
            if actual_text.upper() in exclude:
                continue
            candidates.append((onto_text, actual_text))

        if not candidates:
            return ""

        entity_tokens = [token for token in str(entity_name or "").upper().split("_") if token]
        positive_tokens = {
            "SYSTEM",
            "INFO",
            "PROJECT",
            "TASK",
            "CATALOG",
            "RESOURCE",
            "DATA",
            "DATASET",
            "COMPONENT",
            "SERVER",
            "DEVICE",
            "MACHINE",
            "STANDARD",
            "OPERATION",
        }
        negative_tokens = {"DEPT", "AREA", "OFFICE", "TOP", "ROWGUID", "ROW_ID", "IS_DELETE"}

        best_score = -10**9
        best_field = ""
        for onto_text, actual_text in candidates:
            score = 0
            if onto_text.endswith("_CODE"):
                score += 30
            if onto_text.endswith("_ID"):
                score += 20
            if "CODE" in onto_text:
                score += 10

            for token in positive_tokens:
                if token in onto_text:
                    score += 8
            for token in entity_tokens:
                if token and token in onto_text:
                    score += 15
            for token in negative_tokens:
                if token in onto_text:
                    score -= 18

            if score > best_score:
                best_score = score
                best_field = actual_text

        return best_field or candidates[0][1]

    def _build_where_condition(self, alias: str, actual_field: str, op: str, value):
        column = f"{alias}.{actual_field}" if alias else str(actual_field)
        normalized_op = str(op or "=").strip().lower()

        if normalized_op == "contains":
            if isinstance(value, list):
                like_parts = []
                for item in value:
                    safe = self._escape_sql_str(item)
                    like_parts.append(f"{column} LIKE '%{safe}%'")
                if not like_parts:
                    return "1=0"
                return f"({' OR '.join(like_parts)})"
            safe = self._escape_sql_str(value)
            return f"{column} LIKE '%{safe}%'"

        if normalized_op == "in":
            if isinstance(value, list):
                values = value
            elif isinstance(value, tuple):
                values = list(value)
            else:
                values = [value]
            if not values:
                return "1=0"
            escaped_values = [f"'{self._escape_sql_str(item)}'" for item in values]
            return f"{column} IN ({', '.join(escaped_values)})"

        if isinstance(value, list):
            if not value:
                return "1=0"
            escaped_values = [f"'{self._escape_sql_str(item)}'" for item in value]
            return f"{column} IN ({', '.join(escaped_values)})"

        if value is None:
            if normalized_op in ("!=", "<>"):
                return f"{column} IS NOT NULL"
            return f"{column} IS NULL"

        if isinstance(value, str):
            escaped = self._escape_sql_str(value)
            return f"{column} {normalized_op} '{escaped}'"
        return f"{column} {normalized_op} {value}"

    @staticmethod
    def _pick_agg_target(select_infos: list):
        if not select_infos:
            return None
        for info in select_infos:
            if str(info.get("type", "")).lower() == "number":
                return info
        return select_infos[0]

    @staticmethod
    def _split_field_ref(field_ref: str) -> tuple[str, str]:
        text = str(field_ref or "").strip().strip("`")
        if not text:
            return "", ""
        if "." in text:
            entity_name, field_name = text.rsplit(".", 1)
            return entity_name.strip(), field_name.strip()
        return "", text

    def _resolve_select_info(self, select_infos: list, field_ref: str):
        entity_hint, field_name = self._split_field_ref(field_ref)
        if not field_name:
            return None

        field_upper = field_name.upper()
        entity_upper = entity_hint.upper()
        for info in select_infos:
            info_field = str(info.get("ontology_field", "")).upper()
            if info_field != field_upper:
                continue
            if entity_hint and str(info.get("entity", "")).upper() != entity_upper:
                continue
            return info

        if entity_hint:
            return None

        for info in select_infos:
            info_actual = str(info.get("actual_field", "")).upper()
            if info_actual == field_upper:
                return info
        return None

    def _pick_rate_target(self, select_infos: list, calc_params: dict):
        field_name = str(calc_params.get("rate_field", "")).strip()
        if field_name:
            info = self._resolve_select_info(select_infos, field_name)
            if info:
                return info
        for info in select_infos:
            name = str(info.get("ontology_field", "")).upper()
            if name.startswith("IS_") or name.startswith("IS"):
                return info
        return select_infos[0] if select_infos else None

    @staticmethod
    def _is_boolean_like_rate_target(target_info: dict | None) -> bool:
        if not isinstance(target_info, dict):
            return False
        field_name = str(target_info.get("ontology_field", "")).strip().upper()
        field_type = str(target_info.get("type", "")).strip().lower()
        if field_name.startswith("IS_") or field_name.startswith("IS") or field_name.startswith("HAS_"):
            return True
        if field_type in {"boolean", "bool"}:
            return True
        return False

    @staticmethod
    def _build_rate_true_condition(column_sql: str, true_values: list) -> str:
        clauses = []
        for value in true_values:
            if isinstance(value, (int, float)):
                clauses.append(f"{column_sql} = {value}")
                continue
            escaped = str(value).replace("\\", "\\\\").replace("'", "''")
            clauses.append(f"{column_sql} = '{escaped}'")
        if not clauses:
            return "1=0"
        if len(clauses) == 1:
            return clauses[0]
        return f"({' OR '.join(clauses)})"

    def _build_select_sql(self, calc_type: str, calc_params: dict, raw_fields: list, select_infos: list) -> str:
        measure_specs = self._normalize_measure_specs(calc_params.get("measures", []))
        if measure_specs:
            multi_sql = self._build_multi_measure_select(calc_params, select_infos, measure_specs)
            if multi_sql:
                return multi_sql

        if calc_type == "topn":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            if isinstance(group_by, list) and group_by:
                return self._build_topn_select(calc_params, select_infos)
            if select_infos:
                detail_parts = [f"{info['sql_expr']} AS {self._format_alias(info['alias'])}" for info in select_infos]
                return f"SELECT {', '.join(detail_parts)}"
            return "SELECT *"

        if calc_type == "count":
            return "SELECT COUNT(*) AS total_count"

        if calc_type == "rate":
            metric_alias = str(calc_params.get("metric_alias", "rate_value")).strip() or "rate_value"
            target = self._pick_rate_target(select_infos, calc_params)
            if not target:
                return "SELECT COUNT(*) AS total_count"

            true_values = calc_params.get("rate_true_values", [])
            if isinstance(true_values, str):
                true_values = [part.strip() for part in true_values.split(",") if part.strip()]
            if not isinstance(true_values, list):
                true_values = []
            if not true_values:
                if self._is_boolean_like_rate_target(target):
                    true_values = ["\u662f", "1", "true", "TRUE", "Y", "y", "yes", "YES"]
                else:
                    self.log(
                        f"rate字段 {target.get('ontology_field', '')} 未指定真值集合，"
                        "当前使用默认真值 ['是']"
                    )
                    true_values = ["\u662f"]

            condition_sql = self._build_rate_true_condition(target["sql_expr"], true_values)
            rate_expr = (
                f"ROUND(COUNT(CASE WHEN {condition_sql} THEN 1 END) * 100.0 / "
                f"NULLIF(COUNT(*), 0), 2) AS {self._format_alias(metric_alias)}"
            )

            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]

            group_fields_sql = []
            for group_field in group_by if isinstance(group_by, list) else []:
                info = self._resolve_select_info(select_infos, group_field)
                if not info:
                    continue
                group_fields_sql.append(f"{info['sql_expr']} AS {self._format_alias(info['alias'])}")

            if group_fields_sql:
                return f"SELECT {', '.join(group_fields_sql)}, {rate_expr}"
            return f"SELECT {rate_expr}"

        if calc_type in ("sum", "avg", "max", "min"):
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]
            if not isinstance(group_by, list):
                group_by = []

            group_infos = []
            group_fields_sql = []
            for group_field in group_by:
                info = self._resolve_select_info(select_infos, group_field)
                if not info:
                    continue
                group_infos.append(info)
                group_fields_sql.append(f"{info['sql_expr']} AS {self._format_alias(info['alias'])}")

            metric_candidates = [info for info in select_infos if info not in group_infos]
            target = self._pick_agg_target(metric_candidates or select_infos)
            if not target:
                return "SELECT COUNT(*) AS total_count"
            func_map = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}
            alias_map = {"sum": "sum_value", "avg": "average_value", "max": "max_value", "min": "min_value"}
            metric_alias = self._resolve_measure_output_alias(
                requested_alias=alias_map[calc_type],
                requested_field=str(target.get("ontology_field", "")),
                default_alias=alias_map[calc_type],
                target_info=target,
            )
            metric_sql = f"{func_map[calc_type]}({target['sql_expr']}) AS {self._format_alias(metric_alias)}"
            if group_fields_sql:
                return f"SELECT {', '.join(group_fields_sql)}, {metric_sql}"
            return f"SELECT {metric_sql}"

        if calc_type == "group_count":
            group_by = calc_params.get("group_by", [])
            if isinstance(group_by, str):
                group_by = [group_by]

            group_infos = []
            group_fields_sql = []
            for group_field in group_by:
                info = self._resolve_select_info(select_infos, group_field)
                if not info:
                    continue
                group_infos.append(info)
                group_fields_sql.append(f"{info['sql_expr']} AS {self._format_alias(info['alias'])}")

            if group_fields_sql:
                metric_candidates = [info for info in select_infos if info not in group_infos]
                count_expr = "COUNT(*)"
                if metric_candidates:
                    target_sql = str(metric_candidates[0].get("sql_expr", "")).strip()
                    if target_sql:
                        count_expr = f"COUNT({target_sql})"
                else:
                    target_sql = str(calc_params.get("group_count_target_sql", "")).strip()
                    if target_sql:
                        if bool(calc_params.get("group_count_distinct")):
                            count_expr = f"COUNT(DISTINCT {target_sql})"
                        else:
                            count_expr = f"COUNT({target_sql})"
                return f"SELECT {', '.join(group_fields_sql)}, {count_expr} AS count_value"
            if select_infos:
                detail_parts = [f"{info['sql_expr']} AS {self._format_alias(info['alias'])}" for info in select_infos]
                return f"SELECT {', '.join(detail_parts)}"
            return "SELECT *"

        if select_infos:
            detail_parts = [f"{info['sql_expr']} AS {self._format_alias(info['alias'])}" for info in select_infos]
            return f"SELECT {', '.join(detail_parts)}"
        return "SELECT *"

    def _build_topn_select(self, calc_params: dict, select_infos: list) -> str:
        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []

        group_infos = []
        group_fields_sql = []
        for group_field in group_by:
            info = self._resolve_select_info(select_infos, group_field)
            if not info:
                continue
            group_infos.append(info)
            group_fields_sql.append(f"{info['sql_expr']} AS {self._format_alias(info['alias'])}")

        group_info_keys = {
            (
                str(info.get("entity", "")),
                str(info.get("ontology_field", "")),
                str(info.get("actual_field", "")),
            )
            for info in group_infos
        }
        metric_candidates = []
        for info in select_infos:
            info_key = (
                str(info.get("entity", "")),
                str(info.get("ontology_field", "")),
                str(info.get("actual_field", "")),
            )
            if info_key in group_info_keys:
                continue
            metric_candidates.append(info)

        metric_agg = str(calc_params.get("metric_agg", "sum")).strip().lower()
        if metric_agg not in ("sum", "avg", "max", "min", "count"):
            metric_agg = "sum"
        metric_alias = str(calc_params.get("metric_alias", "metric_value")).strip() or "metric_value"

        if metric_agg == "count":
            metric_sql = f"COUNT(*) AS {metric_alias}"
        else:
            metric_field = str(calc_params.get("metric_field", "")).strip()
            target = self._resolve_select_info(select_infos, metric_field) if metric_field else None
            if not target:
                target = self._pick_agg_target(metric_candidates or select_infos)
            if not target:
                return "SELECT COUNT(*) AS total_count"
            func_map = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}
            metric_sql = f"{func_map[metric_agg]}({target['sql_expr']}) AS {metric_alias}"

        select_parts = group_fields_sql + [metric_sql]
        return f"SELECT {', '.join(select_parts)}"

    def _build_multi_measure_select(self, calc_params: dict, select_infos: list, measure_specs: list) -> str:
        if not isinstance(measure_specs, list) or len(measure_specs) <= 1:
            return ""

        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []

        group_infos = []
        group_fields_sql = []
        for group_field in group_by:
            info = self._resolve_select_info(select_infos, group_field)
            if not info:
                continue
            group_infos.append(info)
            group_fields_sql.append(f"{info['sql_expr']} AS {self._format_alias(info['alias'])}")

        group_info_keys = {
            (
                str(info.get("entity", "")),
                str(info.get("ontology_field", "")),
                str(info.get("actual_field", "")),
            )
            for info in group_infos
        }
        metric_candidates = []
        for info in select_infos:
            info_key = (
                str(info.get("entity", "")),
                str(info.get("ontology_field", "")),
                str(info.get("actual_field", "")),
            )
            if info_key in group_info_keys:
                continue
            metric_candidates.append(info)

        metric_parts = []
        func_map = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}
        default_alias = {
            "count": "count_value",
            "sum": "sum_value",
            "avg": "average_value",
            "max": "max_value",
            "min": "min_value",
        }
        for measure in measure_specs:
            agg = str(measure.get("agg", "")).strip().lower()
            if agg not in ("count", "sum", "avg", "max", "min"):
                continue

            alias = str(measure.get("alias", "")).strip() or default_alias.get(agg, "metric_value")
            if agg == "count":
                metric_parts.append(f"COUNT(*) AS {self._format_alias(alias)}")
                continue

            target = None
            field_ref = str(measure.get("field", "")).strip()
            if field_ref:
                target = self._resolve_select_info(select_infos, field_ref)
            if not target:
                target = self._pick_agg_target(metric_candidates or select_infos)
            if not target:
                continue
            alias = self._resolve_measure_output_alias(
                requested_alias=alias,
                requested_field=field_ref,
                default_alias=default_alias.get(agg, "metric_value"),
                target_info=target,
            )
            metric_parts.append(f"{func_map[agg]}({target['sql_expr']}) AS {self._format_alias(alias)}")

        if not metric_parts:
            return ""

        select_parts = group_fields_sql + metric_parts
        return f"SELECT {', '.join(select_parts)}"

    def _resolve_measure_output_alias(
        self,
        requested_alias: str,
        requested_field: str,
        default_alias: str,
        target_info: dict | None,
    ) -> str:
        alias = self._normalize_output_alias(requested_alias, fallback=default_alias)
        if not isinstance(target_info, dict):
            return alias

        preferred = self._normalize_output_alias(
            target_info.get("alias", ""),
            fallback=target_info.get("ontology_field", ""),
        )
        if not preferred:
            return alias

        alias_upper = alias.upper()
        ontology_field = str(target_info.get("ontology_field", "")).strip().upper()
        actual_field = str(target_info.get("actual_field", "")).strip().upper()
        field_text = str(requested_field or "").strip()
        if "." in field_text:
            field_text = field_text.rsplit(".", 1)[1].strip()
        requested_field_upper = field_text.upper()

        if requested_field_upper and alias_upper == str(default_alias or "").strip().upper():
            return preferred
        if requested_field_upper and alias_upper == requested_field_upper:
            return preferred
        if ontology_field and alias_upper == ontology_field:
            return preferred
        if actual_field and alias_upper == actual_field:
            return preferred
        return alias

    @staticmethod
    def _extract_last_select_alias(select_sql: str) -> str:
        text = str(select_sql or "")
        if not text:
            return ""
        matches = re.findall(r"\bAS\s+`([^`]+)`|\bAS\s+([a-zA-Z0-9_\u4e00-\u9fa5]+)", text, flags=re.IGNORECASE)
        if not matches:
            return ""
        last = matches[-1]
        if isinstance(last, tuple):
            return str(last[0] or last[1] or "").strip()
        return str(last).strip()

    def _find_join_key(self, source_id, left_entity, right_entity):
        join_fields = self.ontology.resolve_join_fields(left_entity, right_entity)
        if join_fields:
            left_onto, right_onto = join_fields
            left_actual = self.mapping.ontology_field_to_actual(source_id, left_entity, left_onto) or left_onto
            right_actual = self.mapping.ontology_field_to_actual(source_id, right_entity, right_onto) or right_onto
            return (left_actual, right_actual), {
                "source": "relation_config",
                "left_onto": left_onto,
                "right_onto": right_onto,
                "left_actual": left_actual,
                "right_actual": right_actual,
            }

        left_mapping = self.mapping.get_field_mapping(source_id, left_entity)
        right_mapping = self.mapping.get_field_mapping(source_id, right_entity)
        for left_onto, left_actual in left_mapping.items():
            if right_entity.lower() in left_onto.lower() or left_onto.endswith("_id"):
                for right_onto, right_actual in right_mapping.items():
                    if right_onto == left_onto or right_actual == left_actual:
                        return (left_actual, right_actual), {
                            "source": "fallback_guess",
                            "left_onto": left_onto,
                            "right_onto": right_onto,
                            "left_actual": left_actual,
                            "right_actual": right_actual,
                        }
        for left_onto, left_actual in left_mapping.items():
            for right_onto, right_actual in right_mapping.items():
                if left_onto == right_onto and left_onto.endswith("_id"):
                    return (left_actual, right_actual), {
                        "source": "fallback_guess",
                        "left_onto": left_onto,
                        "right_onto": right_onto,
                        "left_actual": left_actual,
                        "right_actual": right_actual,
                    }
        return None, None

    def _build_custom_sql(self, source_id, intent, query_plan, conditions, extracted_fields, calc_rule, custom_sql: str) -> str:
        parsed = self._parse_select_sql(custom_sql)
        if not parsed:
            self.log(f"[{source_id}] 预定义SQL解析失败，按原SQL执行")
            return custom_sql

        calc_params = calc_rule.get("params", {}) if isinstance(calc_rule, dict) else {}
        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []
        group_by = [
            self._split_field_ref(str(item).strip())[1]
            for item in group_by
            if str(item).strip()
        ]
        group_by = [item for item in group_by if item]

        order_by = str(calc_params.get("order_by", "")).strip()
        if order_by and not order_by.startswith("__"):
            order_by = self._split_field_ref(order_by)[1]
        order_dir = str(calc_params.get("order_dir", "desc")).strip().lower() or "desc"
        if order_dir not in ("asc", "desc"):
            order_dir = "desc"

        limit = calc_params.get("limit")
        try:
            limit = int(limit) if limit is not None else None
            if limit is not None and limit <= 0:
                limit = None
        except Exception:
            limit = None

        # extracted_fields 可能包含维度字段，补入 group_by
        for field in extracted_fields or []:
            field_name = str(field.get("field", "")).strip()
            if field_name and field_name not in group_by:
                group_by.append(field_name)

        has_dynamic_request = bool(group_by or order_by or limit is not None or conditions)
        if not has_dynamic_request:
            return custom_sql

        primary_entity = self._resolve_primary_entity(intent, query_plan)
        primary_alias = None
        from_sql = parsed["from_clause"]
        if self._is_plain_table_name(from_sql):
            from_sql = f"{from_sql} t0"
            primary_alias = "t0"

        group_selects = []
        group_exprs = []
        for group_field in group_by:
            actual = self.mapping.ontology_field_to_actual(source_id, primary_entity, group_field)
            if not actual:
                self.log(f"[{source_id}] 忽略未映射分组字段: {primary_entity}.{group_field}")
                continue
            column = f"{primary_alias}.{actual}" if primary_alias else actual
            group_selects.append(f"{column} AS {group_field}")
            group_exprs.append(column)

        where_parts = []
        for cond in conditions or []:
            cond_entity = cond.get("entity", "")
            if cond_entity and primary_entity and cond_entity != primary_entity:
                continue
            field = cond.get("field", "")
            actual = self.mapping.ontology_field_to_actual(source_id, primary_entity, field)
            if not actual:
                self.log(f"[{source_id}] 忽略未映射条件字段: {primary_entity}.{field}")
                continue
            sql_cond = self._build_where_condition(primary_alias or "", actual, cond.get("op", "="), cond.get("value", ""))
            if sql_cond:
                where_parts.append(sql_cond)

        base_where = parsed.get("where_clause", "")
        if base_where:
            where_parts = [f"({base_where})"] + where_parts

        metric_expr = parsed["select_clause"]
        metric_alias = parsed.get("metric_alias", "")
        if group_selects:
            select_sql = f"SELECT {', '.join(group_selects)}, {metric_expr}"
        else:
            select_sql = f"SELECT {metric_expr}"

        parts = [select_sql, f"FROM {from_sql}"]
        if where_parts:
            parts.append(f"WHERE {' AND '.join(where_parts)}")
        if group_exprs:
            parts.append(f"GROUP BY {', '.join(group_exprs)}")
        elif parsed.get("group_clause"):
            parts.append(f"GROUP BY {parsed['group_clause']}")

        order_sql = self._build_metric_order_sql(
            source_id=source_id,
            entity_name=primary_entity,
            alias=primary_alias,
            order_by=order_by,
            order_dir=order_dir,
            metric_alias=metric_alias,
            group_exprs=group_exprs,
        )
        if order_sql:
            parts.append(order_sql)
        elif parsed.get("order_clause"):
            parts.append(f"ORDER BY {parsed['order_clause']}")

        if limit is not None:
            parts.append(f"LIMIT {limit}")
        elif parsed.get("limit_clause"):
            parts.append(f"LIMIT {parsed['limit_clause']}")

        return "\n".join(parts)

    def _resolve_primary_entity(self, intent: dict, query_plan: dict) -> str:
        entities = query_plan.get("entities", [])
        if entities:
            return entities[0]["name"]
        target_entities = intent.get("target_entities", [])
        if isinstance(target_entities, list) and target_entities:
            return str(target_entities[0])
        return "INFORMATION_SYSTEM"

    @staticmethod
    def _is_plain_table_name(from_clause: str) -> bool:
        text = str(from_clause or "").strip()
        if not text:
            return False
        if " " in text or "," in text:
            return False
        return bool(re.match(r"^[`a-zA-Z0-9_]+$", text))

    @staticmethod
    def _extract_metric_alias(select_clause: str) -> str:
        text = str(select_clause or "").strip()
        matched = re.search(r"\bas\s+`?([a-zA-Z0-9_\u4e00-\u9fa5]+)`?\s*$", text, flags=re.IGNORECASE)
        if matched:
            return matched.group(1)
        return ""

    def _build_metric_order_sql(
        self,
        source_id: str,
        entity_name: str,
        alias: str | None,
        order_by: str,
        order_dir: str,
        metric_alias: str,
        group_exprs: list,
    ) -> str:
        ob = str(order_by or "").strip()
        if not ob:
            return ""

        direction = "ASC" if str(order_dir).lower() == "asc" else "DESC"
        if ob in ("__metric__", "metric", "指标值"):
            if metric_alias:
                return f"ORDER BY `{metric_alias}` {direction}"
            return f"ORDER BY 1 {direction}" if not group_exprs else f"ORDER BY {len(group_exprs) + 1} {direction}"

        if metric_alias and ob == metric_alias:
            return f"ORDER BY `{metric_alias}` {direction}"

        actual = self.mapping.ontology_field_to_actual(source_id, entity_name, ob)
        if not actual:
            self.log(f"[{source_id}] 忽略未映射排序字段: {entity_name}.{ob}")
            return ""
        column = f"{alias}.{actual}" if alias else actual
        return f"ORDER BY {column} {direction}"

    def _parse_select_sql(self, sql_text: str) -> dict | None:
        text = str(sql_text or "").strip()
        matched = re.match(
            r"^\s*select\s+(?P<select>.+?)\s*\bfrom\b\s+(?P<from>.+?)"
            r"(?:\s+\bwhere\b\s+(?P<where>.+?))?"
            r"(?:\s+\bgroup\s+by\b\s+(?P<group>.+?))?"
            r"(?:\s+\border\s+by\b\s+(?P<order>.+?))?"
            r"(?:\s+\blimit\b\s+(?P<limit>\d+))?\s*$",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not matched:
            return None

        select_clause = matched.group("select").strip()
        from_clause = matched.group("from").strip()
        where_clause = (matched.group("where") or "").strip()
        group_clause = (matched.group("group") or "").strip()
        order_clause = (matched.group("order") or "").strip()
        limit_clause = (matched.group("limit") or "").strip()

        return {
            "select_clause": select_clause,
            "metric_alias": self._extract_metric_alias(select_clause),
            "from_clause": from_clause,
            "where_clause": where_clause,
            "group_clause": group_clause,
            "order_clause": order_clause,
            "limit_clause": limit_clause,
        }

    def _calc_rule_from_query_spec(self, query_spec: dict, fallback_calc_rule: dict) -> dict:
        if not isinstance(query_spec, dict):
            return fallback_calc_rule

        mode = str(query_spec.get("query_mode", "")).strip().lower()
        if not mode:
            return fallback_calc_rule

        params = dict(fallback_calc_rule.get("params", {})) if isinstance(fallback_calc_rule, dict) else {}

        dimensions = query_spec.get("dimensions", [])
        if isinstance(dimensions, str):
            dimensions = [dimensions]
        if not isinstance(dimensions, list):
            dimensions = []
        dimensions = [str(item).strip() for item in dimensions if str(item).strip()]
        if dimensions:
            params["group_by"] = dimensions

        sort_items = query_spec.get("sort", [])
        if isinstance(sort_items, dict):
            sort_items = [sort_items]
        if isinstance(sort_items, list) and sort_items:
            first_sort = sort_items[0] if isinstance(sort_items[0], dict) else {}
            order_by = str(first_sort.get("by", "")).strip()
            order_dir = str(first_sort.get("dir", "desc")).strip().lower()
            if order_by:
                params["order_by"] = order_by
            if order_dir in ("asc", "desc"):
                params["order_dir"] = order_dir

        limit = query_spec.get("limit")
        try:
            if limit is not None:
                limit_num = int(limit)
                if limit_num > 0:
                    params["limit"] = limit_num
        except Exception:
            pass

        fallback_type = str((fallback_calc_rule or {}).get("type", "")).strip().lower()

        if mode == "detail":
            if params.get("order_by") and params.get("limit"):
                return {"type": "topn", "params": params}
            return {"type": "detail", "params": params}

        if mode == "custom_sql":
            return {"type": "custom_sql", "params": params}

        measures = self._normalize_measure_specs(query_spec.get("measures", []))
        if not measures:
            return fallback_calc_rule

        params["measures"] = measures
        first_measure = measures[0]
        agg = str(first_measure.get("agg", "")).strip().lower()
        measure_field = str(first_measure.get("field", "")).strip()
        measure_alias = str(first_measure.get("alias", "")).strip()

        if fallback_type == "topn":
            if measure_field:
                params["metric_field"] = measure_field
            params["metric_agg"] = agg if agg in ("sum", "avg", "max", "min", "count") else "sum"
            params["metric_alias"] = measure_alias or str(params.get("metric_alias", "metric_value")).strip() or "metric_value"
            params.setdefault("order_by", "__metric__")
            params.setdefault("order_dir", "desc")
            params.setdefault("limit", 10)
            return {"type": "topn", "params": params}

        if agg == "count":
            calc_type = "group_count" if dimensions else "count"
        elif agg in ("sum", "avg", "max", "min", "rate"):
            calc_type = agg
        else:
            return fallback_calc_rule

        if calc_type == "rate":
            if measure_field:
                params["rate_field"] = measure_field
            options = first_measure.get("options", {})
            if isinstance(options, dict):
                true_values = options.get("true_values", [])
                if isinstance(true_values, list) and true_values:
                    params["rate_true_values"] = true_values
            if measure_alias:
                params["metric_alias"] = measure_alias

        return {"type": calc_type, "params": params}

    @staticmethod
    def _normalize_measure_specs(measures) -> list:
        if isinstance(measures, dict):
            measures = [measures]
        if not isinstance(measures, list):
            return []

        normalized = []
        for measure in measures:
            if not isinstance(measure, dict):
                continue
            agg = str(measure.get("agg", "")).strip().lower()
            if not agg:
                continue
            item = {"agg": agg}
            field = str(measure.get("field", "")).strip()
            alias = str(measure.get("alias", "")).strip()
            if field:
                item["field"] = field
            if alias:
                item["alias"] = alias
            options = measure.get("options", {})
            if isinstance(options, dict) and options:
                item["options"] = options
            normalized.append(item)
        return normalized

    def _merge_fields_for_query_spec(self, query_plan: dict, extracted_fields: list, query_spec: dict) -> list:
        merged = []
        seen = set()

        def append_field(entity_name: str, field_name: str, field_type: str = "string"):
            entity = str(entity_name or "").strip()
            field = str(field_name or "").strip()
            if not entity or not field:
                return
            key = (entity.upper(), field.upper())
            if key in seen:
                return
            seen.add(key)
            merged.append({"entity": entity, "field": field, "label": field, "type": field_type})

        for item in extracted_fields or []:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity", "")).strip()
            field = str(item.get("field", "")).strip()
            if not entity or not field:
                continue
            key = (entity.upper(), field.upper())
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)

        default_entity = ""
        entities = query_plan.get("entities", [])
        if isinstance(entities, list) and entities:
            first = entities[0]
            if isinstance(first, dict):
                default_entity = str(first.get("name", "")).strip()

        dimensions = query_spec.get("dimensions", [])
        if isinstance(dimensions, str):
            dimensions = [dimensions]
        if isinstance(dimensions, list):
            for item in dimensions:
                entity_hint, field_name = self._split_field_ref(item)
                append_field(entity_hint or default_entity, field_name, "string")

        measures = query_spec.get("measures", [])
        if isinstance(measures, dict):
            measures = [measures]
        if isinstance(measures, list):
            for measure in measures:
                if not isinstance(measure, dict):
                    continue
                field_ref = str(measure.get("field", "")).strip()
                if not field_ref or field_ref == "*":
                    continue
                agg = str(measure.get("agg", "")).strip().lower()
                entity_hint, field_name = self._split_field_ref(field_ref)
                field_type = "number" if agg in ("sum", "avg", "max", "min", "count") else "string"
                append_field(entity_hint or default_entity, field_name, field_type)

        return merged
