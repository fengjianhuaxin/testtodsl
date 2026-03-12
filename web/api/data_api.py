"""数据管理 API - 对 Excel 数据增删改查"""
import json
import os
import sys
import uuid

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config
import pandas as pd

data_bp = Blueprint("data", __name__)


def _resolve_source_id(source_id: str | None = None) -> str:
    sid = str(source_id or "").strip()
    if sid:
        return sid
    return config.get_default_source_id()


def _get_file_path(source_id, entity_name):
    source_id = _resolve_source_id(source_id)
    mapping_file = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    if not os.path.exists(mapping_file):
        return None
    with open(mapping_file, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    tm = mapping.get("table_mappings", {}).get(entity_name, {})
    if not isinstance(tm, dict):
        tm = {}
    primary = tm.get("primary_table", {})
    if not isinstance(primary, dict):
        primary = {}
    fname = str(primary.get("file_name", "")).strip() or str(tm.get("file_name", "")).strip()
    if not fname:
        return None
    return os.path.join(config.DATA_SOURCES[source_id], fname)


@data_bp.route("/data/<source_id>/<entity_name>", methods=["GET"])
def get_data(source_id, entity_name):
    source_id = _resolve_source_id(source_id)
    fpath = _get_file_path(source_id, entity_name)
    if not fpath or not os.path.exists(fpath):
        return jsonify({"error": "数据文件不存在"}), 404
    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 20, type=int)
    search = request.args.get("search", "")

    df = pd.read_excel(fpath)
    if search:
        mask = df.astype(str).apply(lambda x: x.str.contains(search, na=False)).any(axis=1)
        df = df[mask]

    total = len(df)
    start = (page - 1) * page_size
    end = start + page_size
    page_data = df.iloc[start:end]

    return jsonify({
        "total": total,
        "page": page,
        "page_size": page_size,
        "columns": list(df.columns),
        "data": page_data.fillna("").to_dict(orient="records")
    })


@data_bp.route("/data/<entity_name>", methods=["GET"])
def get_data_default(entity_name):
    return get_data(_resolve_source_id(), entity_name)


@data_bp.route("/data/<source_id>/<entity_name>", methods=["POST"])
def add_data(source_id, entity_name):
    source_id = _resolve_source_id(source_id)
    fpath = _get_file_path(source_id, entity_name)
    if not fpath:
        return jsonify({"error": "数据文件不存在"}), 404
    row = request.get_json()
    df = pd.read_excel(fpath) if os.path.exists(fpath) else pd.DataFrame()
    new_row = pd.DataFrame([row])
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_excel(fpath, index=False)
    return jsonify({"success": True})


@data_bp.route("/data/<entity_name>", methods=["POST"])
def add_data_default(entity_name):
    return add_data(_resolve_source_id(), entity_name)


@data_bp.route("/data/<source_id>/<entity_name>/<int:row_index>", methods=["PUT"])
def update_data(source_id, entity_name, row_index):
    source_id = _resolve_source_id(source_id)
    fpath = _get_file_path(source_id, entity_name)
    if not fpath or not os.path.exists(fpath):
        return jsonify({"error": "数据文件不存在"}), 404
    row = request.get_json()
    df = pd.read_excel(fpath)
    if row_index < 0 or row_index >= len(df):
        return jsonify({"error": "行索引越界"}), 400
    for k, v in row.items():
        if k in df.columns:
            df.at[row_index, k] = v
    df.to_excel(fpath, index=False)
    return jsonify({"success": True})


@data_bp.route("/data/<entity_name>/<int:row_index>", methods=["PUT"])
def update_data_default(entity_name, row_index):
    return update_data(_resolve_source_id(), entity_name, row_index)


@data_bp.route("/data/<source_id>/<entity_name>/<int:row_index>", methods=["DELETE"])
def delete_data(source_id, entity_name, row_index):
    source_id = _resolve_source_id(source_id)
    fpath = _get_file_path(source_id, entity_name)
    if not fpath or not os.path.exists(fpath):
        return jsonify({"error": "数据文件不存在"}), 404
    df = pd.read_excel(fpath)
    if row_index < 0 or row_index >= len(df):
        return jsonify({"error": "行索引越界"}), 400
    df = df.drop(index=row_index).reset_index(drop=True)
    df.to_excel(fpath, index=False)
    return jsonify({"success": True})


@data_bp.route("/data/<entity_name>/<int:row_index>", methods=["DELETE"])
def delete_data_default(entity_name, row_index):
    return delete_data(_resolve_source_id(), entity_name, row_index)
