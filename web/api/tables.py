"""数据表管理 API - 展示数据源的表结构"""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, jsonify
import config
import pandas as pd

tables_bp = Blueprint("tables", __name__)


def _get_source_tables(source_id):
    """获取数据源下所有表的结构"""
    source_dir = config.DATA_SOURCES.get(source_id)
    if not source_dir or not os.path.isdir(source_dir):
        return []

    # 读取映射获取表名
    mapping_file = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    mapping = {}
    if os.path.exists(mapping_file):
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping = json.load(f)

    tables = []
    for fname in sorted(os.listdir(source_dir)):
        if not fname.endswith(".xlsx"):
            continue
        fpath = os.path.join(source_dir, fname)
        try:
            df = pd.read_excel(fpath, nrows=0)
            table_info = {
                "file_name": fname,
                "table_name": fname.replace(".xlsx", ""),
                "columns": []
            }
            for col in df.columns:
                table_info["columns"].append({
                    "name": col,
                    "type": "string"  # Excel 无类型信息
                })
            # 匹配映射中的实体
            for entity_name, tm in mapping.get("table_mappings", {}).items():
                if tm.get("file_name") == fname:
                    table_info["mapped_entity"] = entity_name
                    break
            tables.append(table_info)
        except Exception:
            pass
    return tables


def _resolve_source_id(source_id: str | None = None) -> str:
    sid = str(source_id or "").strip()
    if sid:
        return sid
    return config.get_default_source_id()


@tables_bp.route("/tables/sources")
def get_sources():
    """获取所有数据源"""
    sources = []
    for sid, path in config.DATA_SOURCES.items():
        mapping_file = os.path.join(config.MAPPING_DIR, f"{sid}_mapping.json")
        name = sid
        if os.path.exists(mapping_file):
            with open(mapping_file, "r", encoding="utf-8") as f:
                m = json.load(f)
                name = m.get("source_name", sid)
        sources.append({"id": sid, "name": name, "path": path})
    return jsonify(sources)


@tables_bp.route("/tables")
def get_tables_default():
    source_id = _resolve_source_id()
    tables = _get_source_tables(source_id)
    return jsonify(tables)


@tables_bp.route("/tables/<source_id>")
def get_tables(source_id):
    """获取某数据源的所有表结构"""
    tables = _get_source_tables(_resolve_source_id(source_id))
    return jsonify(tables)
