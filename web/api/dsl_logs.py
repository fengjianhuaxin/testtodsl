"""DSL 日志管理 API"""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config

dsl_bp = Blueprint("dsl", __name__)
DSL_FILE = os.path.join(config.DATA_DIR, "dsl_logs.json")


def _load():
    if not os.path.exists(DSL_FILE):
        return []
    with open(DSL_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@dsl_bp.route("/dsl-logs", methods=["GET"])
def get_logs():
    logs = _load()
    search = request.args.get("search", "")
    if search:
        logs = [l for l in logs if search in l.get("question", "")]
    return jsonify(logs)


@dsl_bp.route("/dsl-logs/<log_id>", methods=["GET"])
def get_log_detail(log_id):
    logs = _load()
    entry = next((l for l in logs if l["id"] == log_id), None)
    if not entry:
        return jsonify({"error": "日志不存在"}), 404
    return jsonify(entry)
