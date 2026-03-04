"""Intent clarify agent - parse natural language into structured intent."""
import json
import re
from string import Template

from agents.base_agent import BaseAgent


class IntentClarifyAgent(BaseAgent):
    FROM_PATTERN = re.compile(r"\bfrom\s+`?([a-zA-Z0-9_]+)`?", flags=re.IGNORECASE)
    ORDER_BY_PATTERN = re.compile(r"^(?P<field>.+?)\s+(?P<direction>asc|desc)$", flags=re.IGNORECASE)
    ENTITY_SELECTION_MIN_CONFIDENCE = 0.55
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

        all_entity_names = [str(name).strip().upper() for name in self.ontology.get_all_entity_names()]
        ontology_desc_full = self.ontology.to_description()
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

        entity_selection = self._select_entities_coarse(raw_question, all_entity_names)
        selection_entities = entity_selection.get("target_entities", [])
        selection_primary = str(entity_selection.get("primary_entity", "")).strip().upper()
        use_scoped_ontology = bool(entity_selection.get("use_scoped")) and bool(selection_entities)
        if use_scoped_ontology:
            ontology_desc = self._build_scoped_ontology_desc(selection_entities)
            self.log(
                f"实体粗选完成: entities={selection_entities}, primary={selection_primary}, "
                f"confidence={entity_selection.get('confidence', 0.0)}"
            )
        else:
            ontology_desc = ontology_desc_full
            self.log(
                f"实体粗选未收敛，回退全量本体: entities={selection_entities}, "
                f"confidence={entity_selection.get('confidence', 0.0)}"
            )

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
        if use_scoped_ontology and selection_entities:
            system_prompt += (
                "\n- target_entities must be chosen only from: "
                + ", ".join(selection_entities)
            )

        if not self.llm:
            result = self._normalize_intent_result(
                question=raw_question,
                result={},
                allowed_entities=selection_entities if use_scoped_ontology else all_entity_names,
                fallback_entities=selection_entities if use_scoped_ontology else [],
                fallback_primary_entity=selection_primary,
            )
        else:
            user_prompt = self._render_prompt(
                key="intent_clarify_user",
                default_template="问题：$question",
                context={"question": raw_question},
            )
            model_result = self.llm.chat_json(system_prompt, user_prompt)
            self.log(f"模型返回JSON: {self._json_for_log(model_result)}")
            result = self._normalize_intent_result(
                question=raw_question,
                result=model_result,
                allowed_entities=selection_entities if use_scoped_ontology else all_entity_names,
                fallback_entities=selection_entities if use_scoped_ontology else [],
                fallback_primary_entity=selection_primary,
            )

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
            "entity_selection": entity_selection,
        }

    def _select_entities_coarse(self, question: str, all_entities: list[str]) -> dict:
        heuristic = self._heuristic_entity_candidates(question, all_entities)
        fallback = {
            "target_entities": heuristic.get("target_entities", []),
            "primary_entity": heuristic.get("primary_entity", ""),
            "confidence": heuristic.get("confidence", 0.0),
            "source": "heuristic",
            "use_scoped": bool(heuristic.get("target_entities")),
        }

        if not self.llm or not all_entities:
            return fallback

        catalog_text = self._build_entity_catalog_text(all_entities)
        relation_text = self._build_relation_catalog_text()
        system_template = """你是实体选择器。只做一步：从候选实体中选出与问题最相关的1~4个实体。
输入只有实体与实体关系，不包含属性。

候选实体：
$entity_catalog

实体关系：
$relation_catalog

严格输出JSON，不要解释：
{
  "target_entities": ["实体英文名"],
  "primary_entity": "主实体英文名",
  "confidence": 0.0,
  "reason": "简短原因"
}

规则：
1) target_entities 必须来自候选实体。
2) confidence 范围0~1。
3) 若无法判断，target_entities 输出空数组。"""
        user_template = "问题：$question"
        system_prompt = self._render_prompt(
            key="intent_entity_select_system",
            default_template=system_template,
            context={
                "entity_catalog": catalog_text,
                "relation_catalog": relation_text,
            },
        )
        user_prompt = self._render_prompt(
            key="intent_entity_select_user",
            default_template=user_template,
            context={"question": question},
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.log(f"实体粗选失败，回退启发式: {exc}")
            return fallback

        normalized = self._normalize_entity_selection_result(parsed, all_entities, fallback)
        confidence = float(normalized.get("confidence", 0.0))
        normalized["use_scoped"] = bool(normalized.get("target_entities")) and confidence >= self.ENTITY_SELECTION_MIN_CONFIDENCE
        return normalized

    def _heuristic_entity_candidates(self, question: str, all_entities: list[str]) -> dict:
        text = str(question or "").strip()
        if not text or not all_entities:
            return {"target_entities": [], "primary_entity": "", "confidence": 0.0}

        lowered = text.lower()
        scored = []
        for entity_name in all_entities:
            entity_def = self.ontology.get_entity(entity_name) or {}
            label = str(entity_def.get("label", "")).strip()
            aliases = entity_def.get("aliases", [])

            candidate_tokens = [entity_name, label]
            if isinstance(aliases, list):
                candidate_tokens.extend([str(item).strip() for item in aliases if str(item).strip()])

            best = 0.0
            for token in candidate_tokens:
                token_text = str(token).strip()
                if not token_text:
                    continue
                token_lower = token_text.lower()
                if token_lower in lowered:
                    best = max(best, min(0.95, 0.45 + len(token_text) * 0.03))
                elif len(token_text) >= 4 and lowered in token_lower:
                    best = max(best, 0.35)

            if best > 0:
                scored.append((entity_name, best))

        if not scored:
            return {"target_entities": [], "primary_entity": "", "confidence": 0.0}

        scored.sort(key=lambda item: item[1], reverse=True)
        target_entities = [name for name, _ in scored[:4]]
        confidence = self._clamp_confidence(scored[0][1])
        return {
            "target_entities": target_entities,
            "primary_entity": target_entities[0] if target_entities else "",
            "confidence": confidence,
        }

    def _normalize_entity_selection_result(self, payload: dict, all_entities: list[str], fallback: dict) -> dict:
        parsed = payload if isinstance(payload, dict) else {}
        allowed = {str(item).strip().upper() for item in all_entities if str(item).strip()}

        raw_entities = parsed.get("target_entities", [])
        if isinstance(raw_entities, str):
            raw_entities = [raw_entities]
        if not isinstance(raw_entities, list):
            raw_entities = []

        target_entities = []
        for item in raw_entities:
            name = str(item).strip().upper()
            if not name or name not in allowed or name in target_entities:
                continue
            target_entities.append(name)

        raw_primary = str(parsed.get("primary_entity", "")).strip().upper()
        primary_entity = raw_primary if raw_primary in target_entities else ""
        if not primary_entity and target_entities:
            primary_entity = target_entities[0]

        confidence = self._clamp_confidence(parsed.get("confidence", 0.0))
        if not target_entities and fallback.get("target_entities"):
            return {
                "target_entities": list(fallback.get("target_entities", [])),
                "primary_entity": str(fallback.get("primary_entity", "")).strip().upper(),
                "confidence": max(float(fallback.get("confidence", 0.0)), 0.4),
                "source": "heuristic_fallback",
            }

        return {
            "target_entities": target_entities,
            "primary_entity": primary_entity,
            "confidence": confidence,
            "source": "llm",
        }

    def _build_entity_catalog_text(self, all_entities: list[str]) -> str:
        lines = []
        for entity_name in all_entities:
            entity_def = self.ontology.get_entity(entity_name) or {}
            label = str(entity_def.get("label", "")).strip()
            aliases = entity_def.get("aliases", [])
            alias_texts = []
            if isinstance(aliases, list):
                for item in aliases:
                    text = str(item).strip()
                    if text and text not in alias_texts:
                        alias_texts.append(text)
            line = f"- {entity_name}"
            if label:
                line += f" ({label})"
            if alias_texts:
                line += f" aliases={','.join(alias_texts[:5])}"
            lines.append(line)
        return "\n".join(lines)

    def _build_relation_catalog_text(self) -> str:
        relations = self.ontology.relations if isinstance(self.ontology.relations, list) else []
        if not relations:
            return "(无显式关系)"
        lines = []
        for rel in relations:
            from_entity = str(rel.get("from", "")).strip()
            to_entity = str(rel.get("to", "")).strip()
            label = str(rel.get("label", "")).strip() or str(rel.get("type", "related")).strip()
            from_field = str(rel.get("from_field", "")).strip()
            to_field = str(rel.get("to_field", "")).strip()
            join_hint = ""
            if from_field and to_field:
                join_hint = f" ({from_field}={to_field})"
            if from_entity and to_entity:
                lines.append(f"- {from_entity} --[{label}]--> {to_entity}{join_hint}")
        return "\n".join(lines) if lines else "(无显式关系)"

    def _build_scoped_ontology_desc(self, selected_entities: list[str]) -> str:
        selected = [str(item).strip().upper() for item in selected_entities if str(item).strip()]
        lines = ["本体范围: 实体粗选后子集", "实体定义:"]
        for entity_name in selected:
            entity_def = self.ontology.get_entity(entity_name)
            if not isinstance(entity_def, dict):
                continue
            lines.append(f"  [{entity_name}] ({entity_def.get('label', entity_name)}): {entity_def.get('description', '')}")
            properties = entity_def.get("properties", {})
            if isinstance(properties, dict):
                for prop_name, prop_def in properties.items():
                    if not isinstance(prop_def, dict):
                        continue
                    lines.append(f"    - {prop_name} ({prop_def.get('label', prop_name)}): {prop_def.get('type', 'string')}")

        selected_set = set(selected)
        lines.append("关系定义:")
        has_relation = False
        for rel in self.ontology.relations:
            from_entity = str(rel.get("from", "")).strip().upper()
            to_entity = str(rel.get("to", "")).strip().upper()
            if from_entity not in selected_set or to_entity not in selected_set:
                continue
            has_relation = True
            join_hint = ""
            if rel.get("from_field") and rel.get("to_field"):
                join_hint = f" ({rel.get('from_field')}={rel.get('to_field')})"
            lines.append(f"  {from_entity} --[{rel.get('label', rel.get('type', 'related'))}]--> {to_entity}{join_hint}")
        if not has_relation:
            lines.append("  (无显式关系)")

        return "\n".join(lines)

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

    def _normalize_intent_result(
        self,
        question: str,
        result: dict,
        allowed_entities: list[str] | None = None,
        fallback_entities: list[str] | None = None,
        fallback_primary_entity: str = "",
    ) -> dict:
        parsed = result if isinstance(result, dict) else {}
        clarified_question = str(parsed.get("clarified_question") or question).strip() or str(question or "")
        allowed_entity_set = {
            str(item).strip().upper()
            for item in (allowed_entities or [])
            if str(item).strip()
        }

        target_entities_raw = parsed.get("target_entities", [])
        if isinstance(target_entities_raw, str):
            target_entities_raw = [target_entities_raw]
        if not isinstance(target_entities_raw, list):
            target_entities_raw = []

        target_entities = []
        for item in target_entities_raw:
            entity_name = str(item).strip().upper()
            if not entity_name:
                continue
            if allowed_entity_set and entity_name not in allowed_entity_set:
                continue
            if entity_name not in target_entities:
                target_entities.append(entity_name)

        fallback_entities = [
            str(item).strip().upper()
            for item in (fallback_entities or [])
            if str(item).strip()
        ]
        if not target_entities and fallback_entities:
            target_entities = [item for item in fallback_entities if not allowed_entity_set or item in allowed_entity_set]

        default_entity = target_entities[0] if target_entities else ""
        raw_primary_entity = str(parsed.get("primary_entity", "")).strip().upper()
        primary_entity = ""
        primary_entity_source = ""
        if raw_primary_entity and raw_primary_entity in target_entities:
            primary_entity = raw_primary_entity
            primary_entity_source = "model"
        elif raw_primary_entity and not target_entities:
            candidate_primary = raw_primary_entity
            if allowed_entity_set and candidate_primary not in allowed_entity_set:
                candidate_primary = ""
            if candidate_primary:
                primary_entity = candidate_primary
                target_entities = [candidate_primary]
                default_entity = candidate_primary
                primary_entity_source = "model_only_primary"
            else:
                fallback_primary = str(fallback_primary_entity or "").strip().upper()
                if fallback_primary and (not allowed_entity_set or fallback_primary in allowed_entity_set):
                    primary_entity = fallback_primary
                    target_entities = [fallback_primary]
                    default_entity = fallback_primary
                    primary_entity_source = "selection_fallback_primary"
        elif target_entities:
            primary_entity = target_entities[0]
            primary_entity_source = "fallback_first_entity"
        else:
            fallback_primary = str(fallback_primary_entity or "").strip().upper()
            if fallback_primary and (not allowed_entity_set or fallback_primary in allowed_entity_set):
                primary_entity = fallback_primary
                target_entities = [fallback_primary]
                default_entity = fallback_primary
                primary_entity_source = "selection_fallback_primary"

        if primary_entity and primary_entity not in target_entities:
            target_entities.insert(0, primary_entity)
            default_entity = primary_entity

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
                if allowed_entity_set and entity and entity not in allowed_entity_set:
                    entity = default_entity
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
                if allowed_entity_set and entity and entity not in allowed_entity_set:
                    entity = default_entity
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
    def _clamp_confidence(value) -> float:
        try:
            score = float(value)
        except Exception:
            score = 0.0
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score

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
