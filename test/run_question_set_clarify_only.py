"""Run question set to Step-01 (ontology entity selection + intent clarify DSL) and export to Excel.

Behavior:
- Read input Excel (default: project root "智能问数系统测试用例.xlsx")
- For each question, run:
  1) question split
  2) intent clarify
- Fill two columns:
  - ontology selection JSON (default column: "实体筛选")
  - clarified DSL JSON (default column: "DSL")
- If a question is split into sub-questions, output multiple rows (one row per sub-question).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import OrderedDict
from typing import Any

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import config
from agents.intent_clarify_agent import IntentClarifyAgent
from agents.question_split_agent import QuestionSplitAgent
from knowledge.knowledge_manager import KnowledgeManager
from llm.qwen_client import QwenClient
from mapping.mapping_manager import MappingManager
from ontology.ontology_manager import OntologyManager
from prompts.prompt_manager import PromptManager


def _force_utf8_console() -> None:
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


def _dedupe_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


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


def _find_or_create_output_columns(columns: list[str]) -> tuple[str, str]:
    entity_col = ""
    dsl_col = ""
    for col in columns:
        key = _normalize_key(col)
        if not entity_col and ("实体筛选" in key or "target_entities" in key):
            entity_col = col
        if not dsl_col and ("dsl" in key or "意图澄清" in key):
            dsl_col = col

    if not entity_col:
        entity_col = "实体筛选"
    if not dsl_col:
        dsl_col = "DSL"
    return entity_col, dsl_col


def _find_or_create_prompt_columns(columns: list[str]) -> tuple[str, str]:
    select_prompt_col = ""
    clarify_prompt_col = ""
    for col in columns:
        key = _normalize_key(col)
        if not select_prompt_col and ("实体筛选提示词" in key or "本体筛选提示词" in key):
            select_prompt_col = col
        if not clarify_prompt_col and ("意图澄清提示词" in key or "dsl提示词" in key):
            clarify_prompt_col = col

    if not select_prompt_col:
        select_prompt_col = "本体筛选提示词"
    if not clarify_prompt_col:
        clarify_prompt_col = "意图澄清提示词"
    return select_prompt_col, clarify_prompt_col


def _collect_entities_from_clarified_intent(clarified_intent: dict) -> list[str]:
    entities: list[str] = []

    raw_entities = clarified_intent.get("entity", [])
    if isinstance(raw_entities, str):
        raw_entities = [raw_entities]
    if isinstance(raw_entities, list):
        for item in raw_entities:
            text = str(item or "").strip()
            if text:
                entities.append(text)

    raw_instances = clarified_intent.get("entity_instances", [])
    if isinstance(raw_instances, list):
        for item in raw_instances:
            if not isinstance(item, dict):
                continue
            text = str(item.get("entity", "")).strip()
            if text:
                entities.append(text)

    return _dedupe_keep_order(entities)


def _build_relation_list(clarified_intent: dict) -> list[dict[str, str]]:
    instance_entity = {}
    for item in clarified_intent.get("entity_instances", []) if isinstance(clarified_intent.get("entity_instances", []), list) else []:
        if not isinstance(item, dict):
            continue
        instance_id = str(item.get("id", "")).strip()
        entity_name = str(item.get("entity", "")).strip()
        if instance_id and entity_name:
            instance_entity[instance_id] = entity_name

    relation_rows = []
    for row in clarified_intent.get("relations", []) if isinstance(clarified_intent.get("relations", []), list) else []:
        if not isinstance(row, dict):
            continue

        from_entity = str(row.get("from_entity", "")).strip()
        to_entity = str(row.get("to_entity", "")).strip()
        if not from_entity:
            from_entity = instance_entity.get(str(row.get("from_instance", "")).strip(), "")
        if not to_entity:
            to_entity = instance_entity.get(str(row.get("to_instance", "")).strip(), "")

        relation_name = str(row.get("relation", "") or row.get("type", "")).strip()
        if not from_entity or not to_entity:
            continue

        relation_rows.append(
            {
                "from_entity": from_entity,
                "relation": relation_name,
                "to_entity": to_entity,
            }
        )
    return relation_rows


def _build_condition_list(clarified_intent: dict) -> list[dict[str, Any]]:
    rows = []
    for item in clarified_intent.get("conditions", []) if isinstance(clarified_intent.get("conditions", []), list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip()
        if not field:
            continue
        rows.append(
            {
                "field": field,
                "op": str(item.get("op", "=")).strip() or "=",
                "value": item.get("value", ""),
                "entity": str(item.get("entity", "")).strip(),
            }
        )
    return rows


def _build_output_field_list(clarified_intent: dict) -> list[dict[str, str]]:
    rows = []
    for item in clarified_intent.get("output_fields", []) if isinstance(clarified_intent.get("output_fields", []), list) else []:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field", "")).strip()
        if not field:
            continue
        rows.append(
            {
                "field": field,
                "entity": str(item.get("entity", "")).strip(),
            }
        )
    return rows


def _to_dsl_view(clarified_intent: dict, target_entities: list[str]) -> dict[str, Any]:
    entities = _collect_entities_from_clarified_intent(clarified_intent)
    if not entities and isinstance(target_entities, list):
        entities = _dedupe_keep_order([str(item).strip() for item in target_entities if str(item).strip()])

    calc_type = str(clarified_intent.get("calc_type", "detail")).strip().lower() or "detail"
    calc_params = clarified_intent.get("calc_params", {})
    if not isinstance(calc_params, dict):
        calc_params = {}

    return {
        "entity": entities,
        "relations": _build_relation_list(clarified_intent),
        "conditions": _build_condition_list(clarified_intent),
        "output_fields": _build_output_field_list(clarified_intent),
        "calc_type": calc_type,
        "calc_params": calc_params,
    }


def _parse_json_from_response_text(text: str) -> Any:
    raw = str(text or "").strip()
    if not raw:
        return {}
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw_response": str(text or "")}


def _extract_chat_json_traces(traces: list[dict]) -> list[dict]:
    result = []
    for item in traces:
        if not isinstance(item, dict):
            continue
        if str(item.get("call_type", "")).strip() != "chat_json":
            continue
        result.append(item)
    return result


def _pick_selection_trace(traces: list[dict]) -> dict:
    for item in traces:
        original_system = str(item.get("original_system_prompt", ""))
        if "target_entities" in original_system:
            return item
    return traces[0] if traces else {}


def _pick_clarify_trace(traces: list[dict]) -> dict:
    for item in reversed(traces):
        original_system = str(item.get("original_system_prompt", ""))
        if "NL2DSL" in original_system or "entity_instances" in original_system or "意图澄清" in original_system:
            return item
    return traces[-1] if traces else {}


def _prompt_payload_from_trace(trace: dict) -> dict[str, str]:
    if not isinstance(trace, dict):
        return {"system_prompt": "", "user_prompt": ""}
    system_prompt = str(trace.get("original_system_prompt", "") or trace.get("system_prompt", "")).strip()
    user_prompt = str(trace.get("user_message", "")).strip()
    return {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }


def _model_payload_from_trace(trace: dict) -> Any:
    if not isinstance(trace, dict):
        return {}
    return _parse_json_from_response_text(trace.get("response_text", ""))


class ClarifyOnlyRunner:
    """Run question split + intent clarify only."""

    def __init__(self):
        self.ontology = OntologyManager(os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"))
        self.knowledge = KnowledgeManager(config.KNOWLEDGE_FILE)
        self.prompts = PromptManager(config.PROMPTS_FILE)
        self.mapping = MappingManager(config.MAPPING_DIR)
        self.llm = QwenClient()
        self.splitter = QuestionSplitAgent(self.llm, self.prompts)
        self.intent = IntentClarifyAgent(self.llm, self.ontology, self.knowledge, self.prompts, self.mapping)

    def run_question(self, question: str) -> list[dict[str, Any]]:
        raw_question = str(question or "").strip()
        if not raw_question:
            return []

        normalized_question, rewrite_hits = self.knowledge.normalize_question(raw_question)
        base_pipeline_data = {
            "question": normalized_question,
            "raw_question_input": raw_question,
            "source_id": config.get_default_source_id(),
            "global_rewrite_hits": rewrite_hits,
        }

        split_output = self.splitter.run(dict(base_pipeline_data))
        split_analysis = split_output.get("split_analysis", {})

        tasks = split_analysis.get("sub_tasks", []) if isinstance(split_analysis, dict) else []
        if not isinstance(tasks, list) or not tasks:
            tasks = [{"task_id": "task_1", "question": normalized_question}]

        multi_enabled = bool(split_analysis.get("enabled")) and len(tasks) > 1 if isinstance(split_analysis, dict) else False
        if not multi_enabled:
            tasks = [tasks[0]]

        results = []
        for idx, task in enumerate(tasks, start=1):
            task_id = str(task.get("task_id", f"task_{idx}")).strip() or f"task_{idx}"
            task_question = str(task.get("question", "")).strip() or normalized_question

            task_pipeline = {
                **base_pipeline_data,
                "question": task_question,
                "split_analysis": split_analysis if isinstance(split_analysis, dict) else {},
                "current_task": {
                    "task_id": task_id,
                    "index": idx,
                    "question": task_question,
                    "depends_on": task.get("depends_on", []) if isinstance(task, dict) else [],
                    "bind_output": task.get("bind_output", []) if isinstance(task, dict) else [],
                },
            }
            trace_start = len(self.llm.get_traces()) if hasattr(self.llm, "get_traces") else 0
            step_output = self.intent.run(task_pipeline)
            trace_slice = self.llm.get_traces()[trace_start:] if hasattr(self.llm, "get_traces") else []
            intent_traces = _extract_chat_json_traces(trace_slice)
            selection_trace = _pick_selection_trace(intent_traces)
            clarify_trace = _pick_clarify_trace(intent_traces)

            clarified_intent = step_output.get("clarified_intent", {})
            if not isinstance(clarified_intent, dict):
                clarified_intent = {}

            entity_selection = step_output.get("entity_selection", {})
            if not isinstance(entity_selection, dict):
                entity_selection = {}

            target_entities_raw = entity_selection.get("target_entities", [])
            if isinstance(target_entities_raw, str):
                target_entities_raw = [target_entities_raw]
            if not isinstance(target_entities_raw, list):
                target_entities_raw = []
            target_entities = _dedupe_keep_order([str(item).strip() for item in target_entities_raw if str(item).strip()])
            if not target_entities:
                target_entities = _collect_entities_from_clarified_intent(clarified_intent)

            # Prefer direct model outputs from trace; fallback to normalized pipeline outputs when missing.
            selection_payload = _model_payload_from_trace(selection_trace)
            if not isinstance(selection_payload, dict) or not selection_payload:
                selection_payload = {"target_entities": target_entities}

            dsl_payload = _model_payload_from_trace(clarify_trace)
            if not isinstance(dsl_payload, dict) or not dsl_payload:
                dsl_payload = _to_dsl_view(clarified_intent, target_entities=target_entities)

            results.append(
                {
                    "task_id": task_id,
                    "task_question": task_question,
                    "selection_payload": selection_payload,
                    "dsl_payload": dsl_payload,
                    "selection_prompt_payload": _prompt_payload_from_trace(selection_trace),
                    "clarify_prompt_payload": _prompt_payload_from_trace(clarify_trace),
                }
            )
        return results


def _safe_read_input(path: str) -> str:
    path = str(path or "").strip()
    if path and os.path.exists(path):
        return path

    default_path = os.path.join(ROOT_DIR, "智能问数系统测试用例.xlsx")
    if os.path.exists(default_path):
        return default_path

    candidates = []
    for name in os.listdir(ROOT_DIR):
        if not name.lower().endswith(".xlsx"):
            continue
        if name.startswith("~$"):
            continue
        if "测试用例" in name:
            candidates.append(os.path.join(ROOT_DIR, name))
    if candidates:
        return sorted(candidates)[0]

    raise FileNotFoundError(f"Input excel not found: {path or default_path}")


def run(
    input_excel: str,
    output_excel: str,
    sheet_name: str = "",
    question_col: str = "",
    entity_col: str = "",
    dsl_col: str = "",
    select_prompt_col: str = "",
    clarify_prompt_col: str = "",
    start_row: int = 1,
    limit: int = 0,
) -> str:
    _force_utf8_console()

    input_excel = _safe_read_input(input_excel)
    os.makedirs(os.path.dirname(output_excel), exist_ok=True)

    excel_file = pd.ExcelFile(input_excel)
    target_sheet = sheet_name.strip() if sheet_name and sheet_name.strip() else excel_file.sheet_names[0]
    dataframe = excel_file.parse(target_sheet)

    columns = [str(col) for col in dataframe.columns]
    question_column = question_col.strip() if question_col and question_col.strip() in columns else _find_question_column(columns)
    auto_entity_col, auto_dsl_col = _find_or_create_output_columns(columns)
    auto_select_prompt_col, auto_clarify_prompt_col = _find_or_create_prompt_columns(columns)
    entity_column = entity_col.strip() if entity_col and entity_col.strip() else auto_entity_col
    dsl_column = dsl_col.strip() if dsl_col and dsl_col.strip() else auto_dsl_col
    select_prompt_column = (
        select_prompt_col.strip() if select_prompt_col and select_prompt_col.strip() else auto_select_prompt_col
    )
    clarify_prompt_column = (
        clarify_prompt_col.strip() if clarify_prompt_col and clarify_prompt_col.strip() else auto_clarify_prompt_col
    )

    final_columns = list(columns)
    if entity_column not in final_columns:
        final_columns.append(entity_column)
    if dsl_column not in final_columns:
        final_columns.append(dsl_column)
    if select_prompt_column not in final_columns:
        final_columns.append(select_prompt_column)
    if clarify_prompt_column not in final_columns:
        final_columns.append(clarify_prompt_column)

    print(f"[INFO] input={input_excel}")
    print(f"[INFO] sheet={target_sheet}")
    print(f"[INFO] question_col={question_column}")
    print(f"[INFO] output_cols=({entity_column}, {dsl_column}, {select_prompt_column}, {clarify_prompt_column})")
    total_rows = len(dataframe)
    start_idx = max(int(start_row or 1) - 1, 0)
    if start_idx >= total_rows:
        raise ValueError(f"start_row out of range: {start_row}, total={total_rows}")
    end_idx = total_rows if int(limit or 0) <= 0 else min(total_rows, start_idx + int(limit))

    print(f"[INFO] total_rows={total_rows}")
    print(f"[INFO] run_range=[{start_idx + 1}, {end_idx}]")

    runner = ClarifyOnlyRunner()
    output_rows: list[dict[str, Any]] = []

    selected_df = dataframe.iloc[start_idx:end_idx].copy()
    selected_total = len(selected_df)
    for local_idx, (_, row) in enumerate(selected_df.iterrows(), start=1):
        base_row = OrderedDict()
        for col in final_columns:
            if col in dataframe.columns:
                base_row[col] = row.get(col, "")
            else:
                base_row[col] = ""

        question = str(row.get(question_column, "")).strip()
        if not question:
            base_row[entity_column] = ""
            base_row[dsl_column] = ""
            base_row[select_prompt_column] = ""
            base_row[clarify_prompt_column] = ""
            output_rows.append(dict(base_row))
            print(f"[{local_idx:03d}/{selected_total}] question empty -> skipped")
            continue

        print(f"[{local_idx:03d}/{selected_total}] {question}")
        try:
            results = runner.run_question(question)
            if not results:
                base_row[entity_column] = _to_pretty_json({"target_entities": []})
                base_row[dsl_column] = _to_pretty_json(
                    {
                        "entity": [],
                        "relations": [],
                        "conditions": [],
                        "output_fields": [],
                        "calc_type": "detail",
                        "calc_params": {},
                    }
                )
                base_row[select_prompt_column] = ""
                base_row[clarify_prompt_column] = ""
                output_rows.append(dict(base_row))
                continue

            for item in results:
                out_row = OrderedDict(base_row)
                out_row[question_column] = item.get("task_question", question)
                out_row[entity_column] = _to_pretty_json(item.get("selection_payload", {}))
                out_row[dsl_column] = _to_pretty_json(item.get("dsl_payload", {}))
                out_row[select_prompt_column] = _to_pretty_json(item.get("selection_prompt_payload", {}))
                out_row[clarify_prompt_column] = _to_pretty_json(item.get("clarify_prompt_payload", {}))
                output_rows.append(dict(out_row))
        except Exception as exc:
            error_payload = {"error": str(exc)}
            base_row[entity_column] = _to_pretty_json(error_payload)
            base_row[dsl_column] = _to_pretty_json(error_payload)
            base_row[select_prompt_column] = _to_pretty_json(error_payload)
            base_row[clarify_prompt_column] = _to_pretty_json(error_payload)
            output_rows.append(dict(base_row))
            print(f"  -> ERROR: {exc}")

    out_df = pd.DataFrame(output_rows, columns=final_columns)
    with pd.ExcelWriter(output_excel, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name=target_sheet, index=False)

    print(f"\n[OK] Saved: {output_excel}")
    return output_excel


def main():
    parser = argparse.ArgumentParser(description="Run test question set to intent-clarify stage and export excel.")
    parser.add_argument(
        "--input",
        default=os.path.join(ROOT_DIR, "智能问数系统测试用例.xlsx"),
        help="Input excel path.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(ROOT_DIR, "test", "智能问数系统测试用例_本体筛选与意图澄清结果.xlsx"),
        help="Output excel path.",
    )
    parser.add_argument("--sheet", default="", help="Sheet name. Default: first sheet.")
    parser.add_argument("--question-col", default="", help="Question column name.")
    parser.add_argument("--entity-col", default="", help="Ontology selection output column name.")
    parser.add_argument("--dsl-col", default="", help="Intent DSL output column name.")
    parser.add_argument("--select-prompt-col", default="", help="Entity-selection prompt output column name.")
    parser.add_argument("--clarify-prompt-col", default="", help="Intent-clarify prompt output column name.")
    parser.add_argument("--start-row", type=int, default=1, help="1-based start row in the input sheet.")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to run. 0 means all.")
    args = parser.parse_args()

    run(
        input_excel=args.input,
        output_excel=args.output,
        sheet_name=args.sheet,
        question_col=args.question_col,
        entity_col=args.entity_col,
        dsl_col=args.dsl_col,
        select_prompt_col=args.select_prompt_col,
        clarify_prompt_col=args.clarify_prompt_col,
        start_row=args.start_row,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
