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
        raw_question = str(input_data.get("question", "")).strip()
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
                self.log("检测到术语知识包含SQL文本，但未命中指标SQL规则，不会直接执行")

        if metric_rule_hit:
            target_entities = metric_rule_hit.get("target_entities") or self._extract_target_entities(metric_rule_hit.get("sql", ""))
            primary_entity = target_entities[0] if target_entities else ""
            metric_hints = self._extract_metric_hints(raw_question, target_entities)

            result = {
                "clarified_question": raw_question,
                "target_entities": target_entities,
                "primary_entity": primary_entity,
                "primary_entity_source": "metric_rule_default_first_entity",
                "conditions": metric_hints.get("conditions", []),
                "output_fields": metric_hints.get("output_fields", []),
                "calc_type": "custom_sql",
                "calc_params": metric_hints.get("calc_params", {}),
                "custom_sql": metric_rule_hit["sql"],
                "custom_sql_rule_id": metric_rule_hit.get("id", ""),
                "custom_sql_rule_name": metric_rule_hit.get("name", ""),
                "custom_sql_matched_keywords": metric_rule_hit.get("matched_keywords", []),
            }
            self.log(
                f"命中指标SQL规则: {metric_rule_hit.get('name', '')} "
                f"(关键词: {metric_rule_hit.get('matched_keywords', [])})"
            )
            return {
                **input_data,
                "clarified_intent": result,
                "raw_question": raw_question,
                "knowledge_context": knowledge_snippets,
                "metric_sql_rule_hit": metric_rule_hit,
            }

        knowledge_block = self._build_knowledge_block(knowledge_snippets)
        default_system_template = """你是一个意图澄清智能体，负责把用户问题解析成结构化JSON。
你掌握的本体知识如下：
$ontology_desc
$knowledge_block

请严格返回JSON（不要输出解释文字），格式如下：
{
  "clarified_question": "澄清后的问题",
  "target_entities": ["实体英文名"],
  "primary_entity": "主本体英文名",
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
  }
}

规则：
1) “各/分别/每个/按XX”这类分组语义，必须给出group_by，并把分组字段放入output_fields。
2) “最高/最大”=> order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最小”=> order_by=__metric__, order_dir=asc, limit=1。
4) 条件值保留用户语义文本，不要提前映射为数据库编码。
5) 必须输出primary_entity。"""

        system_prompt = self._render_prompt(
            key="intent_clarify_system",
            default_template=default_system_template,
            context={
                "ontology_desc": ontology_desc,
                "knowledge_block": knowledge_block,
            },
        )
        system_prompt += (
            "\n\nMandatory output contract:"
            "\n- JSON must include field: primary_entity"
            "\n- primary_entity must be one item from target_entities"
            "\n- if model cannot decide, leave primary_entity empty"
        )

        if not self.llm:
            result = self._normalize_intent_result(raw_question, {})
        else:
            model_result = self.llm.chat_json(system_prompt, raw_question)
            self.log(f"模型返回JSON: {self._json_for_log(model_result)}")
            result = self._normalize_intent_result(raw_question, model_result)

        self.log(
            f"意图澄清完成: 目标实体={result.get('target_entities')}, 计算类型={result.get('calc_type')}"
        )
        self.log(f"primary_entity_source={result.get('primary_entity_source', '')}")
        return {
            **input_data,
            "clarified_intent": result,
            "raw_question": raw_question,
            "knowledge_context": knowledge_snippets,
            "metric_sql_rule_hit": None,
        }

    def _build_knowledge_block(self, knowledge_snippets: list) -> str:
        if not knowledge_snippets:
            return ""
        lines = []
        for idx, item in enumerate(knowledge_snippets, start=1):
            lines.append(
                f"{idx}. 术语: {item.get('term', '')}\n"
                f"   命中关键词: {', '.join(item.get('matched_keywords', []))}\n"
                f"   释义: {item.get('content', '')}"
            )
        return "\n\n领域知识（仅供参考，按需使用）\n" + "\n".join(lines)

    def _extract_target_entities(self, sql: str) -> list:
        entities = []
        for match in self.FROM_PATTERN.finditer(sql or ""):
            table_name = match.group(1).strip("`\"[]")
            if not table_name:
                continue
            candidate = table_name.upper()
            if self.ontology.get_entity(candidate) and candidate not in entities:
                entities.append(candidate)
        return entities

    def _extract_metric_hints(self, question: str, target_entities: list) -> dict:
        text = str(question or "")
        primary_entity = target_entities[0] if target_entities else ""

        hints = {
            "conditions": [],
            "output_fields": [],
            "calc_params": {},
        }

        if any(token in text for token in ("区划", "地区", "地市", "各区", "按区")):
            hints["output_fields"].append({
                "field": "AREACODE",
                "entity": primary_entity,
                "label": "区划",
            })
            hints["calc_params"]["group_by"] = ["AREACODE"]

        if any(token in text for token in ("最高", "最大")):
            hints["calc_params"].update({"order_by": "__metric__", "order_dir": "desc", "limit": 1})
        elif any(token in text for token in ("最低", "最小")):
            hints["calc_params"].update({"order_by": "__metric__", "order_dir": "asc", "limit": 1})

        return hints

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

    def _normalize_intent_result(self, question: str, result: dict) -> dict:
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
        raw_primary_entity = str(parsed.get("primary_entity", "")).strip().upper()
        primary_entity = ""
        primary_entity_source = ""
        if raw_primary_entity and raw_primary_entity in target_entities:
            primary_entity = raw_primary_entity
            primary_entity_source = "model"
        elif raw_primary_entity and not target_entities:
            primary_entity = raw_primary_entity
            target_entities = [raw_primary_entity]
            default_entity = raw_primary_entity
            primary_entity_source = "model_only_primary"
        elif target_entities:
            primary_entity = target_entities[0]
            primary_entity_source = "fallback_first_entity"

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
            inferred = self._derive_group_fields_from_outputs(output_fields)
            if inferred:
                calc_params["group_by"] = inferred

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

        return {
            "clarified_question": clarified_question,
            "target_entities": target_entities,
            "primary_entity": primary_entity,
            "primary_entity_source": primary_entity_source,
            "conditions": conditions,
            "output_fields": output_fields,
            "calc_type": calc_type,
            "calc_params": calc_params,
        }

    @staticmethod
    def _question_requires_grouping(question: str) -> bool:
        text = str(question or "")
        grouping_hints = (
            "各", "分别", "每个", "哪个", "排行", "排名", "最高", "最低",
            "top", "前", "区划", "地区", "地市", "区县", "城市",
            "按地区", "按区划", "按地市", "按部门", "按单位", "按类别", "按类型",
        )
        if any(token in text for token in grouping_hints):
            return True
        return bool(re.search(r"按[^，。；,.]{0,8}(分组|统计|汇总|分别|各|排名|排行)", text))

    @staticmethod
    def _question_requires_limit(question: str, calc_type: str = "", order_by: str = "") -> bool:
        text = str(question or "")
        text_lower = text.lower()
        calc_type_text = str(calc_type or "").strip().lower()
        order_by_text = str(order_by or "").strip().lower()

        if calc_type_text == "topn":
            return True

        if any(re.search(pattern, text_lower) for pattern in (r"top\s*\d+", r"前\s*\d+")):
            return True

        ranking_keywords = ("最高", "最低", "最大", "最小", "排行", "排名")
        if any(token in text for token in ranking_keywords):
            return True

        if order_by_text in {"__metric__", "metric"} and any(token in text for token in ranking_keywords):
            return True

        return False

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

    def _derive_group_fields_from_outputs(self, output_fields: list) -> list:
        if not isinstance(output_fields, list):
            return []

        refs = []
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
            refs.append(ref)

        return refs

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
