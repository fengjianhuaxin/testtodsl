# 智能问数系统完整流程图（最新）

本文按当前代码实现整理，重点更新了三处：
- Step00：问题拆解已改为 `LLM任务规划 + 规则校验兜底`。
- JOIN：已支持 `INNER JOIN` 与 `LEFT JOIN`。
- LEFT JOIN 细节：右表条件会下沉到 `ON`，避免被 `WHERE` 退化成 INNER。

## 1. 主流程总览

```mermaid
flowchart TD
    A[输入问题] --> B[全局同义词替换]
    B --> C[步骤00 问题拆解]
    C --> D{是否多任务}

    D -- 否 --> E[单任务执行步骤01到12]
    D -- 是 --> F[逐个子任务执行步骤01到12]
    F --> G[合并子任务结果]

    E --> H[保存中间产物]
    G --> H

    H --> I{是否有DSL结果}
    I -- 是 --> J[保存dsl_query.json]
    I -- 否 --> K[跳过DSL落盘]

    J --> L[保存result.json]
    K --> L
    L --> M[保存llm_traces.json]
    M --> N[输出最终答案]
```

## 2. 步骤00 问题拆解（任务规划）

```mermaid
flowchart TD
    A0[输入原问题] --> A1{LLM可用}

    A1 -- 是 --> B1[加载question_split_system和question_split_user提示词]
    B1 --> B2[调用LLM返回JSON计划]
    B2 --> B3[标准化execution_mode和tasks]
    B3 --> B4{计划是否合法}
    B4 -- 否 --> C1[回退规则拆解]
    B4 -- 是 --> B5{LLM判单任务但命中same_period_compare规则}
    B5 -- 是 --> B6[规则覆盖为multi_sql]
    B5 -- 否 --> B7[采用LLM结果]

    A1 -- 否 --> C1

    C1 --> C2[规则拆解 multi_question/comma/connector/same_period]
    B6 --> D1[输出split_analysis]
    B7 --> D1
    C2 --> D1
```

## 3. 步骤01 意图澄清（结构化意图）

```mermaid
flowchart TD
    E0[输入问题] --> E1[构造本体描述和领域知识上下文]
    E1 --> E2{命中指标预置SQL规则}

    E2 -- 是 --> E3[构造custom_sql意图并补充metric hints]
    E2 -- 否 --> E4[LLM输出意图JSON]

    E3 --> E5[统一归一化 normalize_intent]
    E4 --> E5

    E5 --> E6[校验calc_type和op白名单]
    E6 --> E7[规范calc_params group_by order_by limit]
    E7 --> E8[必要时推断group_by并补入output_fields]
    E8 --> E9[data_source合法性校验]
    E9 --> E10[输出clarified_intent]
```

## 4. 步骤04 查询规划（实体图与JOIN策略）

```mermaid
flowchart TD
    F0[读取target_entities] --> F1[构建entities和relations子图]
    F1 --> F2{实体数是否大于1}

    F2 -- 否 --> F6[仅单表查询]
    F2 -- 是 --> F3[按相邻实体生成joins列表]
    F3 --> F4{问句命中缺失关联语义}
    F4 -- 是 --> F5[设置join_type等于left]
    F4 -- 否 --> F7[设置join_type等于inner]

    F5 --> F8[输出query_plan含joins]
    F7 --> F8
    F6 --> F8
```

说明：缺失关联语义关键词包括“未关联、没有关联、无关联、未匹配、不存在、为空、缺失、未配置”等。

## 5. 步骤05 与 05B（条件筛选和值归一）

```mermaid
flowchart TD
    G0[clarified_intent.conditions] --> G1[步骤05 条件筛选]
    G1 --> G2[操作符归一化和in值标准化]
    G2 --> G3[字段别名解析与字段纠错]
    G3 --> G4[本体value_aliases归一]
    G4 --> G5[区划contains收敛为等号]
    G5 --> H0[步骤05B 值归一]

    H0 --> H1[读取数据层字段值语义]
    H1 --> H2{命中aliases}
    H2 -- 是 --> H3[strict_dict命中]
    H2 -- 否 --> H4{是否存在closed_set}

    H4 -- 是 --> H5[闭集选择并置信度校验]
    H4 -- 否 --> H6{是否严格字段如区划类}
    H6 -- 是 --> H7[保留原值]
    H6 -- 否 --> H8[自由归一LLM兜底]

    H3 --> H9[回写conditions和value_resolution日志]
    H5 --> H9
    H7 --> H9
    H8 --> H9
```

## 6. 步骤08 SQL生成（INNER和LEFT）

```mermaid
flowchart TD
    I0[输入query_plan conditions fields calc_rule] --> I1[按实体生成表别名]
    I1 --> I2[读取joins中的join_type]
    I2 --> I3{join_type是否为left}

    I3 -- 否 --> I4[生成INNER JOIN ON主键关系]
    I3 -- 是 --> I5[生成LEFT JOIN ON主键关系]
    I5 --> I6[将右表条件下沉到ON]

    I4 --> I7[组装SELECT模板 count detail group rate topn]
    I6 --> I7
    I7 --> I8[剩余条件写入WHERE]
    I8 --> I9[追加GROUP BY ORDER BY LIMIT]
    I9 --> I10[输出每个数据源SQL]
```

关键点：
- `LEFT JOIN` 下，右表条件下沉到 `ON`。
- 未下沉的条件才放 `WHERE`。
- 这样可以避免 `LEFT JOIN` 被错误退化为 `INNER JOIN`。

## 7. 步骤09 执行计算（SQL优先）

```mermaid
flowchart TD
    J0[遍历数据源] --> J1{是否有dsl_query.sql且store支持execute_sql}
    J1 -- 是 --> J2[直接执行SQL]
    J1 -- 否 --> J3[DataFrame回退执行]

    J3 --> J4{单实体还是多实体}
    J4 -- 单实体 --> J5[simple_query]
    J4 -- 多实体 --> J6[join_query]

    J6 --> J7[按query_plan.joins使用inner或left merge]
    J5 --> J8[聚合或明细处理]
    J7 --> J8
    J2 --> J8

    J8 --> J9[多数据源结果合并]
    J9 --> J10[输出compute_result]
```

## 8. 步骤10到12（质检 回答 图表）

```mermaid
flowchart TD
    K0[质量检查] --> K1{结果为空或字段缺失}
    K1 -- 是 --> K2[记录issues]
    K1 -- 否 --> K3[质量通过]

    K2 --> L0[答案生成]
    K3 --> L0

    L0 --> L1{LLM回答是否成功}
    L1 -- 是 --> L2[自然语言回答]
    L1 -- 否 --> L3[兜底表格回答]

    L2 --> M0[图表生成]
    L3 --> M0
    M0 --> M1{是否满足图表条件}
    M1 -- 是 --> M2[输出chart.png]
    M1 -- 否 --> M3[跳过图表]
```

## 9. 关键过滤点与兜底点（更新后）

### 关键过滤点
- Step00：LLM规划结果会做结构合法性校验，不合法回退规则拆解。
- Step05：字段不存在先纠错，纠错失败直接丢弃该条件。
- Step05B：闭集选择需满足置信阈值，否则回退原值。
- Step08：`LEFT JOIN` 右表条件优先下沉 `ON`，防止语义误伤。

### 关键兜底点
- Step00：LLM不可用或异常时，回退规则拆解。
- Step01：LLM异常时返回最小结构继续流程。
- Step08：关联键无法解析时退化为弱关联路径并记录日志。
- Step09：单数据源失败不拖垮整体，返回空结果并继续后续步骤。

---

如果你需要，我可以再补一版“按文件/函数级别”的流程图（每个节点标注到具体函数名）。
