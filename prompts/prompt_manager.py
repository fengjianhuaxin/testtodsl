"""Prompt manager - load and persist configurable prompt templates."""
import json
import os
from string import Template


class PromptManager:
    """Manage prompt templates with defaults and runtime overrides."""

    DEFAULT_PROMPTS = {
        "intent_clarify_system": {
            "name": "意图澄清系统提示词",
            "description": "步骤01：通用意图澄清（实体/条件/输出字段/计算类型）",
            "template": """你是一个意图澄清智能体，负责把用户问题解析成结构化 JSON。
你掌握的本体知识如下：
$ontology_desc

$knowledge_block

请严格返回 JSON（不要输出解释文字），格式如下：
{
  "clarified_question": "澄清后的问题",
  "target_entities": ["实体英文名"],
  "primary_entity": "主本体英文名",
  "conditions": [
    {"field":"属性英文名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体英文名"}
  ],
  "output_fields": [
    {"field":"属性英文名","entity":"实体英文名","label":"显示名"}
  ],
  "calc_type": "detail|count|sum|avg|rate|max|min|group_count|topn",
  "calc_params": {
    "group_by": ["属性英文名或ENTITY.FIELD"],
    "order_by": "属性英文名或ENTITY.FIELD或__metric__",
    "order_dir": "asc或desc",
    "limit": 10
  }
}

规则：
1) “各/分别/每个/按XX”这类分组语义，必须给出 group_by，并把分组字段放入 output_fields。
2) “最高/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) 如果是 sum/avg/max/min 且同时询问“各XX分别”，优先按维度字段分组，不要仅返回全局汇总。
5) 必须输出 primary_entity。
6) 多本体时，primary_entity 必须来自 target_entities，并代表问题的主语义主体。
7) 单本体时，primary_entity 必须等于 target_entities[0]。""",
        },
        "metric_hint_system": {
            "name": "指标规则补充提示词",
            "description": "命中指标SQL规则后，补充抽取条件/分组/排序/限制",
            "template": """你是“指标查询补充解析器”。
已确定用户要查询一个预定义指标，不要改指标本身，只抽取“筛选条件/分组/排序/限制”。
目标实体: $primary_entity
可用属性:
$property_block

请返回 JSON:
{
  "conditions": [
    {"field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}
  ],
  "output_fields": [
    {"field":"属性名","entity":"实体名","label":"显示名"}
  ],
  "calc_params": {
    "group_by": ["属性名"],
    "order_by": "属性名或__metric__",
    "order_dir": "asc或desc",
    "limit": 1
  }
}

规则:
1) 没提到就留空列表或空字符串，不要臆造。
2) “最高/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) “哪个区划/各区划/按区划”优先用 AREACODE 做 group_by，并放入 output_fields。
5) entity 统一填 $primary_entity。""",
        },
        "answer_system": {
            "name": "答案生成系统提示词",
            "description": "步骤11：将结构化结果转自然语言",
            "template": """你是一个数据分析助手。请根据用户的原始问题和查询结果，用简洁、友好的自然语言回复用户。
要求：
1. 先用一句话概括回答
2. 然后给出详细数据
3. 有统计数据时，尽量直观呈现
4. 语言简洁明了""",
        },
        "answer_user": {
            "name": "答案生成用户提示词",
            "description": "步骤11：注入问题、计算类型和表格数据",
            "template": """原始问题: $raw_question
计算类型: $calc_type ($calc_desc)
查询结果数据:
$table_text

请用自然语言回复用户。""",
        },
        "mapping_analyze_system": {
            "name": "映射分析系统提示词",
            "description": "映射管理-LLM预分析的系统提示词",
            "template": "你是一个数据映射分析专家。",
        },
        "mapping_analyze_user": {
            "name": "映射分析用户提示词",
            "description": "映射管理-LLM预分析的业务提示词",
            "template": """请分析以下本体实体属性与数据表字段的对应关系。
本体实体: $entity_name ($entity_label)
属性列表:
$prop_desc

数据表字段: $columns
样本数据: $sample_data

请以 JSON 格式返回属性到字段的映射，格式如下：
{"属性名": "字段名", ...}
只返回有把握的映射。""",
        },
        "question_split_system": {
            "name": "问题拆解-系统提示词",
            "description": "步骤00：任务规划（single_sql/multi_sql）",
            "template": """你是任务规划器。请先判断用户问题是否需要拆成多个独立查询任务。
原则：single_sql 优先，只要能用一条 SQL 完成，就不要拆。
只有确实独立且无法同 SQL 表达时，才用 multi_sql。
严格输出 JSON：
{
  "is_multi_task": true/false,
  "execution_mode": "single_sql" or "multi_sql",
  "tasks": ["子问题1", "子问题2"],
  "reason": "简短原因",
  "confidence": 0到1
}""",
        },
        "question_split_user": {
            "name": "问题拆解-用户提示词",
            "description": "步骤00：注入原始问题，变量：$question",
            "template": "原始问题: $question",
        },
        "join_type_system": {
            "name": "JOIN判定-系统提示词",
            "description": "步骤04：判定两实体连接类型（left join/join）",
            "template": """你是SQL JOIN类型判定器。
仅判断两表连接类型，返回JSON且只包含两个字段：
{"type":"left join或join","reason":"简短原因"}。
规则：
1) 当问题要求左侧主体全量保留（即使右侧无匹配也要保留）时，type=left join。
2) 其余场景 type=join。
3) 禁止输出除type/reason外的字段。""",
        },
        "join_type_user": {
            "name": "JOIN判定-用户提示词",
            "description": "步骤04：变量：$question $left_entity $left_label $right_entity $right_label",
            "template": """问题: $question
左侧实体: $left_entity($left_label)
右侧实体: $right_entity($right_label)
请输出JSON。""",
        },
        "value_resolve_closed_set_system": {
            "name": "值归一闭集-系统提示词",
            "description": "步骤05B：闭集值选择，只允许从候选值中选",
            "template": """你是字段值归一器。
你只能从给定候选值中选择一个，不能生成新值。
如果无法判断，返回 UNKNOWN。
输出 JSON: {"selected_value":"候选值或UNKNOWN","confidence":0到1}""",
        },
        "value_resolve_closed_set_user": {
            "name": "值归一闭集-用户提示词",
            "description": "步骤05B：变量：$question $entity $field $raw_value $options_text",
            "template": """问题: $question
字段: $entity.$field
用户原值: $raw_value
候选值:
$options_text""",
        },
        "value_resolve_free_system": {
            "name": "值归一自由-系统提示词",
            "description": "步骤05B：无闭集时的值归一",
            "template": """你是字段值归一器。
基于问题语义，将用户值归一成更适合数据库过滤的值。
如果不确定，保持原值。
输出 JSON: {"resolved_value":"值","confidence":0到1}""",
        },
        "value_resolve_free_user": {
            "name": "值归一自由-用户提示词",
            "description": "步骤05B：变量：$question $entity $field $raw_value",
            "template": """问题: $question
字段: $entity.$field
用户原值: $raw_value""",
        },
    }

    def __init__(self, prompt_file: str):
        self.prompt_file = prompt_file

    def _load(self) -> dict:
        if not os.path.exists(self.prompt_file):
            return {"prompts": {}}
        try:
            with open(self.prompt_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, dict):
                return {"prompts": {}}
            data.setdefault("prompts", {})
            if not isinstance(data["prompts"], dict):
                data["prompts"] = {}
            return data
        except Exception:
            return {"prompts": {}}

    def _save(self, data: dict):
        os.makedirs(os.path.dirname(self.prompt_file), exist_ok=True)
        with open(self.prompt_file, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def list_prompts(self) -> list:
        data = self._load()
        overrides = data.get("prompts", {})
        keys = list(self.DEFAULT_PROMPTS.keys())
        for extra_key in overrides.keys():
            if extra_key not in keys:
                keys.append(extra_key)

        items = []
        for key in keys:
            default_item = self.DEFAULT_PROMPTS.get(key, {})
            override_item = overrides.get(key, {}) if isinstance(overrides.get(key, {}), dict) else {}
            items.append(
                {
                    "key": key,
                    "name": str(override_item.get("name", default_item.get("name", key))),
                    "description": str(override_item.get("description", default_item.get("description", ""))),
                    "template": str(override_item.get("template", default_item.get("template", ""))),
                    "has_override": key in overrides,
                }
            )
        return items

    def get_prompt(self, key: str) -> dict:
        for item in self.list_prompts():
            if item["key"] == key:
                return item
        return {
            "key": key,
            "name": key,
            "description": "",
            "template": "",
            "has_override": False,
        }

    def render(self, key: str, context: dict | None = None, fallback_template: str = "") -> str:
        prompt = self.get_prompt(key)
        template_text = prompt.get("template") or fallback_template or ""
        safe_context = {k: str(v) for k, v in (context or {}).items()}
        try:
            return Template(template_text).safe_substitute(**safe_context)
        except Exception:
            return template_text

    def update_prompt(self, key: str, template: str, name: str | None = None, description: str | None = None):
        data = self._load()
        prompts = data.setdefault("prompts", {})
        item = prompts.get(key, {}) if isinstance(prompts.get(key, {}), dict) else {}
        item["template"] = str(template or "")
        if name is not None:
            item["name"] = str(name)
        if description is not None:
            item["description"] = str(description)
        prompts[key] = item
        data["prompts"] = prompts
        self._save(data)

    def reset_prompt(self, key: str):
        data = self._load()
        prompts = data.get("prompts", {})
        if key in prompts:
            del prompts[key]
        data["prompts"] = prompts
        self._save(data)
