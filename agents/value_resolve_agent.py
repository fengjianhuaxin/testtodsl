"""Value resolve agent - normalize condition values in second stage."""
import json

from agents.base_agent import BaseAgent


class ValueResolveAgent(BaseAgent):
    """Resolve condition values using data-layer semantics and LLM."""

    MODEL_FALLBACK_THRESHOLD = 0.6
    TEXT_OPS = {"=", "!=", "contains", "in"}

    def __init__(self, llm_client, mapping_manager, prompt_manager=None):
        super().__init__("值归一智能体", "第二段字段值归一：闭集选择或模型判断")
        self.llm = llm_client
        self.mapping = mapping_manager
        self.prompts = prompt_manager

    def run(self, input_data: dict) -> dict:
        conditions = input_data.get("conditions", [])
        if not isinstance(conditions, list) or not conditions:
            return input_data

        question = str(input_data.get("raw_question") or input_data.get("question") or "").strip()
        source_ids = self._resolve_source_ids(input_data)

        resolved_conditions = []
        traces = []

        for condition in conditions:
            if not isinstance(condition, dict):
                resolved_conditions.append(condition)
                continue

            resolved, trace = self._resolve_one_condition(
                condition=condition,
                question=question,
                source_ids=source_ids,
            )
            resolved_conditions.append(resolved)
            if trace:
                traces.append(trace)
                self.log(
                    f"值归一明细: {trace.get('entity', '')}.{trace.get('field', '')} "
                    f"{trace.get('op', '=')} {self._fmt(trace.get('original_value'))} -> "
                    f"{self._fmt(trace.get('resolved_value'))} | "
                    f"strategy={trace.get('strategy', '')}, source={trace.get('source', '')}, "
                    f"confidence={trace.get('confidence', 0.0):.2f}"
                )

        if traces:
            changed = sum(1 for item in traces if item.get("changed"))
            self.log(f"值归一完成: {len(traces)} 条条件, 变更 {changed} 条")

        return {
            **input_data,
            "conditions": resolved_conditions,
            "value_resolution": traces,
        }

    @staticmethod
    def _fmt(value) -> str:
        try:
            return repr(value)
        except Exception:
            return str(value)

    def _resolve_source_ids(self, input_data: dict) -> list:
        dispatch = input_data.get("dispatch", {})
        data_sources = dispatch.get("data_sources", []) if isinstance(dispatch, dict) else []
        source_ids = [str(item).strip() for item in data_sources if str(item).strip()]
        if source_ids:
            return source_ids

        preferred = str(input_data.get("preferred_source", "")).strip()
        if preferred:
            return [preferred]

        intent = input_data.get("clarified_intent", {})
        source_id = str(intent.get("data_source", "")).strip() if isinstance(intent, dict) else ""
        if source_id and source_id != "all":
            return [source_id]

        available_sources = input_data.get("available_sources", {})
        if isinstance(available_sources, dict) and available_sources:
            return [next(iter(available_sources.keys()))]
        return []

    def _resolve_one_condition(self, condition: dict, question: str, source_ids: list) -> tuple[dict, dict]:
        entity = str(condition.get("entity", "")).strip().upper()
        field = str(condition.get("field", "")).strip()
        op = str(condition.get("op", "=")).strip().lower() or "="
        original_value = condition.get("value")

        output_condition = dict(condition)

        if not entity or not field or op not in self.TEXT_OPS:
            return output_condition, {
                "entity": entity,
                "field": field,
                "op": op,
                "original_value": original_value,
                "resolved_value": original_value,
                "strategy": "skip",
                "changed": False,
            }

        semantics = self._pick_semantics(source_ids, entity, field)
        if semantics and semantics.get("closed_set"):
            resolved_value, meta = self._resolve_with_closed_set(
                question=question,
                entity=entity,
                field=field,
                op=op,
                value=original_value,
                semantics=semantics,
            )
        else:
            resolved_value, meta = self._resolve_without_closed_set(
                question=question,
                entity=entity,
                field=field,
                op=op,
                value=original_value,
            )

        output_condition["value"] = resolved_value
        if meta.get("changed"):
            output_condition["normalized"] = True
        if entity and field:
            output_condition["description"] = f"{entity}.{field} {op} {resolved_value}"
        output_condition["value_resolution"] = {
            "strategy": meta.get("strategy", "fallback"),
            "confidence": meta.get("confidence", 0.0),
            "source": meta.get("source", ""),
        }

        trace = {
            "entity": entity,
            "field": field,
            "op": op,
            "original_value": original_value,
            "resolved_value": resolved_value,
            "strategy": meta.get("strategy", "fallback"),
            "source": meta.get("source", ""),
            "confidence": meta.get("confidence", 0.0),
            "changed": bool(meta.get("changed")),
        }
        return output_condition, trace

    def _pick_semantics(self, source_ids: list, entity: str, field: str) -> dict | None:
        if not source_ids:
            return None

        found = []
        for source_id in source_ids:
            semantics = self.mapping.get_field_value_semantics(source_id, entity, field)
            if isinstance(semantics, dict) and semantics.get("closed_set"):
                found.append(semantics)

        if not found:
            return None

        if len(found) == 1:
            return found[0]

        baseline = json.dumps(found[0], ensure_ascii=False, sort_keys=True)
        for item in found[1:]:
            if json.dumps(item, ensure_ascii=False, sort_keys=True) != baseline:
                self.log(f"检测到多数据源值语义不一致，跳过闭集约束: {entity}.{field}")
                return None
        return found[0]

    def _resolve_with_closed_set(self, question: str, entity: str, field: str, op: str, value, semantics: dict) -> tuple[object, dict]:
        if op == "in" and isinstance(value, list):
            items = []
            changed = False
            min_conf = 1.0
            for one in value:
                resolved_one, meta = self._resolve_scalar_with_closed_set(question, entity, field, one, semantics)
                items.append(resolved_one)
                changed = changed or bool(meta.get("changed"))
                min_conf = min(min_conf, float(meta.get("confidence", 0.0)))
            unique = []
            for item in items:
                if item not in unique:
                    unique.append(item)
            return unique, {
                "strategy": "closed_set",
                "source": "mixed",
                "confidence": min_conf if items else 0.0,
                "changed": changed,
            }

        return self._resolve_scalar_with_closed_set(question, entity, field, value, semantics)

    def _resolve_scalar_with_closed_set(self, question: str, entity: str, field: str, value, semantics: dict) -> tuple[object, dict]:
        closed_set = semantics.get("closed_set", [])
        aliases = semantics.get("aliases", {})
        labels = semantics.get("labels", {})
        threshold = float(semantics.get("confidence_threshold", 0.65))

        if isinstance(value, str):
            raw_text = value.strip()
        else:
            raw_text = str(value).strip()

        if raw_text in closed_set:
            return raw_text, {
                "strategy": "closed_set",
                "source": "exact",
                "confidence": 1.0,
                "changed": raw_text != value,
            }

        alias_hit = self._lookup_alias(raw_text, aliases)
        if alias_hit and alias_hit in closed_set:
            return alias_hit, {
                "strategy": "closed_set",
                "source": "alias",
                "confidence": 1.0,
                "changed": alias_hit != value,
            }

        if self.llm:
            selected_value, confidence = self._llm_pick_closed_set(
                question=question,
                entity=entity,
                field=field,
                raw_value=raw_text,
                closed_set=closed_set,
                labels=labels,
            )
            if selected_value in closed_set and confidence >= threshold:
                return selected_value, {
                    "strategy": "closed_set",
                    "source": "llm",
                    "confidence": confidence,
                    "changed": selected_value != value,
                }

        return value, {
            "strategy": "closed_set",
            "source": "fallback",
            "confidence": 0.0,
            "changed": False,
        }

    def _resolve_without_closed_set(self, question: str, entity: str, field: str, op: str, value) -> tuple[object, dict]:
        if not self.llm:
            return value, {
                "strategy": "model_free",
                "source": "passthrough",
                "confidence": 0.0,
                "changed": False,
            }

        if op == "in" and isinstance(value, list):
            items = []
            changed = False
            min_conf = 1.0
            for one in value:
                resolved_one, meta = self._resolve_scalar_without_closed_set(question, entity, field, one)
                items.append(resolved_one)
                changed = changed or bool(meta.get("changed"))
                min_conf = min(min_conf, float(meta.get("confidence", 0.0)))
            return items, {
                "strategy": "model_free",
                "source": "llm",
                "confidence": min_conf if items else 0.0,
                "changed": changed,
            }

        return self._resolve_scalar_without_closed_set(question, entity, field, value)

    def _resolve_scalar_without_closed_set(self, question: str, entity: str, field: str, value) -> tuple[object, dict]:
        raw_text = str(value).strip()
        if not raw_text:
            return value, {
                "strategy": "model_free",
                "source": "empty",
                "confidence": 0.0,
                "changed": False,
            }

        normalized_value, confidence = self._llm_free_resolve(
            question=question,
            entity=entity,
            field=field,
            raw_value=raw_text,
        )
        if normalized_value and confidence >= self.MODEL_FALLBACK_THRESHOLD:
            return normalized_value, {
                "strategy": "model_free",
                "source": "llm",
                "confidence": confidence,
                "changed": normalized_value != value,
            }

        return value, {
            "strategy": "model_free",
            "source": "fallback",
            "confidence": confidence,
            "changed": False,
        }

    @staticmethod
    def _lookup_alias(raw_value: str, aliases: dict) -> str:
        if not isinstance(aliases, dict):
            return ""

        value_text = str(raw_value or "").strip()
        if not value_text:
            return ""

        if value_text in aliases:
            return str(aliases.get(value_text, "")).strip()

        lowered = value_text.lower()
        for alias, target in aliases.items():
            alias_text = str(alias).strip()
            if alias_text.lower() == lowered:
                return str(target).strip()
        return ""

    def _llm_pick_closed_set(self, question: str, entity: str, field: str, raw_value: str, closed_set: list, labels: dict) -> tuple[str, float]:
        options_lines = []
        for value in closed_set:
            label = str(labels.get(value, "")).strip()
            if label:
                options_lines.append(f"- {value}: {label}")
            else:
                options_lines.append(f"- {value}")
        options_text = "\n".join(options_lines)

        default_system = (
            "你是字段值归一器。"
            "你只能从给定候选值中选择一个，不能生成新值。"
            "如果无法判断，返回 UNKNOWN。"
            "输出 JSON: {\"selected_value\":\"候选值或UNKNOWN\",\"confidence\":0到1}"
        )
        system_prompt = self._render_prompt(
            key="value_resolve_closed_set_system",
            default_template=default_system,
            context={},
        )

        user_message = (
            f"问题: {question}\n"
            f"字段: {entity}.{field}\n"
            f"用户原值: {raw_value}\n"
            f"候选值:\n{options_text}"
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_message)
            selected = str(parsed.get("selected_value", "")).strip()
            confidence = float(parsed.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            if selected.upper() == "UNKNOWN":
                return "", confidence
            return selected, confidence
        except Exception as exc:
            self.log(f"闭集值选择失败: {entity}.{field} -> {exc}")
            return "", 0.0

    def _llm_free_resolve(self, question: str, entity: str, field: str, raw_value: str) -> tuple[str, float]:
        default_system = (
            "你是字段值归一器。"
            "基于问题语义，将用户值归一成更适合数据库过滤的值。"
            "如果不确定，保持原值。"
            "输出 JSON: {\"resolved_value\":\"值\",\"confidence\":0到1}"
        )
        system_prompt = self._render_prompt(
            key="value_resolve_free_system",
            default_template=default_system,
            context={},
        )
        user_message = (
            f"问题: {question}\n"
            f"字段: {entity}.{field}\n"
            f"用户原值: {raw_value}"
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_message)
            resolved = str(parsed.get("resolved_value", "")).strip()
            confidence = float(parsed.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            if not resolved:
                return "", confidence
            return resolved, confidence
        except Exception as exc:
            self.log(f"自由值归一失败: {entity}.{field} -> {exc}")
            return "", 0.0

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        return default_template
