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
        target_entities = intent.get("target_entities", [])
        primary_entity = str(intent.get("primary_entity", "")).strip().upper()
        target_entities = self._reorder_by_primary_entity(target_entities, primary_entity)
        question_text = str(input_data.get("raw_question") or input_data.get("question") or "").strip()
        self.log(f"构建对象子图，涉及实体: {target_entities}")
        if len(target_entities) > 1 and primary_entity:
            self.log(f"主本体锚定: primary_entity={primary_entity}, ordered={target_entities}")

        subgraph = {"entities": [], "relations": [], "paths": [], "joins": []}

        for entity_name in target_entities:
            entity_def = self.ontology.get_entity(entity_name)
            if entity_def:
                subgraph["entities"].append(
                    {
                        "name": entity_name,
                        "label": entity_def.get("label", entity_name),
                        "properties": list(entity_def.get("properties", {}).keys()),
                    }
                )

        if len(target_entities) > 1:
            for i in range(len(target_entities)):
                for j in range(i + 1, len(target_entities)):
                    path = self.ontology.find_path(target_entities[i], target_entities[j])
                    if path:
                        subgraph["paths"].append(
                            {
                                "from": target_entities[i],
                                "to": target_entities[j],
                                "path": path,
                            }
                        )

            for idx in range(1, len(target_entities)):
                left_entity = str(target_entities[idx - 1]).strip().upper()
                right_entity = str(target_entities[idx]).strip().upper()
                join_type, reason = self._infer_join_type(
                    question_text=question_text,
                    left_entity=left_entity,
                    right_entity=right_entity,
                )
                subgraph["joins"].append(
                    {
                        "left_entity": left_entity,
                        "right_entity": right_entity,
                        "join_type": join_type,
                        "reason": reason,
                    }
                )
                self.log(f"JOIN策略: {left_entity} -> {right_entity} 使用 {join_type.upper()} JOIN ({reason})")

        for entity_name in target_entities:
            rels = self.ontology.get_relations_for(entity_name)
            for rel in rels:
                if rel not in subgraph["relations"]:
                    subgraph["relations"].append(rel)

        self.log(f"子图构建完成: {len(subgraph['entities'])}个实体, {len(subgraph['relations'])}个关系")
        return {**input_data, "query_plan": subgraph}

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
