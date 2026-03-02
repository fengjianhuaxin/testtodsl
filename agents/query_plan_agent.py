"""Query plan agent (Step A) - build entity subgraph and join strategy."""
import re

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

    def __init__(self, ontology_manager):
        super().__init__("查询规划智能体", "从本体中定位所需实体并构建关联路径(步骤A)")
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        target_entities = intent.get("target_entities", [])
        question_text = str(input_data.get("raw_question") or input_data.get("question") or "").strip()
        self.log(f"构建对象子图，涉及实体: {target_entities}")

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

    def _infer_join_type(self, question_text: str, left_entity: str, right_entity: str) -> tuple[str, str]:
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
