"""Intent clarify agent - parse natural language into structured intent."""
import json
import re
from string import Template

from agents.base_agent import BaseAgent


class IntentClarifyAgent(BaseAgent):
    FROM_PATTERN = re.compile(r"\bfrom\s+`?([a-zA-Z0-9_]+)`?", flags=re.IGNORECASE)
    ORDER_BY_PATTERN = re.compile(r"^(?P<field>.+?)\s+(?P<direction>asc|desc)$", flags=re.IGNORECASE)
    ALLOWED_CALC_TYPES = {
        "detail", "count", "sum", "avg", "rate", "max", "min", "group_count", "topn", "custom_sql",
    }
    SUPPORTED_OPS = {"=", "!=", ">", "<", ">=", "<=", "contains", "in"}

    def __init__(self, llm_client, ontology_manager, knowledge_manager=None, prompt_manager=None):
        super().__init__("意图澄清智能体", "理解用户问题，明确查询目标、约束和输出要求")
        self.llm = llm_client
        self.ontology = ontology_manager
        self.knowledge = knowledge_manager
        self.prompts = prompt_manager

    def run(self, input_data: dict) -> dict:
        raw_question = input_data["question"]
        self.log(f"分析用户问题: {raw_question}")

        ontology_desc = self.ontology.to_description()
        knowledge_snippets = []
        metric_rule_hit = None
        if self.knowledge:
            knowledge_snippets = self.knowledge.retrieve_term_knowledge(raw_question, top_k=3)
            if knowledge_snippets:
                self.log(f"命中领域知识: {len(knowledge_snippets)}条")

            metric_rule_hit = self.knowledge.match_metric_sql_rule(raw_question)
            if not metric_rule_hit and self._contains_embedded_sql(knowledge_snippets):
                self.log("检测到术语知识包含 SQL 文本，但未命中“指标SQL规则”，不会直接执行")

        if metric_rule_hit:
            preferred_source = input_data.get("preferred_source")
            source_id = preferred_source or metric_rule_hit.get("data_source") or "xksx"
            target_entities = metric_rule_hit.get("target_entities") or self._extract_target_entities(
                metric_rule_hit.get("sql", "")
            )
            metric_hints = self._extract_metric_hints(raw_question, target_entities)
            conditions = metric_hints.get("conditions", [])
            output_fields = metric_hints.get("output_fields", [])
            calc_params = metric_hints.get("calc_params", {})
            if conditions or output_fields or calc_params:
                self.log(
                    "指标规则补充解析: "
                    f"条件={len(conditions)}, 输出字段={len(output_fields)}, 参数={calc_params}"
                )

            result = {
                "clarified_question": raw_question,
                "target_entities": target_entities,
                "conditions": conditions,
                "output_fields": output_fields,
                "calc_type": "custom_sql",
                "calc_params": calc_params,
                "data_source": source_id,
                "custom_sql": metric_rule_hit["sql"],
                "custom_sql_rule_id": metric_rule_hit.get("id", ""),
                "custom_sql_rule_name": metric_rule_hit.get("name", ""),
                "custom_sql_matched_keywords": metric_rule_hit.get("matched_keywords", []),
            }
            self.log(
                f"命中指标SQL规则: {metric_rule_hit.get('name', '')} "
                f"(关键词: {metric_rule_hit.get('matched_keywords', [])})，直接使用预定义SQL"
            )
            self.log(
                f"意图澄清完成: 目标实体={result.get('target_entities')}, "
                f"计算类型={result.get('calc_type')}, 数据源={result.get('data_source')}"
            )
            return {
                **input_data,
                "clarified_intent": result,
                "raw_question": raw_question,
                "knowledge_context": knowledge_snippets,
                "metric_sql_rule_hit": metric_rule_hit,
            }

        knowledge_block = ""
        if knowledge_snippets:
            lines = []
            for i, item in enumerate(knowledge_snippets, start=1):
                lines.append(
                    f"{i}. 术语: {item.get('term', '')}\n"
                    f"   命中关键词: {', '.join(item.get('matched_keywords', []))}\n"
                    f"   释义: {item.get('content', '')}"
                )
            knowledge_block = "\n\n领域知识（仅供参考，按需使用）:\n" + "\n".join(lines)

        default_system_template = """你是一个意图澄清智能体，负责把用户问题解析成结构化 JSON。
你掌握的本体知识如下：
$ontology_desc

可用数据源: $available_sources
$knowledge_block

请严格返回 JSON（不要输出解释文字），格式如下：
{
  "clarified_question": "澄清后的问题",
  "target_entities": ["实体英文名"],
  "conditions": [
    {"field":"属性英文名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体英文名"}
  ],
  "output_fields": [
    {"field":"属性英文名","entity":"实体英文名","label":"显示名"}
  ],
  "calc_type": "detail|count|sum|avg|rate|max|min|group_count|topn",
  "calc_params": {
    "group_by": ["属性英文名或ENTITY.FIELD"],
    "order_by": "属性英文名或ENTITY.FIELD或__metric__",
    "order_dir": "asc或desc",
    "limit": 10
  },
  "data_source": "all或具体数据源ID"
}

规则：
1) “各/分别/每个/按XX”这类分组语义，必须给出 group_by，并把分组字段放入 output_fields。
2) “最高/最多/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最少/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) 如果是 sum/avg/max/min 且同时询问“各XX分别”，优先按维度字段分组，不要仅返回全局汇总。
5) data_source 除非用户明确指定，否则一律为 all。"""
        system_prompt = self._render_prompt(
            key="intent_clarify_system",
            default_template=default_system_template,
            context={
                "ontology_desc": ontology_desc,
                "available_sources": ", ".join(input_data.get("available_sources", {}).keys()),
                "knowledge_block": knowledge_block,
            },
        )

        if not self.llm:
            result = self._normalize_intent_result(
                raw_question,
                {},
                available_sources=input_data.get("available_sources", {}),
            )
        else:
            result = self.llm.chat_json(system_prompt, raw_question)
            self.log(f"模型返回JSON: {self._json_for_log(result)}")
            result = self._normalize_intent_result(
                raw_question,
                result,
                available_sources=input_data.get("available_sources", {}),
            )

        self.log(
            f"意图澄清完成: 目标实体={result.get('target_entities')}, "
            f"计算类型={result.get('calc_type')}, 数据源={result.get('data_source')}"
        )
        return {
            **input_data,
            "clarified_intent": result,
            "raw_question": raw_question,
            "knowledge_context": knowledge_snippets,
            "metric_sql_rule_hit": None,
        }

    def _extract_target_entities(self, sql: str) -> list:
        entities = []
        for match in self.FROM_PATTERN.finditer(sql or ""):
            table_name = match.group(1).strip("`\"[]")
            if not table_name:
                continue
            candidate = table_name.upper()
            if self.ontology.get_entity(candidate):
                entities.append(candidate)
        unique = []
        for name in entities:
            if name not in unique:
                unique.append(name)
        return unique

    def _extract_metric_hints(self, question: str, target_entities: list) -> dict:
        if not self.llm:
            return self._fallback_metric_hints(question, target_entities)

        primary_entity = target_entities[0] if target_entities else ""
        properties = self.ontology.get_entity_properties(primary_entity) if primary_entity else {}
        property_lines = []
        for prop_name, prop_def in properties.items():
            label = prop_def.get("label", "")
            aliases = prop_def.get("aliases", [])
            alias_text = f" 别名={aliases}" if aliases else ""
            property_lines.append(f"- {prop_name}({label}){alias_text}")
        property_block = "\n".join(property_lines) if property_lines else "- 无可用属性"

        default_system_template = """你是“指标查询补充解析器”。
已确定用户要查询一个预定义指标，不要改指标本身，只抽取“筛选条件+分组/排序/限制”。
目标实体: $primary_entity
可用属性:
$property_block

请返回 JSON:
{
  "conditions": [
    {"field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}
  ],
  "output_fields": [
    {"field":"属性名","entity":"实体名","label":"显示名"}
  ],
  "calc_params": {
    "group_by": ["属性名"],
    "order_by": "属性名或__metric__",
    "order_dir": "asc或desc",
    "limit": 1
  }
}

规则:
1) 没提到就留空列表或空字符串，不要臆造。
2) “最高/最多/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最少/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) “哪个区划/各区划/按区划”优先用 AREACODE 做 group_by，并放入 output_fields。
5) entity 统一填 $primary_entity。"""
        system_prompt = self._render_prompt(
            key="metric_hint_system",
            default_template=default_system_template,
            context={
                "primary_entity": primary_entity or "INFORMATION_SYSTEM",
                "property_block": property_block,
            },
        )

        try:
            parsed = self.llm.chat_json(system_prompt, question)
            self.log(f"指标补充模型返回JSON: {self._json_for_log(parsed)}")
        except Exception as exc:
            self.log(f"指标规则补充解析失败，改用兜底规则: {exc}")
            return self._fallback_metric_hints(question, target_entities)

        if not isinstance(parsed, dict):
            return self._fallback_metric_hints(question, target_entities)

        conditions = []
        for item in parsed.get("conditions", []) if isinstance(parsed.get("conditions"), list) else []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            op = str(item.get("op", "=")).strip().lower() or "="
            if op not in self.SUPPORTED_OPS:
                op = "="
            value = item.get("value", "")
            entity = str(item.get("entity", "")).strip().upper() or primary_entity
            conditions.append({
                "field": field,
                "op": op,
                "value": value,
                "entity": entity,
            })

        output_fields = []
        for item in parsed.get("output_fields", []) if isinstance(parsed.get("output_fields"), list) else []:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            entity = str(item.get("entity", "")).strip().upper() or primary_entity
            label = str(item.get("label", "")).strip() or field
            output_fields.append({
                "field": field,
                "entity": entity,
                "label": label,
            })

        normalized_calc_params = self._normalize_calc_params(parsed.get("calc_params", {}))

        should_group = self._question_requires_grouping(question)
        if not should_group:
            normalized_calc_params.pop("group_by", None)
            normalized_calc_params.pop("order_by", None)
            normalized_calc_params.pop("order_dir", None)
            normalized_calc_params.pop("limit", None)

        conditions = [
            item for item in conditions
            if self._condition_supported_by_question(
                question=question,
                entity=item.get("entity", ""),
                field=item.get("field", ""),
                value=item.get("value"),
            )
        ]

        grouped_fields = set(
            self._split_field_ref(item)[1].upper()
            for item in normalized_calc_params.get("group_by", [])
            if str(item).strip()
        )
        output_fields = [
            item for item in output_fields
            if str(item.get("field", "")).strip().upper() in grouped_fields
            or self._field_supported_by_question(
                question=question,
                entity=item.get("entity", ""),
                field=item.get("field", ""),
            )
        ]

        return {
            "conditions": conditions,
            "output_fields": output_fields,
            "calc_params": normalized_calc_params,
        }

    def _fallback_metric_hints(self, question: str, target_entities: list) -> dict:
        text = str(question or "")
        primary_entity = target_entities[0] if target_entities else "INFORMATION_SYSTEM"
        hints = {
            "conditions": [],
            "output_fields": [],
            "calc_params": {},
        }

        ask_area = any(token in text for token in ("区划", "地区", "地市", "各区", "按区"))
        if ask_area:
            hints["output_fields"].append({
                "field": "AREACODE",
                "entity": primary_entity,
                "label": "区划",
            })
            hints["calc_params"]["group_by"] = ["AREACODE"]

        if any(token in text for token in ("最高", "最大", "最多")):
            hints["calc_params"]["order_by"] = "__metric__"
            hints["calc_params"]["order_dir"] = "desc"
            hints["calc_params"]["limit"] = 1
        elif any(token in text for token in ("最低", "最小", "最少")):
            hints["calc_params"]["order_by"] = "__metric__"
            hints["calc_params"]["order_dir"] = "asc"
            hints["calc_params"]["limit"] = 1

        return hints

    @staticmethod
    def _question_requires_grouping(question: str) -> bool:
        text = str(question or "")
        grouping_hints = (
            "各", "分别", "每个", "按", "哪个", "哪一个", "排行", "排名", "最高", "最低",
            "top", "前", "区划", "地区", "地市", "区县", "城市",
        )
        return any(token in text for token in grouping_hints)

    @staticmethod
    def _question_requires_limit(question: str, calc_type: str = "", order_by: str = "") -> bool:
        text = str(question or "")
        text_lower = text.lower()
        calc_type_text = str(calc_type or "").strip().lower()
        order_by_text = str(order_by or "").strip().lower()

        if calc_type_text == "topn":
            return True

        explicit_limit_patterns = (
            r"top\s*\d+",
            r"前\s*\d+",
            r"首\s*\d+",
            r"前十|前五|前三|前二十|前30|前20|前10|前5|前3",
        )
        if any(re.search(pattern, text_lower) for pattern in explicit_limit_patterns):
            return True

        ranking_keywords = ("最高", "最低", "最大", "最小", "最多", "最少", "排行", "排名")
        if any(token in text for token in ranking_keywords):
            return True

        if order_by_text in {"__metric__", "metric"} and any(token in text for token in ranking_keywords):
            return True

        return False

    def _field_supported_by_question(self, question: str, entity: str, field: str) -> bool:
        q = str(question or "")
        f = str(field or "").strip()
        if not q or not f:
            return False
        if f in q:
            return True

        props = self.ontology.get_entity_properties(entity)
        prop_def = props.get(f, {}) if isinstance(props, dict) else {}
        candidates = [str(prop_def.get("label", "")).strip()]
        aliases = prop_def.get("aliases", [])
        if isinstance(aliases, list):
            candidates.extend(str(item).strip() for item in aliases if str(item).strip())
        return any(token and token in q for token in candidates)

    def _condition_supported_by_question(self, question: str, entity: str, field: str, value) -> bool:
        q = str(question or "")
        if not q:
            return False

        field_hit = self._field_supported_by_question(question, entity, field)

        value_hits = False
        value_text = ""
        if isinstance(value, list):
            value_hits = any(str(v).strip() and str(v).strip() in q for v in value)
            value_text = ",".join(str(v).strip() for v in value if str(v).strip())
        elif value is not None:
            value_text = str(value).strip()
            if value_text:
                value_hits = value_text in q

        if field_hit:
            return True

        if value_hits:
            if self._is_geo_value(value_text):
                return self._is_geo_field(field)
            return True

        if isinstance(value, bool):
            return False
        if isinstance(value, str) and value.strip().lower() in ("true", "false", "是", "否", "0", "1"):
            return False

        return False

    @staticmethod
    def _is_geo_field(field: str) -> bool:
        field_upper = str(field or "").strip().upper()
        if field_upper in {"AREACODE", "AREA_CODE", "PROVINCE", "CITY", "COUNTY", "REGION"}:
            return True
        return any(token in field_upper for token in ("AREA", "PROVINCE", "CITY", "COUNTY", "REGION"))

    @staticmethod
    def _is_geo_value(value: str) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        if text in {"江苏省", "全省", "省内", "全国", "全市", "全区", "全县"}:
            return True
        if any(token in text for token in ("厅", "局", "委", "办", "院", "校", "公司", "集团", "法院", "检察院")):
            return False
        return bool(re.match(r"^[\u4e00-\u9fa5]{2,8}(省|市|区|县|自治州)?$", text))

    @staticmethod
    def _contains_embedded_sql(knowledge_snippets: list) -> bool:
        for item in knowledge_snippets or []:
            content = str(item.get("content", "")).lower()
            if "select " in content or "对应sql" in content:
                return True
        return False

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        try:
            return Template(default_template).safe_substitute(**{k: str(v) for k, v in (context or {}).items()})
        except Exception:
            return default_template

    def _normalize_intent_result(self, question: str, result: dict, available_sources: dict | None = None) -> dict:
        parsed = result if isinstance(result, dict) else {}
        clarified_question = str(parsed.get("clarified_question") or question).strip() or str(question or "")

        target_entities_raw = parsed.get("target_entities", [])
        if isinstance(target_entities_raw, str):
            target_entities_raw = [target_entities_raw]
        if not isinstance(target_entities_raw, list):
            target_entities_raw = []
        target_entities = []
        for item in target_entities_raw:
            entity_name = str(item).strip().upper()
            if entity_name and entity_name not in target_entities:
                target_entities.append(entity_name)
        default_entity = target_entities[0] if target_entities else ""

        conditions = []
        raw_conditions = parsed.get("conditions", [])
        if isinstance(raw_conditions, list):
            for item in raw_conditions:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field", "")).strip()
                if not field:
                    continue
                op = str(item.get("op", "=")).strip().lower() or "="
                if op not in self.SUPPORTED_OPS:
                    op = "="
                entity = str(item.get("entity", "")).strip().upper() or default_entity
                conditions.append({
                    "field": field,
                    "op": op,
                    "value": item.get("value", ""),
                    "entity": entity,
                })
        output_fields = []
        raw_output_fields = parsed.get("output_fields", [])
        if isinstance(raw_output_fields, list):
            for item in raw_output_fields:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field", "")).strip()
                if not field:
                    continue
                entity = str(item.get("entity", "")).strip().upper() or default_entity
                label = str(item.get("label", "")).strip() or field
                output_fields.append({
                    "field": field,
                    "entity": entity,
                    "label": label,
                })

        calc_type = str(parsed.get("calc_type", "detail")).strip().lower() or "detail"
        if calc_type not in self.ALLOWED_CALC_TYPES:
            calc_type = "detail"

        calc_params = self._normalize_calc_params(parsed.get("calc_params", {}))

        should_group = self._question_requires_grouping(question)
        aggregate_types = {"count", "sum", "avg", "max", "min", "rate", "group_count"}
        if calc_type in aggregate_types and should_group and not calc_params.get("group_by"):
            inferred_group_fields = self._derive_group_fields_from_outputs(output_fields)
            if inferred_group_fields:
                calc_params["group_by"] = inferred_group_fields

        if calc_type == "group_count" and not calc_params.get("group_by"):
            inferred_group_fields = self._derive_group_fields_from_outputs(output_fields, prefer_dimension_only=False)
            if inferred_group_fields:
                calc_params["group_by"] = inferred_group_fields

        if calc_type == "topn":
            calc_params.setdefault("order_by", "__metric__")
            calc_params.setdefault("order_dir", "desc")
            calc_params.setdefault("limit", 10)

        if "limit" in calc_params and not self._question_requires_limit(
            question=question,
            calc_type=calc_type,
            order_by=calc_params.get("order_by", ""),
        ):
            calc_params.pop("limit", None)

        for group_ref in calc_params.get("group_by", []):
            group_entity, group_field = self._split_field_ref(group_ref)
            if not group_field:
                continue
            has_field = any(
                str(item.get("field", "")).upper() == group_field.upper()
                and (not group_entity or str(item.get("entity", "")).upper() == group_entity.upper())
                for item in output_fields
            )
            if has_field:
                continue
            output_fields.insert(0, {
                "field": group_field,
                "entity": group_entity or default_entity,
                "label": group_field,
            })

        output_fields = self._normalize_output_labels(
            question=question,
            calc_type=calc_type,
            calc_params=calc_params,
            output_fields=output_fields,
        )

        data_source = str(parsed.get("data_source", "all")).strip() or "all"
        source_keys = set((available_sources or {}).keys())
        if data_source != "all" and source_keys and data_source not in source_keys:
            data_source = "all"

        return {
            "clarified_question": clarified_question,
            "target_entities": target_entities,
            "conditions": conditions,
            "output_fields": output_fields,
            "calc_type": calc_type,
            "calc_params": calc_params,
            "data_source": data_source,
        }

    def _normalize_output_labels(self, question: str, calc_type: str, calc_params: dict, output_fields: list) -> list:
        if not isinstance(output_fields, list):
            return output_fields

        text = str(question or "")
        group_by = calc_params.get("group_by", []) if isinstance(calc_params, dict) else []
        if isinstance(group_by, str):
            group_by = [group_by]
        group_fields = set()
        for item in group_by if isinstance(group_by, list) else []:
            _, field_name = self._split_field_ref(item)
            if field_name:
                group_fields.add(field_name.upper())

        needs_total_suffix = str(calc_type or "").strip().lower() == "sum" and any(
            token in text for token in ("总和", "合计", "总计", "总数")
        )

        normalized = []
        for item in output_fields:
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            field_name = str(copied.get("field", "")).strip()
            label = str(copied.get("label", "")).strip() or field_name
            field_upper = field_name.upper()

            if field_upper == "AREACODE":
                if "地区" in text:
                    label = "地区"
                elif "区划" in text:
                    label = "区划"

            if needs_total_suffix and field_upper not in group_fields:
                if not any(token in label for token in ("总和", "总计", "合计", "总数")):
                    suffix = "总数" if any(token in label for token in ("数量", "个数", "条数", "项数")) else "总和"
                    label = f"{label}{suffix}"

            copied["label"] = label
            normalized.append(copied)
        return normalized

    def _normalize_calc_params(self, calc_params: dict) -> dict:
        if not isinstance(calc_params, dict):
            return {}

        normalized = {}

        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [part.strip() for part in re.split(r"[，,]", group_by) if part.strip()]
        if isinstance(group_by, list):
            group_items = []
            seen = set()
            for item in group_by:
                text = str(item).strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                group_items.append(text)
            if group_items:
                normalized["group_by"] = group_items

        order_by = calc_params.get("order_by", "")
        if isinstance(order_by, list):
            order_by = order_by[0] if order_by else ""
        order_by = str(order_by).strip()

        order_dir = str(calc_params.get("order_dir", "")).strip().lower()
        if order_dir not in ("asc", "desc"):
            order_dir = ""

        matched = self.ORDER_BY_PATTERN.match(order_by) if order_by else None
        if matched:
            order_by = matched.group("field").strip()
            if not order_dir:
                order_dir = matched.group("direction").strip().lower()

        desc_value = calc_params.get("desc", None)
        if not order_dir:
            desc_bool = self._to_bool(desc_value)
            if desc_bool is not None:
                order_dir = "desc" if desc_bool else "asc"

        if order_by:
            normalized["order_by"] = order_by
        if order_dir:
            normalized["order_dir"] = order_dir

        limit = calc_params.get("limit", None)
        try:
            if limit is not None:
                limit = int(limit)
                if limit > 0:
                    normalized["limit"] = limit
        except Exception:
            pass

        for key, value in calc_params.items():
            if key in {"group_by", "order_by", "order_dir", "limit", "desc"}:
                continue
            normalized[key] = value

        return normalized

    def _derive_group_fields_from_outputs(self, output_fields: list, prefer_dimension_only: bool = True) -> list:
        if not isinstance(output_fields, list):
            return []

        dimension_fields = []
        fallback_fields = []
        seen = set()
        for item in output_fields:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity", "")).strip().upper()
            field = str(item.get("field", "")).strip()
            if not field:
                continue
            ref = f"{entity}.{field}" if entity else field
            if ref in seen:
                continue
            seen.add(ref)
            fallback_fields.append(ref)
            if not self._is_numeric_like_field(entity, field):
                dimension_fields.append(ref)

        if dimension_fields:
            return dimension_fields
        if prefer_dimension_only:
            return []
        return fallback_fields[:1]

    def _is_numeric_like_field(self, entity: str, field: str) -> bool:
        field_name = str(field or "").strip()
        if not field_name:
            return False

        properties = self.ontology.get_entity_properties(entity) if entity else {}
        prop_def = properties.get(field_name, {}) if isinstance(properties, dict) else {}
        field_type = str(prop_def.get("type", "")).strip().lower()
        if field_type in {"number", "int", "integer", "float", "double", "decimal", "long", "short"}:
            return True
        if field_type in {"boolean", "bool"}:
            return False

        name_upper = field_name.upper()
        if name_upper.startswith("IS_") or name_upper.startswith("HAS_"):
            return False
        numeric_tokens = (
            "COUNT", "NUM", "AMOUNT", "COST", "FEE", "BUDGET", "EXPENSE", "TOTAL",
            "USE", "USAGE", "VALUE", "RATE", "PERCENT", "CPU", "MEM", "DISK",
        )
        return any(token in name_upper for token in numeric_tokens)

    @staticmethod
    def _split_field_ref(field_ref: str) -> tuple[str, str]:
        text = str(field_ref or "").strip().strip("`")
        if not text:
            return "", ""
        if "." in text:
            entity_name, field_name = text.rsplit(".", 1)
            return entity_name.strip().upper(), field_name.strip()
        return "", text

    @staticmethod
    def _to_bool(value):
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "是"}:
            return True
        if text in {"0", "false", "no", "n", "否"}:
            return False
        return None


    @staticmethod
    def _json_for_log(payload) -> str:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return str(payload)
