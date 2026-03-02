"""用户权限管理 API"""
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify

users_bp = Blueprint("users", __name__)


@users_bp.route("/users", methods=["GET"])
def get_users():
    from web.auth import get_all_users
    users = get_all_users()
    # 隐藏密码
    safe = [{k: v for k, v in u.items() if k != "password"} for u in users]
    return jsonify(safe)


@users_bp.route("/users", methods=["POST"])
def add_user():
    from web.auth import add_user
    data = request.get_json()
    required = ["username", "password", "name"]
    for r in required:
        if not data.get(r):
            return jsonify({"error": f"缺少字段: {r}"}), 400
    user = {
        "username": data["username"],
        "password": data["password"],
        "name": data["name"],
        "role": data.get("role", "user"),
        "can_login": data.get("can_login", True),
        "can_query": data.get("can_query", False),
        "is_admin": data.get("is_admin", False),
    }
    ok, msg = add_user(user)
    if ok:
        return jsonify({"success": True})
    return jsonify({"error": msg}), 400


@users_bp.route("/users/<username>", methods=["PUT"])
def update_user(username):
    from web.auth import update_user
    data = request.get_json()
    ok, msg = update_user(username, data)
    if ok:
        return jsonify({"success": True})
    return jsonify({"error": msg}), 400


@users_bp.route("/users/<username>", methods=["DELETE"])
def delete_user(username):
    from web.auth import delete_user
    if username == "admin":
        return jsonify({"error": "不能删除管理员账户"}), 400
    ok, msg = delete_user(username)
    return jsonify({"success": True})
