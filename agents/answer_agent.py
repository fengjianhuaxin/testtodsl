"""答案回复智能体 - 将结构化结果转为自然语言"""
from string import Template

from agents.base_agent import BaseAgent
from tabulate import tabulate


class AnswerAgent(BaseAgent):

    def __init__(self, llm_client, prompt_manager=None):
        super().__init__("答案回复智能体", "将结构化数据转为自然语言答案")
        self.llm = llm_client
        self.prompts = prompt_manager

    def run(self, input_data: dict) -> dict:
        compute_result = input_data.get("compute_result", [])
        raw_question = input_data.get("raw_question", "")
        calc_rule = input_data.get("calc_rule", {})
        quality_check = input_data.get("quality_check", {})
        self.log("生成自然语言答案...")

        # 如果质检未通过
        if not quality_check.get("passed", True):
            issues_text = "; ".join(quality_check.get("issues", []))
            answer = f"查询执行完成，但发现以下问题：{issues_text}\n\n"
        else:
            answer = ""

        # 构建结果文本
        if not compute_result:
            answer += "未找到符合条件的数据。"
        else:
            # 格式化为表格
            table_text = tabulate(compute_result, headers="keys", tablefmt="grid",
                                  showindex=False)

            default_system_prompt = """你是一个数据分析助手。请根据用户的原始问题和查询结果，用简洁、友好的自然语言回复用户。
要求：
1. 先用一句话概括回答
2. 然后给出详细数据
3. 如果有统计数据，用直观的方式呈现
4. 语言简洁明了"""
            system_prompt = self._render_prompt(
                key="answer_system",
                default_template=default_system_prompt,
                context={},
            )

            default_user_prompt = """原始问题: $raw_question
计算类型: $calc_type ($calc_desc)
查询结果数据:
$table_text

请用自然语言回复用户。"""
            user_msg = self._render_prompt(
                key="answer_user",
                default_template=default_user_prompt,
                context={
                    "raw_question": raw_question,
                    "calc_type": calc_rule.get("type", "detail"),
                    "calc_desc": calc_rule.get("description", ""),
                    "table_text": table_text,
                },
            )

            try:
                answer += self.llm.chat(system_prompt, user_msg)
            except Exception as e:
                self.log(f"LLM 调用失败，使用默认格式: {e}")
                answer += f"查询结果如下（共{len(compute_result)}条记录）：\n\n{table_text}"

        self.log("答案生成完成")
        return {**input_data, "answer": answer}

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        try:
            return Template(default_template).safe_substitute(**{k: str(v) for k, v in (context or {}).items()})
        except Exception:
            return default_template
