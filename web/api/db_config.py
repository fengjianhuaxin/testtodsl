"""Database runtime config API."""
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify

import config

db_config_bp = Blueprint("db_config", __name__)


def _sanitize_store_type(value: str) -> str:
    store_type = str(value or "").strip().lower()
    return store_type if store_type in ("sheet", "mysql") else "sheet"


def _sanitize_mysql(payload: dict, fallback: dict) -> dict:
    payload = payload or {}
    result = dict(fallback)
    result.update({
        "host": str(payload.get("host", result.get("host", "localhost"))).strip() or "localhost",
        "user": str(payload.get("user", result.get("user", ""))).strip(),
        "password": str(payload.get("password", result.get("password", ""))),
        "database": str(payload.get("database", result.get("database", ""))).strip(),
    })
    try:
        result["port"] = int(payload.get("port", result.get("port", 3306)))
    except Exception:
        result["port"] = 3306
    return result


@db_config_bp.route("/db-config", methods=["GET"])
def get_db_config():
    current = config.load_runtime_db_config()
    mysql_cfg = dict(current.get("mysql", {}))
    has_password = bool(mysql_cfg.get("password"))
    mysql_cfg["password"] = ""
    mysql_cfg["has_password"] = has_password
    return jsonify({
        "store_type": current.get("store_type", "sheet"),
        "mysql": mysql_cfg,
    })


@db_config_bp.route("/db-config", methods=["POST"])
def save_db_config():
    payload = request.get_json() or {}
    current = config.load_runtime_db_config()

    store_type = _sanitize_store_type(payload.get("store_type", current.get("store_type", "sheet")))
    input_mysql = payload.get("mysql", {})
    keep_password = bool(payload.get("keep_password", True))

    mysql_cfg = _sanitize_mysql(input_mysql, current.get("mysql", {}))
    if keep_password and not str(input_mysql.get("password", "")).strip():
        mysql_cfg["password"] = current.get("mysql", {}).get("password", "")

    config.save_runtime_db_config({
        "store_type": store_type,
        "mysql": mysql_cfg,
    })
    return jsonify({"success": True, "store_type": store_type})


@db_config_bp.route("/db-config/test", methods=["POST"])
def test_db_config():
    payload = request.get_json() or {}
    current = config.load_runtime_db_config()
    mysql_cfg = _sanitize_mysql(payload.get("mysql", {}), current.get("mysql", {}))

    try:
        import pymysql
        connection = pymysql.connect(
            host=mysql_cfg.get("host", "localhost"),
            port=int(mysql_cfg.get("port", 3306)),
            user=mysql_cfg.get("user", ""),
            password=mysql_cfg.get("password", ""),
            database=mysql_cfg.get("database", ""),
            charset="utf8mb4",
            autocommit=True,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        connection.close()
        return jsonify({"success": True, "message": "MySQL连接成功"})
    except Exception as error:
        return jsonify({"success": False, "error": f"MySQL连接失败: {error}"}), 400
