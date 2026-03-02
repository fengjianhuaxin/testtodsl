"""调度智能体 - 根据意图复杂度分配任务"""
from agents.base_agent import BaseAgent


class DispatchAgent(BaseAgent):

    def __init__(self):
        super().__init__("调度智能体", "根据意图复杂度分配任务给后续智能体")

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        verify = input_data["knowledge_verify"]
        self.log("分析任务复杂度并分配执行路径...")

        if not verify["passed"]:
            self.log(f"知识验证存在问题: {verify['issues']}，继续执行（作为警告）")

        # 判断任务类型
        calc_type = intent.get("calc_type", "detail")
        target_entities = intent.get("target_entities", [])
        conditions = intent.get("conditions", [])
        data_source = intent.get("data_source", "all")

        # CLI 指定的数据源覆盖 LLM 推断
        if input_data.get("preferred_source"):
            data_source = input_data["preferred_source"]

        # 判断复杂度
        needs_join = len(target_entities) > 1
        needs_calc = calc_type not in ("detail",)
        all_sources = list(input_data.get("available_sources", {}).keys())
        needs_multi_source = data_source == "all"

        task_plan = {
            "action": "execute",
            "data_sources": all_sources if needs_multi_source else [data_source],
            "needs_join": needs_join,
            "needs_calc": needs_calc,
            "pipeline": ["query_plan", "condition_filter", "field_extract", "calc_method",
                         "dsl_query", "compute", "quality_check", "answer", "chart"]
        }

        self.log(f"任务分配完成: 数据源={task_plan['data_sources']}, 需要关联={needs_join}, 需要计算={needs_calc}")
        return {**input_data, "dispatch": task_plan}
