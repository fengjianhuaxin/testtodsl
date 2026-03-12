"""Run a 33-question accuracy suite and export question + clarified intent + SQL to Excel."""
import json
import os
import sys
from copy import deepcopy
from typing import Any

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from pipeline.orchestrator import Orchestrator


QUESTIONS = [
    # 单实体基础查询
    "户籍登记信息总人数是多少",
    "户籍登记信息中男性有多少人",
    "户籍登记信息中女性有多少人",
    "出生信息总记录数是多少",
    "殡葬信息总记录数是多少",
    "婚姻关系中已婚人数有多少",
    "婚姻历史记录总数是多少",

    # 聚合统计查询
    "户籍登记信息按民族统计人数",
    "户籍登记信息按户籍性质统计人数",
    "出生信息中各性别人数分别是多少",
    "出生信息中各民族人数分别是多少",
    "殡葬信息中各殡葬类型人数分别是多少",
    "婚姻关系中各婚姻状态人数分别是多少",
    "婚姻历史记录按登记机关统计人数",

    # TopN 排序查询
    "户籍登记信息中人数最多的前3个民族是哪些",
    "户籍登记信息中人数最多的前3个户籍性质是哪些",
    "出生信息中人数最多的前2个出生地省市县是哪些",
    "婚姻历史记录中离婚原因前3名是什么",
    "婚姻历史记录按登记机关统计前5名",

    # 时间与条件过滤
    "2020年之后的殡葬记录有多少",
    "2020年之后出生的人数有多少",
    "婚姻历史记录中2020年之后状态变更的人数有多少",

    # 跨实体关联查询
    "户籍登记信息与出生信息关联后按性别统计人数",
    "有户籍登记信息但没有婚姻关系记录的人数是多少",
    "有出生信息但没有殡葬信息记录的人数是多少",
    "户籍登记信息中有婚姻历史记录的人数是多少",

    # 单人关系穿透（含自连接场景）
    "王芳的出生日期是什么",
    "王芳的母亲什么时候出生的",

    # 问题拆分测试
    "户籍登记信息总人数是多少，人数最多的民族是哪个",
    "王芳的母亲什么时候出生的？王芳的父亲证件号码是什么？",

    # 新增问题
    "王芳的母亲户籍在哪边的，和谁结过婚",
    "王芳的母亲出生日期是什么时候，是什么时候去世的",
    "王芳的母亲户籍在哪边的",
]


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _force_utf8_console():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="ignore")
            except Exception:
                pass


def _to_pretty_json(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)


def _extract_clarified_intent(intermediates: dict) -> dict:
    step = intermediates.get("step_01_意图澄清", {})
    if not isinstance(step, dict):
        step = {}
    clarified = step.get("clarified_intent", {})
    if isinstance(clarified, dict) and clarified:
        return clarified

    multi_task = intermediates.get("multi_task", {})
    if not isinstance(multi_task, dict):
        return {}

    tasks = multi_task.get("tasks", [])
    if not isinstance(tasks, list):
        return {}

    extracted_tasks = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        steps = task.get("steps", {})
        if not isinstance(steps, dict):
            continue
        task_step = steps.get("step_01_意图澄清", {})
        if not isinstance(task_step, dict):
            continue
        task_intent = task_step.get("clarified_intent", {})
        if not isinstance(task_intent, dict) or not task_intent:
            continue
        extracted_tasks.append(
            {
                "task_id": task.get("task_id", ""),
                "question": task.get("question", ""),
                "clarified_intent": task_intent,
            }
        )

    if extracted_tasks:
        return {"split_mode": "multi_task", "tasks": extracted_tasks}
    return {}


def _extract_sql_map(dsl_payload: dict) -> dict:
    if not isinstance(dsl_payload, dict):
        return {}

    sql_map = {}
    for key, value in dsl_payload.items():
        if isinstance(value, dict) and "sql" in value:
            sql_map[key] = str(value.get("sql", ""))
            continue

        if isinstance(value, dict):
            inner = {}
            for sk, sv in value.items():
                if isinstance(sv, dict) and "sql" in sv:
                    inner[sk] = str(sv.get("sql", ""))
            if inner:
                sql_map[key] = inner
    return sql_map


def _load_ontology_maps() -> tuple[dict, dict, dict, dict]:
    ontology_path = os.path.join(ROOT_DIR, "ontology", "student_mgmt_ontology.json")
    ontology = _load_json(ontology_path)
    entities = ontology.get("entities", {}) if isinstance(ontology, dict) else {}

    entity_key_to_label = {}
    entity_label_to_key = {}
    prop_key_to_label = {}
    prop_label_to_key = {}

    for entity_key, entity_def in entities.items():
        key = str(entity_key).strip().upper()
        label = str((entity_def or {}).get("label", "")).strip() or key
        entity_key_to_label[key] = label
        entity_label_to_key[label] = key

        props = (entity_def or {}).get("properties", {})
        key_map = {}
        label_map = {}
        if isinstance(props, dict):
            for prop_key, prop_def in props.items():
                p_key = str(prop_key).strip()
                p_label = str((prop_def or {}).get("label", "")).strip() or p_key
                key_map[p_key.upper()] = p_label
                label_map[p_label] = p_key
        prop_key_to_label[key] = key_map
        prop_label_to_key[key] = label_map

    return entity_key_to_label, entity_label_to_key, prop_key_to_label, prop_label_to_key


def _resolve_entity_key(entity_name: str, entity_key_to_label: dict, entity_label_to_key: dict) -> str:
    raw = str(entity_name or "").strip()
    if not raw:
        return ""
    upper = raw.upper()
    if upper in entity_key_to_label:
        return upper
    if raw in entity_label_to_key:
        return entity_label_to_key[raw]
    return ""


def _entity_to_label(entity_name: str, entity_key_to_label: dict, entity_label_to_key: dict) -> str:
    key = _resolve_entity_key(entity_name, entity_key_to_label, entity_label_to_key)
    if key:
        return entity_key_to_label.get(key, entity_name)
    return str(entity_name or "")


def _field_to_label(field_name: str, entity_key: str, prop_key_to_label: dict) -> str:
    text = str(field_name or "").strip()
    if not text:
        return ""
    if not entity_key:
        return text

    key_map = prop_key_to_label.get(entity_key, {})
    if not isinstance(key_map, dict):
        return text

    mapped = key_map.get(text.upper())
    if mapped:
        return mapped

    values = set(key_map.values())
    if text in values:
        return text
    return text


def _field_ref_to_label(
    field_ref: str,
    default_entity_key: str,
    entity_key_to_label: dict,
    entity_label_to_key: dict,
    prop_key_to_label: dict,
) -> str:
    text = str(field_ref or "").strip()
    if not text or text in {"__metric__", "metric"}:
        return text

    if "." in text:
        entity_part, field_part = [part.strip() for part in text.rsplit(".", 1)]
        entity_key = _resolve_entity_key(entity_part, entity_key_to_label, entity_label_to_key)
        entity_label = _entity_to_label(entity_part, entity_key_to_label, entity_label_to_key)
        field_label = _field_to_label(field_part, entity_key, prop_key_to_label)
        return f"{entity_label}.{field_label}" if entity_label and field_label else text

    return _field_to_label(text, default_entity_key, prop_key_to_label)


def _clarified_intent_to_chinese(
    intent: dict,
    entity_key_to_label: dict,
    entity_label_to_key: dict,
    prop_key_to_label: dict,
) -> dict:
    if not isinstance(intent, dict):
        return {}

    # Multi-task output: convert each sub-task clarified intent recursively.
    if isinstance(intent.get("tasks"), list):
        result = deepcopy(intent)
        converted_tasks = []
        for task in result.get("tasks", []):
            if not isinstance(task, dict):
                continue
            converted = deepcopy(task)
            converted["clarified_intent"] = _clarified_intent_to_chinese(
                converted.get("clarified_intent", {}),
                entity_key_to_label,
                entity_label_to_key,
                prop_key_to_label,
            )
            converted_tasks.append(converted)
        result["tasks"] = converted_tasks
        return result

    result = deepcopy(intent)

    target_entities = result.get("target_entities", [])
    if isinstance(target_entities, list):
        result["target_entities"] = [
            _entity_to_label(item, entity_key_to_label, entity_label_to_key)
            for item in target_entities
        ]

    primary_entity = result.get("primary_entity", "")
    result["primary_entity"] = _entity_to_label(primary_entity, entity_key_to_label, entity_label_to_key)
    primary_entity_key = _resolve_entity_key(primary_entity, entity_key_to_label, entity_label_to_key)

    instances = result.get("entity_instances", [])
    instance_entity_key = {}
    if isinstance(instances, list):
        for item in instances:
            if not isinstance(item, dict):
                continue
            entity_raw = item.get("entity", "")
            key = _resolve_entity_key(entity_raw, entity_key_to_label, entity_label_to_key)
            item["entity"] = _entity_to_label(entity_raw, entity_key_to_label, entity_label_to_key)
            instance_id = str(item.get("id", "")).strip()
            if instance_id and key:
                instance_entity_key[instance_id] = key

    conditions = result.get("conditions", [])
    if isinstance(conditions, list):
        for item in conditions:
            if not isinstance(item, dict):
                continue
            entity_raw = item.get("entity", "")
            entity_key = _resolve_entity_key(entity_raw, entity_key_to_label, entity_label_to_key) or primary_entity_key
            item["entity"] = _entity_to_label(entity_raw, entity_key_to_label, entity_label_to_key)
            item["field"] = _field_to_label(item.get("field", ""), entity_key, prop_key_to_label)
            if "raw_field" in item:
                item["raw_field"] = _field_to_label(item.get("raw_field", ""), entity_key, prop_key_to_label)

    output_fields = result.get("output_fields", [])
    if isinstance(output_fields, list):
        for item in output_fields:
            if not isinstance(item, dict):
                continue
            entity_raw = item.get("entity", "")
            entity_key = _resolve_entity_key(entity_raw, entity_key_to_label, entity_label_to_key) or primary_entity_key
            item["entity"] = _entity_to_label(entity_raw, entity_key_to_label, entity_label_to_key)
            item["field"] = _field_to_label(item.get("field", ""), entity_key, prop_key_to_label)

    relations = result.get("relations", [])
    if isinstance(relations, list):
        for item in relations:
            if not isinstance(item, dict):
                continue
            left_key = instance_entity_key.get(str(item.get("left_instance", "")).strip(), primary_entity_key)
            right_key = instance_entity_key.get(str(item.get("right_instance", "")).strip(), primary_entity_key)
            item["left_field"] = _field_to_label(item.get("left_field", ""), left_key, prop_key_to_label)
            item["right_field"] = _field_to_label(item.get("right_field", ""), right_key, prop_key_to_label)

    calc_params = result.get("calc_params", {})
    if isinstance(calc_params, dict):
        group_by = calc_params.get("group_by", [])
        if isinstance(group_by, list):
            calc_params["group_by"] = [
                _field_ref_to_label(item, primary_entity_key, entity_key_to_label, entity_label_to_key, prop_key_to_label)
                for item in group_by
            ]
        elif isinstance(group_by, str):
            calc_params["group_by"] = _field_ref_to_label(
                group_by,
                primary_entity_key,
                entity_key_to_label,
                entity_label_to_key,
                prop_key_to_label,
            )

        order_by = calc_params.get("order_by", "")
        if isinstance(order_by, str):
            calc_params["order_by"] = _field_ref_to_label(
                order_by,
                primary_entity_key,
                entity_key_to_label,
                entity_label_to_key,
                prop_key_to_label,
            )

    return result


def run_suite(output_excel_path: str):
    _force_utf8_console()
    entity_key_to_label, entity_label_to_key, prop_key_to_label, _ = _load_ontology_maps()

    rows = []
    for index, question in enumerate(QUESTIONS, start=1):
        print(f"[{index:02d}/{len(QUESTIONS)}] {question}")
        run_id = ""
        error_msg = ""
        clarified_intent = {}
        clarified_intent_zh = {}
        sql_map = {}
        answer = ""
        result_count = 0

        orch = None
        try:
            orch = Orchestrator()
            run_id = orch.run_id
            result = orch.run(question)
            answer = str(result.get("answer", "")).strip()
            result_count = len(result.get("compute_result", []) or [])

            intermediates_path = os.path.join(orch.output_dir, "intermediates.json")
            dsl_path = os.path.join(orch.output_dir, "dsl_query.json")

            intermediates = _load_json(intermediates_path)
            dsl_payload = _load_json(dsl_path)
            clarified_intent = _extract_clarified_intent(intermediates)
            clarified_intent_zh = _clarified_intent_to_chinese(
                clarified_intent,
                entity_key_to_label,
                entity_label_to_key,
                prop_key_to_label,
            )
            sql_map = _extract_sql_map(dsl_payload)
        except Exception as exc:
            error_msg = str(exc)
            if orch and getattr(orch, "output_dir", None):
                intermediates = _load_json(os.path.join(orch.output_dir, "intermediates.json"))
                dsl_payload = _load_json(os.path.join(orch.output_dir, "dsl_query.json"))
                clarified_intent = _extract_clarified_intent(intermediates)
                clarified_intent_zh = _clarified_intent_to_chinese(
                    clarified_intent,
                    entity_key_to_label,
                    entity_label_to_key,
                    prop_key_to_label,
                )
                sql_map = _extract_sql_map(dsl_payload)

        rows.append(
            {
                "case_id": f"Q{index:02d}",
                "question": question,
                "run_id": run_id,
                "dsl_clarified_intent_json": _to_pretty_json(clarified_intent_zh),
                "sql_json": _to_pretty_json(sql_map),
                "answer": answer,
                "result_count": result_count,
                "error": error_msg,
            }
        )

    dataframe = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(output_excel_path), exist_ok=True)
    dataframe.to_excel(output_excel_path, index=False)
    print(f"\nSaved: {output_excel_path}")


if __name__ == "__main__":
    out_file = os.path.join("test", "question_suite_accuracy_33_with_dsl_sql.xlsx")
    run_suite(out_file)
