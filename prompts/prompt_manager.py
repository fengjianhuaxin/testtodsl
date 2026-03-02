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

可用数据源: $available_sources
$knowledge_block

请严格返回 JSON（不要输出解释文字），格式如下：
{
  "clarified_question": "澄清后的问题",
  "target_entities": ["实体英文名"],
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
  },
  "data_source": "all或具体数据源ID"
}

规则：
1) “各/分别/每个/按XX”这类分组语义，必须给出 group_by，并把分组字段放入 output_fields。
2) “最高/最多/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最少/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) 如果是 sum/avg/max/min 且同时询问“各XX分别”，优先按维度字段分组，不要仅返回全局汇总。
5) data_source 除非用户明确指定，否则一律为 all。""",
        },
        "metric_hint_system": {
            "name": "指标规则补充提示词",
            "description": "命中指标SQL规则后，补充抽取条件/分组/排序/限制",
            "template": """你是“指标查询补充解析器”。
已确定用户要查询一个预定义指标，不要改指标本身，只抽取“筛选条件+分组/排序/限制”。
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
2) “最高/最多/最大” => order_by=__metric__, order_dir=desc, limit=1。
3) “最低/最少/最小” => order_by=__metric__, order_dir=asc, limit=1。
4) “哪个区划/各区划/按区划”优先用 AREACODE 做 group_by，并放入 output_fields。
5) entity 统一填 $primary_entity。""",
        },
        "answer_system": {
            "name": "答案生成系统提示词",
            "description": "步骤11：将结构化结果转自然语言",
            "template": """你是一个数据分析助手。请根据用户原始问题和查询结果，用简洁、友好的自然语言回答。
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

请以 JSON 格式返回属性到字段的映射，格式为:
{"属性名": "字段名", ...}
只返回有把握的映射。""",
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
            items.append({
                "key": key,
                "name": str(override_item.get("name", default_item.get("name", key))),
                "description": str(override_item.get("description", default_item.get("description", ""))),
                "template": str(override_item.get("template", default_item.get("template", ""))),
                "has_override": key in overrides,
            })
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
