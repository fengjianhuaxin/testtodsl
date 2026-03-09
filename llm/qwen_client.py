"""Qwen/OpenAI-compatible LLM client."""
import json
import time

from openai import OpenAI

import config


class QwenClient:
    """Thin wrapper for OpenAI-compatible chat completions."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        runtime_cfg = config.load_runtime_llm_config()
        final_api_key = str(api_key or runtime_cfg.get("api_key") or config.LLM_API_KEY).strip()
        final_base_url = str(base_url or runtime_cfg.get("base_url") or config.LLM_BASE_URL).strip()
        final_model = str(model or runtime_cfg.get("model") or config.LLM_MODEL).strip()

        self.client = OpenAI(api_key=final_api_key, base_url=final_base_url)
        self.model = final_model
        self.base_url = final_base_url
        self._traces = []

    def reset_traces(self):
        self._traces = []

    def get_traces(self) -> list:
        return list(self._traces)

    def _append_trace(self, payload: dict):
        record = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model": self.model,
            "base_url": self.base_url,
        }
        record.update(payload or {})
        self._traces.append(record)

    def chat(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.3,
        trace_meta: dict | None = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            text = (response.choices[0].message.content or "").strip()
            self._append_trace({
                "call_type": "chat",
                "temperature": temperature,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "response_text": text,
                "success": True,
                **(trace_meta or {}),
            })
            return text
        except Exception as error:
            self._append_trace({
                "call_type": "chat",
                "temperature": temperature,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "response_text": "",
                "success": False,
                "error": str(error),
                **(trace_meta or {}),
            })
            raise

    def chat_json(self, system_prompt: str, user_message: str, temperature: float = 0.1) -> dict:
        full_system = system_prompt + "\n\n请严格以 JSON 格式返回结果，不要包含 markdown 代码块标记。"
        text = self.chat(
            full_system,
            user_message,
            temperature,
            trace_meta={
                "call_type": "chat_json",
                "original_system_prompt": system_prompt,
            },
        )

        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        try:
            return json.loads(text)
        except Exception as error:
            self._append_trace({
                "call_type": "chat_json_parse",
                "temperature": temperature,
                "system_prompt": full_system,
                "user_message": user_message,
                "response_text": text,
                "success": False,
                "error": str(error),
            })
            raise
