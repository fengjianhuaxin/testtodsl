# 本体驱动智能问数系统（Demo1）

本项目是一个“自然语言问数 -> 本体语义解析 -> DSL/SQL生成 -> 查询执行 -> 自然语言回答”的多智能体系统。

用户提问后，系统会依次完成：
- 意图澄清（识别实体、字段、条件、计算类型）
- 本体与数据层映射
- 条件值归一（数据层值语义）
- SQL生成与执行
- 结果质检与回答生成

---

## 1. 快速启动

### 1.1 安装依赖

```bash
pip install -r requirements.txt
```

### 1.2 启动Web

```bash
python web/app.py
```

访问地址：
- 用户端：`http://localhost:5000`
- 管理端：`http://localhost:5000/admin`
- 登录页：`http://localhost:5000/login`

### 1.3 命令行查询

```bash
python main.py query "江苏省有哪些关联的系统未上云的项目" --source xksx
```

---

## 2. 流程总览（Step00~Step12）

- `Step00A` 全局同义词归一（`rewrite_rules`）
- `Step00B` 问题拆解（单任务/多任务）
- `Step01` 意图澄清（实体、条件、输出字段、计算类型）
- `Step02` 知识验证
- `Step03` 调度（数据源、是否JOIN、是否计算）
- `Step04` 查询规划（子图、JOIN方向）
- `Step05` 条件筛选
- `Step05B` 值归一（数据层语义：闭集/别名/LLM）
- `Step06` 字段提取
- `Step07` 计算方法（count/sum/rate等）
- `Step08` DSL/SQL生成
- `Step09` 执行查询
- `Step10` 结果质检
- `Step11` 回答生成
- `Step12` 图表生成

中间产物输出在：`output/<run_id>/`

---

## 3. 映射管理怎么配（重点）

管理端入口：`管理端 -> 映射管理 -> 选择数据源 -> 选择实体 -> 编辑`

会看到两个核心JSON：
- `字段映射(JSON)`
- `字段值语义(JSON，数据层)`

底层落盘文件：`mapping/<source_id>_mapping.json`

### 3.1 字段映射(JSON) 作用

把本体字段映射到物理表字段（SQL生成必须依赖它）。

示例：

```json
{
  "PROJECT_CODE": "PROJECT_CODE",
  "PROJECT_NAME": "PROJECT_NAME",
  "PROJECT_STATUS": "PROJECT_STATUS",
  "AREACODE": "AREACODE"
}
```

说明：
- 左边是本体字段（ontology field）
- 右边是数据库/Excel真实字段（actual field）
- 生成SQL时会把左边自动替换成右边

### 3.2 字段值语义(JSON，数据层) 作用

用于Step05B“值归一”，解决“用户说线上，库里存1”这类问题。

支持配置结构如下：

```json
{
  "ISUNCLOUD": {
    "closed_set": ["是", "否"],
    "labels": {
      "是": "未上云",
      "否": "已上云"
    },
    "aliases": {
      "未上云": "是",
      "已上云": "否",
      "不上云": "是"
    },
    "rate_true_values": ["是"],
    "confidence_threshold": 0.75,
    "unknown_policy": "ask"
  },
  "PROJECT_STATUS": {
    "closed_set": ["申报中", "已竣工"],
    "aliases": {
      "正在申报": "申报中"
    },
    "rate_true_values": ["申报中"],
    "confidence_threshold": 0.7,
    "unknown_policy": "fallback"
  },
  "AREACODE": {
    "aliases": {
      "江苏省": "320000",
      "全省": "320000",
      "32": "320000"
    },
    "confidence_threshold": 1,
    "unknown_policy": "ask"
  }
}
```

字段说明：
- `closed_set`：该字段在库里的合法值闭集。
- `labels`：给模型看的解释文本（值 -> 语义）。
- `aliases`：自然语言别名到真实值的映射。
- `rate_true_values`：当计算“率”时，分子认定为“真”的值集合。
- `confidence_threshold`：模型选值最低置信度阈值。
- `unknown_policy`：未知值策略，支持 `fallback` / `ask` / `reject`。

生效规则（关键）：
- 有`aliases`命中：优先直接转换（严格字典）。
- 有`closed_set`：会受约束在闭集里选值。
- 没有闭集：走自由归一（LLM判断，低置信度回退原值）。
- `AREACODE`这类区划字段建议一定配`aliases`，减少模型漂移。

常见坑：
- 别名配在了错误实体下（例如配在`GOVERNMENT_PROJECT`，但条件落在`INFORMATION_SYSTEM`）。
- 配了字段但字段名大小写不一致（系统已做兼容，但建议统一大写）。
- `rate_true_values`没配，导致“率”兜底走`是/否`猜测。

---

## 4. 知识配置三类怎么用（重点）

管理端入口：`管理端 -> 知识配置`

底层文件：`knowledge/domain_knowledge.json`

### 4.1 术语知识（term_knowledge，解释型）

用途：给意图澄清补充上下文解释，不直接执行SQL。

示例：

```json
{
  "id": "term_open_rate",
  "term": "开放率",
  "keywords": ["开放率", "目录开放率"],
  "content": "开放率口径：开放目录数 / 目录总数 * 100%。开放目录通常指无条件开放+有条件开放。"
}
```

什么时候用：
- 业务术语含义复杂、口径不直观时。

### 4.2 全局同义词转换（rewrite_rules，键值替换型）

用途：在Step00A先替换问句中的词，降低后续歧义。

示例：

```json
{
  "id": "rw_js_province",
  "source": "全省",
  "target": "江苏省",
  "description": "统一省域表达",
  "enabled": true
}
```

什么时候用：
- “全省/省内/本省”这类稳定同义词。

注意：
- 这是全局字符串替换，建议只配稳定、不会误伤的词。

### 4.3 指标SQL规则（sql_metric_rules，执行型）

用途：命中后直接进入`custom_sql`路径，优先级最高，可用于固定口径指标。

示例：

```json
{
  "id": "metric_open_catalog_count",
  "name": "开放目录数量",
  "keywords": ["开放目录数量", "开放目录数"],
  "target_entities": ["DATA_CATALOG"],
  "data_source": "xksx",
  "sql": "SELECT COUNT(*) AS `开放目录数量` FROM DATA_CATALOG WHERE OPEN_TYPE IN ('无条件开放','有条件开放')",
  "priority": 190,
  "description": "固定口径指标SQL",
  "enabled": true
}
```

约束：
- 仅允许单条`SELECT`查询。
- 禁止`insert/update/delete/drop/alter/truncate/create/replace`。

什么时候用：
- 指标口径必须严格一致，不能让模型自由发挥。

---

## 5. 推荐配置顺序（实操）

1. 先配好`字段映射(JSON)`，确保能生成正确字段SQL。  
2. 再配`字段值语义(JSON)`，先从高频字段开始：区划、状态、类型、是否类字段。  
3. 对强口径指标补`sql_metric_rules`，保证稳定复现。  
4. 再用`term_knowledge`补解释，用`rewrite_rules`补同义词。  
5. 每次改完跑2~3条回归问句，看Step05B日志和最终SQL是否符合预期。

---

## 6. 排查建议（看哪些日志）

先看`output/<run_id>/intermediates.json`和控制台日志：

- Step01：模型是否识别到正确`target_entities/conditions/output_fields`。
- Step05B：值归一策略和结果。
  - `strategy=strict_dict`：命中别名
  - `strategy=closed_set`：闭集受约束选择
  - `strategy=model_free`：无闭集，模型自由归一
- Step07：率计算的`rate_true_values`来源。
- Step08：最终SQL与JOIN方向。

如果结果不对，优先检查：
- 字段映射是否指向了错误物理字段
- 字段值语义是否配置在了错误实体
- 问句目标粒度是“项目”还是“项目-系统明细”

---

## 7. 关键文件索引

- 主流程：`pipeline/orchestrator.py`
- 意图澄清：`agents/intent_clarify_agent.py`
- 值归一：`agents/value_resolve_agent.py`
- 计算规则：`agents/calc_method_agent.py`
- SQL生成：`agents/dsl_query_agent.py`
- 映射管理API：`web/api/mapping_api.py`
- 知识管理API：`web/api/knowledge.py`
- 提示词模板：`prompts/prompt_manager.py`
- 数据层映射：`mapping/xksx_mapping.json`
- 知识配置：`knowledge/domain_knowledge.json`

---

## 8. Git协作说明

本项目已有独立说明：`GIT_WORKFLOW.md`。  
建议按“功能分支 -> 提交 -> 推送 -> PR/合并”流程进行，避免在主分支直接改动。
