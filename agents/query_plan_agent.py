"""查询规划智能体 (A) - 构建对象子图，确定实体关联路径"""
from agents.base_agent import BaseAgent


class QueryPlanAgent(BaseAgent):

    def __init__(self, ontology_manager):
        super().__init__("查询规划智能体", "从本体中定位所需实体并构建关联路径 (A步骤)")
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        target_entities = intent.get("target_entities", [])
        self.log(f"构建对象子图，涉及实体: {target_entities}")

        subgraph = {"entities": [], "relations": [], "paths": []}

        # 收集所有涉及实体的详细信息
        for entity_name in target_entities:
            entity_def = self.ontology.get_entity(entity_name)
            if entity_def:
                subgraph["entities"].append({
                    "name": entity_name,
                    "label": entity_def["label"],
                    "properties": list(entity_def.get("properties", {}).keys())
                })

        # 查找实体之间的关系路径
        if len(target_entities) > 1:
            for i in range(len(target_entities)):
                for j in range(i + 1, len(target_entities)):
                    path = self.ontology.find_path(target_entities[i], target_entities[j])
                    if path:
                        subgraph["paths"].append({
                            "from": target_entities[i],
                            "to": target_entities[j],
                            "path": path
                        })

        # 收集相关关系
        for entity_name in target_entities:
            rels = self.ontology.get_relations_for(entity_name)
            for rel in rels:
                if rel not in subgraph["relations"]:
                    subgraph["relations"].append(rel)

        self.log(f"子图构建完成: {len(subgraph['entities'])}个实体, {len(subgraph['relations'])}个关系")
        return {**input_data, "query_plan": subgraph}
