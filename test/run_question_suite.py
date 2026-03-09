"""Run a question suite and export question + clarified intent + SQL to Excel."""
import json
import os
import sys
from typing import Any

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from pipeline.orchestrator import Orchestrator


QUESTIONS = [
    "户籍人口总数是多少",
    "户籍人口按民族分别有多少人",
    "户籍人口数排前3个民族是哪些",
    "男性户籍人口有多少",
    "女性户籍人口有多少",
    "已注销户籍人口有多少",
    "各宗教信仰的户籍人口数量",
    "王芳的母亲是什么时候出生的",
    "王芳的父亲身份证号是多少",
    "出生信息中不同性别人数统计",
    "2020年之后的丧葬记录有多少",
    "丧葬类型前2名分别是什么",
    "婚姻关系中已婚人数有多少",
    "婚姻关系中各婚姻状态人数分别是多少",
    "婚姻历史记录中离婚原因最多的前3个原因",
    "有出生信息但没有婚姻关系记录的人数是多少",
    "户籍信息与出生信息关联后按性别统计人数",
    "王芳的配偶姓名是什么",
    "婚姻历史记录按登记机关统计前5名",
    "户籍人口中不同户籍类型人数前5名",
]


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _force_utf8_console():
    # Avoid Windows GBK encoding crashes when logs/answers contain emoji.
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
        return {}
    clarified = step.get("clarified_intent", {})
    return clarified if isinstance(clarified, dict) else {}


def _extract_sql_map(dsl_payload: dict) -> dict:
    if not isinstance(dsl_payload, dict):
        return {}

    sql_map = {}
    for key, value in dsl_payload.items():
        if isinstance(value, dict) and "sql" in value:
            sql_map[key] = str(value.get("sql", ""))
            continue

        # multi-task style: {task_1: {xksx: {sql: ...}}}
        if isinstance(value, dict):
            inner = {}
            for sk, sv in value.items():
                if isinstance(sv, dict) and "sql" in sv:
                    inner[sk] = str(sv.get("sql", ""))
            if inner:
                sql_map[key] = inner
    return sql_map


def run_suite(output_excel_path: str):
    _force_utf8_console()
    rows = []
    for index, question in enumerate(QUESTIONS, start=1):
        print(f"[{index:02d}/{len(QUESTIONS)}] {question}")
        run_id = ""
        error_msg = ""
        clarified_intent = {}
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
            sql_map = _extract_sql_map(dsl_payload)
        except Exception as exc:
            error_msg = str(exc)
            if orch and getattr(orch, "output_dir", None):
                intermediates = _load_json(os.path.join(orch.output_dir, "intermediates.json"))
                dsl_payload = _load_json(os.path.join(orch.output_dir, "dsl_query.json"))
                clarified_intent = _extract_clarified_intent(intermediates)
                sql_map = _extract_sql_map(dsl_payload)

        rows.append(
            {
                "case_id": f"Q{index:02d}",
                "question": question,
                "run_id": run_id,
                "dsl_clarified_intent_json": _to_pretty_json(clarified_intent),
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
    out_file = os.path.join("test", "question_suite_with_dsl_sql.xlsx")
    run_suite(out_file)
