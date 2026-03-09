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

    def __init__(self, llm_client, ontology_manager, knowledge_manager=None, prompt_manager=None, mapping_manager=None):
        super().__init__("意图澄清智能体", "理解用户问题，明确查询目标、约束和输出要求")
        self.llm = llm_client
        self.ontology = ontology_manager
        self.knowledge = knowledge_manager
        self.prompts = prompt_manager
        self.mapping = mapping_manager

    def _log_substep(self, substep: str, message: str):
        self.log(f"[{substep}] {message}")

    def run(self, input_data: dict) -> dict:
        raw_question = str(input_data.get("question", "")).strip()
        self._log_substep("01_01", f"分析用户问题: {raw_question}")

        all_entity_names = [str(name).strip().upper() for name in self.ontology.get_all_entity_names()]
        ontology_desc_full = self._build_scoped_ontology_desc(
            all_entity_names,
            scope_title="本体范围: 全量实体",
        )
        knowledge_snippets = []
        metric_rule_hit = None

        self._log_substep("01_02", "检索领域知识与指标SQL规则")
        if self.knowledge:
            knowledge_snippets = self.knowledge.retrieve_term_knowledge(raw_question, top_k=3)
            if knowledge_snippets:
                self._log_substep("01_02", f"命中领域知识: {len(knowledge_snippets)}条")

            metric_rule_hit = self.knowledge.match_metric_sql_rule(raw_question)
            if not metric_rule_hit and self._contains_embedded_sql(knowledge_snippets):
                self._log_substep("01_02", "检测到术语知识包含SQL文本，但未命中指标SQL规则，不会直接执行")

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
            self._log_substep(
                "01_02",
                f"命中指标SQL规则: {metric_rule_hit.get('name', '')} "
                f"(关键词: {metric_rule_hit.get('matched_keywords', [])})"
            )
            self._log_substep(
                "01_08",
                f"意图澄清完成(custom_sql): 目标实体={result.get('target_entities')}, primary={result.get('primary_entity')}"
            )
            return {
                **input_data,
                "clarified_intent": result,
                "raw_question": raw_question,
                "knowledge_context": knowledge_snippets,
                "metric_sql_rule_hit": metric_rule_hit,
            }

        self._log_substep("01_03", "执行实体粗选")
        entity_selection = self._select_entities_coarse(raw_question, all_entity_names)
        selection_entities_cn = entity_selection.get("target_entities", [])
        selection_primary_cn = str(entity_selection.get("primary_entity", "")).strip()
        selection_entity_keys = entity_selection.get("target_entity_keys", [])
        if not isinstance(selection_entity_keys, list):
            selection_entity_keys = []
        selection_primary_key = str(entity_selection.get("primary_entity_key", "")).strip().upper()
        use_scoped_ontology = bool(entity_selection.get("use_scoped")) and bool(selection_entity_keys)
        if use_scoped_ontology:
            ontology_desc = self._build_scoped_ontology_desc(selection_entity_keys)
            self._log_substep(
                "01_03",
                f"实体粗选完成: entities={selection_entities_cn}, primary={selection_primary_cn}, "
                f"confidence={entity_selection.get('confidence', 0.0)}"
            )
        else:
            ontology_desc = ontology_desc_full
            self._log_substep(
                "01_03",
                f"实体粗选未收敛，回退全量本体: entities={selection_entities_cn}, "
                f"confidence={entity_selection.get('confidence', 0.0)}"
            )

        self._log_substep("01_04", "构建意图澄清提示词")
        knowledge_block = self._build_knowledge_block(knowledge_snippets)
        allowed_target_entities = "未提供"
        if use_scoped_ontology and selection_entity_keys:
            selection_entity_labels = [self._entity_label(item) for item in selection_entity_keys]
            if selection_entity_labels:
                allowed_target_entities = "、".join(selection_entity_labels)
        default_system_template = """你是一个意图澄清智能体，负责把用户问题解析成结构化JSON。
你掌握的本体知识如下：
$ontology_desc
$knowledge_block

请严格返回JSON（不要输出解释文字），格式如下：
{
  "clarified_question": "澄清后的问题",
  "target_entities": ["实体名"],
  "primary_entity": "主实体名",
  "conditions": [
    {"field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}
  ],
  "output_fields": [
    {"field":"属性名","entity":"实体名","label":"显示名"}
  ],
  "calc_type": "detail|count|sum|avg|rate|max|min|topn",
  "calc_params": {
    "group_by": ["属性名或实体名.属性名"],
    "order_by": "属性名或实体名.属性名或__metric__",
    "order_dir": "asc或desc",
    "limit": 10
  }
}

规则：
1) “各/分别/每个/按XX”这类分组语义，必须给出group_by，并把分组字段放入output_fields。
2) “最高/最大”=> order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最小”=> order_by=__metric__, order_dir=asc, limit=1。
4) 条件值保留用户语义文本，不要提前映射为数据库编码。
5) 必须输出primary_entity。
6) 严格校验：conditions 和 output_fields 中的 field 必须 100% 存在于上述提供的属性列表中。
7) 输出契约补充（说明为中文，字段键名保持英文）：
   - JSON 必须包含字段 primary_entity。
   - primary_entity 必须是 target_entities 的成员；无法判断时可留空。
   - target_entities/primary_entity/conditions.entity/output_fields.entity 使用中文实体名。
   - conditions.field/output_fields.field/calc_params.group_by/order_by 使用中文属性名或“实体名.属性名”。
   - 涉及关系穿透/自连接时，可输出可选字段：
     entity_instances: [{"id":"inst_id","entity":"实体名","role":"可选角色"}]
     relations: [{"left_instance":"inst1","left_field":"属性名A","right_instance":"inst2","right_field":"属性名B","join_type":"inner|left"}]
   - conditions/output_fields 可使用 entity_instance 绑定到具体实例。
   - 如果 relations.left_field/right_field 使用逗号分隔复合键（如 证件类型,证件号码），左右字段必须一一对应，不能把整个逗号串当作单字段名。
8) target_entities 只能从以下实体中选择（中文名）: $allowed_target_entities。"""

        system_prompt = self._render_prompt(
            key="intent_clarify_system",
            default_template=default_system_template,
            context={
                "ontology_desc": ontology_desc,
                "knowledge_block": knowledge_block,
                "allowed_target_entities": allowed_target_entities,
            },
        )

        self._log_substep("01_05", "执行结构化意图生成")
        if not self.llm:
            result = self._normalize_intent_result(
                question=raw_question,
                result={},
                allowed_entities=selection_entity_keys if use_scoped_ontology else all_entity_names,
                fallback_entities=selection_entity_keys if use_scoped_ontology else [],
                fallback_primary_entity=selection_primary_key,
            )
        else:
            user_prompt = self._render_prompt(
                key="intent_clarify_user",
                default_template="问题：$question",
                context={"question": raw_question},
            )
            model_result = self.llm.chat_json(system_prompt, user_prompt)
            self._log_substep("01_05", f"模型返回JSON: {self._json_for_log(model_result)}")
            result = self._normalize_intent_result(
                question=raw_question,
                result=model_result,
                allowed_entities=selection_entity_keys if use_scoped_ontology else all_entity_names,
                fallback_entities=selection_entity_keys if use_scoped_ontology else [],
                fallback_primary_entity=selection_primary_key,
            )

        self._log_substep(
            "01_06",
            f"意图归一化完成: entities={result.get('target_entities')}, primary={result.get('primary_entity')}, calc={result.get('calc_type')}"
        )
        self._log_substep("01_07", "尝试关系实例化（自连接判定）")
        result = self._try_expand_relation_instances(raw_question, result, input_data.get("source_id"))
        self._log_substep(
            "01_08",
            f"意图澄清完成: 目标实体={result.get('target_entities')}, 计算类型={result.get('calc_type')}"
        )
        self._log_substep("01_08", f"primary_entity_source={result.get('primary_entity_source', '')}")
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
            "target_entity_keys": heuristic.get("target_entity_keys", []),
            "primary_entity_key": heuristic.get("primary_entity_key", ""),
            "confidence": heuristic.get("confidence", 0.0),
            "source": "heuristic",
            "use_scoped": bool(heuristic.get("target_entity_keys")),
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
  "target_entities": ["实体中文名"],
  "primary_entity": "主实体中文名",
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
            self._log_substep("01_03", f"实体粗选失败，回退启发式: {exc}")
            return fallback

        normalized = self._normalize_entity_selection_result(parsed, all_entities, fallback)
        confidence = float(normalized.get("confidence", 0.0))
        normalized["use_scoped"] = bool(normalized.get("target_entity_keys")) and confidence >= self.ENTITY_SELECTION_MIN_CONFIDENCE
        return normalized

    def _heuristic_entity_candidates(self, question: str, all_entities: list[str]) -> dict:
        text = str(question or "").strip()
        if not text or not all_entities:
            return {
                "target_entities": [],
                "primary_entity": "",
                "target_entity_keys": [],
                "primary_entity_key": "",
                "confidence": 0.0,
            }

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
            return {
                "target_entities": [],
                "primary_entity": "",
                "target_entity_keys": [],
                "primary_entity_key": "",
                "confidence": 0.0,
            }

        scored.sort(key=lambda item: item[1], reverse=True)
        target_entity_keys = [name for name, _ in scored[:4]]
        target_entities = [self._entity_label(name) for name in target_entity_keys]
        primary_entity_key = target_entity_keys[0] if target_entity_keys else ""
        confidence = self._clamp_confidence(scored[0][1])
        return {
            "target_entities": target_entities,
            "primary_entity": self._entity_label(primary_entity_key) if primary_entity_key else "",
            "target_entity_keys": target_entity_keys,
            "primary_entity_key": primary_entity_key,
            "confidence": confidence,
        }

    def _normalize_entity_selection_result(self, payload: dict, all_entities: list[str], fallback: dict) -> dict:
        parsed = payload if isinstance(payload, dict) else {}
        allowed = {str(item).strip().upper() for item in all_entities if str(item).strip()}
        key_to_label, token_to_key = self._build_entity_label_lookup(all_entities)

        raw_entities = parsed.get("target_entities", [])
        if isinstance(raw_entities, str):
            raw_entities = [raw_entities]
        if not isinstance(raw_entities, list):
            raw_entities = []

        target_entity_keys = []
        for item in raw_entities:
            name = self._resolve_entity_key(item, token_to_key, allowed)
            if not name or name in target_entity_keys:
                continue
            target_entity_keys.append(name)

        raw_primary = self._resolve_entity_key(parsed.get("primary_entity", ""), token_to_key, allowed)
        primary_entity_key = raw_primary if raw_primary in target_entity_keys else ""
        if not primary_entity_key and target_entity_keys:
            primary_entity_key = target_entity_keys[0]

        confidence = self._clamp_confidence(parsed.get("confidence", 0.0))
        if not target_entity_keys:
            fallback_keys_raw = fallback.get("target_entity_keys", [])
            if not isinstance(fallback_keys_raw, list):
                fallback_keys_raw = []
            fallback_keys = []
            for item in fallback_keys_raw:
                candidate = self._resolve_entity_key(item, token_to_key, allowed)
                if candidate and candidate not in fallback_keys:
                    fallback_keys.append(candidate)
            if not fallback_keys:
                for item in fallback.get("target_entities", []) or []:
                    candidate = self._resolve_entity_key(item, token_to_key, allowed)
                    if candidate and candidate not in fallback_keys:
                        fallback_keys.append(candidate)
            fallback_primary_key = self._resolve_entity_key(
                fallback.get("primary_entity_key", "") or fallback.get("primary_entity", ""),
                token_to_key,
                allowed,
            )
            if not fallback_primary_key and fallback_keys:
                fallback_primary_key = fallback_keys[0]
            return {
                "target_entities": [key_to_label.get(item, item) for item in fallback_keys],
                "primary_entity": key_to_label.get(fallback_primary_key, fallback_primary_key),
                "target_entity_keys": fallback_keys,
                "primary_entity_key": fallback_primary_key,
                "confidence": max(float(fallback.get("confidence", 0.0)), 0.4),
                "source": "heuristic_fallback",
            }

        target_entities = [key_to_label.get(item, item) for item in target_entity_keys]
        return {
            "target_entities": target_entities,
            "primary_entity": key_to_label.get(primary_entity_key, primary_entity_key),
            "target_entity_keys": target_entity_keys,
            "primary_entity_key": primary_entity_key,
            "confidence": confidence,
            "source": "llm",
        }

    def _entity_label(self, entity_name: str) -> str:
        entity_key = str(entity_name or "").strip().upper()
        if not entity_key:
            return ""
        entity_def = self.ontology.get_entity(entity_key) or {}
        label = str(entity_def.get("label", "")).strip()
        return label or entity_key

    @staticmethod
    def _normalize_entity_token(value) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"\s+", "", text)

    def _build_entity_label_lookup(self, all_entities: list[str]) -> tuple[dict[str, str], dict[str, str]]:
        key_to_label = {}
        token_to_key = {}
        for item in all_entities:
            entity_key = str(item).strip().upper()
            if not entity_key:
                continue
            entity_def = self.ontology.get_entity(entity_key) or {}
            label = str(entity_def.get("label", "")).strip() or entity_key
            key_to_label[entity_key] = label

            token_candidates = [entity_key, label]
            aliases = entity_def.get("aliases", [])
            if isinstance(aliases, list):
                token_candidates.extend([str(alias).strip() for alias in aliases if str(alias).strip()])

            for token in token_candidates:
                normalized = self._normalize_entity_token(token)
                if normalized and normalized not in token_to_key:
                    token_to_key[normalized] = entity_key

        return key_to_label, token_to_key

    def _resolve_entity_key(self, value, token_to_key: dict[str, str], allowed_keys: set[str]) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        direct = raw.upper()
        if direct in allowed_keys:
            return direct
        normalized = self._normalize_entity_token(raw)
        matched = token_to_key.get(normalized, "")
        if matched and matched in allowed_keys:
            return matched
        return ""

    def _property_label(self, entity_name: str, field_name: str) -> str:
        entity_key = str(entity_name or "").strip().upper()
        field_key = str(field_name or "").strip()
        if not entity_key or not field_key:
            return field_key
        props = self.ontology.get_entity_properties(entity_key)
        if not isinstance(props, dict):
            return field_key
        prop_def = props.get(field_key, {})
        if isinstance(prop_def, dict):
            label = str(prop_def.get("label", "")).strip()
            if label:
                return label
        return field_key

    def _resolve_property_key(self, entity_name: str, field_name) -> str:
        entity_key = str(entity_name or "").strip().upper()
        raw = str(field_name or "").strip()
        if not raw:
            return ""
        if not entity_key:
            return raw

        # 支持实体前缀写法，如“出生信息.出生日期”或“BIRTH_INFO.BIRTH_DATE”。
        entity_part = ""
        field_part = raw
        if "." in raw:
            entity_part, field_part = [str(part).strip() for part in raw.rsplit(".", 1)]
        if entity_part:
            all_entities = [item for item in self.ontology.get_all_entity_names() if str(item).strip()]
            scope = {str(item).strip().upper() for item in all_entities}
            _, token_lookup = self._build_entity_label_lookup(list(scope))
            resolved_entity = self._resolve_entity_key(entity_part, token_lookup, scope)
            if resolved_entity:
                entity_key = resolved_entity
        field_part = str(field_part).strip()
        if not field_part:
            return ""

        props = self.ontology.get_entity_properties(entity_key)
        if not isinstance(props, dict) or not props:
            return field_part
        if field_part in props:
            return field_part

        normalized_field = self.ontology.resolve_property_name(entity_key, field_part)
        if normalized_field in props:
            return normalized_field

        normalized_token = self._normalize_entity_token(field_part)
        for prop_name, prop_def in props.items():
            if not isinstance(prop_def, dict):
                continue
            label = str(prop_def.get("label", "")).strip()
            if label and self._normalize_entity_token(label) == normalized_token:
                return prop_name
            aliases = prop_def.get("aliases", [])
            if isinstance(aliases, list):
                for alias in aliases:
                    alias_text = str(alias).strip()
                    if alias_text and self._normalize_entity_token(alias_text) == normalized_token:
                        return prop_name

        suggested = self.ontology.suggest_property_name(entity_key, field_part)
        if suggested:
            return suggested
        return field_part

    def _normalize_calc_param_fields(
        self,
        calc_params: dict,
        default_entity: str,
        token_to_key: dict[str, str],
        allowed_entities: set[str],
    ) -> dict:
        if not isinstance(calc_params, dict):
            return {}
        normalized = dict(calc_params)

        group_by = normalized.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if isinstance(group_by, list):
            mapped = []
            seen = set()
            for item in group_by:
                resolved = self._normalize_calc_field_ref(item, default_entity, token_to_key, allowed_entities)
                if not resolved or resolved in seen:
                    continue
                seen.add(resolved)
                mapped.append(resolved)
            normalized["group_by"] = mapped

        order_by = str(normalized.get("order_by", "")).strip()
        if order_by and order_by not in {"__metric__", "metric"}:
            normalized["order_by"] = self._normalize_calc_field_ref(
                order_by,
                default_entity,
                token_to_key,
                allowed_entities,
            )
        return normalized

    def _normalize_calc_field_ref(
        self,
        field_ref,
        default_entity: str,
        token_to_key: dict[str, str],
        allowed_entities: set[str],
    ) -> str:
        text = str(field_ref or "").strip()
        if not text:
            return ""
        if text in {"__metric__", "metric"}:
            return text

        entity_part = ""
        field_part = text
        if "." in text:
            entity_part, field_part = [str(part).strip() for part in text.rsplit(".", 1)]

        entity_key = self._resolve_entity_key(entity_part, token_to_key, allowed_entities) if entity_part else ""
        if not entity_key:
            entity_key = str(default_entity or "").strip().upper()

        resolved_field = self._resolve_property_key(entity_key, field_part)
        if not resolved_field:
            return ""
        if entity_part:
            return f"{entity_key}.{resolved_field}" if entity_key else resolved_field
        return resolved_field

    def _build_entity_catalog_text(self, all_entities: list[str]) -> str:
        lines = []
        for entity_name in all_entities:
            entity_def = self.ontology.get_entity(entity_name) or {}
            label = str(entity_def.get("label", "")).strip() or str(entity_name).strip()
            description = str(entity_def.get("description", "")).strip()
            line = f"- {label}"
            if description:
                line += f":{description}"
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
            label = str(rel.get("label", "")).strip() or str(rel.get("type", "")).strip() or "关联"
            if from_entity and to_entity:
                from_label = self._entity_label(from_entity)
                to_label = self._entity_label(to_entity)
                lines.append(f"- {from_label} --[{label}]--> {to_label}")
        return "\n".join(lines) if lines else "(无显式关系)"

    def _build_scoped_ontology_desc(self, selected_entities: list[str], scope_title: str = "本体范围: 实体粗选后子集") -> str:
        selected = [str(item).strip().upper() for item in selected_entities if str(item).strip()]
        lines = [scope_title, "实体定义:"]
        for entity_name in selected:
            entity_def = self.ontology.get_entity(entity_name)
            if not isinstance(entity_def, dict):
                continue
            entity_label = str(entity_def.get("label", "")).strip() or entity_name
            entity_desc = str(entity_def.get("description", "")).strip()
            lines.append(f"  [{entity_label}]: {entity_desc}")
            properties = entity_def.get("properties", {})
            if isinstance(properties, dict):
                for prop_name, prop_def in properties.items():
                    if not isinstance(prop_def, dict):
                        continue
                    prop_label = str(prop_def.get("label", "")).strip() or str(prop_name).strip()
                    if prop_label:
                        lines.append(f"    - {prop_label}")

        selected_set = set(selected)
        lines.append("关系定义:")
        has_relation = False
        for rel in self.ontology.relations:
            from_entity = str(rel.get("from", "")).strip().upper()
            to_entity = str(rel.get("to", "")).strip().upper()
            if from_entity not in selected_set or to_entity not in selected_set:
                continue
            has_relation = True
            from_label = self._entity_label(from_entity)
            to_label = self._entity_label(to_entity)
            relation_label = str(rel.get("label", "")).strip() or str(rel.get("type", "")).strip() or "关联"
            join_hint = ""
            if rel.get("from_field") and rel.get("to_field"):
                left_fields = [self._property_label(from_entity, item) for item in self._split_join_fields(rel.get("from_field"))]
                right_fields = [self._property_label(to_entity, item) for item in self._split_join_fields(rel.get("to_field"))]
                if left_fields and right_fields and len(left_fields) == len(right_fields):
                    join_hint = f" ({','.join(left_fields)}={','.join(right_fields)})"
            lines.append(f"  {from_label} --[{relation_label}]--> {to_label}{join_hint}")
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
        all_entity_keys = [
            str(item).strip().upper()
            for item in self.ontology.get_all_entity_names()
            if str(item).strip()
        ]
        entity_scope = sorted(allowed_entity_set) if allowed_entity_set else all_entity_keys
        resolve_allowed_set = set(entity_scope)
        _, entity_token_lookup = self._build_entity_label_lookup(entity_scope)

        target_entities_raw = parsed.get("target_entities", [])
        if isinstance(target_entities_raw, str):
            target_entities_raw = [target_entities_raw]
        if not isinstance(target_entities_raw, list):
            target_entities_raw = []

        target_entities = []
        for item in target_entities_raw:
            entity_name = self._resolve_entity_key(item, entity_token_lookup, resolve_allowed_set)
            if not entity_name:
                continue
            if allowed_entity_set and entity_name not in allowed_entity_set:
                continue
            if entity_name not in target_entities:
                target_entities.append(entity_name)

        fallback_entities = [
            self._resolve_entity_key(item, entity_token_lookup, resolve_allowed_set)
            for item in (fallback_entities or [])
        ]
        fallback_entities = [item for item in fallback_entities if item]
        if not target_entities and fallback_entities:
            target_entities = [item for item in fallback_entities if not allowed_entity_set or item in allowed_entity_set]

        raw_instances = parsed.get("entity_instances", [])
        if isinstance(raw_instances, dict):
            raw_instances = [raw_instances]
        if not isinstance(raw_instances, list):
            raw_instances = []

        entity_instances = []
        instance_id_set = set()
        for idx, item in enumerate(raw_instances, start=1):
            if not isinstance(item, dict):
                continue
            entity_name = self._resolve_entity_key(item.get("entity", ""), entity_token_lookup, resolve_allowed_set)
            if not entity_name:
                continue
            if allowed_entity_set and entity_name not in allowed_entity_set:
                continue

            instance_id = str(item.get("id", "") or item.get("instance_id", "")).strip()
            if not instance_id:
                instance_id = f"{entity_name.lower()}_{idx}"
            instance_id = re.sub(r"[^a-zA-Z0-9_]", "_", instance_id)
            if not instance_id or instance_id in instance_id_set:
                continue
            instance_id_set.add(instance_id)

            role = str(item.get("role", "")).strip()
            entity_instances.append({
                "id": instance_id,
                "entity": entity_name,
                "role": role,
            })

        if entity_instances:
            for item in entity_instances:
                entity_name = item["entity"]
                if entity_name not in target_entities:
                    target_entities.append(entity_name)

        instance_entity_by_id = {item["id"]: item["entity"] for item in entity_instances}

        raw_relations = parsed.get("relations", [])
        if isinstance(raw_relations, dict):
            raw_relations = [raw_relations]
        if not isinstance(raw_relations, list):
            raw_relations = []

        relations = []
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            left_instance = str(item.get("left_instance", "") or item.get("from_instance", "")).strip()
            right_instance = str(item.get("right_instance", "") or item.get("to_instance", "")).strip()
            left_entity = instance_entity_by_id.get(left_instance, "")
            right_entity = instance_entity_by_id.get(right_instance, "")
            left_field = self._resolve_property_key(left_entity, item.get("left_field", "") or item.get("from_field", ""))
            right_field = self._resolve_property_key(right_entity, item.get("right_field", "") or item.get("to_field", ""))
            if not left_instance or not right_instance or not left_field or not right_field:
                continue
            if left_instance not in instance_id_set or right_instance not in instance_id_set:
                continue
            join_type = str(item.get("join_type", "inner")).strip().lower()
            if join_type in ("left join", "left_join"):
                join_type = "left"
            if join_type not in ("inner", "left"):
                join_type = "inner"
            relations.append({
                "left_instance": left_instance,
                "left_field": left_field,
                "right_instance": right_instance,
                "right_field": right_field,
                "join_type": join_type,
            })

        instance_by_id = {item["id"]: item for item in entity_instances}
        default_instance_by_entity = {}
        for item in entity_instances:
            default_instance_by_entity.setdefault(item["entity"], item["id"])

        default_entity = target_entities[0] if target_entities else ""
        raw_primary_entity = self._resolve_entity_key(parsed.get("primary_entity", ""), entity_token_lookup, resolve_allowed_set)
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
                fallback_primary = self._resolve_entity_key(fallback_primary_entity, entity_token_lookup, resolve_allowed_set)
                if fallback_primary and (not allowed_entity_set or fallback_primary in allowed_entity_set):
                    primary_entity = fallback_primary
                    target_entities = [fallback_primary]
                    default_entity = fallback_primary
                    primary_entity_source = "selection_fallback_primary"
        elif target_entities:
            primary_entity = target_entities[0]
            primary_entity_source = "fallback_first_entity"
        else:
            fallback_primary = self._resolve_entity_key(fallback_primary_entity, entity_token_lookup, resolve_allowed_set)
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
                entity_instance = str(item.get("entity_instance", "") or item.get("instance", "")).strip()
                entity = self._resolve_entity_key(item.get("entity", ""), entity_token_lookup, resolve_allowed_set)
                if entity_instance and entity_instance in instance_by_id:
                    entity = instance_by_id[entity_instance]["entity"]
                entity = entity or default_entity
                if allowed_entity_set and entity and entity not in allowed_entity_set:
                    entity = default_entity
                    entity_instance = default_instance_by_entity.get(entity, entity_instance)
                if not entity_instance and entity:
                    entity_instance = default_instance_by_entity.get(entity, "")
                field = self._resolve_property_key(entity, field)
                if not field:
                    continue

                condition_item = {
                    "field": field,
                    "op": op,
                    "value": item.get("value", ""),
                    "entity": entity,
                }
                if entity_instance:
                    condition_item["entity_instance"] = entity_instance
                conditions.append(condition_item)

        output_fields = []
        raw_output_fields = parsed.get("output_fields", [])
        if isinstance(raw_output_fields, list):
            for item in raw_output_fields:
                if not isinstance(item, dict):
                    continue
                field = str(item.get("field", "")).strip()
                if not field:
                    continue
                entity_instance = str(item.get("entity_instance", "") or item.get("instance", "")).strip()
                entity = self._resolve_entity_key(item.get("entity", ""), entity_token_lookup, resolve_allowed_set)
                if entity_instance and entity_instance in instance_by_id:
                    entity = instance_by_id[entity_instance]["entity"]
                entity = entity or default_entity
                if allowed_entity_set and entity and entity not in allowed_entity_set:
                    entity = default_entity
                    entity_instance = default_instance_by_entity.get(entity, entity_instance)
                if not entity_instance and entity:
                    entity_instance = default_instance_by_entity.get(entity, "")
                field = self._resolve_property_key(entity, field)
                if not field:
                    continue
                label = str(item.get("label", "")).strip() or field
                field_item = {
                    "field": field,
                    "entity": entity,
                    "label": label,
                }
                if entity_instance:
                    field_item["entity_instance"] = entity_instance
                output_fields.append(field_item)

        calc_type = str(parsed.get("calc_type", "detail")).strip().lower() or "detail"
        # 对外统一为 count + group_by，兼容模型误回 group_count。
        if calc_type == "group_count":
            calc_type = "count"
        if calc_type not in self.ALLOWED_CALC_TYPES:
            calc_type = "detail"

        calc_params = self._normalize_calc_params(parsed.get("calc_params", {}))
        calc_params = self._normalize_calc_param_fields(
            calc_params=calc_params,
            default_entity=primary_entity or default_entity,
            token_to_key=entity_token_lookup,
            allowed_entities=resolve_allowed_set,
        )

        should_group = self._question_requires_grouping(question)
        aggregate_types = {"count", "sum", "avg", "max", "min", "rate"}
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
            "entity_instances": entity_instances,
            "relations": relations,
            "calc_type": calc_type,
            "calc_params": calc_params,
        }

    def _try_expand_relation_instances(self, question: str, intent: dict, source_id: str | None = None) -> dict:
        if not self.llm or not isinstance(intent, dict):
            return intent

        target_entities = intent.get("target_entities", [])
        if not isinstance(target_entities, list) or len(target_entities) != 1:
            self._log_substep("01_07", "关系实例化跳过: 非单实体意图")
            return intent

        entity_name = str(target_entities[0]).strip().upper()
        if not entity_name:
            self._log_substep("01_07", "关系实例化跳过: 空实体名")
            return intent

        policy = self._resolve_self_join_policy(source_id, entity_name)
        if not policy.get("enabled", False):
            self._log_substep("01_07", f"关系实例化跳过: 映射未启用自连接 {entity_name}")
            return intent

        props = self.ontology.get_entity_properties(entity_name)
        if not isinstance(props, dict) or not props:
            self._log_substep("01_07", f"关系实例化跳过: 未找到实体属性 {entity_name}")
            return intent

        policy_pairs = policy.get("candidate_pairs", [])
        policy_mode = str(policy.get("mode", "llm")).strip().lower()
        allow_inferred = policy_mode != "explicit_only"
        key_fields, candidate_pairs = self._collect_self_join_candidates(
            entity_name=entity_name,
            props=props,
            configured_pairs=policy_pairs,
            allow_inferred=allow_inferred,
        )
        if not key_fields:
            self._log_substep("01_07", f"关系实例化跳过: {entity_name} 未识别主键字段")
            return intent
        if not candidate_pairs:
            self._log_substep("01_07", f"关系实例化跳过: {entity_name} 未识别可用自连接候选")
            return intent

        candidate_pairs_json = self._json_for_log(candidate_pairs)
        self._log_substep(
            "01_07",
            f"关系实例化候选: entity={entity_name}, keys={key_fields}, "
            f"pairs={candidate_pairs_json}"
        )

        raw_instances = intent.get("entity_instances", [])
        if isinstance(raw_instances, dict):
            raw_instances = [raw_instances]
        if not isinstance(raw_instances, list):
            raw_instances = []
        existing_instance_count = len([item for item in raw_instances if isinstance(item, dict)])

        if existing_instance_count >= 2:
            self._log_substep(
                "01_07",
                f"检测到单实体多实例，进入关系实例化复核: entity={entity_name}, instances={existing_instance_count}"
            )
            return self._review_existing_relation_instances(
                question=question,
                intent=intent,
                entity_name=entity_name,
                key_fields=key_fields,
                candidate_pairs=candidate_pairs,
            )

        if existing_instance_count > 0:
            self._log_substep(
                "01_07",
                f"检测到实例数量不足2个，清理后重做关系实例化: entity={entity_name}, instances={existing_instance_count}"
            )
            intent = self._clear_instance_graph(intent, entity_name)

        system_prompt = self._render_prompt(
            key="relation_instance_split_system",
            default_template=(
                "你是关系实例化判定器。给定问题和单实体意图，判断是否需要把同一实体拆成多个实例进行自连接。"
                "仅输出JSON："
                "{\"need_instance_split\":true|false,"
                "\"instances\":[{\"id\":\"inst_a\",\"role\":\"...\"},{\"id\":\"inst_b\",\"role\":\"...\"}],"
                "\"relation\":{\"left_instance\":\"inst_a\",\"left_field\":\"FIELD\",\"right_instance\":\"inst_b\","
                "\"right_field\":\"FIELD\",\"join_type\":\"inner|left\"},"
                "\"condition_instance\":\"inst_a\",\"output_instance\":\"inst_b\"}。"
                "规则："
                "1) 只有当问题明确是“通过关联对象再取其属性”时才返回 need_instance_split=true。"
                "2) relation(left_field,right_field) 必须来自候选对。"
                "3) 无法确定时返回 need_instance_split=false。"
            ),
            context={},
        )
        user_prompt = (
            f"问题: {question}\n"
            f"实体: {entity_name}\n"
            f"当前意图: {self._json_for_log(intent)}\n"
            f"主键字段: {key_fields}\n"
            f"候选连接对: {candidate_pairs_json}"
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self._log_substep("01_07", f"关系实例化判定失败，跳过: {exc}")
            return intent
        self._log_substep("01_07", f"关系实例化判定结果: {self._json_for_log(parsed)}")

        if not isinstance(parsed, dict) or not bool(parsed.get("need_instance_split", False)):
            self._log_substep("01_07", "关系实例化判定: 不需要拆分实例")
            return intent

        decision = self._normalize_instance_split_decision(
            parsed=parsed,
            entity_name=entity_name,
            candidate_pairs=candidate_pairs,
        )
        if not decision:
            self._log_substep("01_07", "关系实例化判定: 输出结构不完整，跳过")
            return intent

        expanded = self._apply_instance_split(intent, entity_name, decision)

        self._log_substep(
            "01_07",
            "关系实例化已启用: "
            f"{decision['relation']['left_instance']}.{decision['relation']['left_field']} -> "
            f"{decision['relation']['right_instance']}.{decision['relation']['right_field']}, "
            f"condition_instance={decision['condition_instance']}, output_instance={decision['output_instance']}"
        )
        return expanded

    def _review_existing_relation_instances(
        self,
        question: str,
        intent: dict,
        entity_name: str,
        key_fields: list[str],
        candidate_pairs: list[dict],
    ) -> dict:
        existing_valid = self._is_existing_self_join_valid(intent, entity_name, candidate_pairs)
        self._log_substep("01_07", f"关系实例复核: existing_valid={existing_valid}")

        system_prompt = self._render_prompt(
            key="relation_instance_review_system",
            default_template=(
                "你是关系实例化复核器。给定问题、单实体意图（已含多实例）与候选连接对，"
                "判断该自连接是否合理。仅输出JSON："
                "{\"need_instance_split\":true|false,"
                "\"instances\":[{\"id\":\"inst_a\",\"role\":\"...\"},{\"id\":\"inst_b\",\"role\":\"...\"}],"
                "\"relation\":{\"left_instance\":\"inst_a\",\"left_field\":\"FIELD\",\"right_instance\":\"inst_b\","
                "\"right_field\":\"FIELD\",\"join_type\":\"inner|left\"},"
                "\"condition_instance\":\"inst_a\",\"output_instance\":\"inst_b\"}。"
                "规则："
                "1) 如果当前意图不该自连接，返回 need_instance_split=false。"
                "2) 如果应自连接，relation(left_field,right_field) 必须来自候选连接对。"
                "3) 可以保留现有实例关系，也可以重写为更合理的实例关系。"
            ),
            context={},
        )
        user_prompt = (
            f"问题: {question}\n"
            f"实体: {entity_name}\n"
            f"当前意图: {self._json_for_log(intent)}\n"
            f"主键字段: {key_fields}\n"
            f"候选连接对: {self._json_for_log(candidate_pairs)}"
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self._log_substep("01_07", f"关系实例复核调用失败，保留原意图: {exc}")
            return intent
        self._log_substep("01_07", f"关系实例复核结果: {self._json_for_log(parsed)}")

        if not isinstance(parsed, dict):
            self._log_substep("01_07", "关系实例复核: 返回非JSON对象，保留原意图")
            return intent

        if not bool(parsed.get("need_instance_split", False)):
            self._log_substep("01_07", "关系实例复核: 判定不需要自连接，清理实例关系")
            return self._clear_instance_graph(intent, entity_name)

        decision = self._normalize_instance_split_decision(
            parsed=parsed,
            entity_name=entity_name,
            candidate_pairs=candidate_pairs,
        )
        if decision:
            rewritten = self._apply_instance_split(intent, entity_name, decision)
            self._log_substep(
                "01_07",
                "关系实例复核通过: "
                f"{decision['relation']['left_instance']}.{decision['relation']['left_field']} -> "
                f"{decision['relation']['right_instance']}.{decision['relation']['right_field']}"
            )
            return rewritten

        if existing_valid:
            self._log_substep("01_07", "关系实例复核: 输出结构不完整，保留原实例关系")
            return intent

        self._log_substep("01_07", "关系实例复核: 输出结构不完整且原关系无效，清理实例关系")
        return self._clear_instance_graph(intent, entity_name)

    def _normalize_instance_split_decision(self, parsed: dict, entity_name: str, candidate_pairs: list[dict]) -> dict | None:
        if not isinstance(parsed, dict):
            return None

        instances_raw = parsed.get("instances", [])
        relation_raw = parsed.get("relation", {})
        if isinstance(instances_raw, dict):
            instances_raw = [instances_raw]
        if not isinstance(instances_raw, list) or len(instances_raw) < 2 or not isinstance(relation_raw, dict):
            return None

        normalized_instances = []
        instance_ids = set()
        for idx, item in enumerate(instances_raw, start=1):
            if not isinstance(item, dict):
                continue
            instance_id = str(item.get("id", "") or item.get("instance_id", "")).strip()
            if not instance_id:
                instance_id = f"{entity_name.lower()}_{idx}"
            instance_id = re.sub(r"[^a-zA-Z0-9_]", "_", instance_id)
            if not instance_id or instance_id in instance_ids:
                continue
            instance_ids.add(instance_id)
            normalized_instances.append({
                "id": instance_id,
                "entity": entity_name,
                "role": str(item.get("role", "")).strip(),
            })
        if len(normalized_instances) < 2:
            return None

        left_instance = str(relation_raw.get("left_instance", "")).strip()
        right_instance = str(relation_raw.get("right_instance", "")).strip()
        left_field = str(relation_raw.get("left_field", "")).strip().upper()
        right_field = str(relation_raw.get("right_field", "")).strip().upper()
        candidate_pair_set = self._build_candidate_pair_set(candidate_pairs)
        if (
            left_instance not in instance_ids
            or right_instance not in instance_ids
            or (left_field, right_field) not in candidate_pair_set
        ):
            return None

        join_type = str(relation_raw.get("join_type", "inner")).strip().lower()
        if join_type in ("left join", "left_join"):
            join_type = "left"
        if join_type not in ("inner", "left"):
            join_type = "inner"

        condition_instance = str(parsed.get("condition_instance", "")).strip()
        output_instance = str(parsed.get("output_instance", "")).strip()
        if condition_instance not in instance_ids:
            condition_instance = left_instance
        if output_instance not in instance_ids:
            output_instance = right_instance

        return {
            "instances": normalized_instances,
            "relation": {
                "left_instance": left_instance,
                "left_field": left_field,
                "right_instance": right_instance,
                "right_field": right_field,
                "join_type": join_type,
            },
            "condition_instance": condition_instance,
            "output_instance": output_instance,
        }

    def _apply_instance_split(self, intent: dict, entity_name: str, decision: dict) -> dict:
        expanded = dict(intent)
        expanded["entity_instances"] = decision["instances"]
        expanded["relations"] = [decision["relation"]]

        updated_conditions = []
        for item in expanded.get("conditions", []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied["entity"] = entity_name
            copied.setdefault("entity_instance", decision["condition_instance"])
            updated_conditions.append(copied)
        expanded["conditions"] = updated_conditions

        updated_fields = []
        for item in expanded.get("output_fields", []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied["entity"] = entity_name
            copied.setdefault("entity_instance", decision["output_instance"])
            updated_fields.append(copied)
        expanded["output_fields"] = updated_fields
        return expanded

    def _clear_instance_graph(self, intent: dict, entity_name: str) -> dict:
        cleaned = dict(intent)
        cleaned["entity_instances"] = []
        cleaned["relations"] = []

        fixed_conditions = []
        for item in cleaned.get("conditions", []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied.pop("entity_instance", None)
            copied.pop("instance", None)
            copied["entity"] = entity_name
            fixed_conditions.append(copied)
        cleaned["conditions"] = fixed_conditions

        fixed_output_fields = []
        for item in cleaned.get("output_fields", []):
            if not isinstance(item, dict):
                continue
            copied = dict(item)
            copied.pop("entity_instance", None)
            copied.pop("instance", None)
            copied["entity"] = entity_name
            fixed_output_fields.append(copied)
        cleaned["output_fields"] = fixed_output_fields
        return cleaned

    def _is_existing_self_join_valid(self, intent: dict, entity_name: str, candidate_pairs: list[dict]) -> bool:
        raw_instances = intent.get("entity_instances", [])
        if isinstance(raw_instances, dict):
            raw_instances = [raw_instances]
        if not isinstance(raw_instances, list):
            return False

        instance_ids = set()
        for item in raw_instances:
            if not isinstance(item, dict):
                continue
            instance_id = str(item.get("id", "")).strip()
            instance_entity = str(item.get("entity", "")).strip().upper()
            if not instance_id or instance_entity != entity_name:
                continue
            instance_ids.add(instance_id)
        if len(instance_ids) < 2:
            return False

        raw_relations = intent.get("relations", [])
        if isinstance(raw_relations, dict):
            raw_relations = [raw_relations]
        if not isinstance(raw_relations, list):
            return False

        candidate_pair_set = self._build_candidate_pair_set(candidate_pairs)
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            left_instance = str(item.get("left_instance", "")).strip()
            right_instance = str(item.get("right_instance", "")).strip()
            left_field = str(item.get("left_field", "")).strip().upper()
            right_field = str(item.get("right_field", "")).strip().upper()
            if not left_instance or not right_instance or not left_field or not right_field:
                continue
            if left_instance not in instance_ids or right_instance not in instance_ids:
                continue
            if (left_field, right_field) not in candidate_pair_set:
                continue
            return True
        return False

    @staticmethod
    def _build_candidate_pair_set(candidate_pairs: list[dict]) -> set[tuple[str, str]]:
        return {
            (str(item.get("left_field", "")).strip().upper(), str(item.get("right_field", "")).strip().upper())
            for item in (candidate_pairs or [])
            if isinstance(item, dict)
        }

    def _resolve_self_join_policy(self, source_id: str | None, entity_name: str) -> dict:
        default_policy = {
            "enabled": False,
            "mode": "disabled",
            "candidate_pairs": [],
        }
        if not self.mapping:
            return default_policy

        sid = str(source_id or "").strip()
        if not sid:
            source_ids = self.mapping.get_source_ids() if hasattr(self.mapping, "get_source_ids") else []
            if len(source_ids) == 1:
                sid = source_ids[0]
        if not sid:
            return default_policy

        try:
            policy = self.mapping.get_self_join_policy(sid, entity_name)
        except Exception as exc:
            self._log_substep("01_07", f"读取自连接策略失败，按禁用处理: {exc}")
            return default_policy
        if not isinstance(policy, dict):
            return default_policy
        return policy

    def _collect_self_join_candidates(
        self,
        entity_name: str,
        props: dict,
        configured_pairs: list[dict] | None = None,
        allow_inferred: bool = True,
    ) -> tuple[list[str], list[dict]]:
        key_fields = [name for name, p in props.items() if isinstance(p, dict) and p.get("is_key")]
        if not key_fields:
            return [], []

        candidates = []
        seen_pairs = set()

        configured_pairs = configured_pairs if isinstance(configured_pairs, list) else []
        for item in configured_pairs:
            if not isinstance(item, dict):
                continue
            lf = str(item.get("left_field", "")).strip().upper()
            rf = str(item.get("right_field", "")).strip().upper()
            if not lf or not rf:
                continue
            if lf not in props or rf not in props:
                continue
            if (lf, rf) in seen_pairs:
                continue
            seen_pairs.add((lf, rf))
            candidates.append({
                "left_field": lf,
                "right_field": rf,
                "source": str(item.get("source", "mapping_policy")).strip() or "mapping_policy",
                "reason": str(item.get("reason", "configured")).strip() or "configured",
            })

        # 1) explicit ontology self-relations first
        for rel in self.ontology.get_relations_for(entity_name):
            if not isinstance(rel, dict):
                continue
            if str(rel.get("from", "")).strip().upper() != entity_name:
                continue
            if str(rel.get("to", "")).strip().upper() != entity_name:
                continue

            left_fields = self._split_join_fields(rel.get("from_field"))
            right_fields = self._split_join_fields(rel.get("to_field"))
            if not left_fields or not right_fields or len(left_fields) != len(right_fields):
                continue

            for left_field, right_field in zip(left_fields, right_fields):
                lf = str(left_field).strip().upper()
                rf = str(right_field).strip().upper()
                if not lf or not rf:
                    continue
                if lf not in props or rf not in props:
                    continue
                if (lf, rf) in seen_pairs:
                    continue
                seen_pairs.add((lf, rf))
                candidates.append({
                    "left_field": lf,
                    "right_field": rf,
                    "source": "ontology_relation",
                    "reason": "explicit_self_relation",
                })

        # 2) generic fallback: infer "reference-like field" -> key by structural similarity
        if not allow_inferred:
            return key_fields, candidates

        for field_name, field_def in props.items():
            lf = str(field_name).strip().upper()
            if not lf or lf in key_fields:
                continue

            field_type = str((field_def or {}).get("type", "")).strip().lower()
            if field_type and field_type not in {"string", "text"}:
                continue

            for key in key_fields:
                rf = str(key).strip().upper()
                if not rf:
                    continue
                score, reason = self._score_reference_to_key(lf, rf)
                if score < 3:
                    continue
                if (lf, rf) in seen_pairs:
                    continue
                seen_pairs.add((lf, rf))
                candidates.append({
                    "left_field": lf,
                    "right_field": rf,
                    "source": "inferred_structure",
                    "reason": reason,
                })

        return key_fields, candidates

    @staticmethod
    def _split_join_fields(raw_value) -> list[str]:
        if isinstance(raw_value, list):
            values = raw_value
        else:
            text = str(raw_value or "").strip()
            if not text:
                return []
            values = text.split(",")

        result = []
        for item in values:
            field = str(item or "").strip().upper()
            if field:
                result.append(field)
        return result

    @staticmethod
    def _tokenize_field_name(field_name: str) -> list[str]:
        return [token for token in re.split(r"[^A-Z0-9]+", str(field_name or "").upper()) if token]

    def _score_reference_to_key(self, left_field: str, right_key: str) -> tuple[int, str]:
        lf = str(left_field or "").strip().upper()
        rk = str(right_key or "").strip().upper()
        if not lf or not rk or lf == rk:
            return 0, ""

        score = 0
        reasons = []
        if lf.endswith(f"_{rk}") or lf.endswith(rk):
            score += 3
            reasons.append("suffix_match")

        left_tokens = self._tokenize_field_name(lf)
        right_tokens = self._tokenize_field_name(rk)
        if right_tokens:
            overlap = len(set(left_tokens) & set(right_tokens)) / float(len(set(right_tokens)))
            if overlap >= 1.0:
                score += 2
                reasons.append("token_cover")
            elif overlap >= 0.5:
                score += 1
                reasons.append("token_partial")

        if score <= 0:
            return 0, ""
        return score, "+".join(reasons)

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

