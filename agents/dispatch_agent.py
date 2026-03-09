"""Dispatch agent - decide execution plan from clarified intent."""

import config
from agents.base_agent import BaseAgent


class DispatchAgent(BaseAgent):
    def __init__(self):
        super().__init__("调度智能体", "根据意图复杂度分配后续执行路径")

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        verify = input_data["knowledge_verify"]
        self.log("分析任务复杂度并分配执行路径...")

        if not verify.get("passed", False):
            self.log(f"知识验证存在问题: {verify.get('issues', [])}，继续执行（告警）")

        calc_type = intent.get("calc_type", "detail")
        target_entities = intent.get("target_entities", [])
        entity_instances = intent.get("entity_instances", [])
        relations = intent.get("relations", [])
        needs_join = (
            len(target_entities) > 1
            or (isinstance(entity_instances, list) and len(entity_instances) > 1)
            or (isinstance(relations, list) and len(relations) > 0)
        )
        needs_calc = calc_type not in ("detail",)
        source_id = str(input_data.get("source_id", "")).strip() or config.get_default_source_id()

        task_plan = {
            "action": "execute",
            "source_id": source_id,
            "needs_join": needs_join,
            "needs_calc": needs_calc,
            "pipeline": [
                "query_plan",
                "condition_filter",
                "value_resolve",
                "field_extract",
                "calc_method",
                "dsl_query",
                "compute",
                "quality_check",
                "answer",
                "chart",
            ],
        }

        self.log(
            f"任务分配完成: 需要关联={needs_join}, 需要计算={needs_calc}"
        )
        return {**input_data, "dispatch": task_plan}
