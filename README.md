# 🎓 本体驱动智能问数系统（Demo1）

基于“本体 + 多智能体流水线 + DSL(SQL/SPARQL)”的自然语言问数系统。  
用户提问后，系统会自动完成：

1. 语义理解（实体、属性、关系、条件、计算方式）
2. 本体/映射对齐
3. DSL 生成（SQL 为主）
4. 数据查询与计算
5. 结果质检、自然语言回答、图表输出

---

## 当前版本说明（重要）

当前仓库默认业务域是 `xksx`（项目数据版）：

- 本体文件：`ontology/student_mgmt_ontology.json`
- 映射文件：`mapping/xksx_mapping.json`
- 数据目录：`data/xksx/*.xlsx`

说明：仓库中仍保留了早期 `university_a/university_b` 示例代码与文档痕迹，但主流程已切换到 `xksx`。

---

## 快速启动

### 1) 安装依赖

```bash
pip install -r requirements.txt
```

### 2) 启动 Web 服务

```bash
python web/app.py
```

访问：

- 用户端：`http://localhost:5000`
- 管理端：`http://localhost:5000/admin`
- 登录页：`http://localhost:5000/login`

默认测试账号见 `data/users.json`（如 `admin/admin123`）。

### 3) CLI 单次查询

```bash
python main.py query "2023年度江苏省内各地区的系统运维费用总和分别是多少？" --source xksx
```

### 4) CLI 交互模式

```bash
python main.py interactive
```

---

## 命令参考（CLI）

| 命令 | 说明 |
|------|------|
| `query "问题" --source xksx` | 单次查询 |
| `interactive` | 交互式查询 |
| `ontology show` | 打印本体定义 |
| `ontology search 关键词` | 搜索本体实体/属性 |
| `mapping show --source xksx` | 打印映射配置 |

---

## 详细步骤分析（问题 -> SQL -> 结果）

下面是一次查询在 `pipeline/orchestrator.py` 内的完整执行链路（字段级）。

### Step 00A：全局同义词归一化

- 输入：`question`
- 处理：命中 `knowledge/domain_knowledge.json` 中 `rewrite_rules`
- 输出：
  - `question`（归一化后）
  - `global_rewrite_hits`（命中详情）

### Step 00B：问题拆解（多子任务）

- 组件：`agents/question_split_agent.py`
- 输入：`question`
- 输出：`split_analysis`
  - `enabled`：是否拆解
  - `sub_tasks`：子问题数组
  - `reason/confidence`：触发原因与置信度

若 `enabled=true` 且子问题>1，每个子问题都会单独执行 Step01~Step12，最后聚合答案。

### Step 01：意图澄清（最关键）

- 组件：`agents/intent_clarify_agent.py`
- 输入：
  - 归一化问题
  - 本体描述（OntologyManager）
  - 术语知识片段（KnowledgeManager）
  - 可用数据源
- 输出：`clarified_intent`

`clarified_intent` 结构示例：

```json
{
  "clarified_question": "...",
  "target_entities": ["INFORMATION_SYSTEM"],
  "conditions": [{"field":"AREACODE","op":"contains","value":"江苏省","entity":"INFORMATION_SYSTEM"}],
  "output_fields": [{"field":"AREACODE","entity":"INFORMATION_SYSTEM","label":"区划"}],
  "calc_type": "sum",
  "calc_params": {"group_by":["AREACODE"],"order_by":"__metric__","order_dir":"desc","limit":10},
  "data_source": "xksx"
}
```

特殊分支：若命中 `sql_metric_rules`，会直接生成 `calc_type=custom_sql` 并携带 `custom_sql`。

### Step 02：知识验证

- 组件：`agents/knowledge_verify_agent.py`
- 输入：`clarified_intent`
- 输出：`knowledge_verify`
  - `passed`
  - `verified_entities`
  - `issues`

### Step 03：调度

- 组件：`agents/dispatch_agent.py`
- 输入：`clarified_intent`, `knowledge_verify`, `preferred_source`
- 输出：`dispatch`
  - `data_sources`
  - `needs_join`
  - `needs_calc`
  - `pipeline`

### Step 04：查询规划A（对象子图）

- 组件：`agents/query_plan_agent.py`
- 输入：`target_entities`
- 输出：`query_plan`
  - `entities`
  - `relations`
  - `paths`

### Step 05：条件筛选B

- 组件：`agents/condition_filter_agent.py`
- 输入：`clarified_intent.conditions`
- 处理：
  - 操作符归一化（如 `equals -> =`）
  - 属性名纠错/别名归一化
  - 属性值别名归一化（`value_aliases`）
- 输出：`conditions`（结构化条件）

### Step 06：字段提取B

- 组件：`agents/field_extract_agent.py`
- 输入：`clarified_intent.output_fields`
- 输出：`extracted_fields`（带字段类型）

### Step 07：计算方法C

- 组件：`agents/calc_method_agent.py`
- 输入：`clarified_intent`, `conditions`, `extracted_fields`, `raw_question`
- 输出：
  - `calc_rule`（最终计算规则）
  - `query_spec`（规范化查询规格）

`query_spec` 是后续 SQL 与执行层对齐的关键中间结构：

```json
{
  "query_mode": "detail|aggregate|custom_sql",
  "entities": ["INFORMATION_SYSTEM"],
  "filters": [],
  "dimensions": ["AREACODE"],
  "measures": [{"agg":"sum","field":"SYSTEM_MAINTENANCE_COST","alias":"sum_value"}],
  "sort": [{"by":"__metric__","dir":"desc"}],
  "limit": 10
}
```

### Step 08：DSL 查询生成

- 组件：`agents/dsl_query_agent.py`
- 输入：`query_plan`, `conditions`, `extracted_fields`, `calc_rule`, `query_spec`, `dispatch`
- 输出：`dsl_query`

结构：

```json
{
  "xksx": {
    "sparql": "...",
    "sql": "SELECT ... FROM ... WHERE ... GROUP BY ... ORDER BY ... LIMIT ..."
  }
}
```

关键逻辑：

1. 先按映射把本体实体/字段转换为物理表/字段
2. JOIN 优先使用关系配置的 `from_field/to_field`
3. 按 `calc_rule/query_spec` 生成 `SELECT/GROUP BY/ORDER BY/LIMIT`
4. `custom_sql` 分支会在预定义 SQL 基础上拼接动态条件/分组/排序

### Step 09：计算执行

- 组件：`agents/compute_agent.py`
- 输入：`dsl_query`, `query_plan`, `conditions`, `calc_rule`, `query_spec`, `dispatch`
- 执行策略：
  - 如果 `store` 支持 `execute_sql`：直接执行 SQL（MySQL）
  - 否则：走 DataFrame 查询/聚合（Sheet）
- 输出：
  - `compute_result`（list[dict]）
  - `compute_result_df`（DataFrame）

### Step 10：质检验证

- 组件：`agents/quality_check_agent.py`
- 输入：`compute_result`, `calc_rule`, `query_spec`, `extracted_fields`
- 输出：`quality_check`
  - `passed`
  - `issues`
  - `record_count`

### Step 11：答案回复

- 组件：`agents/answer_agent.py`
- 输入：`raw_question`, `compute_result`, `calc_rule`, `quality_check`
- 输出：`answer`（自然语言结果）

### Step 12：图表生成

- 组件：`agents/chart_agent.py`
- 输入：`compute_result`, `calc_rule`, `raw_question`
- 输出：`chart_path`（统计类查询生成柱状图）

---

## 每次运行的输出文件

在 `output/<run_id>/` 下：

- `intermediates.json`：每步快照（排查首选）
- `dsl_query.json`：最终 DSL（含 SQL）
- `result.json`：答案、结果集、图表路径
- `llm_traces.json`：模型请求/响应轨迹
- `chart.png`：图表（若生成）

---

## 目录结构（核心）

```text
agents/          # 12个智能体实现
pipeline/        # Orchestrator 编排
ontology/        # 本体定义与管理器
mapping/         # 映射定义与管理器
knowledge/       # 术语知识/同义词/指标SQL规则
store/           # 数据存储抽象 + sheet/mysql 实现
llm/             # 大模型客户端
web/             # Flask 服务 + 前端 + 各管理API
data/            # 运行时配置、日志、用户、xksx数据
output/          # 每次问答产物
```

---

## 配置说明

### LLM 配置

- 默认配置在 `config.py`
- 运行时覆盖在 `data/llm_config.json`
- 可通过管理端“提示词配置”页面维护

### 数据库配置

- 运行时配置在 `data/db_config.json`
- `store_type` 支持：`sheet` / `mysql`
- 可通过管理端“数据库连接”页面测试与保存

---

## 安全与生产建议

1. 不要在仓库中保存真实 API Key/数据库密码，建议改为环境变量注入。
2. `users.json` 当前为明文密码，仅适合 POC；生产环境应改为哈希存储 + 持久会话。
3. `sql_metric_rules` 虽做了 SELECT 白名单限制，仍建议在数据库层做只读账号与最小权限控制。
