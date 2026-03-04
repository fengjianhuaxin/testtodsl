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
            "table_mappings": {},
        }

    table_mappings = mapping.get("table_mappings", {})
    if not isinstance(table_mappings, dict):
        table_mappings = {}
    current = table_mappings.get(entity_name, {})
    if not isinstance(current, dict):
        current = {}

    table_name = data.get("table_name", current.get("table_name", ""))
    file_name = data.get("file_name", current.get("file_name", ""))

    field_mappings = data.get("field_mappings", current.get("field_mappings", {}))
    if not isinstance(field_mappings, dict):
        field_mappings = {}

    field_value_semantics = data.get(
        "field_value_semantics",
        data.get("value_semantics", current.get("field_value_semantics", current.get("value_semantics", {}))),
    )
    if not isinstance(field_value_semantics, dict):
        field_value_semantics = {}

    table_mappings[entity_name] = {
        "table_name": str(table_name or "").strip(),
        "file_name": str(file_name or "").strip(),
        "field_mappings": field_mappings,
        "field_value_semantics": field_value_semantics,
    }
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
