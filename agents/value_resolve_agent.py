"""Value resolve agent - normalize condition values in second stage."""
import json

from agents.base_agent import BaseAgent


class ValueResolveAgent(BaseAgent):
    """Resolve condition values using data-layer semantics and LLM."""

    TEXT_OPS = {"=", "!=", "contains", "in"}
    STRICT_ALIAS_FIELDS = {"AREACODE", "AREA_CODE", "PROVINCE", "CITY", "COUNTY", "REGION"}

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
        source_id = str(dispatch.get("source_id", "")).strip() if isinstance(dispatch, dict) else ""
        if source_id:
            return [source_id]

        source_id = str(input_data.get("source_id", "")).strip()
        if source_id:
            return [source_id]
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
        if semantics:
            resolved_value, meta = self._resolve_with_semantics(
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
            if not isinstance(semantics, dict):
                continue
            has_closed_set = bool(semantics.get("closed_set"))
            has_aliases = isinstance(semantics.get("aliases", {}), dict) and bool(semantics.get("aliases", {}))
            if has_closed_set or has_aliases:
                found.append(semantics)

        if not found:
            return None

        if len(found) == 1:
            return found[0]

        baseline = json.dumps(found[0], ensure_ascii=False, sort_keys=True)
        for item in found[1:]:
            if json.dumps(item, ensure_ascii=False, sort_keys=True) != baseline:
                self.log(f"检测到多数据源值语义不一致，跳过语义约束: {entity}.{field}")
                return None
        return found[0]

    def _resolve_with_semantics(self, question: str, entity: str, field: str, op: str, value, semantics: dict) -> tuple[object, dict]:
        if op == "in" and isinstance(value, list):
            items = []
            changed = False
            min_conf = 1.0
            dominant_strategy = ""
            dominant_source = ""
            for one in value:
                resolved_one, meta = self._resolve_scalar_with_semantics(question, entity, field, one, semantics)
                items.append(resolved_one)
                changed = changed or bool(meta.get("changed"))
                min_conf = min(min_conf, float(meta.get("confidence", 0.0)))
                if not dominant_strategy:
                    dominant_strategy = str(meta.get("strategy", "")).strip()
                    dominant_source = str(meta.get("source", "")).strip()
            unique = []
            for item in items:
                if item not in unique:
                    unique.append(item)
            return unique, {
                "strategy": dominant_strategy or "semantics",
                "source": dominant_source or "mixed",
                "confidence": min_conf if items else 0.0,
                "changed": changed,
            }
        return self._resolve_scalar_with_semantics(question, entity, field, value, semantics)

    def _resolve_scalar_with_semantics(self, question: str, entity: str, field: str, value, semantics: dict) -> tuple[object, dict]:
        aliases = semantics.get("aliases", {})
        closed_set = semantics.get("closed_set", [])

        raw_text = str(value).strip() if not isinstance(value, str) else value.strip()
        alias_hit = self._lookup_alias(raw_text, aliases)
        if alias_hit:
            if not closed_set or alias_hit in closed_set:
                return alias_hit, {
                    "strategy": "strict_dict",
                    "source": "alias",
                    "confidence": 1.0,
                    "changed": alias_hit != value,
                }

        if closed_set:
            return self._resolve_scalar_with_closed_set(question, entity, field, value, semantics)

        if self._is_strict_alias_field(field):
            return value, {
                "strategy": "strict_dict",
                "source": "alias_miss",
                "confidence": 0.0,
                "changed": False,
            }

        return self._resolve_without_closed_set(question, entity, field, "=", value)

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
        # No closed-set semantics: keep literal user value to avoid semantic flips
        # (e.g. "!= 未婚" being rewritten as "!= 已婚").
        return value, {
            "strategy": "model_free",
            "source": "passthrough_no_semantics",
            "confidence": 0.0,
            "changed": False,
        }

    @classmethod
    def _is_strict_alias_field(cls, field: str) -> bool:
        field_upper = str(field or "").strip().upper()
        if field_upper in cls.STRICT_ALIAS_FIELDS:
            return True
        return any(token in field_upper for token in ("AREA", "PROVINCE", "CITY", "COUNTY", "REGION"))

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
            context={
                "question": question,
                "entity": entity,
                "field": field,
                "raw_value": raw_value,
                "options_text": options_text,
            },
        )
        user_message = self._render_prompt(
            key="value_resolve_closed_set_user",
            default_template=(
                "问题: $question\n"
                "字段: $entity.$field\n"
                "用户原值: $raw_value\n"
                "候选值:\n$options_text"
            ),
            context={
                "question": question,
                "entity": entity,
                "field": field,
                "raw_value": raw_value,
                "options_text": options_text,
            },
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_message)
            selected = str(parsed.get("selected_value", "")).strip()
            confidence = float(parsed.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            if selected.upper() == "UNKNOWN":
                return "", confidence
            normalized = self._normalize_closed_set_selection(selected, closed_set, labels)
            return normalized, confidence
        except Exception as exc:
            self.log(f"闭集值选择失败: {entity}.{field} -> {exc}")
            return "", 0.0

    @staticmethod
    def _normalize_closed_set_selection(selected_value: str, closed_set: list, labels: dict) -> str:
        text = str(selected_value or "").strip()
        if not text:
            return ""

        candidates = [str(item).strip() for item in (closed_set or []) if str(item).strip()]
        if not candidates:
            return ""

        # Try exact/case-insensitive match first.
        for item in candidates:
            if text == item:
                return item
        text_lower = text.lower()
        for item in candidates:
            if text_lower == item.lower():
                return item

        # Common model output: "值: 标签" 或 "- 值: 标签"
        stripped = text.strip("`\"' ").lstrip("-* ").strip()
        splits = [stripped]
        if "：" in stripped:
            splits.append(stripped.split("：", 1)[0].strip())
        if ":" in stripped:
            splits.append(stripped.split(":", 1)[0].strip())

        for part in splits:
            if not part:
                continue
            for item in candidates:
                if part == item or part.lower() == item.lower():
                    return item

        # Match by label text as fallback.
        if isinstance(labels, dict):
            for item in candidates:
                label = str(labels.get(item, "")).strip()
                if not label:
                    continue
                if stripped == label or stripped.lower() == label.lower():
                    return item
                if stripped.endswith(label):
                    return item

        return ""

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        return default_template
