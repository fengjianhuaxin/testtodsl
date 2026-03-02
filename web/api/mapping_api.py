"""映射管理 API - 本体与数据表的映射配置"""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config
from prompts.prompt_manager import PromptManager

mapping_bp = Blueprint("mapping", __name__)


def _load_mapping(source_id):
    fpath = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    if not os.path.exists(fpath):
        return None
    with open(fpath, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_mapping(source_id, data):
    fpath = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


@mapping_bp.route("/mapping/<source_id>", methods=["GET"])
def get_mapping(source_id):
    mapping = _load_mapping(source_id)
    if not mapping:
        return jsonify({"error": "映射不存在"}), 404
    return jsonify(mapping)


@mapping_bp.route("/mapping/<source_id>/entity/<entity_name>", methods=["PUT"])
def update_entity_mapping(source_id, entity_name):
    """更新某个实体的映射"""
    data = request.get_json()
    mapping = _load_mapping(source_id)
    if not mapping:
        mapping = {
            "source_name": source_id,
            "source_id": source_id,
            "description": "",
            "table_mappings": {}
        }
    mapping["table_mappings"][entity_name] = {
        "table_name": data.get("table_name", ""),
        "file_name": data.get("file_name", ""),
        "field_mappings": data.get("field_mappings", {})
    }
    _save_mapping(source_id, mapping)
    return jsonify({"success": True})


@mapping_bp.route("/mapping/<source_id>/entity/<entity_name>", methods=["DELETE"])
def delete_entity_mapping(source_id, entity_name):
    mapping = _load_mapping(source_id)
    if not mapping:
        return jsonify({"error": "映射不存在"}), 404
    if entity_name in mapping.get("table_mappings", {}):
        del mapping["table_mappings"][entity_name]
        _save_mapping(source_id, mapping)
    return jsonify({"success": True})


@mapping_bp.route("/mapping/llm-analyze", methods=["POST"])
def llm_analyze():
    """LLM 预分析：根据实体属性和表字段自动推荐映射"""
    data = request.get_json()
    entity_name = data.get("entity_name")
    source_id = data.get("source_id")
    file_name = data.get("file_name")

    # 获取实体属性
    onto_file = os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
    with open(onto_file, "r", encoding="utf-8") as f:
        onto = json.load(f)
    entity = onto.get("entities", {}).get(entity_name)
    if not entity:
        return jsonify({"error": "实体不存在"}), 404

    # 获取表字段
    import pandas as pd
    source_dir = config.DATA_SOURCES.get(source_id)
    if not source_dir:
        return jsonify({"error": "数据源不存在"}), 404
    fpath = os.path.join(source_dir, file_name)
    if not os.path.exists(fpath):
        return jsonify({"error": "文件不存在"}), 404
    df = pd.read_excel(fpath, nrows=3)
    columns = list(df.columns)
    sample_data = df.head(3).to_dict(orient="records")

    # 构建 LLM prompt
    props = entity.get("properties", {})
    prop_desc = "\n".join([f"  - {k}: {v.get('label', k)} (类型: {v.get('type', 'string')})" for k, v in props.items()])
    col_desc = "\n".join([f"  - {c}" for c in columns])

    prompt_manager = PromptManager(config.PROMPTS_FILE)
    prompt = prompt_manager.render(
        key="mapping_analyze_user",
        context={
            "entity_name": entity_name,
            "entity_label": entity.get("label", ""),
            "prop_desc": prop_desc,
            "columns": columns,
            "sample_data": json.dumps(sample_data, ensure_ascii=False),
        },
        fallback_template="""请分析以下本体实体属性与数据表字段的对应关系。

本体实体: $entity_name ($entity_label)
属性列表:
$prop_desc

数据表字段: $columns
样本数据: $sample_data

请以 JSON 格式返回属性到字段的映射，格式为:
{"属性名": "字段名", ...}
只返回有把握的映射。""",
    )
    system_prompt = prompt_manager.render(
        key="mapping_analyze_system",
        context={},
        fallback_template="你是一个数据映射分析专家。",
    )

    try:
        from llm.qwen_client import QwenClient
        llm = QwenClient()
        result = llm.chat_json(system_prompt, prompt)
        return jsonify({"success": True, "suggested_mapping": result, "columns": columns, "sample_data": sample_data})
    except Exception as e:
        return jsonify({"error": f"LLM 分析失败: {str(e)}"}), 500
