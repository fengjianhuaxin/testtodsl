"""多智能体流水线编排器"""
import json
import os
import time
import pandas as pd

from ontology.ontology_manager import OntologyManager
from knowledge.knowledge_manager import KnowledgeManager
from prompts.prompt_manager import PromptManager
from mapping.mapping_manager import MappingManager
from store.factory import create_data_store
from llm.qwen_client import QwenClient
from agents.question_split_agent import QuestionSplitAgent
from agents.intent_clarify_agent import IntentClarifyAgent
from agents.knowledge_verify_agent import KnowledgeVerifyAgent
from agents.dispatch_agent import DispatchAgent
from agents.query_plan_agent import QueryPlanAgent
from agents.condition_filter_agent import ConditionFilterAgent
from agents.field_extract_agent import FieldExtractAgent
from agents.calc_method_agent import CalcMethodAgent
from agents.dsl_query_agent import DSLQueryAgent
from agents.compute_agent import ComputeAgent
from agents.quality_check_agent import QualityCheckAgent
from agents.answer_agent import AnswerAgent
from agents.chart_agent import ChartAgent
import config


class Orchestrator:
    """多智能体流水线编排器 - 按序执行智能体并记录全链路中间产物"""

    def __init__(self):
        # 初始化基础组件
        self.ontology = OntologyManager(
            os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
        )
        self.knowledge = KnowledgeManager(config.KNOWLEDGE_FILE)
        self.prompts = PromptManager(config.PROMPTS_FILE)
        self.mapping = MappingManager(config.MAPPING_DIR)
        self.data_store = create_data_store(self.mapping)
        self.llm = QwenClient()
        self.splitter = QuestionSplitAgent()

        # 输出目录
        self.run_id = time.strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(config.OUTPUT_DIR, self.run_id)
        os.makedirs(self.output_dir, exist_ok=True)

        # 初始化智能体
        self.agents = [
            ("01_意图澄清", IntentClarifyAgent(self.llm, self.ontology, self.knowledge, self.prompts)),
            ("02_知识验证", KnowledgeVerifyAgent(self.ontology)),
            ("03_调度", DispatchAgent()),
            ("04_查询规划A", QueryPlanAgent(self.ontology)),
            ("05_条件筛选B", ConditionFilterAgent(self.ontology)),
            ("06_字段提取B", FieldExtractAgent(self.ontology)),
            ("07_计算方法C", CalcMethodAgent()),
            ("08_DSL查询", DSLQueryAgent(self.llm, self.mapping, self.ontology)),
            ("09_计算执行", ComputeAgent(self.data_store, self.mapping, self.ontology)),
            ("10_质检验证", QualityCheckAgent()),
            ("11_答案回复", AnswerAgent(self.llm, self.prompts)),
            ("12_图表报告", ChartAgent(self.output_dir)),
        ]

    def run(self, question: str, source: str = None) -> dict:
        """执行完整流水线"""
        if hasattr(self.llm, "reset_traces"):
            self.llm.reset_traces()

        print(f"\n{'='*60}")
        print(f"🚀 开始处理: {question}")
        print(f"{'='*60}")

        normalized_question, rewrite_hits = self.knowledge.normalize_question(question)
        if rewrite_hits:
            print(f"\n--- 步骤 00_全局同义词归一 ---")
            for hit in rewrite_hits:
                ts = time.strftime("%H:%M:%S")
                print(
                    f"  [{ts}]  全局同义词: 命中替换 "
                    f"'{hit['source']}' -> '{hit['target']}' x{hit['count']}"
                )
            if normalized_question != question:
                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}]  全局同义词: 问题归一化后: {normalized_question}")

        # 初始化流水线数据
        base_pipeline_data = {
            "question": normalized_question,
            "raw_question_input": question,
            "available_sources": config.DATA_SOURCES,
            "global_rewrite_hits": rewrite_hits,
        }

        # 如果指定了数据源，在意图中预设
        if source:
            base_pipeline_data["preferred_source"] = source

        intermediates = {"raw_input": question}
        if rewrite_hits:
            intermediates["step_00_全局同义词归一"] = {
                "original_question": question,
                "normalized_question": normalized_question,
                "rewrite_hits": rewrite_hits,
            }

        print(f"\n--- 步骤 00_问题拆解 ---")
        split_input = dict(base_pipeline_data)
        split_output = self.splitter.run(split_input)
        split_analysis = split_output.get("split_analysis", {})
        intermediates["step_00_问题拆解"] = split_analysis

        tasks = split_analysis.get("sub_tasks", []) if isinstance(split_analysis, dict) else []
        if not isinstance(tasks, list) or not tasks:
            tasks = [{"task_id": "task_1", "question": normalized_question}]

        multi_enabled = bool(split_analysis.get("enabled")) and len(tasks) > 1

        if multi_enabled:
            task_results = []
            intermediates["multi_task"] = {
                "enabled": True,
                "task_count": len(tasks),
                "tasks": [],
            }

            for idx, task in enumerate(tasks, start=1):
                task_id = str(task.get("task_id", f"task_{idx}")).strip() or f"task_{idx}"
                task_question = str(task.get("question", "")).strip() or normalized_question
                print(f"\n{'-'*60}")
                print(f"🔹 子任务 {idx}/{len(tasks)} [{task_id}]：{task_question}")
                print(f"{'-'*60}")

                task_pipeline = {
                    **base_pipeline_data,
                    "question": task_question,
                    "split_analysis": split_analysis,
                    "current_task": {
                        "task_id": task_id,
                        "index": idx,
                        "question": task_question,
                    },
                }
                task_intermediates = {}
                final_task_data = self._run_agents(task_pipeline, task_intermediates)
                intermediates["multi_task"]["tasks"].append({
                    "task_id": task_id,
                    "question": task_question,
                    "steps": task_intermediates,
                })

                task_results.append({
                    "task_id": task_id,
                    "question": task_question,
                    "answer": final_task_data.get("answer", ""),
                    "compute_result": final_task_data.get("compute_result", []),
                    "dsl_query": final_task_data.get("dsl_query", {}),
                    "quality_check": final_task_data.get("quality_check", {}),
                    "chart_path": final_task_data.get("chart_path"),
                })

            combined_answer = self._build_multi_answer(task_results)
            combined_compute_result = []
            for item in task_results:
                rows = item.get("compute_result", [])
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            combined_compute_result.append({
                                "_task_id": item["task_id"],
                                "_task_question": item["question"],
                                **row,
                            })
                        else:
                            combined_compute_result.append({
                                "_task_id": item["task_id"],
                                "_task_question": item["question"],
                                "value": row,
                            })

            pipeline_data = {
                **base_pipeline_data,
                "split_analysis": split_analysis,
                "multi_task": {
                    "enabled": True,
                    "task_count": len(task_results),
                    "task_results": task_results,
                },
                "dsl_query": {
                    item["task_id"]: item.get("dsl_query", {})
                    for item in task_results
                },
                "compute_result": combined_compute_result,
                "answer": combined_answer,
                "chart_path": None,
            }
        else:
            pipeline_data = {
                **base_pipeline_data,
                "split_analysis": split_analysis,
            }
            pipeline_data = self._run_agents(pipeline_data, intermediates)

        # 保存中间产物
        self._save_intermediates(intermediates)

        # 单独保存 DSL 查询
        dsl = pipeline_data.get("dsl_query", {})
        if dsl:
            self._save_dsl(dsl)

        # 单独保存结果
        result = {
            "question": question,
            "answer": pipeline_data.get("answer", ""),
            "chart_path": pipeline_data.get("chart_path"),
            "compute_result": pipeline_data.get("compute_result", []),
        }
        if pipeline_data.get("multi_task", {}).get("enabled"):
            result["multi_task"] = pipeline_data.get("multi_task", {})
        self._save_result(result)
        self._save_llm_traces(question)

        # 打印最终结果
        print(f"\n{'='*60}")
        print("📋 最终答案:")
        print(f"{'='*60}")
        print(pipeline_data.get("answer", "无答案"))

        if pipeline_data.get("chart_path"):
            print(f"\n📊 图表已保存: {pipeline_data['chart_path']}")

        print(f"\n📁 中间产物目录: {self.output_dir}")
        return result

    def _run_agents(self, pipeline_data: dict, intermediates: dict) -> dict:
        current = pipeline_data
        for step_name, agent in self.agents:
            print(f"\n--- 步骤 {step_name} ---")
            try:
                current = agent.run(current)
                intermediates[f"step_{step_name}"] = self._snapshot(current)

                if step_name == "03_调度":
                    dispatch = current.get("dispatch", {})
                    if dispatch.get("action") == "error":
                        print(f"\n❌ 流程中止: {dispatch.get('reason')}")
                        break
            except Exception as e:
                print(f"\n❌ 步骤 {step_name} 执行失败: {e}")
                intermediates[f"step_{step_name}_error"] = str(e)
                import traceback
                traceback.print_exc()
                break
        return current

    @staticmethod
    def _snapshot(pipeline_data: dict) -> dict:
        step_data = {}
        for k, v in pipeline_data.items():
            if k == "compute_result_df":
                continue
            if isinstance(v, pd.DataFrame):
                step_data[k] = v.to_dict(orient="records")
            else:
                try:
                    json.dumps(v, ensure_ascii=False)
                    step_data[k] = v
                except (TypeError, ValueError):
                    step_data[k] = str(v)
        return step_data

    @staticmethod
    def _build_multi_answer(task_results: list) -> str:
        if not isinstance(task_results, list) or not task_results:
            return ""
        lines = ["已按多问题拆解执行，结果如下："]
        for idx, item in enumerate(task_results, start=1):
            question = str(item.get("question", "")).strip()
            answer = str(item.get("answer", "")).strip()
            lines.append(f"\n【子问题{idx}】{question}")
            lines.append(answer or "无结果")
        return "\n".join(lines)

    def _save_intermediates(self, intermediates):
        path = os.path.join(self.output_dir, "intermediates.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(intermediates, f, ensure_ascii=False, indent=2, default=str)

    def _save_dsl(self, dsl):
        path = os.path.join(self.output_dir, "dsl_query.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dsl, f, ensure_ascii=False, indent=2)

    def _save_result(self, result):
        path = os.path.join(self.output_dir, "result.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

    def _save_llm_traces(self, question: str):
        traces = []
        if hasattr(self.llm, "get_traces"):
            traces = self.llm.get_traces() or []

        payload = {
            "question": question,
            "llm_traces": traces,
        }
        path = os.path.join(self.output_dir, "llm_traces.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
