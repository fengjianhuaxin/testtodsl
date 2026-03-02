"""Prompt and runtime-LLM config APIs."""
import os
import sys

from flask import Blueprint, jsonify, request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

import config
from prompts.prompt_manager import PromptManager

prompts_bp = Blueprint("prompts", __name__)


def _manager() -> PromptManager:
    return PromptManager(config.PROMPTS_FILE)


@prompts_bp.route("/prompts", methods=["GET"])
def get_prompts():
    manager = _manager()
    return jsonify(manager.list_prompts())


@prompts_bp.route("/prompts/<prompt_key>", methods=["PUT"])
def update_prompt(prompt_key):
    payload = request.get_json() or {}
    template = str(payload.get("template", "")).strip()
    name = payload.get("name")
    description = payload.get("description")

    if not template:
        return jsonify({"error": "提示词模板不能为空"}), 400

    manager = _manager()
    manager.update_prompt(
        key=prompt_key,
        template=template,
        name=str(name) if name is not None else None,
        description=str(description) if description is not None else None,
    )
    return jsonify({"success": True})


@prompts_bp.route("/prompts/<prompt_key>/reset", methods=["POST"])
def reset_prompt(prompt_key):
    manager = _manager()
    manager.reset_prompt(prompt_key)
    return jsonify({"success": True})


@prompts_bp.route("/prompts/llm-config", methods=["GET"])
def get_runtime_llm_config():
    overrides = config.get_runtime_llm_overrides()
    effective = config.load_runtime_llm_config()
    return jsonify({
        "api_key": overrides.get("api_key", ""),
        "base_url": overrides.get("base_url", ""),
        "model": overrides.get("model", ""),
        "effective": {
            "base_url": effective.get("base_url", ""),
            "model": effective.get("model", ""),
            "api_key_masked": _mask_secret(effective.get("api_key", "")),
        },
    })


@prompts_bp.route("/prompts/llm-config", methods=["PUT"])
def save_runtime_llm_config():
    payload = request.get_json() or {}
    data = {
        "api_key": str(payload.get("api_key", "")).strip(),
        "base_url": str(payload.get("base_url", "")).strip(),
        "model": str(payload.get("model", "")).strip(),
    }
    config.save_runtime_llm_config(data)
    return jsonify({"success": True})


@prompts_bp.route("/prompts/llm-config/reset", methods=["POST"])
def reset_runtime_llm_config():
    config.save_runtime_llm_config({})
    return jsonify({"success": True})


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}{'*' * (len(text) - 8)}{text[-4:]}"
