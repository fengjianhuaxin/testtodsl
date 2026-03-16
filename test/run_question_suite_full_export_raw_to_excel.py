"""Batch-run the full pipeline for questions in an Excel and export raw outputs to another Excel.

Input (default): project root `智能问数系统测试用例.xlsx`
Output (required fields):
- 实体筛选模型原生返回值（LLM trace response_text for intent_entity_select chat_json）
- DSL生成模型原生返回值（LLM trace response_text for intent_clarify chat_json）
- 生成的 SQL（from `output/<run_id>/dsl_query.json`）
- 最终输出文本（AnswerAgent result: `result['answer']`）

Both the script and the output Excel should live under `test/`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from pipeline.orchestrator import Orchestrator


def _normalize_key(text: str) -> str:
    return "".join(str(text or "").strip().lower().split())


def _find_question_column(columns: list[str]) -> str:
    if not columns:
        raise ValueError("Input sheet has no columns.")

    preferred = ("问题", "question", "query", "ask")
    for col in columns:
        key = _normalize_key(col)
        if any(token in key for token in preferred):
            return col

    return columns[1] if len(columns) >= 2 else columns[0]


def _iter_chat_json_traces(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in traces or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("call_type", "")).strip() != "chat_json":
            continue
        result.append(item)
    return result


def _pick_entity_select_raw(traces: list[dict[str, Any]]) -> list[str]:
    """Pick raw model outputs for entity coarse selection.

    Prefer using response schema ("target_entities") because prompt templates may vary
    between `PromptManager.DEFAULT_PROMPTS` and runtime overrides in `data/prompt_templates.json`.
    """
    matched: list[str] = []
    for item in traces:
        response_text = str(item.get("response_text", "") or "")
        if "target_entities" in response_text:
            matched.append(response_text)
    if matched:
        return matched

    # Fallback to prompt heuristics.
    for item in traces:
        original_system = str(item.get("original_system_prompt", "") or "")
        if "target_entities" in original_system:
            response_text = str(item.get("response_text", "") or "")
            if response_text.strip():
                return [response_text]
    return []


def _pick_intent_dsl_raw(traces: list[dict[str, Any]]) -> list[str]:
    """Pick raw model outputs for intent->DSL generation."""
    matched: list[str] = []
    for item in traces:
        response_text = str(item.get("response_text", "") or "")
        if "entity_instances" in response_text or "calc_params" in response_text:
            matched.append(response_text)
    if matched:
        return matched

    for item in traces:
        original_system = str(item.get("original_system_prompt", "") or "")
        if "NL2DSL" in original_system or "意图澄清" in original_system:
            response_text = str(item.get("response_text", "") or "")
            if response_text.strip():
                return [response_text]
    return []


def _format_raw_responses(responses: list[str]) -> str:
    cleaned = [str(x) for x in responses if str(x).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return json.dumps(cleaned, ensure_ascii=False, indent=2)


def _read_json_if_exists(path: str) -> Any:
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _extract_sql_view(dsl_query: Any) -> str:
    """Return a compact SQL view from dsl_query.json.

    - single task: {source_id: {sql: "..."}}
    - multi task:  {task_id: {source_id: {sql: "..."}}}
    """
    if not isinstance(dsl_query, dict) or not dsl_query:
        return ""

    def _extract_sql_map(layer: dict) -> dict[str, str]:
        sql_map: dict[str, str] = {}
        for key, payload in layer.items():
            if not isinstance(payload, dict):
                continue
            sql_text = payload.get("sql")
            if isinstance(sql_text, str) and sql_text.strip():
                sql_map[str(key)] = sql_text.strip()
        return sql_map

    # single task
    single = _extract_sql_map(dsl_query)
    if single:
        if len(single) == 1:
            return next(iter(single.values()))
        return json.dumps(single, ensure_ascii=False, indent=2)

    # multi task
    multi: dict[str, Any] = {}
    for task_id, layer in dsl_query.items():
        if not isinstance(layer, dict):
            continue
        sql_map = _extract_sql_map(layer)
        if sql_map:
            multi[str(task_id)] = sql_map

    if not multi:
        return json.dumps(dsl_query, ensure_ascii=False, indent=2, default=str)
    if len(multi) == 1:
        only_task = next(iter(multi.values()))
        if isinstance(only_task, dict) and len(only_task) == 1:
            return next(iter(only_task.values()))
    return json.dumps(multi, ensure_ascii=False, indent=2)


def run(
    input_excel: str,
    output_excel: str,
    sheet_name: str = "",
    question_col: str = "",
    start_row: int = 1,
    limit: int = 0,
    sleep_sec: float = 0.0,
) -> str:
    input_excel = os.path.abspath(input_excel)
    output_excel = os.path.abspath(output_excel)
    os.makedirs(os.path.dirname(output_excel), exist_ok=True)

    excel_file = pd.ExcelFile(input_excel)
    target_sheet = sheet_name.strip() if sheet_name and sheet_name.strip() else excel_file.sheet_names[0]
    dataframe = excel_file.parse(target_sheet)

    columns = [str(col) for col in dataframe.columns]
    question_column = question_col.strip() if question_col and question_col.strip() in columns else _find_question_column(columns)

    total_rows = len(dataframe)
    start_idx = max(int(start_row or 1) - 1, 0)
    if start_idx >= total_rows:
        raise ValueError(f"start_row out of range: {start_row}, total={total_rows}")
    end_idx = total_rows if int(limit or 0) <= 0 else min(total_rows, start_idx + int(limit))

    print(f"[INFO] input={input_excel}")
    print(f"[INFO] output={output_excel}")
    print(f"[INFO] sheet={target_sheet}")
    print(f"[INFO] question_col={question_column}")
    print(f"[INFO] run_range=[{start_idx + 1}, {end_idx}] total_rows={total_rows}")

    output_rows: list[dict[str, Any]] = []
    selected_df = dataframe.iloc[start_idx:end_idx].copy()
    selected_total = len(selected_df)

    for local_idx, (_, row) in enumerate(selected_df.iterrows(), start=1):
        question = str(row.get(question_column, "")).strip()
        if not question:
            output_rows.append(
                {
                    **{col: row.get(col, "") for col in columns},
                    "实体筛选_模型原生返回": "",
                    "DSL生成_模型原生返回": "",
                    "SQL": "",
                    "最终输出文本": "",
                    "run_id": "",
                    "output_dir": "",
                    "error": "",
                }
            )
            print(f"[{local_idx:03d}/{selected_total}] question empty -> skipped")
            continue

        print(f"[{local_idx:03d}/{selected_total}] {question}")

        orchestrator = Orchestrator()
        error_text = ""
        try:
            result = orchestrator.run(question)
        except Exception as exc:
            # Orchestrator._run_agents already catches most exceptions, but keep this as a hard guard.
            result = {}
            error_text = str(exc)

        traces = orchestrator.llm.get_traces() if hasattr(orchestrator.llm, "get_traces") else []
        chat_json_traces = _iter_chat_json_traces(traces)

        entity_raws = _pick_entity_select_raw(chat_json_traces)
        dsl_raws = _pick_intent_dsl_raw(chat_json_traces)

        # Fallbacks: keep something rather than returning blank when heuristics miss.
        if not entity_raws and chat_json_traces:
            entity_raws = [str(chat_json_traces[0].get("response_text", "") or "")]
        if not dsl_raws and chat_json_traces:
            dsl_raws = [str(chat_json_traces[-1].get("response_text", "") or "")]

        dsl_query_path = os.path.join(orchestrator.output_dir, "dsl_query.json")
        dsl_query = _read_json_if_exists(dsl_query_path)
        sql_view = _extract_sql_view(dsl_query)

        answer_text = str((result or {}).get("answer", "") or "")
        if not error_text and not answer_text and isinstance(result, dict) and result.get("compute_result") is None:
            error_text = "pipeline returned empty result"

        output_rows.append(
            {
                **{col: row.get(col, "") for col in columns},
                "实体筛选_模型原生返回": _format_raw_responses(entity_raws),
                "DSL生成_模型原生返回": _format_raw_responses(dsl_raws),
                "SQL": sql_view,
                "最终输出文本": answer_text,
                "run_id": orchestrator.run_id,
                "output_dir": orchestrator.output_dir,
                "error": error_text,
            }
        )

        if sleep_sec and sleep_sec > 0:
            time.sleep(float(sleep_sec))

    out_df = pd.DataFrame(output_rows)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name=target_sheet, index=False)

    print(f"\n[OK] Saved: {output_excel}")
    return output_excel


def main() -> None:
    default_in = os.path.join(ROOT_DIR, "智能问数系统测试用例.xlsx")
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    default_out = os.path.join(ROOT_DIR, "test", f"智能问数系统测试用例_全流程输出_{timestamp}.xlsx")

    parser = argparse.ArgumentParser(description="Batch-run full pipeline and export raw LLM+SQL+answer to Excel.")
    parser.add_argument("--input", default=default_in, help="Input excel path.")
    parser.add_argument("--output", default=default_out, help="Output excel path (under test/).")
    parser.add_argument("--sheet", default="", help="Sheet name. Default: first sheet.")
    parser.add_argument("--question-col", default="", help="Question column name. Default: auto-detect.")
    parser.add_argument("--start-row", type=int, default=1, help="1-based start row in the input sheet.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to run. 0 means all.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between questions (avoid rate-limit).")
    args = parser.parse_args()

    run(
        input_excel=args.input,
        output_excel=args.output,
        sheet_name=args.sheet,
        question_col=args.question_col,
        start_row=args.start_row,
        limit=args.limit,
        sleep_sec=args.sleep,
    )


if __name__ == "__main__":
    main()
