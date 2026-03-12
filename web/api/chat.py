"""问答 API - 对接现有 Pipeline，流式返回 think 过程"""
import json
import os
import sys
import time
import uuid
import traceback

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config

chat_bp = Blueprint("chat", __name__)

HISTORY_FILE = os.path.join(config.DATA_DIR, "chat_history.json")
DSL_LOGS_FILE = os.path.join(config.DATA_DIR, "dsl_logs.json")


def _load_json(path):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_text(path: str) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@chat_bp.route("/chat", methods=["POST"])
def chat():
    """执行问答，返回完整结果"""
    from web.auth import get_current_user
    from flask import session as flask_session

    token = flask_session.get("token") or request.headers.get("X-Token")
    user = get_current_user(token)
    if not user:
        return jsonify({"error": "请先登录"}), 401
    if not user.get("can_query"):
        return jsonify({"error": "您没有查询权限"}), 403

    data = request.get_json() or {}
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "请输入问题"}), 400

    try:
        from pipeline.orchestrator import Orchestrator
        orch = Orchestrator()

        # 收集 agent 日志
        agent_logs = []
        original_print = print

        def capture_print(*args, **kwargs):
            msg = " ".join(str(a) for a in args)
            agent_logs.append(msg)
            original_print(*args, **kwargs)

        import builtins
        builtins.print = capture_print
        try:
            orch.run(question)
        finally:
            builtins.print = original_print

        # 读取中间产物
        intermediates = {}
        intermediates_file = os.path.join(orch.output_dir, "intermediates.json")
        if os.path.exists(intermediates_file):
            with open(intermediates_file, "r", encoding="utf-8") as f:
                intermediates = json.load(f)

        # 读取 DSL
        dsl_data = {}
        dsl_file = os.path.join(orch.output_dir, "dsl_query.json")
        if os.path.exists(dsl_file):
            with open(dsl_file, "r", encoding="utf-8") as f:
                dsl_data = json.load(f)

        # 读取结果
        result_data = {}
        result_file = os.path.join(orch.output_dir, "result.json")
        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                result_data = json.load(f)

        llm_traces_pretty = _load_text(os.path.join(orch.output_dir, "llm_traces_pretty.md"))

        # 图表路径转为相对路径
        chart_path = result_data.get("chart_path")
        chart_url = None
        if chart_path and os.path.exists(chart_path):
            chart_url = f"/api/chart/{orch.run_id}"

        # 保存聊天历史
        chat_id = str(uuid.uuid4())[:8]
        history_entry = {
            "id": chat_id,
            "question": question,
            "answer": result_data.get("answer", ""),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "user": user["username"],
            "run_id": orch.run_id,
            "has_chart": chart_url is not None
        }
        history = _load_json(HISTORY_FILE)
        history.insert(0, history_entry)
        _save_json(HISTORY_FILE, history)

        # 保存 DSL 日志
        if dsl_data:
            dsl_log = {
                "id": str(uuid.uuid4())[:8],
                "question": question,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "user": user["username"],
                "target_entities": intermediates.get("step_01_意图澄清", {}).get("clarified_intent", {}).get("target_entities", []),
                "calc_type": intermediates.get("step_01_意图澄清", {}).get("clarified_intent", {}).get("calc_type", ""),
                "dsl": dsl_data,
                "status": "success" if result_data.get("answer") else "empty"
            }
            dsl_logs = _load_json(DSL_LOGS_FILE)
            dsl_logs.insert(0, dsl_log)
            _save_json(DSL_LOGS_FILE, dsl_logs)

        response = {
            "success": True,
            "chat_id": chat_id,
            "question": question,
            "answer": result_data.get("answer", "未获取到结果"),
            "compute_result": result_data.get("compute_result", []),
            "chart_url": chart_url,
            "dsl": dsl_data,
            "intermediates": intermediates,
            "agent_logs": agent_logs,
            "llm_traces_pretty": llm_traces_pretty,
            "run_id": orch.run_id
        }
        return jsonify(response)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"查询执行失败: {str(e)}"}), 500


@chat_bp.route("/chart/<run_id>")
def get_chart(run_id):
    """返回图表图片"""
    chart_path = os.path.join(config.OUTPUT_DIR, run_id, "chart.png")
    if os.path.exists(chart_path):
        from flask import send_file
        return send_file(chart_path, mimetype="image/png")
    return jsonify({"error": "图表不存在"}), 404


@chat_bp.route("/chat/history")
def get_history():
    from web.auth import get_current_user
    from flask import session as flask_session
    token = flask_session.get("token") or request.headers.get("X-Token")
    user = get_current_user(token)
    if not user:
        return jsonify({"error": "请先登录"}), 401
    history = _load_json(HISTORY_FILE)
    # 只返回当前用户的历史
    user_history = [h for h in history if h.get("user") == user["username"]]
    return jsonify(user_history)


@chat_bp.route("/chat/history/<chat_id>")
def get_chat_detail(chat_id):
    """获取某次对话的完整中间产物"""
    from web.auth import get_current_user
    from flask import session as flask_session

    token = flask_session.get("token") or request.headers.get("X-Token")
    user = get_current_user(token)
    if not user:
        return jsonify({"error": "请先登录"}), 401

    history = _load_json(HISTORY_FILE)
    entry = next((h for h in history if h.get("id") == chat_id and h.get("user") == user["username"]), None)
    if not entry:
        return jsonify({"error": "记录不存在"}), 404

    run_id = str(entry.get("run_id", "")).strip()
    run_dir = os.path.join(config.OUTPUT_DIR, run_id) if run_id else ""

    intermediates_file = os.path.join(run_dir, "intermediates.json") if run_dir else ""
    intermediates = {}
    if intermediates_file and os.path.exists(intermediates_file):
        with open(intermediates_file, "r", encoding="utf-8") as f:
            intermediates = json.load(f)

    result_file = os.path.join(run_dir, "result.json") if run_dir else ""
    result_data = {}
    if result_file and os.path.exists(result_file):
        with open(result_file, "r", encoding="utf-8") as f:
            result_data = json.load(f)

    dsl_file = os.path.join(run_dir, "dsl_query.json") if run_dir else ""
    dsl_data = {}
    if dsl_file and os.path.exists(dsl_file):
        with open(dsl_file, "r", encoding="utf-8") as f:
            dsl_data = json.load(f)

    chart_url = None
    chart_file = os.path.join(run_dir, "chart.png") if run_dir else ""
    if run_id and os.path.exists(chart_file):
        chart_url = f"/api/chart/{run_id}"

    llm_traces_pretty = _load_text(os.path.join(run_dir, "llm_traces_pretty.md")) if run_dir else ""
    return jsonify({
        **entry,
        "run_id": run_id,
        "answer": result_data.get("answer", entry.get("answer", "")),
        "compute_result": result_data.get("compute_result", []),
        "chart_url": chart_url,
        "has_chart": chart_url is not None,
        "dsl": dsl_data,
        "intermediates": intermediates,
        "llm_traces_pretty": llm_traces_pretty,
    })


@chat_bp.route("/download/dsl/<run_id>")
def download_dsl(run_id):
    """下载 DSL 文件"""
    from flask import send_file
    dsl_path = os.path.join(config.OUTPUT_DIR, run_id, "dsl_query.json")
    if os.path.exists(dsl_path):
        return send_file(dsl_path, as_attachment=True, download_name=f"dsl_{run_id}.json")
    return jsonify({"error": "DSL 文件不存在"}), 404


@chat_bp.route("/download/result/<run_id>")
def download_result(run_id):
    """下载结果文件"""
    from flask import send_file
    result_path = os.path.join(config.OUTPUT_DIR, run_id, "result.json")
    if os.path.exists(result_path):
        return send_file(result_path, as_attachment=True, download_name=f"result_{run_id}.json")
    return jsonify({"error": "结果文件不存在"}), 404
