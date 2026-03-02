"""Flask 主应用 - 本体问答系统 Web 服务"""
import os
import sys

# 将项目根目录加入 path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from flask import Flask, send_from_directory, request, jsonify, session
from functools import wraps

app = Flask(__name__,
            static_folder="static",
            template_folder="templates")
app.secret_key = "xindian-ontology-poc-2026"

# ---------- 认证装饰器 ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from web.auth import get_current_user
        token = session.get("token") or request.headers.get("X-Token")
        user = get_current_user(token)
        if not user:
            return jsonify({"error": "请先登录"}), 401
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from web.auth import get_current_user
        token = session.get("token") or request.headers.get("X-Token")
        user = get_current_user(token)
        if not user:
            return jsonify({"error": "请先登录"}), 401
        if not user.get("is_admin"):
            return jsonify({"error": "需要管理员权限"}), 403
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


def query_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        from web.auth import get_current_user
        token = session.get("token") or request.headers.get("X-Token")
        user = get_current_user(token)
        if not user:
            return jsonify({"error": "请先登录"}), 401
        if not user.get("can_query"):
            return jsonify({"error": "您没有查询权限"}), 403
        request.current_user = user
        return f(*args, **kwargs)
    return decorated


# ---------- 认证路由 ----------
@app.route("/api/login", methods=["POST"])
def api_login():
    from web.auth import login
    data = request.get_json()
    ok, result, token = login(data.get("username", ""), data.get("password", ""))
    if ok:
        session["token"] = token
        return jsonify({"success": True, "user": result, "token": token})
    return jsonify({"success": False, "error": result}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    from web.auth import logout
    token = session.get("token") or request.headers.get("X-Token")
    if token:
        logout(token)
        session.pop("token", None)
    return jsonify({"success": True})


@app.route("/api/me")
def api_me():
    from web.auth import get_current_user
    token = session.get("token") or request.headers.get("X-Token")
    user = get_current_user(token)
    if user:
        return jsonify({"logged_in": True, "user": user})
    return jsonify({"logged_in": False})


# ---------- 页面路由 ----------
@app.route("/")
def index():
    return send_from_directory("templates", "chat.html")


@app.route("/login")
def login_page():
    return send_from_directory("templates", "login.html")


@app.route("/admin")
def admin_page():
    return send_from_directory("templates", "admin.html")


# ---------- 注册 API 蓝图 ----------
def register_blueprints():
    from web.api.chat import chat_bp
    from web.api.ontology_api import ontology_bp
    from web.api.tables import tables_bp
    from web.api.mapping_api import mapping_bp
    from web.api.data_api import data_bp
    from web.api.dsl_logs import dsl_bp
    from web.api.mcp import mcp_bp
    from web.api.users import users_bp
    from web.api.graph import graph_bp
    from web.api.knowledge import knowledge_bp
    from web.api.db_config import db_config_bp
    from web.api.prompts import prompts_bp

    app.register_blueprint(chat_bp, url_prefix="/api")
    app.register_blueprint(ontology_bp, url_prefix="/api")
    app.register_blueprint(tables_bp, url_prefix="/api")
    app.register_blueprint(mapping_bp, url_prefix="/api")
    app.register_blueprint(data_bp, url_prefix="/api")
    app.register_blueprint(dsl_bp, url_prefix="/api")
    app.register_blueprint(mcp_bp, url_prefix="/api")
    app.register_blueprint(users_bp, url_prefix="/api")
    app.register_blueprint(graph_bp, url_prefix="/api")
    app.register_blueprint(knowledge_bp, url_prefix="/api")
    app.register_blueprint(db_config_bp, url_prefix="/api")
    app.register_blueprint(prompts_bp, url_prefix="/api")


register_blueprints()

if __name__ == "__main__":
    print("=" * 50)
    print("🎓 新点本体问答系统 POC")
    print("   用户端: http://localhost:5000")
    print("   管理端: http://localhost:5000/admin")
    print("   登 录: http://localhost:5000/login")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=True)
