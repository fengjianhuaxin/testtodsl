"""Evaluate Step-01 intent clarify prompt variants on a target question."""

import argparse
import json
import os
import sys
import uuid
import time
from typing import Any

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

import config
from agents.intent_clarify_agent import IntentClarifyAgent
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


def _normalize_entity_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.upper()


def _normalize_field_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.split(".")[-1].strip()


def _collect_entities(intent: dict) -> set[str]:
    entities = set()

    rows = intent.get("entity", [])
    if isinstance(rows, list):
        for item in rows:
            name = _normalize_entity_name(item)
            if name:
                entities.add(name)

    rows = intent.get("target_entities", [])
    if isinstance(rows, list):
        for item in rows:
            name = _normalize_entity_name(item)
            if name:
                entities.add(name)

    rows = intent.get("entity_instances", [])
    if isinstance(rows, list):
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = _normalize_entity_name(item.get("entity", ""))
            if name:
                entities.add(name)

    for key in ("conditions", "output_fields"):
        rows = intent.get(key, [])
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            name = _normalize_entity_name(item.get("entity", ""))
            if name:
                entities.add(name)

    return entities


def _evaluate_intent(intent: dict, question: str) -> dict:
    outputs = intent.get("output_fields", [])
    if not isinstance(outputs, list):
        outputs = []

    conditions = intent.get("conditions", [])
    if not isinstance(conditions, list):
        conditions = []

    entities = _collect_entities(intent)

    has_birth_output = False
    has_wrong_name_output = False
    for item in outputs:
        if not isinstance(item, dict):
            continue
        field = _normalize_field_name(item.get("field", ""))
        entity = _normalize_entity_name(item.get("entity", ""))
        if field == "新生儿出生日期" and entity == "出生医学证明":
            has_birth_output = True
        if field == "姓名":
            has_wrong_name_output = True

    has_name_condition = False
    for item in conditions:
        if not isinstance(item, dict):
            continue
        field = _normalize_field_name(item.get("field", ""))
        entity = _normalize_entity_name(item.get("entity", ""))
        op = str(item.get("op", "")).strip()
        value = str(item.get("value", "")).strip()
        if field == "姓名" and entity == "自然人" and op == "=" and value == "张三":
            has_name_condition = True
            break

    has_birth_entity = "出生医学证明" in entities
    has_person_entity = "自然人" in entities

    checks = {
        "has_birth_output": has_birth_output,
        "has_name_condition": has_name_condition,
        "has_birth_entity": has_birth_entity,
        "has_person_entity": has_person_entity,
        "no_wrong_name_output": not has_wrong_name_output,
    }
    score = sum(1 for value in checks.values() if value)
    passed = (
        checks["has_birth_output"]
        and checks["has_name_condition"]
        and checks["has_birth_entity"]
        and checks["has_person_entity"]
    )
    return {
        "question": question,
        "passed": passed,
        "score": score,
        "checks": checks,
        "entities_seen": sorted(list(entities)),
    }


def _extract_clarify_trace(traces: list[dict]) -> dict:
    for item in traces:
        if not isinstance(item, dict):
            continue
        if item.get("call_type") != "chat_json":
            continue
        original_system = str(item.get("original_system_prompt", ""))
        if "意图澄清智能体" in original_system or "NL2DSL" in original_system:
            return item
    return {}


def _build_default_candidates(current_template: str) -> list[dict]:
    strict_template_v1 = """你是一个意图澄清智能体，请把用户问题解析成结构化 JSON。
本体上下文：
$ontology_desc

$knowledge_block

请严格只返回 JSON（不要输出解释文字），格式如下：
{
  "entities": ["实体名"],
  "relations": [{"from_entity":"实体名","relation":"本体关系名","to_entity":"实体名"}],
  "conditions": [{"field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}],
  "output_fields": [{"field":"属性名","entity":"实体名","label":"显示名"}],
  "calc_type": "detail|count|sum|avg|rate|max|min|topn",
  "calc_params": {"group_by":[],"order_by":"","order_dir":"asc","limit":10},
  "data_source": "all"
}

规则：
1) entities/conditions.entity/output_fields.entity 只能从以下实体中选：$allowed_target_entities。
2) output_fields.field 必须与本体属性名完全一致，禁止输出同义词字段（如“出生日期”）。
3) 问题含“出生/出生日期/什么时候”时，output_fields 必须输出日期语义属性。
4) 若当前实体无目标属性，且1跳关联实体有目标属性，必须引入关联实体并输出其属性。
5) 禁止把 conditions.field 直接复用到 output_fields.field，除非问题明确询问该字段本身。
6) 仅返回 JSON。"""

    strict_template_v2 = """你是本体DSL解析器。根据问题与本体，仅输出JSON：
{
  "entities": ["实体名"],
  "relations": [{"from_entity":"实体名","relation":"本体关系名","to_entity":"实体名"}],
  "conditions": [{"field":"属性名","op":"=|!=|>|<|>=|<=|contains|in","value":"值","entity":"实体名"}],
  "output_fields": [{"field":"属性名","entity":"实体名","label":"显示名"}],
  "calc_type":"detail|count|sum|avg|rate|max|min|topn",
  "calc_params":{"group_by":[],"order_by":"","order_dir":"asc","limit":10},
  "data_source":"all"
}

本体：
$ontology_desc

约束（强制）：
1) 所有 field 必须是本体中真实存在的属性名。
2) 若问题目标是“出生日期”，且“自然人”无出生日期属性，必须改从“出生医学证明.新生儿出生日期”输出。
3) 若输出字段不满足问题目标语义，判定为无效并重新选择。
4) conditions 需保留“自然人.姓名=用户姓名”。
5) 可选实体仅限：$allowed_target_entities。"""

    candidates = [
        {"name": "current", "template": current_template},
        {"name": "strict_v1", "template": strict_template_v1},
        {"name": "strict_v2", "template": strict_template_v2},
    ]
    return candidates


def _load_candidates(candidate_file: str, current_template: str) -> list[dict]:
    if not candidate_file:
        return _build_default_candidates(current_template)

    with open(candidate_file, "r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError("candidate file must be a JSON array")

    candidates = []
    for idx, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", f"candidate_{idx}")).strip() or f"candidate_{idx}"
        template = str(item.get("template", "")).strip()
        if not template:
            continue
        candidates.append({"name": name, "template": template})
    if not candidates:
        raise ValueError("no valid candidates in candidate file")
    return candidates


def _make_prompt_manager(candidate_name: str, template: str) -> tuple[PromptManager, str]:
    tmp_root = os.path.join(ROOT_DIR, "test", "prompt_eval_outputs", "_tmp_prompts")
    os.makedirs(tmp_root, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in candidate_name)
    run_dir = os.path.join(tmp_root, f"intent_prompt_eval_{safe_name}_{uuid.uuid4().hex[:8]}")
    os.makedirs(run_dir, exist_ok=True)
    temp_path = os.path.join(run_dir, "prompt_templates.json")
    payload = {
        "prompts": {
            "intent_clarify_system": {
                "name": f"intent_clarify_system::{candidate_name}",
                "description": "prompt eval candidate",
                "template": template,
            }
        }
    }
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return PromptManager(temp_path), temp_path


def run_eval(question: str, source_id: str, candidate_file: str = "") -> str:
    _force_utf8_console()

    ontology = OntologyManager(os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"))
    knowledge = KnowledgeManager(config.KNOWLEDGE_FILE)
    mapping = MappingManager(config.MAPPING_DIR)
    llm = QwenClient()

    default_pm = PromptManager(config.PROMPTS_FILE)
    current_template = default_pm.get_prompt("intent_clarify_system").get("template", "")
    candidates = _load_candidates(candidate_file, current_template)

    print(f"Question: {question}")
    print(f"Candidates: {len(candidates)}")

    records = []
    for idx, candidate in enumerate(candidates, start=1):
        name = candidate["name"]
        template = candidate["template"]
        print(f"\n[{idx}/{len(candidates)}] testing prompt: {name}")

        pm, temp_prompt_path = _make_prompt_manager(name, template)
        agent = IntentClarifyAgent(llm, ontology, knowledge, pm, mapping)

        llm.reset_traces()
        start_at = time.time()
        error = ""
        clarified_intent = {}
        entity_selection = {}
        trace = {}
        try:
            output = agent.run({"question": question, "source_id": source_id})
            clarified_intent = output.get("clarified_intent", {}) if isinstance(output, dict) else {}
            entity_selection = output.get("entity_selection", {}) if isinstance(output, dict) else {}
            trace = _extract_clarify_trace(llm.get_traces())
        except Exception as exc:
            error = str(exc)
        elapsed = round(time.time() - start_at, 3)

        eval_result = _evaluate_intent(clarified_intent if isinstance(clarified_intent, dict) else {}, question)
        print(
            f"  passed={eval_result['passed']} score={eval_result['score']} "
            f"elapsed={elapsed}s error={'none' if not error else error}"
        )

        records.append(
            {
                "candidate_name": name,
                "elapsed_sec": elapsed,
                "error": error,
                "evaluation": eval_result,
                "entity_selection": entity_selection,
                "clarified_intent": clarified_intent,
                "clarify_trace_prompt": trace.get("original_system_prompt", ""),
                "clarify_trace_response": trace.get("response_text", ""),
                "temp_prompt_path": temp_prompt_path,
            }
        )

    records.sort(
        key=lambda item: (
            not bool(item.get("evaluation", {}).get("passed", False)),
            -(item.get("evaluation", {}).get("score", 0)),
            item.get("elapsed_sec", 9999),
        )
    )

    out_dir = os.path.join(ROOT_DIR, "test", "prompt_eval_outputs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"intent_clarify_prompt_eval_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_path, "w", encoding="utf-8") as file:
        json.dump(
            {
                "question": question,
                "source_id": source_id,
                "candidate_file": candidate_file,
                "results": records,
            },
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nSaved report: {out_path}")
    print("Top candidates:")
    for row in records[:3]:
        ev = row.get("evaluation", {})
        print(
            f"- {row.get('candidate_name')} | passed={ev.get('passed')} "
            f"score={ev.get('score')} | error={'none' if not row.get('error') else row.get('error')}"
        )
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Evaluate Step-01 intent clarify prompt variants.")
    parser.add_argument("--question", type=str, default="张三什么时候出生的", help="question to test")
    parser.add_argument("--source-id", type=str, default=config.get_default_source_id(), help="source id")
    parser.add_argument(
        "--candidate-file",
        type=str,
        default="",
        help="optional JSON file: [{\"name\":\"...\",\"template\":\"...\"}]",
    )
    args = parser.parse_args()

    run_eval(question=args.question, source_id=args.source_id, candidate_file=args.candidate_file)


if __name__ == "__main__":
    main()
