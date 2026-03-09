"""本体管理 API"""
import json
import os
import sys
from pathlib import Path

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify, session
import config
from import_xksx_ontology_mapping import import_ontology_mapping

ontology_bp = Blueprint("ontology", __name__)

ONTOLOGY_FILE = os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
DEFAULT_SOURCE_ID = config.get_default_source_id()
MAPPING_FILE = os.path.join(config.MAPPING_DIR, f"{DEFAULT_SOURCE_ID}_mapping.json")


def _load():
    with open(ONTOLOGY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(ONTOLOGY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _normalize_aliases(value):
    """归一化别名配置，统一为字符串列表"""
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        return []
    aliases = []
    for p in parts:
        s = str(p).strip()
        if s and s not in aliases:
            aliases.append(s)
    return aliases


def _require_admin():
    from web.auth import get_current_user

    token = session.get("token") or request.headers.get("X-Token")
    user = get_current_user(token)
    if not user:
        return None, (jsonify({"error": "请先登录"}), 401)
    if not user.get("is_admin"):
        return None, (jsonify({"error": "需要管理员权限"}), 403)
    return user, None


def _guess_default_excel_path() -> str:
    download_dir = Path.home() / "AppData" / "Roaming" / "EpointMsg" / "downloadFiles"
    if not download_dir.exists():
        return ""

    candidates = [p for p in download_dir.glob("*数据要素本体*.xlsx") if not p.name.startswith("~$")]
    if not candidates:
        return ""
    best = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(best)


def _normalize_properties(properties):
    """归一化属性配置，补齐 aliases/value_aliases 结构"""
    result = {}
    if not isinstance(properties, dict):
        return result

    for prop_name, prop_def in properties.items():
        if not isinstance(prop_def, dict):
            continue
        normalized = dict(prop_def)
        normalized["aliases"] = _normalize_aliases(prop_def.get("aliases"))
        value_aliases = prop_def.get("value_aliases", {})
        normalized["value_aliases"] = value_aliases if isinstance(value_aliases, dict) else {}
        result[prop_name] = normalized
    return result


def _normalize_relation(data, onto):
    """Normalize and validate relation config, supports composite join keys."""
    if not isinstance(data, dict):
        raise ValueError("????????")

    entities = onto.get("entities", {})

    from_entity = str(data.get("from", "")).strip()
    to_entity = str(data.get("to", "")).strip()
    rel_type = str(data.get("type", "related")).strip() or "related"
    label = str(data.get("label", "")).strip()
    from_field_raw = data.get("from_field", "")
    to_field_raw = data.get("to_field", "")

    if not from_entity or not to_entity:
        raise ValueError("???????????????")
    if from_entity not in entities:
        raise ValueError(f"??????: {from_entity}")
    if to_entity not in entities:
        raise ValueError(f"???????: {to_entity}")

    from_props = entities.get(from_entity, {}).get("properties", {})
    to_props = entities.get(to_entity, {}).get("properties", {})

    def _split_fields(raw_value):
        if isinstance(raw_value, list):
            values = raw_value
        else:
            text = str(raw_value or "").strip()
            if not text:
                return []
            values = text.split(",")
        return [str(v).strip() for v in values if str(v).strip()]

    from_fields = _split_fields(from_field_raw)
    to_fields = _split_fields(to_field_raw)

    if (from_fields and not to_fields) or (to_fields and not from_fields):
        raise ValueError("??????????????????")
    if from_fields and len(from_fields) != len(to_fields):
        raise ValueError("?????????")

    for field in from_fields:
        if field not in from_props:
            raise ValueError(f"????????: {from_entity}.{field}")
    for field in to_fields:
        if field not in to_props:
            raise ValueError(f"?????????: {to_entity}.{field}")

    return {
        "from": from_entity,
        "to": to_entity,
        "type": rel_type,
        "label": label,
        "from_field": ",".join(from_fields),
        "to_field": ",".join(to_fields),
    }


def _check_mapping_impact(entity_name=None, prop_name=None):
    """检查本体变更对映射的影响"""
    impacts = []
    mapping_dir = config.MAPPING_DIR
    for fname in os.listdir(mapping_dir):
        if not fname.endswith("_mapping.json"):
            continue
        fpath = os.path.join(mapping_dir, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        source_name = mapping.get("source_name", fname)
        tm = mapping.get("table_mappings", {})
        if entity_name and entity_name in tm:
            if prop_name:
                fm = tm[entity_name].get("field_mappings", {})
                if prop_name in fm:
                    impacts.append(f"数据源'{source_name}'中实体'{entity_name}'的属性'{prop_name}'已有字段映射'{fm[prop_name]}'")
            else:
                impacts.append(f"数据源'{source_name}'中实体'{entity_name}'已有表映射'{tm[entity_name].get('table_name', '')}'")
    return impacts


@ontology_bp.route("/ontology", methods=["GET"])
def get_ontology():
    return jsonify(_load())


@ontology_bp.route("/ontology/entities", methods=["GET"])
def get_entities():
    onto = _load()
    return jsonify(onto.get("entities", {}))


@ontology_bp.route("/ontology/import-xlsx", methods=["POST"])
def import_ontology_from_xlsx():
    _, denied = _require_admin()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    excel_path = str(payload.get("excel_path", "")).strip()
    if not excel_path:
        excel_path = _guess_default_excel_path()

    if not excel_path:
        return jsonify({"error": "未找到默认本体Excel，请手动填写路径"}), 400
    if not os.path.exists(excel_path):
        return jsonify({"error": f"Excel文件不存在: {excel_path}"}), 400

    try:
        result = import_ontology_mapping(
            excel_path=excel_path,
            ontology_out=ONTOLOGY_FILE,
            mapping_out=MAPPING_FILE,
            source_dir=config.DATA_SOURCES.get(DEFAULT_SOURCE_ID, ""),
            source_id=DEFAULT_SOURCE_ID,
            source_name=DEFAULT_SOURCE_ID,
        )
    except Exception as error:
        return jsonify({"error": f"导入失败: {error}"}), 500

    return jsonify({"success": True, **result})


@ontology_bp.route("/ontology/entities/<name>", methods=["GET"])
def get_entity(name):
    onto = _load()
    entity = onto.get("entities", {}).get(name)
    if not entity:
        return jsonify({"error": "实体不存在"}), 404
    return jsonify({"name": name, **entity})


@ontology_bp.route("/ontology/entities", methods=["POST"])
def add_entity():
    data = request.get_json()
    name = data.get("name")
    if not name:
        return jsonify({"error": "实体名不能为空"}), 400
    onto = _load()
    if name in onto.get("entities", {}):
        return jsonify({"error": "实体已存在"}), 400
    onto["entities"][name] = {
        "label": data.get("label", name),
        "description": data.get("description", ""),
        "aliases": _normalize_aliases(data.get("aliases")),
        "properties": _normalize_properties(data.get("properties", {}))
    }
    _save(onto)
    return jsonify({"success": True})


@ontology_bp.route("/ontology/entities/<name>", methods=["PUT"])
def update_entity(name):
    data = request.get_json()
    onto = _load()
    if name not in onto.get("entities", {}):
        return jsonify({"error": "实体不存在"}), 404
    impacts = _check_mapping_impact(entity_name=name)
    onto["entities"][name].update({
        "label": data.get("label", onto["entities"][name]["label"]),
        "description": data.get("description", onto["entities"][name].get("description", "")),
        "aliases": _normalize_aliases(data.get("aliases", onto["entities"][name].get("aliases", []))),
    })
    if "properties" in data:
        onto["entities"][name]["properties"] = _normalize_properties(data["properties"])
    _save(onto)
    return jsonify({"success": True, "mapping_impacts": impacts})


@ontology_bp.route("/ontology/entities/<name>", methods=["DELETE"])
def delete_entity(name):
    onto = _load()
    if name not in onto.get("entities", {}):
        return jsonify({"error": "实体不存在"}), 404
    impacts = _check_mapping_impact(entity_name=name)
    # 删除实体
    del onto["entities"][name]
    # 删除相关关系
    onto["relations"] = [r for r in onto.get("relations", [])
                         if r["from"] != name and r["to"] != name]
    # 断开相关映射
    for fname in os.listdir(config.MAPPING_DIR):
        if not fname.endswith("_mapping.json"):
            continue
        fpath = os.path.join(config.MAPPING_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        if name in mapping.get("table_mappings", {}):
            del mapping["table_mappings"][name]
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=4)
    _save(onto)
    return jsonify({"success": True, "mapping_impacts": impacts, "mappings_removed": True})


@ontology_bp.route("/ontology/entities/<name>/properties/<prop>", methods=["DELETE"])
def delete_property(name, prop):
    onto = _load()
    if name not in onto.get("entities", {}):
        return jsonify({"error": "实体不存在"}), 404
    props = onto["entities"][name].get("properties", {})
    if prop not in props:
        return jsonify({"error": "属性不存在"}), 404
    impacts = _check_mapping_impact(entity_name=name, prop_name=prop)
    del props[prop]
    # 断开该属性的映射
    for fname in os.listdir(config.MAPPING_DIR):
        if not fname.endswith("_mapping.json"):
            continue
        fpath = os.path.join(config.MAPPING_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        tm = mapping.get("table_mappings", {}).get(name, {})
        fm = tm.get("field_mappings", {})
        if prop in fm:
            del fm[prop]
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=4)
    _save(onto)
    return jsonify({"success": True, "mapping_impacts": impacts})


@ontology_bp.route("/ontology/relations", methods=["GET"])
def get_relations():
    onto = _load()
    relations = []
    for rel in onto.get("relations", []):
        normalized = {
            "from": rel.get("from", ""),
            "to": rel.get("to", ""),
            "type": rel.get("type", "related"),
            "label": rel.get("label", ""),
            "from_field": rel.get("from_field", ""),
            "to_field": rel.get("to_field", "")
        }
        relations.append(normalized)
    return jsonify(relations)


@ontology_bp.route("/ontology/relations", methods=["POST"])
def add_relation():
    data = request.get_json()
    onto = _load()
    try:
        rel = _normalize_relation(data, onto)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    onto.setdefault("relations", []).append(rel)
    _save(onto)
    return jsonify({"success": True})


@ontology_bp.route("/ontology/relations/<int:index>", methods=["PUT"])
def update_relation(index):
    data = request.get_json()
    onto = _load()
    rels = onto.get("relations", [])
    if index < 0 or index >= len(rels):
        return jsonify({"error": "关系不存在"}), 404
    try:
        rel = _normalize_relation(data, onto)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    rels[index] = rel
    _save(onto)
    return jsonify({"success": True})


@ontology_bp.route("/ontology/relations/<int:index>", methods=["DELETE"])
def delete_relation(index):
    onto = _load()
    rels = onto.get("relations", [])
    if index < 0 or index >= len(rels):
        return jsonify({"error": "关系不存在"}), 404
    rels.pop(index)
    _save(onto)
    return jsonify({"success": True})
