"""Prompt manager - load and persist configurable prompt templates."""
import json
import os
from string import Template


class PromptManager:
    """Manage prompt templates with defaults and runtime overrides."""

    DEFAULT_PROMPTS = {
        "intent_entity_select_system": {
            "name": "意图实体粗选-系统提示词",
            "description": "步骤01-A：仅基于实体与关系做候选实体粗选",
            "template": """你是实体选择器。只做一步：从候选实体中选出与问题最相关的1~4个实体。
输入只有实体与实体关系，不包含属性。

候选实体：
$entity_catalog

实体关系：
$relation_catalog

严格输出JSON，不要解释：
{
  "target_entities": ["实体中文名"],
  "primary_entity": "主实体中文名",
  "confidence": 0.0,
  "reason": "简短原因"
}

规则：
1) target_entities 必须来自候选实体。
2) confidence 范围0~1。
3) 若无法判断，target_entities 输出空数组。""",
        },
        "intent_entity_select_user": {
            "name": "意图实体粗选-用户提示词",
            "description": "步骤01-A：变量：$question",
            "template": "问题：$question",
        },
        "intent_clarify_system": {
            "name": "意图澄清系统提示词",
            "description": "步骤01：意图澄清（本体层 DSL）",
            "template": """你是一个意图澄清智能体，请把用户问题解析成结构化 JSON。
本体上下文：
$ontology_desc

$knowledge_block

数据查询描述（仅作提示增强，不作为真理来源，如与原问题冲突以原问题为准）：
$query_description_block

请严格只返回 JSON（不要输出解释文字），格式如下：
{
  "entity_instances": [
    {"id":"实例ID","entity":"实体名","role":"角色说明，可选"}
  ],
  "relations": [
    {"from_instance":"实例ID","relation":"本体关系名","to_instance":"实例ID"}
  ],
  "conditions": [
    {"instance_id":"实例ID或空","field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}
  ],
  "output_fields": [
    {"instance_id":"实例ID或空","field":"属性名","entity":"实体名","label":"显示名"}
  ],
  "calc_type": "detail|count|sum|avg|rate|max|min|topn",
  "calc_params": {
    "group_by": ["属性名或实体名.属性名"],
    "order_by": "属性名或实体名.属性名或__metric__",
    "order_dir": "asc或desc",
    "limit": 10
  },
  "data_source": "all或数据源ID"
}

规则：
1) 禁止输出 thought/self_check/clarified_question/target_entities/primary_entity。
2) JSON 键名固定英文；实体名/属性名/关系名/说明使用中文。
3) entity_instances.entity、conditions.entity、output_fields.entity 只能来自本体实体；可选实体仅限：$allowed_target_entities。
4) conditions.field、output_fields.field、calc_params.group_by/order_by 只能来自本体属性。
5) conditions/output_fields 的 instance_id 要与 entity_instances.id 对应；单实例不写时可留空字符串。
6) relations 仅表达本体语义关系，不允许 left_field/right_field/join_type 这类数据层连接键。
7) 若主实体不含目标属性，可引入关联实体并通过本体关系补齐；不可解时 output_fields 可为空。
8) “各/分别/每个/分布/按XX”需给出 group_by，并把分组字段放入 output_fields。
9) “最高/最多/最大” => order_by=__metric__, order_dir=desc, limit=1；“最低/最少/最小”相反。
10) 时间条件（date/datetime）按时间范围表达，禁止 contains/like。
11) conditions.value 保留用户原始语义，不提前映射数据库编码。
12) 数据查询描述只作为辅助信息，不得覆盖用户原问题明确表达。
"""
        },
        "intent_clarify_user": {
            "name": "意图澄清用户提示词",
            "description": "步骤01-B：变量：$question",
            "template": "问题：$question",
        },
        "query_desc_extract_system": {
            "name": "数据查询描述抽取-系统提示词",
            "description": "步骤01-前置：抽取基础条件、其他条件、结果信息要素、结果形式",
            "template": """请从以下数据查询描述中提取关键信息，格式为基础条件、其他条件、结果信息要素、结果形式四部分。

- 基础条件：从“基本信息”字段列表中匹配，如果描述中提到则列出，未提到则不列。
- 其他条件：从文本中提取归纳出的额外筛选条件（非基本信息）。
- 结果信息要素：结果包含的信息字段或指标。根据结果形式的不同，提取规则如下：
  - 如果结果形式为明细数据，则要素应为描述中明确要求返回的列名（如“返回姓名和年龄”则要素为“姓名,年龄”）；若未明确，则返回“未明确”。
  - 如果结果形式为统计数据，则要素应为描述中统计值的含义。例如：
    * “统计出生人数” -> 要素为“出生人数”
    * “找出最大年龄” -> 要素为“年龄”
    * “计算平均工资” -> 要素为“工资”
    * 若无法推断，则返回“未明确”。
-结果形式：包括明细数据、统计数据。如果是统计数据，请进一步说明统计类型（计数、去重计数、求和、平均值、中位数、最大值、最小值）。如果描述中指定了分组维度（如“每年”、“按月”、“分地区”等），则在统计类型后注明分组依据，格式为“统计数据：统计类型（分组依据：具体维度）”；若无分组，则仅写统计类型。

基本信息包括：姓名、出生日期、户籍、婚姻状态、性别、职称、身份证号

输出 JSON:
{"基础条件":"","其他条件":"","结果信息要素":"","结果形式":""}
""",
        },
        "query_desc_extract_user": {
            "name": "数据查询描述抽取-用户提示词",
            "description": "步骤01-前置：变量：$question",
            "template": "数据查询描述：$question",
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
            "description": "步骤00：任务规划（single_sql/multi_sql_independent/multi_sql_dependent）",
            "template": """你是任务规划器。请判断用户问题属于以下哪种模式：
1) single_sql：一条 SQL 可直接回答；
2) multi_sql_independent：多个子问题彼此独立；
3) multi_sql_dependent：后续子问题依赖前一步结果（链式推理）。

核心原则：
1) single_sql 优先：能用一条 SQL 回答就不拆分。
2) 依赖推理必须拆分：出现“先求中间对象，再查询该对象属性”的语义时，必须用 multi_sql_dependent。
3) 身份终点问句不拆分：若问题本身是“X的母亲/父亲/配偶是谁(或叫什么/姓名是什么)”，这是终点查询，必须 single_sql。
4) 只有当第二问明确依赖第一问结果（例如“其/她/他/{变量名}”）时，才允许 multi_sql_dependent。

判定示例：
- single_sql: “王芳的母亲是谁”
- multi_sql_dependent: “王芳的母亲出生日期是什么时候”（可拆为“王芳的母亲是谁” + “{mother_name}的出生日期是什么时候”）

严格输出 JSON：
{
  "is_multi_task": true/false,
  "execution_mode": "single_sql" or "multi_sql_independent" or "multi_sql_dependent",
  "tasks": [
    {
      "task_id": "task_1",
      "question": "子问题文本",
      "depends_on": [],
      "bind_output": []
    },
    {
      "task_id": "task_2",
      "question": "{变量名}的出生日期是什么时候",
      "depends_on": ["task_1"],
      "bind_output": [
        {"from_task":"task_1","field":"字段名","var":"变量名"}
      ]
    }
  ],
  "reason": "简短原因",
  "confidence": 0到1
}

约束：
1) tasks 按执行顺序输出；depends_on 只能引用已出现的 task_id。
2) 若 execution_mode=single_sql，tasks 只保留 1 条。
3) 若 execution_mode=multi_sql_dependent，至少 1 个任务必须包含 depends_on 或 bind_output。
4) 如果第二问需要引用上一步结果，question 中使用 {var} 占位符。""",
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
        "relation_instance_split_system": {
            "name": "关系实例化判定-系统提示词",
            "description": "步骤01_07：单实体是否拆分为多实例自连接",
            "template": """你是关系实例化判定器。给定问题和单实体意图，判断是否需要把同一实体拆成多个实例进行自连接。\n仅输出JSON：\n{\"need_instance_split\":true|false,\"instances\":[{\"id\":\"inst_a\",\"role\":\"...\"},{\"id\":\"inst_b\",\"role\":\"...\"}],\"relation\":{\"left_instance\":\"inst_a\",\"left_field\":\"FIELD\",\"right_instance\":\"inst_b\",\"right_field\":\"FIELD\",\"join_type\":\"inner|left\"},\"condition_instance\":\"inst_a\",\"output_instance\":\"inst_b\"}。\n规则：\n1) 只有当问题明确是“通过关联对象再取其属性”时才返回 need_instance_split=true。\n2) relation(left_field,right_field) 必须来自候选对。\n3) 无法确定时返回 need_instance_split=false。""",
        },
        "relation_instance_review_system": {
            "name": "关系实例化复核-系统提示词",
            "description": "步骤01_07：已有多实例意图的自连接复核",
            "template": """你是关系实例化复核器。给定问题、单实体意图（已含多实例）与候选连接对，判断该自连接是否合理。\n仅输出JSON：\n{\"need_instance_split\":true|false,\"instances\":[{\"id\":\"inst_a\",\"role\":\"...\"},{\"id\":\"inst_b\",\"role\":\"...\"}],\"relation\":{\"left_instance\":\"inst_a\",\"left_field\":\"FIELD\",\"right_instance\":\"inst_b\",\"right_field\":\"FIELD\",\"join_type\":\"inner|left\"},\"condition_instance\":\"inst_a\",\"output_instance\":\"inst_b\"}。\n规则：\n1) 如果当前意图不该自连接，返回 need_instance_split=false。\n2) 如果应自连接，relation(left_field,right_field) 必须来自候选连接对。\n3) 可以保留现有实例关系，也可以重写为更合理的实例关系。""",
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
    }

    def __init__(self, prompt_file: str):
        self.prompt_file = prompt_file

    @staticmethod
    def is_user_prompt_key(key: str) -> bool:
        return str(key or "").strip().lower().endswith("_user")

    def is_prompt_configurable(self, key: str) -> bool:
        return not self.is_user_prompt_key(key)

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

    def list_prompts(self, include_user_prompts: bool = False) -> list:
        data = self._load()
        overrides = data.get("prompts", {})
        keys = list(self.DEFAULT_PROMPTS.keys())
        for extra_key in overrides.keys():
            if extra_key not in keys:
                keys.append(extra_key)

        items = []
        for key in keys:
            if not include_user_prompts and self.is_user_prompt_key(key):
                continue
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
        for item in self.list_prompts(include_user_prompts=True):
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
        if self.is_user_prompt_key(key):
            template_text = fallback_template or self.DEFAULT_PROMPTS.get(key, {}).get("template", "")
        else:
            prompt = self.get_prompt(key)
            template_text = prompt.get("template") or fallback_template or ""
        safe_context = {k: str(v) for k, v in (context or {}).items()}
        try:
            return Template(template_text).safe_substitute(**safe_context)
        except Exception:
            return template_text

    def update_prompt(self, key: str, template: str, name: str | None = None, description: str | None = None):
        if not self.is_prompt_configurable(key):
            raise ValueError(f"prompt '{key}' is not configurable")
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
        if not self.is_prompt_configurable(key):
            raise ValueError(f"prompt '{key}' is not configurable")
        data = self._load()
        prompts = data.get("prompts", {})
        if key in prompts:
            del prompts[key]
        data["prompts"] = prompts
        self._save(data)



