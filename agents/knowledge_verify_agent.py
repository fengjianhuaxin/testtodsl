"""知识验证智能体 - 验证意图中的概念是否存在于本体中"""
from agents.base_agent import BaseAgent


class KnowledgeVerifyAgent(BaseAgent):

    def __init__(self, ontology_manager):
        super().__init__("知识验证智能体", "验证问题概念是否存在于本体知识图谱中")
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        self.log("开始验证意图中的概念...")

        issues = []
        verified_entities = []

        # 验证目标实体
        for entity_name in intent.get("target_entities", []):
            entity = self.ontology.get_entity(entity_name)
            if entity:
                verified_entities.append({"name": entity_name, "label": entity["label"]})
            else:
                # 尝试按标签查找
                result = self.ontology.get_entity_by_label(entity_name)
                if result:
                    verified_entities.append({"name": result[0], "label": result[1]["label"]})
                else:
                    issues.append(f"实体 '{entity_name}' 不存在于本体中")

        # 验证条件中的字段
        for cond in intent.get("conditions", []):
            entity = cond.get("entity", "")
            field = cond.get("field", "")
            props = self.ontology.get_entity_properties(entity)
            if props and field not in props:
                issues.append(f"实体 '{entity}' 没有属性 '{field}'")

        # 验证输出字段（跳过聚合计算字段）
        agg_fields = {"count", "avg", "sum", "max", "min", "count_value", "avg_value", "总数", "人数", "平均"}
        for out_f in intent.get("output_fields", []):
            entity = out_f.get("entity", "")
            field = out_f.get("field", "")
            if field in agg_fields:
                continue  # 聚合字段不是实体属性，跳过
            # 跳过函数式写法如 avg(score), count(*), sum(credit) 等
            if "(" in field and ")" in field:
                continue
            props = self.ontology.get_entity_properties(entity)
            if props and field not in props:
                issues.append(f"实体 '{entity}' 没有属性 '{field}'")

        passed = len(issues) == 0
        self.log(f"验证{'通过 ✅' if passed else '未通过 ❌'}, 发现{len(issues)}个问题")

        return {
            **input_data,
            "knowledge_verify": {
                "passed": passed,
                "verified_entities": verified_entities,
                "issues": issues
            }
        }
