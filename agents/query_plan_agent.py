"""Query plan agent (Step A) - build entity subgraph and join strategy."""
import re
from string import Template

from agents.base_agent import BaseAgent


class QueryPlanAgent(BaseAgent):
    """Build query subgraph and decide join type for adjacent entities."""

    # Missing-relation semantics: usually requires LEFT JOIN to preserve left side.
    LEFT_JOIN_TRIGGERS = (
        "未关联",
        "没有关联",
        "无关联",
        "未匹配",
        "没有匹配",
        "无匹配",
        "不存在",
        "为空",
        "空值",
        "缺失",
        "未配置",
    )

    def __init__(self, ontology_manager, llm_client=None, prompt_manager=None):
        super().__init__("查询规划智能体", "从本体中定位所需实体并构建关联路径(步骤A)")
        self.ontology = ontology_manager
        self.llm = llm_client
        self.prompts = prompt_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        target_entities = self._collect_target_entities(intent)
        raw_instances = intent.get("entity_instances", [])
        raw_relations = intent.get("relations", [])
        primary_entity = str(intent.get("primary_entity", "")).strip().upper()
        if not primary_entity and target_entities:
            primary_entity = str(target_entities[0]).strip().upper()
        target_entities = self._reorder_by_primary_entity(target_entities, primary_entity)
        question_text = str(input_data.get("raw_question") or input_data.get("question") or "").strip()
        self.log(f"构建对象子图，涉及实体: {target_entities}")
        if len(target_entities) > 1 and primary_entity:
            self.log(f"主本体锚定: primary_entity={primary_entity}, ordered={target_entities}")

        subgraph = {"entities": [], "relations": [], "paths": [], "joins": []}
        instance_entity_map = {}

        if isinstance(raw_instances, list) and raw_instances:
            seen_ids = set()
            for idx, item in enumerate(raw_instances, start=1):
                if not isinstance(item, dict):
                    continue
                entity_name = str(item.get("entity", "")).strip().upper()
                if not entity_name:
                    continue
                instance_id = str(item.get("id", "") or item.get("instance_id", "")).strip()
                if not instance_id:
                    instance_id = f"{entity_name.lower()}_{idx}"
                instance_id = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5]", "_", instance_id)
                if not instance_id or instance_id in seen_ids:
                    continue
                seen_ids.add(instance_id)

                entity_def = self.ontology.get_entity(entity_name) or {}
                subgraph["entities"].append(
                    {
                        "name": entity_name,
                        "instance_id": instance_id,
                        "role": str(item.get("role", "")).strip(),
                        "label": entity_def.get("label", entity_name),
                        "properties": list(entity_def.get("properties", {}).keys()),
                    }
                )
                instance_entity_map[instance_id] = entity_name
                if entity_name not in target_entities:
                    target_entities.append(entity_name)
        else:
            for idx, entity_name in enumerate(target_entities, start=1):
                entity_def = self.ontology.get_entity(entity_name)
                if not entity_def:
                    continue
                instance_id = f"{entity_name.lower()}_{idx}"
                subgraph["entities"].append(
                    {
                        "name": entity_name,
                        "instance_id": instance_id,
                        "label": entity_def.get("label", entity_name),
                        "properties": list(entity_def.get("properties", {}).keys()),
                    }
                )
                instance_entity_map[instance_id] = entity_name

        unique_entities = []
        for item in target_entities:
            name = str(item).strip().upper()
            if name and name not in unique_entities:
                unique_entities.append(name)

        if len(unique_entities) > 1:
            for i in range(len(unique_entities)):
                for j in range(i + 1, len(unique_entities)):
                    path = self.ontology.find_path(unique_entities[i], unique_entities[j])
                    if path:
                        subgraph["paths"].append(
                            {
                                "from": unique_entities[i],
                                "to": unique_entities[j],
                                "path": path,
                            }
                        )

        intent_join_relations = []
        if isinstance(raw_relations, list):
            for rel_item in raw_relations:
                if not isinstance(rel_item, dict):
                    continue
                left_field_raw = str(rel_item.get("left_field", "") or rel_item.get("from_field", "")).strip()
                right_field_raw = str(rel_item.get("right_field", "") or rel_item.get("to_field", "")).strip()
                if left_field_raw and right_field_raw:
                    intent_join_relations.append(rel_item)

        if intent_join_relations:
            for item in intent_join_relations:
                if not isinstance(item, dict):
                    continue
                left_instance = str(item.get("left_instance", "")).strip()
                right_instance = str(item.get("right_instance", "")).strip()
                left_field = str(item.get("left_field", "")).strip()
                right_field = str(item.get("right_field", "")).strip()
                if (
                    not left_instance
                    or not right_instance
                    or not left_field
                    or not right_field
                    or left_instance not in instance_entity_map
                    or right_instance not in instance_entity_map
                ):
                    continue

                join_type = self._normalize_join_type(str(item.get("join_type", "inner")))
                if not join_type:
                    join_type = "inner"
                left_entity = instance_entity_map[left_instance]
                right_entity = instance_entity_map[right_instance]
                join_item = {
                    "left_entity": left_entity,
                    "right_entity": right_entity,
                    "left_instance": left_instance,
                    "right_instance": right_instance,
                    "left_field": left_field,
                    "right_field": right_field,
                    "join_type": join_type,
                    "reason": "intent_relation",
                }
                subgraph["joins"].append(join_item)
                self.log(
                    f"JOIN策略: {left_instance}({left_entity}).{left_field} -> "
                    f"{right_instance}({right_entity}).{right_field} 使用 {join_type.upper()} JOIN (intent_relation)"
                )
        elif len(subgraph["entities"]) > 1:
            for idx in range(1, len(subgraph["entities"])):
                left_item = subgraph["entities"][idx - 1]
                right_item = subgraph["entities"][idx]
                left_entity = str(left_item["name"]).strip().upper()
                right_entity = str(right_item["name"]).strip().upper()
                join_type, reason = self._infer_join_type(
                    question_text=question_text,
                    left_entity=left_entity,
                    right_entity=right_entity,
                )
                subgraph["joins"].append(
                    {
                        "left_entity": left_entity,
                        "right_entity": right_entity,
                        "left_instance": left_item.get("instance_id", ""),
                        "right_instance": right_item.get("instance_id", ""),
                        "join_type": join_type,
                        "reason": reason,
                    }
                )
                self.log(
                    f"JOIN策略: {left_item.get('instance_id')}({left_entity}) -> "
                    f"{right_item.get('instance_id')}({right_entity}) 使用 {join_type.upper()} JOIN ({reason})"
                )

        for entity_name in unique_entities:
            rels = self.ontology.get_relations_for(entity_name)
            for rel in rels:
                if rel not in subgraph["relations"]:
                    subgraph["relations"].append(rel)

        self.log(f"子图构建完成: {len(subgraph['entities'])}个实体, {len(subgraph['relations'])}个关系")
        return {**input_data, "query_plan": subgraph}

    @staticmethod
    def _collect_target_entities(intent: dict) -> list:
        entities = []

        for item in intent.get("target_entities", []) if isinstance(intent.get("target_entities", []), list) else []:
            name = str(item).strip().upper()
            if name and name not in entities:
                entities.append(name)

        raw_instances = intent.get("entity_instances", [])
        if isinstance(raw_instances, list):
            for item in raw_instances:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("entity", "")).strip().upper()
                if name and name not in entities:
                    entities.append(name)

        for section in ("conditions", "output_fields"):
            rows = intent.get(section, [])
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("entity", "")).strip().upper()
                if name and name not in entities:
                    entities.append(name)

        return entities
    @staticmethod
    def _reorder_by_primary_entity(target_entities: list, primary_entity: str) -> list:
        entities = []
        for item in target_entities if isinstance(target_entities, list) else []:
            name = str(item).strip().upper()
            if name and name not in entities:
                entities.append(name)
        if len(entities) <= 1:
            return entities

        primary = str(primary_entity or "").strip().upper()
        if primary and primary in entities:
            ordered = [primary]
            ordered.extend(name for name in entities if name != primary)
            return ordered
        return entities

    def _infer_join_type(self, question_text: str, left_entity: str, right_entity: str) -> tuple[str, str]:
        llm_decision = self._infer_join_type_with_llm(question_text, left_entity, right_entity)
        if llm_decision:
            return llm_decision

        text = str(question_text or "").strip()
        if not text:
            return "inner", "default"

        lowered = text.lower()
        if any(trigger in text for trigger in self.LEFT_JOIN_TRIGGERS):
            return "left", "missing_relation_semantics"

        # Slightly stricter pattern: no/without + relation words.
        if re.search(r"(没有|无|未)(关联|匹配|对应)", lowered):
            return "left", "missing_relation_pattern"

        return "inner", "default"

    def _infer_join_type_with_llm(self, question_text: str, left_entity: str, right_entity: str) -> tuple[str, str] | None:
        if not self.llm:
            return None

        left_label = self._entity_label(left_entity)
        right_label = self._entity_label(right_entity)
        system_prompt = self._render_prompt(
            key="join_type_system",
            default_template=(
                "你是SQL JOIN类型判定器。"
                "仅判断两表连接类型，返回JSON且只包含两个字段："
                "{\"type\":\"left join或join\",\"reason\":\"简短原因\"}。"
                "规则："
                "1) 当问题要求左侧主体全量保留（即使右侧无匹配也要保留）时，type=left join。"
                "2) 其余场景 type=join。"
                "3) 禁止输出除type/reason外的字段。"
            ),
            context={},
        )
        user_prompt = self._render_prompt(
            key="join_type_user",
            default_template=(
                "问题: $question\n"
                "左侧实体: $left_entity($left_label)\n"
                "右侧实体: $right_entity($right_label)\n"
                "请输出JSON。"
            ),
            context={
                "question": question_text,
                "left_entity": left_entity,
                "left_label": left_label,
                "right_entity": right_entity,
                "right_label": right_label,
            },
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.log(f"JOIN类型模型判定失败，回退规则: {left_entity}->{right_entity}, err={exc}")
            return None

        if not isinstance(parsed, dict):
            return None

        join_type = self._normalize_join_type(str(parsed.get("type", "")).strip())
        if not join_type:
            return None

        reason = str(parsed.get("reason", "")).strip() or "llm"
        return join_type, f"llm:{reason}"

    @staticmethod
    def _normalize_join_type(value: str) -> str:
        text = str(value or "").strip().lower()
        if text in {"left join", "left_join", "left"}:
            return "left"
        if text in {"join", "inner join", "inner_join", "inner"}:
            return "inner"
        return ""

    def _entity_label(self, entity_name: str) -> str:
        entity = self.ontology.get_entity(entity_name)
        if isinstance(entity, dict):
            return str(entity.get("label", "")).strip()
        return ""

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        try:
            safe_context = {k: str(v) for k, v in (context or {}).items()}
            return Template(default_template).safe_substitute(**safe_context)
        except Exception:
            return default_template
