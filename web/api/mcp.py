"""MCP 管理 API"""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config

mcp_bp = Blueprint("mcp", __name__)
MCP_FILE = os.path.join(config.DATA_DIR, "mcp_config.json")


def _load():
    if not os.path.exists(MCP_FILE):
        return []
    with open(MCP_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(MCP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@mcp_bp.route("/mcp", methods=["GET"])
def get_mcps():
    return jsonify(_load())


@mcp_bp.route("/mcp", methods=["POST"])
def add_mcp():
    data = request.get_json()
    mcps = _load()
    import uuid
    data["id"] = str(uuid.uuid4())[:8]
    data["status"] = "active"
    mcps.append(data)
    _save(mcps)
    return jsonify({"success": True, "id": data["id"]})


@mcp_bp.route("/mcp/<mcp_id>", methods=["PUT"])
def update_mcp(mcp_id):
    data = request.get_json()
    mcps = _load()
    for i, m in enumerate(mcps):
        if m.get("id") == mcp_id:
            mcps[i].update(data)
            _save(mcps)
            return jsonify({"success": True})
    return jsonify({"error": "MCP 不存在"}), 404


@mcp_bp.route("/mcp/<mcp_id>", methods=["DELETE"])
def delete_mcp(mcp_id):
    mcps = _load()
    mcps = [m for m in mcps if m.get("id") != mcp_id]
    _save(mcps)
    return jsonify({"success": True})


@mcp_bp.route("/mcp/test", methods=["POST"])
def test_mcp():
    data = request.get_json()
    # POC 简单返回连接测试结果
    return jsonify({"success": True, "message": f"MCP '{data.get('name', '')}' 连接测试通过（POC 模拟）"})
