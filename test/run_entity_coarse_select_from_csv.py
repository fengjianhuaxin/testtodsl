"""Batch-run entity coarse selection prompt on a CSV question set and export model raw outputs."""

import argparse
import json
import os
import sys
import time
from typing import Any

import pandas as pd

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import config
from knowledge.knowledge_manager import KnowledgeManager
from llm.qwen_client import QwenClient
from mapping.mapping_manager import MappingManager
from ontology.ontology_manager import OntologyManager
from prompts.prompt_manager import PromptManager


def _force_utf8_console():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="ignore")
            except Exception:
                pass


def _safe_json(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload)


def _load_csv_with_fallback(csv_path: str) -> pd.DataFrame:
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error = None
    for encoding in encodings:
        try:
            return pd.read_csv(csv_path, encoding=encoding)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"读取CSV失败: {csv_path}, err={last_error}")


def _pick_question_column(columns: list[str], preferred: str | None = None) -> str:
    normalized = {str(col).strip(): str(col).strip() for col in columns}
    if preferred:
        candidate = str(preferred).strip()
        if candidate in normalized:
            return normalized[candidate]

    default_candidates = [
        "问题描述",
        "问题",
        "question",
        "query",
        "user_question",
    ]
    for candidate in default_candidates:
        if candidate in normalized:
            return normalized[candidate]

    raise ValueError(f"未找到问题列，请通过 --question-column 指定。当前列: {list(normalized.keys())}")


def _extract_last_chat_json_trace(traces: list[dict]) -> dict:
    for item in reversed(traces):
        if not isinstance(item, dict):
            continue
        if item.get("call_type") != "chat_json":
            continue
        return item
    return {}


def _extract_trace_error(traces: list[dict]) -> str:
    for item in reversed(traces):
        if not isinstance(item, dict):
            continue
        error = str(item.get("error", "")).strip()
        if not error:
            continue
        if item.get("call_type") in {"chat", "chat_json", "chat_json_parse"}:
            return error
    return ""


def run_batch(csv_path: str, output_excel: str, question_column: str | None = None):
    _force_utf8_console()

    ontology = OntologyManager(os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"))
    knowledge = KnowledgeManager(config.KNOWLEDGE_FILE)
    prompts = PromptManager(config.PROMPTS_FILE)
    mapping = MappingManager(config.MAPPING_DIR)
    llm = QwenClient()
    # Keep these managers initialized to match runtime dependencies in production pipeline.
    _ = (knowledge, mapping)

    all_entities = [str(name).strip().upper() for name in ontology.get_all_entity_names()]
    entity_catalog = []
    for entity_name in all_entities:
        entity_def = ontology.get_entity(entity_name) or {}
        label = str(entity_def.get("label", "")).strip() or entity_name
        entity_catalog.append(f"- {label}")
    entity_catalog_text = "\n".join(entity_catalog)

    relation_lines = []
    for rel in ontology.relations:
        from_name = str(rel.get("from", "")).strip().upper()
        to_name = str(rel.get("to", "")).strip().upper()
        rel_label = str(rel.get("label", "")).strip() or str(rel.get("name", "")).strip()
        if not from_name or not to_name:
            continue
        from_label = str((ontology.get_entity(from_name) or {}).get("label", "")).strip() or from_name
        to_label = str((ontology.get_entity(to_name) or {}).get("label", "")).strip() or to_name
        relation_lines.append(f"- {from_label} --[{rel_label}]--> {to_label}")
    relation_catalog_text = "\n".join(relation_lines) if relation_lines else "- (无)"

    default_system_template = PromptManager.DEFAULT_PROMPTS["intent_entity_select_system"]["template"]
    default_user_template = PromptManager.DEFAULT_PROMPTS["intent_entity_select_user"]["template"]

    dataframe = _load_csv_with_fallback(csv_path)
    q_col = _pick_question_column(list(dataframe.columns), preferred=question_column)
    total = len(dataframe.index)
    rows = []

    print(f"加载问题集: {csv_path}")
    print(f"问题列: {q_col}")
    print(f"总问题数: {total}")

    for idx, row in dataframe.iterrows():
        question = str(row.get(q_col, "")).strip()
        case_id = str(row.get("问题编号", "")).strip()
        if not question or question.lower() == "nan":
            out = row.to_dict()
            out.update(
                {
                    "模型返回_json": "",
                    "模型原始返回文本": "",
                    "目标实体": "",
                    "主实体": "",
                    "置信度": "",
                    "结果来源": "",
                    "是否使用子本体": False,
                    "模型调用错误": "",
                    "错误": "空问题，已跳过",
                    "耗时毫秒": 0,
                }
            )
            rows.append(out)
            continue

        trace_start = len(llm.get_traces())
        started = time.time()
        model_result = {}
        error_msg = ""

        try:
            system_prompt = prompts.render(
                key="intent_entity_select_system",
                context={
                    "entity_catalog": entity_catalog_text,
                    "relation_catalog": relation_catalog_text,
                },
                fallback_template=default_system_template,
            )
            user_prompt = prompts.render(
                key="intent_entity_select_user",
                context={"question": question},
                fallback_template=default_user_template,
            )
            model_result = llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            error_msg = str(exc)
            model_result = {}
        elapsed_ms = int((time.time() - started) * 1000)

        trace_slice = llm.get_traces()[trace_start:]
        coarse_trace = _extract_last_chat_json_trace(trace_slice)
        raw_response_text = str(coarse_trace.get("response_text", "")).strip()
        trace_error = _extract_trace_error(trace_slice)
        target_entities = []
        if isinstance(model_result, dict):
            raw_targets = model_result.get("target_entities", [])
            if isinstance(raw_targets, list):
                target_entities = [str(item).strip() for item in raw_targets if str(item).strip()]

        print(f"[{idx + 1:03d}/{total:03d}] {case_id or f'row_{idx + 1}'} | {question}")

        out = row.to_dict()
        out.update(
            {
                "模型返回_json": _safe_json(model_result),
                "模型原始返回文本": raw_response_text,
                "目标实体": "、".join(target_entities),
                "模型调用错误": trace_error,
                "错误": error_msg,
                "耗时毫秒": elapsed_ms,
            }
        )
        rows.append(out)

    result_df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(output_excel), exist_ok=True)
    result_df.to_excel(output_excel, index=False)
    print(f"\n导出完成: {output_excel}")


def main():
    parser = argparse.ArgumentParser(description="批量执行实体粗选并导出Excel")
    parser.add_argument(
        "--input-csv",
        default=os.path.join(ROOT_DIR, "test", "人口领域测试问题集_优化版.csv"),
        help="问题集CSV路径",
    )
    parser.add_argument(
        "--output-excel",
        default=os.path.join(ROOT_DIR, "test", "人口领域测试问题集_实体粗选结果.xlsx"),
        help="导出Excel路径",
    )
    parser.add_argument(
        "--question-column",
        default="问题描述",
        help="问题文本列名，默认：问题描述",
    )
    args = parser.parse_args()
    run_batch(args.input_csv, args.output_excel, args.question_column)


if __name__ == "__main__":
    main()
