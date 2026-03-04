"""领域知识管理 API - 术语知识与全局同义词规则"""
import json
import os
import re
import sys
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify

import config

knowledge_bp = Blueprint("knowledge", __name__)
KNOWLEDGE_FILE = config.KNOWLEDGE_FILE
DEFAULT_SOURCE_ID = config.get_default_source_id()


def _load():
    if not os.path.exists(KNOWLEDGE_FILE):
        return {
            "term_knowledge": [],
            "rewrite_rules": [],
            "sql_metric_rules": [],
        }
    with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("term_knowledge", [])
    data.setdefault("rewrite_rules", [])
    data.setdefault("sql_metric_rules", [])
    return data


def _save(data):
    os.makedirs(os.path.dirname(KNOWLEDGE_FILE), exist_ok=True)
    with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _normalize_keywords(value):
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        return []

    result = []
    for part in parts:
        text = str(part).strip()
        if text and text not in result:
            result.append(text)
    return result


def _normalize_bool(value, default=True):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "启用", "是"):
        return True
    if text in ("0", "false", "no", "n", "off", "禁用", "否"):
        return False
    return default


def _normalize_sql_text(sql: str) -> str:
    text = str(sql or "").strip()
    if text.startswith("```sql"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    if text.endswith(";"):
        text = text[:-1].strip()
    return text


def _is_safe_select_sql(sql: str) -> bool:
    text = _normalize_sql_text(sql)
    if not text:
        return False
    if not re.match(r"^\s*select\b", text, flags=re.IGNORECASE):
        return False
    if ";" in text:
        return False
    if re.search(r"\b(insert|update|delete|drop|alter|truncate|create|replace)\b", text, flags=re.IGNORECASE):
        return False
    return True


def _normalize_target_entities(value):
    if value is None:
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = value
    else:
        return []

    result = []
    for part in parts:
        text = str(part).strip()
        if text and text not in result:
            result.append(text)
    return result


@knowledge_bp.route("/knowledge", methods=["GET"])
def get_knowledge():
    data = _load()
    return jsonify(data.get("term_knowledge", []))


@knowledge_bp.route("/knowledge", methods=["POST"])
def add_knowledge():
    payload = request.get_json() or {}
    term = str(payload.get("term", "")).strip()
    content = str(payload.get("content", "")).strip()
    if not term:
        return jsonify({"error": "术语不能为空"}), 400
    if not content:
        return jsonify({"error": "术语释义不能为空"}), 400

    data = _load()
    terms = data.get("term_knowledge", [])
    item_id = str(payload.get("id", "")).strip() or str(uuid.uuid4())[:8]
    if any(str(item.get("id", "")) == item_id for item in terms):
        item_id = str(uuid.uuid4())[:8]

    terms.append(
        {
            "id": item_id,
            "term": term,
            "keywords": _normalize_keywords(payload.get("keywords", [])),
            "content": content,
        }
    )
    data["term_knowledge"] = terms
    _save(data)
    return jsonify({"success": True, "id": item_id})


@knowledge_bp.route("/knowledge/<item_id>", methods=["PUT"])
def update_knowledge(item_id):
    payload = request.get_json() or {}
    data = _load()
    terms = data.get("term_knowledge", [])

    index = next((i for i, item in enumerate(terms) if str(item.get("id", "")) == str(item_id)), -1)
    if index < 0:
        return jsonify({"error": "知识条目不存在"}), 404

    item = terms[index]
    if "term" in payload:
        term = str(payload.get("term", "")).strip()
        if not term:
            return jsonify({"error": "术语不能为空"}), 400
        item["term"] = term
    if "content" in payload:
        content = str(payload.get("content", "")).strip()
        if not content:
            return jsonify({"error": "术语释义不能为空"}), 400
        item["content"] = content
    if "keywords" in payload:
        item["keywords"] = _normalize_keywords(payload.get("keywords"))

    terms[index] = item
    data["term_knowledge"] = terms
    _save(data)
    return jsonify({"success": True})


@knowledge_bp.route("/knowledge/<item_id>", methods=["DELETE"])
def delete_knowledge(item_id):
    data = _load()
    terms = data.get("term_knowledge", [])
    new_terms = [item for item in terms if str(item.get("id", "")) != str(item_id)]
    if len(new_terms) == len(terms):
        return jsonify({"error": "知识条目不存在"}), 404
    data["term_knowledge"] = new_terms
    _save(data)
    return jsonify({"success": True})


@knowledge_bp.route("/knowledge/rewrite-rules", methods=["GET"])
def get_rewrite_rules():
    data = _load()
    return jsonify(data.get("rewrite_rules", []))


@knowledge_bp.route("/knowledge/rewrite-rules", methods=["POST"])
def add_rewrite_rule():
    payload = request.get_json() or {}
    source = str(payload.get("source", "")).strip()
    target = str(payload.get("target", "")).strip()
    description = str(payload.get("description", "")).strip()
    enabled = _normalize_bool(payload.get("enabled"), default=True)

    if not source:
        return jsonify({"error": "原词不能为空"}), 400
    if not target:
        return jsonify({"error": "目标词不能为空"}), 400

    data = _load()
    rules = data.get("rewrite_rules", [])
    item_id = str(payload.get("id", "")).strip() or str(uuid.uuid4())[:8]
    if any(str(item.get("id", "")) == item_id for item in rules):
        item_id = str(uuid.uuid4())[:8]

    rules.append(
        {
            "id": item_id,
            "source": source,
            "target": target,
            "description": description,
            "enabled": enabled,
        }
    )
    data["rewrite_rules"] = rules
    _save(data)
    return jsonify({"success": True, "id": item_id})


@knowledge_bp.route("/knowledge/rewrite-rules/<item_id>", methods=["PUT"])
def update_rewrite_rule(item_id):
    payload = request.get_json() or {}
    data = _load()
    rules = data.get("rewrite_rules", [])

    index = next((i for i, item in enumerate(rules) if str(item.get("id", "")) == str(item_id)), -1)
    if index < 0:
        return jsonify({"error": "规则不存在"}), 404

    item = rules[index]
    if "source" in payload:
        source = str(payload.get("source", "")).strip()
        if not source:
            return jsonify({"error": "原词不能为空"}), 400
        item["source"] = source
    if "target" in payload:
        target = str(payload.get("target", "")).strip()
        if not target:
            return jsonify({"error": "目标词不能为空"}), 400
        item["target"] = target
    if "description" in payload:
        item["description"] = str(payload.get("description", "")).strip()
    if "enabled" in payload:
        item["enabled"] = _normalize_bool(payload.get("enabled"), default=True)

    rules[index] = item
    data["rewrite_rules"] = rules
    _save(data)
    return jsonify({"success": True})


@knowledge_bp.route("/knowledge/rewrite-rules/<item_id>", methods=["DELETE"])
def delete_rewrite_rule(item_id):
    data = _load()
    rules = data.get("rewrite_rules", [])
    new_rules = [item for item in rules if str(item.get("id", "")) != str(item_id)]
    if len(new_rules) == len(rules):
        return jsonify({"error": "规则不存在"}), 404
    data["rewrite_rules"] = new_rules
    _save(data)
    return jsonify({"success": True})


@knowledge_bp.route("/knowledge/sql-rules", methods=["GET"])
def get_sql_rules():
    data = _load()
    return jsonify(data.get("sql_metric_rules", []))


@knowledge_bp.route("/knowledge/sql-rules", methods=["POST"])
def add_sql_rule():
    payload = request.get_json() or {}
    name = str(payload.get("name", "")).strip()
    sql = _normalize_sql_text(payload.get("sql", ""))
    if not name:
        return jsonify({"error": "规则名称不能为空"}), 400
    if not sql:
        return jsonify({"error": "SQL不能为空"}), 400
    if not _is_safe_select_sql(sql):
        return jsonify({"error": "仅允许单条SELECT查询SQL"}), 400

    data = _load()
    rules = data.get("sql_metric_rules", [])
    item_id = str(payload.get("id", "")).strip() or str(uuid.uuid4())[:8]
    if any(str(item.get("id", "")) == item_id for item in rules):
        item_id = str(uuid.uuid4())[:8]

    try:
        priority = int(payload.get("priority", 100))
    except Exception:
        priority = 100

    rules.append(
        {
            "id": item_id,
            "name": name,
            "keywords": _normalize_keywords(payload.get("keywords", [])),
            "target_entities": _normalize_target_entities(payload.get("target_entities", [])),
            "data_source": str(payload.get("data_source", DEFAULT_SOURCE_ID)).strip() or DEFAULT_SOURCE_ID,
            "sql": sql,
            "priority": priority,
            "description": str(payload.get("description", "")).strip(),
            "enabled": _normalize_bool(payload.get("enabled"), default=True),
        }
    )
    data["sql_metric_rules"] = rules
    _save(data)
    return jsonify({"success": True, "id": item_id})


@knowledge_bp.route("/knowledge/sql-rules/<item_id>", methods=["PUT"])
def update_sql_rule(item_id):
    payload = request.get_json() or {}
    data = _load()
    rules = data.get("sql_metric_rules", [])

    index = next((i for i, item in enumerate(rules) if str(item.get("id", "")) == str(item_id)), -1)
    if index < 0:
        return jsonify({"error": "规则不存在"}), 404

    item = rules[index]
    if "name" in payload:
        name = str(payload.get("name", "")).strip()
        if not name:
            return jsonify({"error": "规则名称不能为空"}), 400
        item["name"] = name
    if "sql" in payload:
        sql = _normalize_sql_text(payload.get("sql", ""))
        if not sql:
            return jsonify({"error": "SQL不能为空"}), 400
        if not _is_safe_select_sql(sql):
            return jsonify({"error": "仅允许单条SELECT查询SQL"}), 400
        item["sql"] = sql
    if "keywords" in payload:
        item["keywords"] = _normalize_keywords(payload.get("keywords"))
    if "target_entities" in payload:
        item["target_entities"] = _normalize_target_entities(payload.get("target_entities"))
    if "data_source" in payload:
        item["data_source"] = str(payload.get("data_source", DEFAULT_SOURCE_ID)).strip() or DEFAULT_SOURCE_ID
    if "priority" in payload:
        try:
            item["priority"] = int(payload.get("priority", item.get("priority", 100)))
        except Exception:
            item["priority"] = 100
    if "description" in payload:
        item["description"] = str(payload.get("description", "")).strip()
    if "enabled" in payload:
        item["enabled"] = _normalize_bool(payload.get("enabled"), default=True)

    rules[index] = item
    data["sql_metric_rules"] = rules
    _save(data)
    return jsonify({"success": True})


@knowledge_bp.route("/knowledge/sql-rules/<item_id>", methods=["DELETE"])
def delete_sql_rule(item_id):
    data = _load()
    rules = data.get("sql_metric_rules", [])
    new_rules = [item for item in rules if str(item.get("id", "")) != str(item_id)]
    if len(new_rules) == len(rules):
        return jsonify({"error": "规则不存在"}), 404
    data["sql_metric_rules"] = new_rules
    _save(data)
    return jsonify({"success": True})
