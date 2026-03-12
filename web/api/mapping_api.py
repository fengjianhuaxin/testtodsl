"""Mapping APIs for ontology-table mapping management."""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify

import config
from prompts.prompt_manager import PromptManager

mapping_bp = Blueprint("mapping", __name__)


def _resolve_source_id(source_id: str | None = None) -> str:
    sid = str(source_id or "").strip()
    if sid:
        return sid
    return config.get_default_source_id()


def _load_mapping(source_id: str):
    path = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def _save_mapping(source_id: str, data: dict):
    path = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def _normalize_table_relations_payload(raw_relations):
    if isinstance(raw_relations, dict):
        raw_relations = list(raw_relations.values())
    if not isinstance(raw_relations, list):
        return []

    result = []
    for item in raw_relations:
        if not isinstance(item, dict):
            continue
        left_table = str(item.get("left_table", "") or item.get("from_table", "")).strip()
        right_table = str(item.get("right_table", "") or item.get("to_table", "")).strip()
        if not left_table or not right_table:
            continue

        raw_pairs = item.get("join_pairs", item.get("join_on", item.get("pairs", [])))
        if isinstance(raw_pairs, dict):
            raw_pairs = [raw_pairs]
        if not isinstance(raw_pairs, list):
            raw_pairs = []
        join_pairs = []
        seen = set()
        for pair in raw_pairs:
            if isinstance(pair, dict):
                left = str(pair.get("left", "") or pair.get("left_field", "")).strip()
                right = str(pair.get("right", "") or pair.get("right_field", "")).strip()
            elif isinstance(pair, (list, tuple)) and len(pair) >= 2:
                left = str(pair[0]).strip()
                right = str(pair[1]).strip()
            else:
                continue
            if not left or not right:
                continue
            key = (left, right)
            if key in seen:
                continue
            seen.add(key)
            join_pairs.append({"left": left, "right": right})

        if not join_pairs:
            left_field = str(item.get("left_field", "")).strip()
            right_field = str(item.get("right_field", "")).strip()
            if left_field and right_field:
                join_pairs = [{"left": left_field, "right": right_field}]
        if not join_pairs:
            continue

        join_type = str(item.get("join_type", "left")).strip().lower()
        if join_type not in {"left", "left_join", "left join", "inner"}:
            join_type = "left"
        if join_type in {"left_join", "left join"}:
            join_type = "left"

        result.append({
            "left_table": left_table,
            "right_table": right_table,
            "join_pairs": join_pairs,
            "join_type": join_type,
        })
    return result


@mapping_bp.route("/mapping/<source_id>", methods=["GET"])
def get_mapping(source_id):
    mapping = _load_mapping(_resolve_source_id(source_id))
    if not mapping:
        return jsonify({"error": "mapping not found"}), 404
    return jsonify(mapping)


@mapping_bp.route("/mapping", methods=["GET"])
def get_mapping_default():
    source_id = _resolve_source_id()
    mapping = _load_mapping(source_id)
    if not mapping:
        return jsonify({"error": "mapping not found"}), 404
    return jsonify(mapping)


@mapping_bp.route("/mapping/<source_id>/table-relations", methods=["GET"])
def get_table_relations(source_id):
    mapping = _load_mapping(_resolve_source_id(source_id))
    if not mapping:
        return jsonify({"error": "mapping not found"}), 404
    relations = _normalize_table_relations_payload(mapping.get("table_relations", []))
    return jsonify({"table_relations": relations})


@mapping_bp.route("/mapping/table-relations", methods=["GET"])
def get_table_relations_default():
    return get_table_relations(_resolve_source_id())


@mapping_bp.route("/mapping/<source_id>/table-relations", methods=["PUT"])
def update_table_relations(source_id):
    source_id = _resolve_source_id(source_id)
    data = request.get_json() or {}
    mapping = _load_mapping(source_id)
    if not mapping:
        mapping = {
            "source_name": source_id,
            "source_id": source_id,
            "description": "",
            "table_relations": [],
            "table_mappings": {},
        }
    relations = _normalize_table_relations_payload(data.get("table_relations", data))
    mapping["table_relations"] = relations
    _save_mapping(source_id, mapping)
    return jsonify({"success": True, "table_relations": relations})


@mapping_bp.route("/mapping/table-relations", methods=["PUT"])
def update_table_relations_default():
    return update_table_relations(_resolve_source_id())


@mapping_bp.route("/mapping/<source_id>/entity/<entity_name>", methods=["PUT"])
def update_entity_mapping(source_id, entity_name):
    """Create/update entity mapping and keep field semantics in data layer."""
    data = request.get_json() or {}
    source_id = _resolve_source_id(source_id)
    mapping = _load_mapping(source_id)
    if not mapping:
        mapping = {
            "source_name": source_id,
            "source_id": source_id,
            "description": "",
            "table_relations": [],
            "table_mappings": {},
        }

    table_mappings = mapping.get("table_mappings", {})
    if not isinstance(table_mappings, dict):
        table_mappings = {}
    current = table_mappings.get(entity_name, {})
    if not isinstance(current, dict):
        current = {}

    # primary table (legacy fields keep backward compatibility)
    table_name = data.get("table_name", current.get("table_name", ""))
    file_name = data.get("file_name", current.get("file_name", ""))
    primary_table = data.get("primary_table", current.get("primary_table", {}))
    if not isinstance(primary_table, dict):
        primary_table = {}
    if not primary_table:
        primary_table = {
            "table_name": str(table_name or "").strip(),
            "file_name": str(file_name or "").strip(),
        }
    else:
        primary_table = {
            "table_name": str(primary_table.get("table_name", "")).strip() or str(table_name or "").strip(),
            "file_name": str(primary_table.get("file_name", "")).strip() or str(file_name or "").strip(),
        }

    field_mappings = data.get("field_mappings", current.get("field_mappings", {}))
    if not isinstance(field_mappings, dict):
        field_mappings = {}

    field_value_semantics = data.get(
        "field_value_semantics",
        data.get("value_semantics", current.get("field_value_semantics", current.get("value_semantics", {}))),
    )
    if not isinstance(field_value_semantics, dict):
        field_value_semantics = {}

    self_join_policy = data.get("self_join_policy", current.get("self_join_policy", {}))
    if isinstance(self_join_policy, bool):
        self_join_policy = {
            "enabled": bool(self_join_policy),
            "mode": "llm" if self_join_policy else "disabled",
            "candidate_pairs": [],
        }
    if not isinstance(self_join_policy, dict):
        self_join_policy = {}

    secondary_tables = data.get("secondary_tables", current.get("secondary_tables", []))
    if isinstance(secondary_tables, dict):
        normalized_secondary = {}
        for key, item in secondary_tables.items():
            if isinstance(item, dict):
                normalized_secondary[str(key)] = item
        secondary_tables = normalized_secondary
    elif isinstance(secondary_tables, list):
        secondary_tables = [item for item in secondary_tables if isinstance(item, dict)]
    else:
        secondary_tables = []

    field_sources = data.get(
        "field_sources",
        data.get("field_source_mappings", current.get("field_sources", current.get("field_source_mappings", {}))),
    )
    if not isinstance(field_sources, dict):
        field_sources = {}

    # Preserve unknown keys for forward compatibility.
    merged = dict(current)
    merged.update({
        "table_name": str(table_name or "").strip(),
        "file_name": str(file_name or "").strip(),
        "primary_table": primary_table,
        "field_mappings": field_mappings,
        "field_value_semantics": field_value_semantics,
        "self_join_policy": self_join_policy,
        "secondary_tables": secondary_tables,
        "field_sources": field_sources,
    })
    table_mappings[entity_name] = merged
    mapping["table_mappings"] = table_mappings

    _save_mapping(source_id, mapping)
    return jsonify({"success": True})


@mapping_bp.route("/mapping/entity/<entity_name>", methods=["PUT"])
def update_entity_mapping_default(entity_name):
    return update_entity_mapping(_resolve_source_id(), entity_name)


@mapping_bp.route("/mapping/<source_id>/entity/<entity_name>", methods=["DELETE"])
def delete_entity_mapping(source_id, entity_name):
    source_id = _resolve_source_id(source_id)
    mapping = _load_mapping(source_id)
    if not mapping:
        return jsonify({"error": "mapping not found"}), 404

    table_mappings = mapping.get("table_mappings", {})
    if isinstance(table_mappings, dict) and entity_name in table_mappings:
        del table_mappings[entity_name]
        mapping["table_mappings"] = table_mappings
        _save_mapping(source_id, mapping)
    return jsonify({"success": True})


@mapping_bp.route("/mapping/entity/<entity_name>", methods=["DELETE"])
def delete_entity_mapping_default(entity_name):
    return delete_entity_mapping(_resolve_source_id(), entity_name)


@mapping_bp.route("/mapping/llm-analyze", methods=["POST"])
def llm_analyze():
    """LLM pre-analysis: suggest field mapping from ontology + sheet columns."""
    data = request.get_json() or {}
    entity_name = data.get("entity_name")
    source_id = _resolve_source_id(data.get("source_id"))
    file_name = data.get("file_name")

    ontology_file = os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
    with open(ontology_file, "r", encoding="utf-8") as file:
        ontology_data = json.load(file)

    entity = ontology_data.get("entities", {}).get(entity_name)
    if not entity:
        return jsonify({"error": "entity not found"}), 404

    source_dir = config.DATA_SOURCES.get(source_id)
    if not source_dir:
        return jsonify({"error": "source not found"}), 404

    table_file = os.path.join(source_dir, file_name)
    if not os.path.exists(table_file):
        return jsonify({"error": "file not found"}), 404

    import pandas as pd

    dataframe = pd.read_excel(table_file, nrows=3)
    columns = list(dataframe.columns)
    sample_data = dataframe.head(3).to_dict(orient="records")

    props = entity.get("properties", {})
    prop_desc = "\n".join(
        [f"  - {name}: {item.get('label', name)} (类型: {item.get('type', 'string')})" for name, item in props.items()]
    )

    prompt_manager = PromptManager(config.PROMPTS_FILE)
    user_prompt = prompt_manager.render(
        key="mapping_analyze_user",
        context={
            "entity_name": entity_name,
            "entity_label": entity.get("label", ""),
            "prop_desc": prop_desc,
            "columns": columns,
            "sample_data": json.dumps(sample_data, ensure_ascii=False),
        },
        fallback_template=(
            "请分析以下本体实体属性与数据表字段的对应关系。\n\n"
            "本体实体: $entity_name ($entity_label)\n"
            "属性列表:\n$prop_desc\n\n"
            "数据表字段: $columns\n"
            "样本数据: $sample_data\n\n"
            "请以 JSON 返回映射，格式: {\"属性名\": \"字段名\"}"
        ),
    )
    system_prompt = prompt_manager.render(
        key="mapping_analyze_system",
        context={},
        fallback_template="你是一个数据映射分析专家。",
    )

    try:
        from llm.qwen_client import QwenClient

        llm = QwenClient()
        suggested = llm.chat_json(system_prompt, user_prompt)
        return jsonify({
            "success": True,
            "suggested_mapping": suggested,
            "columns": columns,
            "sample_data": sample_data,
        })
    except Exception as error:
        return jsonify({"error": f"LLM analyze failed: {error}"}), 500
