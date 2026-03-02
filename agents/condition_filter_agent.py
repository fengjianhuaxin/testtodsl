"""Condition filter agent (Step B) - normalize and structure where conditions."""
import ast

from agents.base_agent import BaseAgent


class ConditionFilterAgent(BaseAgent):
    OP_ALIASES = {
        "eq": "=",
        "equals": "=",
        "==": "=",
        "=": "=",
        "ne": "!=",
        "!=": "!=",
        "<>": "!=",
        "gt": ">",
        ">": ">",
        "lt": "<",
        "<": "<",
        "ge": ">=",
        ">=": ">=",
        "le": "<=",
        "<=": "<=",
        "contains": "contains",
        "like": "contains",
        "包含": "contains",
        "in": "in",
        "属于": "in",
    }

    def __init__(self, ontology_manager):
        super().__init__("条件筛选智能体", "应用筛选条件，过滤符合条件的对象(B步骤)")
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        conditions = intent.get("conditions", [])
        self.log(f"解析筛选条件: {len(conditions)}个条件")

        structured_conditions = []
        for cond in conditions:
            entity = cond.get("entity", "")
            raw_field = cond.get("field", "")
            raw_op = cond.get("op", "=")
            raw_value = cond.get("value", "")

            op = self._normalize_operator(raw_op)
            normalized_field = raw_field
            normalized_value = self._normalize_in_value(op, raw_value)
            field_changed = False
            value_changed = False

            if entity and raw_field:
                props = self.ontology.get_entity_properties(entity)
                normalized_field = self.ontology.resolve_property_name(entity, raw_field)
                field_changed = normalized_field != raw_field

                if props and normalized_field not in props:
                    suggested = self.ontology.suggest_property_name(entity, normalized_field)
                    if suggested:
                        self.log(f"字段纠错: {entity}.{raw_field} -> {entity}.{suggested}")
                        normalized_field = suggested
                        field_changed = True
                    else:
                        self.log(f"字段不存在，忽略该条件: {entity}.{raw_field}")
                        continue

                normalized_field, normalized_value, value_changed = self.ontology.normalize_property_value(
                    entity, normalized_field, normalized_value
                )
                if value_changed:
                    self.log(
                        f"值别名归一化: {entity}.{normalized_field} "
                        f"{raw_value!r} -> {normalized_value!r}"
                    )

            if self._should_use_exact_area_match(normalized_field, op, normalized_value):
                self.log(
                    f"区划条件收敛: {entity}.{normalized_field} contains -> = {normalized_value!r}"
                )
                op = "="

            structured_conditions.append({
                "entity": entity,
                "field": normalized_field,
                "op": op,
                "value": normalized_value,
                "raw_field": raw_field,
                "raw_value": raw_value,
                "normalized": field_changed or value_changed or (str(raw_op) != str(op)),
                "description": f"{entity}.{normalized_field} {op} {normalized_value}",
            })

        self.log(f"条件解析完成: {[c['description'] for c in structured_conditions]}")
        return {**input_data, "conditions": structured_conditions}

    def _normalize_operator(self, op) -> str:
        key = str(op or "=").strip().lower()
        return self.OP_ALIASES.get(key, "=")

    def _normalize_in_value(self, op, value):
        if op != "in":
            return value
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if not isinstance(value, str):
            return value

        text = value.strip()
        if not text:
            return value

        if (text.startswith("[") and text.endswith("]")) or (text.startswith("(") and text.endswith(")")):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, (list, tuple, set)):
                    return list(parsed)
            except Exception:
                pass

        if "," in text or "，" in text:
            sep = "," if "," in text else "，"
            parts = [part.strip() for part in text.split(sep)]
            return [part for part in parts if part]

        return value

    @staticmethod
    def _should_use_exact_area_match(field: str, op: str, value) -> bool:
        if str(op).lower() != "contains":
            return False
        if not isinstance(value, str):
            return False

        field_upper = str(field or "").strip().upper()
        if field_upper not in {"AREACODE", "AREA_CODE", "PROVINCE", "CITY", "COUNTY"}:
            return False

        text = value.strip()
        if not text:
            return False
        if any(token in text for token in ("%", "*", ",", "，", "或", "/")):
            return False
        return True
