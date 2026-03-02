"""Calc method agent (Step C)."""
from agents.base_agent import BaseAgent


class CalcMethodAgent(BaseAgent):
    BOOLEAN_RATE_TRUE_VALUES = ["\u662f", "1", "true", "TRUE", "Y", "y", "yes", "YES"]
    BOOLEAN_RATE_FALSE_VALUES = ["\u5426", "0", "false", "FALSE", "N", "n", "no", "NO"]

    def __init__(self, mapping_manager=None):
        super().__init__(
            "\u8ba1\u7b97\u65b9\u6cd5\u667a\u80fd\u4f53",
            "\u786e\u5b9a\u8ba1\u7b97\u89c4\u5219\uff08\u660e\u7ec6/\u8ba1\u6570/\u6c42\u548c/\u5e73\u5747\u7b49\uff09(C\u6b65\u9aa4)",
        )
        self.mapping = mapping_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data.get("clarified_intent", {})
        calc_type = str(intent.get("calc_type", "detail")).strip().lower() or "detail"
        calc_params = dict(intent.get("calc_params", {}))
        raw_question = str(input_data.get("raw_question") or input_data.get("question") or "").strip()
        output_fields = intent.get("output_fields", []) or []
        extracted_fields = input_data.get("extracted_fields", []) or []
        conditions = input_data.get("conditions", []) or []
        entities = intent.get("target_entities", []) or []

        group_by = calc_params.get("group_by")
        if isinstance(group_by, str):
            calc_params["group_by"] = [group_by]

        if calc_type != "custom_sql":
            rate_rule, rate_reason = self._infer_rate_rule(
                question=raw_question,
                extracted_fields=extracted_fields,
                output_fields=output_fields,
                target_entities=entities,
            )
            if rate_rule:
                self.log(f"比率规则识别: {rate_reason}")
                if calc_type != "rate":
                    self.log(f"\u89e6\u53d1\u515c\u5e95\u89c4\u5219: {rate_reason}")
                    self.log(
                        f"\u6839\u636e\u515c\u5e95\u89c4\u5219\u4fee\u6b63\u8ba1\u7b97\u7c7b\u578b: {calc_type} -> rate"
                    )
                calc_type = "rate"
                calc_params.update(rate_rule)

        if calc_type == "rate":
            enhanced = self._enhance_rate_params(
                input_data=input_data,
                calc_params=calc_params,
                extracted_fields=extracted_fields,
                output_fields=output_fields,
                target_entities=entities,
                question=raw_question,
            )
            if enhanced:
                calc_params.update(enhanced)
            self._log_rate_params(calc_params, source=enhanced.get("rate_true_values_source", ""))

        if calc_type == "detail":
            inferred_type, inferred_reason = self._infer_calc_type(raw_question, output_fields)
            if inferred_type != "detail":
                self.log(f"\u89e6\u53d1\u515c\u5e95\u89c4\u5219: {inferred_reason}")
                self.log(f"\u6839\u636e\u515c\u5e95\u89c4\u5219\u4fee\u6b63\u8ba1\u7b97\u7c7b\u578b: detail -> {inferred_type}")
                calc_type = inferred_type

        calc_rule = {
            "type": calc_type,
            "params": calc_params,
            "description": self._describe(calc_type, calc_params),
        }
        query_spec = self._build_query_spec(
            entities=entities,
            conditions=conditions,
            extracted_fields=extracted_fields,
            output_fields=output_fields,
            calc_type=calc_type,
            calc_params=calc_params,
            raw_question=raw_question,
        )

        self.log(f"\u786e\u5b9a\u8ba1\u7b97\u65b9\u6cd5: {calc_type}")
        self.log(f"\u8ba1\u7b97\u89c4\u5219: {calc_rule['description']}")
        return {**input_data, "calc_rule": calc_rule, "query_spec": query_spec}

    def _infer_rate_rule(
        self,
        question: str,
        extracted_fields: list,
        output_fields: list,
        target_entities: list,
    ) -> tuple[dict, str] | tuple[None, str]:
        text = str(question or "")
        rate_keywords = ("\u7387", "\u5360\u6bd4", "\u6bd4\u4f8b", "\u6bd4\u7387")
        if not any(keyword in text for keyword in rate_keywords):
            return None, "\u672a\u547d\u4e2d\u201c\u7387\u201d\u7c7b\u5173\u952e\u8bcd"

        candidates = []
        default_entity = str(target_entities[0]).strip().upper() if target_entities else ""
        for field in extracted_fields or []:
            if isinstance(field, dict):
                candidates.append({
                    "field": field.get("field", ""),
                    "label": field.get("label", ""),
                    "type": field.get("type", "string"),
                    "entity": str(field.get("entity", "")).strip().upper() or default_entity,
                })
        for field in output_fields or []:
            if not isinstance(field, dict):
                continue
            candidates.append({
                "field": field.get("field", ""),
                "label": field.get("label", ""),
                "type": field.get("type", "string"),
                "entity": str(field.get("entity", "")).strip().upper() or default_entity,
            })

        target_field = None
        target_entity = default_entity
        primary_candidates = []
        for item in candidates:
            field_name = str(item.get("field", "")).strip()
            label = str(item.get("label", "")).strip()
            field_type = str(item.get("type", "")).strip().lower()
            if field_name:
                primary_candidates.append((field_name, str(item.get("entity", "")).strip().upper() or default_entity, label))
            if self._is_boolean_like_field(field_name, label, field_type):
                target_field = field_name
                target_entity = str(item.get("entity", "")).strip().upper() or default_entity
                break

        if not target_field:
            # For enum-like rate fields (e.g. OPEN_TYPE), try semantic keyword match first.
            text_l = text.lower()
            enum_keywords = ("开放", "在线", "上线", "启用", "完成", "通过", "有效", "正常")
            for field_name, entity_name, label in primary_candidates:
                haystack = f"{field_name} {label}".lower()
                if any(token in text for token in enum_keywords) and any(token in haystack for token in enum_keywords):
                    target_field = field_name
                    target_entity = entity_name
                    break

        if not target_field and primary_candidates:
            target_field = primary_candidates[0][0]
            target_entity = primary_candidates[0][1]

        if not target_field:
            return None, "\u7387\u7c7b\u95ee\u53e5\u4f46\u672a\u80fd\u8bc6\u522b\u76ee\u6807\u5b57\u6bb5"

        return {
            "rate_field": target_field,
            "metric_alias": "rate_value",
            "metric_label": "\u6bd4\u7387",
        }, f"\u95ee\u53e5\u5305\u542b\u201c\u7387\u201d\u5173\u952e\u8bcd\uff0c\u5e76\u8bc6\u522b\u5230\u6307\u6807\u5b57\u6bb5 {target_field}"

    @staticmethod
    def _is_boolean_like_field(field_name: str, label: str, field_type: str) -> bool:
        name = str(field_name or "").strip().upper()
        label_text = str(label or "")
        if name.startswith("IS_") or name.startswith("IS"):
            return True
        if any(token in label_text for token in ("\u662f\u5426", "\u662f\u4e0d\u662f", "\u53ef\u5426", "\u5df2\u5426")):
            return True
        if field_type == "boolean":
            return True
        return False

    def _enhance_rate_params(
        self,
        input_data: dict,
        calc_params: dict,
        extracted_fields: list,
        output_fields: list,
        target_entities: list,
        question: str = "",
    ) -> dict:
        if not isinstance(calc_params, dict):
            return {}

        result = {}
        rate_field = str(calc_params.get("rate_field", "")).strip()
        default_entity = str(target_entities[0]).strip().upper() if target_entities else ""
        candidates = []

        for item in extracted_fields or []:
            if not isinstance(item, dict):
                continue
            candidates.append({
                "entity": str(item.get("entity", "")).strip().upper() or default_entity,
                "field": str(item.get("field", "")).strip(),
                "label": str(item.get("label", "")).strip(),
                "type": str(item.get("type", "string")).strip().lower(),
            })
        for item in output_fields or []:
            if not isinstance(item, dict):
                continue
            candidates.append({
                "entity": str(item.get("entity", "")).strip().upper() or default_entity,
                "field": str(item.get("field", "")).strip(),
                "label": str(item.get("label", "")).strip(),
                "type": str(item.get("type", "string")).strip().lower(),
            })

        if not rate_field:
            for item in candidates:
                field_name = item.get("field", "")
                if not field_name:
                    continue
                if self._is_boolean_like_field(field_name, item.get("label", ""), item.get("type", "string")):
                    rate_field = field_name
                    break
            if not rate_field and candidates:
                rate_field = candidates[0].get("field", "")
            if rate_field:
                result["rate_field"] = rate_field

        if not rate_field:
            return result

        candidate = None
        rate_upper = rate_field.upper()
        for item in candidates:
            if str(item.get("field", "")).strip().upper() == rate_upper:
                candidate = item
                break
        if not candidate:
            candidate = {"entity": default_entity, "field": rate_field, "label": "", "type": "string"}

        current_true_values = calc_params.get("rate_true_values", [])
        has_true_values = isinstance(current_true_values, list) and len(current_true_values) > 0
        if has_true_values:
            result["rate_true_values_source"] = "model_specified"
            return result

        source_ids = self._infer_source_ids(input_data)
        semantics = self._get_consistent_rate_semantics(
            source_ids=source_ids,
            entity=str(candidate.get("entity", "")).strip().upper() or default_entity,
            field=rate_field,
        )
        semantic_true_values = self._pick_rate_true_values_from_semantics(
            source_ids=source_ids,
            entity=str(candidate.get("entity", "")).strip().upper() or default_entity,
            field=rate_field,
        )
        if semantic_true_values:
            final_values = semantic_true_values
            source = "data_semantics_closed_set"
            polarity_mode = self._detect_rate_polarity(question)
            if polarity_mode == "negative" and isinstance(semantics, dict):
                closed_set = [
                    str(value).strip()
                    for value in semantics.get("closed_set", [])
                    if str(value).strip()
                ]
                complement = [value for value in closed_set if value not in semantic_true_values]
                if complement:
                    final_values = complement
                    source = "data_semantics_closed_set_complement"
                    self.log(
                        f"rate polarity=negative, use closed-set complement: "
                        f"{candidate.get('entity', '')}.{rate_field} -> {final_values}"
                    )
                else:
                    self.log(
                        f"rate polarity=negative but complement empty, keep positive set: "
                        f"{candidate.get('entity', '')}.{rate_field} -> {semantic_true_values}"
                    )

            result["rate_true_values"] = final_values
            result["rate_true_values_source"] = source
            self.log(
                f"rate true-values from data semantics: {candidate.get('entity', '')}.{rate_field} -> {final_values}"
            )
            return result

        if self._is_boolean_like_field(
            rate_field,
            candidate.get("label", ""),
            candidate.get("type", "string"),
        ):
            polarity_mode = self._detect_rate_polarity(question)
            if polarity_mode == "negative":
                result["rate_true_values"] = list(self.BOOLEAN_RATE_FALSE_VALUES)
                result["rate_true_values_source"] = "boolean_negative_fallback"
            else:
                result["rate_true_values"] = list(self.BOOLEAN_RATE_TRUE_VALUES)
                result["rate_true_values_source"] = "boolean_fallback"
            self.log(
                f"rate true-values fallback: {candidate.get('entity', '')}.{rate_field} -> {result['rate_true_values']}"
            )

        return result

    def _infer_source_ids(self, input_data: dict) -> list:
        dispatch = input_data.get("dispatch", {}) if isinstance(input_data, dict) else {}
        data_sources = dispatch.get("data_sources", []) if isinstance(dispatch, dict) else []
        source_ids = [str(item).strip() for item in data_sources if str(item).strip()]
        if source_ids:
            return source_ids

        preferred = str(input_data.get("preferred_source", "")).strip() if isinstance(input_data, dict) else ""
        if preferred:
            return [preferred]

        intent = input_data.get("clarified_intent", {}) if isinstance(input_data, dict) else {}
        source_id = str(intent.get("data_source", "")).strip() if isinstance(intent, dict) else ""
        if source_id and source_id != "all":
            return [source_id]

        available_sources = input_data.get("available_sources", {}) if isinstance(input_data, dict) else {}
        if isinstance(available_sources, dict) and available_sources:
            return [next(iter(available_sources.keys()))]
        return []

    def _get_consistent_rate_semantics(self, source_ids: list, entity: str, field: str) -> dict | None:
        if not self.mapping or not source_ids or not entity or not field:
            return None

        semantics_list = []
        for source_id in source_ids:
            semantics = self.mapping.get_field_value_semantics(source_id, entity, field)
            if isinstance(semantics, dict) and isinstance(semantics.get("closed_set"), list) and semantics.get("closed_set"):
                semantics_list.append(semantics)

        if not semantics_list:
            return None

        import json

        baseline = json.dumps(semantics_list[0], ensure_ascii=False, sort_keys=True)
        for item in semantics_list[1:]:
            if json.dumps(item, ensure_ascii=False, sort_keys=True) != baseline:
                self.log(f"rate field closed-set differs across sources, skip: {entity}.{field}")
                return None

        return semantics_list[0]

    def _pick_rate_true_values_from_semantics(self, source_ids: list, entity: str, field: str) -> list:
        semantics = self._get_consistent_rate_semantics(source_ids, entity, field)
        if not isinstance(semantics, dict):
            return []
        closed_set = [str(v).strip() for v in semantics.get("closed_set", []) if str(v).strip()]
        if not closed_set:
            return []

        explicit_rate_values = semantics.get("rate_true_values", [])
        if isinstance(explicit_rate_values, str):
            explicit_rate_values = [part.strip() for part in explicit_rate_values.split(",") if part.strip()]
        if isinstance(explicit_rate_values, list) and explicit_rate_values:
            normalized = []
            for item in explicit_rate_values:
                text = str(item).strip()
                if text and text in closed_set and text not in normalized:
                    normalized.append(text)
            if normalized:
                return normalized

        positive_tokens = ("\u662f", "true", "yes", "y", "\u7ebf\u4e0a", "\u5728\u7ebf", "\u542f\u7528", "\u6709\u6548", "\u5df2")

        for preferred in ("\u662f", "1", "Y", "y", "true", "TRUE", "yes", "YES"):
            if preferred in closed_set:
                return [preferred]

        labels = semantics.get("labels", {})
        if isinstance(labels, dict):
            for value in closed_set:
                label_text = str(labels.get(value, "")).strip().lower()
                if label_text and any(token in label_text for token in positive_tokens):
                    return [value]

        aliases = semantics.get("aliases", {})
        if isinstance(aliases, dict):
            score = {}
            for alias, mapped in aliases.items():
                target = str(mapped).strip()
                if target not in closed_set:
                    continue
                alias_text = str(alias).strip().lower()
                if not alias_text:
                    continue
                if any(token in alias_text for token in positive_tokens):
                    score[target] = score.get(target, 0) + 1
            if score:
                return [max(score.items(), key=lambda x: x[1])[0]]

        return []

    @staticmethod
    def _detect_rate_polarity(question: str) -> str:
        text = str(question or "")
        if not text:
            return "positive"

        negative_phrases = (
            "\u4e0d\u5171\u4eab", "\u672a\u5171\u4eab",
            "\u4e0d\u5f00\u653e", "\u672a\u5f00\u653e",
            "\u4e0d\u66f4\u65b0", "\u672a\u66f4\u65b0",
            "\u4e0d\u6309\u65f6", "\u672a\u6309\u65f6",
            "\u4e0d\u5728\u7ebf", "\u79bb\u7ebf",
            "\u4e0d\u542f\u7528", "\u672a\u542f\u7528",
            "\u4e0d\u901a\u8fc7", "\u672a\u901a\u8fc7",
            "\u4e0d\u5408\u683c", "\u4e0d\u6b63\u5e38", "\u65e0\u6548", "\u5931\u8d25", "\u5f02\u5e38",
        )
        if any(token in text for token in negative_phrases):
            return "negative"

        negative_prefixes = ("\u4e0d", "\u672a", "\u65e0", "\u975e", "\u5426")
        polarity_targets = (
            "\u5171\u4eab", "\u5f00\u653e", "\u66f4\u65b0", "\u6309\u65f6",
            "\u5728\u7ebf", "\u542f\u7528", "\u901a\u8fc7", "\u5b8c\u6210",
            "\u53ef\u7528", "\u6b63\u5e38", "\u5408\u89c4", "\u8fbe\u6807",
        )
        for idx, ch in enumerate(text):
            if ch not in negative_prefixes:
                continue
            window = text[idx + 1: idx + 6]
            if any(token in window for token in polarity_targets):
                return "negative"

        return "positive"

    def _log_rate_params(self, calc_params: dict, source: str = ""):
        if not isinstance(calc_params, dict):
            return

        rate_field = str(calc_params.get("rate_field", "")).strip()
        true_values = calc_params.get("rate_true_values", [])
        if isinstance(true_values, str):
            true_values = [item.strip() for item in true_values.split(",") if item.strip()]
        if not isinstance(true_values, list):
            true_values = []

        resolved_source = str(source or "").strip()
        if not resolved_source:
            if true_values == list(self.BOOLEAN_RATE_TRUE_VALUES):
                resolved_source = "boolean_fallback"
            elif true_values:
                resolved_source = "model_specified"
            else:
                resolved_source = "unset"

        self.log(f"比率参数明细: rate_field={rate_field or '-'}, true_values={true_values}, 来源={resolved_source}")

    def _infer_calc_type(self, question: str, output_fields: list) -> tuple[str, str]:
        text = str(question or "")

        def has_any(keywords: tuple[str, ...]) -> bool:
            return any(keyword in text for keyword in keywords)

        detail_hints = (
            "\u660e\u7ec6", "\u5217\u8868", "\u9010\u6761", "\u6bcf\u4e2a",
            "\u5206\u522b", "\u6709\u54ea\u4e9b", "\u5217\u51fa",
        )
        if has_any(detail_hints):
            return "detail", "\u95ee\u53e5\u5305\u542b\u660e\u7ec6\u5173\u952e\u8bcd"

        if has_any(("\u5e73\u5747", "\u5747\u503c", "\u5e73\u5747\u503c")):
            return "avg", "\u95ee\u53e5\u5305\u542b\u5e73\u5747\u5173\u952e\u8bcd"
        if has_any(("\u6700\u5927", "\u6700\u9ad8", "\u5cf0\u503c")):
            return "max", "\u95ee\u53e5\u5305\u542b\u6700\u5927\u503c\u5173\u952e\u8bcd"
        if has_any(("\u6700\u5c0f", "\u6700\u4f4e")):
            return "min", "\u95ee\u53e5\u5305\u542b\u6700\u5c0f\u503c\u5173\u952e\u8bcd"

        count_keywords = (
            "\u6570\u91cf", "\u4e2a\u6570", "\u591a\u5c11\u4e2a", "\u4eba\u6570",
            "\u51e0\u5bb6", "\u51e0\u6761", "\u51e0\u9879",
        )
        if has_any(count_keywords):
            return "count", "\u95ee\u53e5\u5305\u542b\u8ba1\u6570\u5173\u952e\u8bcd"

        sum_keywords = (
            "\u603b", "\u5408\u8ba1", "\u603b\u8ba1", "\u603b\u989d", "\u603b\u5171",
            "\u6c47\u603b", "\u7d2f\u8ba1", "\u603b\u91cf", "\u8d39\u7528", "\u91d1\u989d",
            "\u7ecf\u8d39", "\u6295\u5165", "\u6210\u672c", "\u9884\u7b97", "\u652f\u51fa",
            "\u6295\u8d44", "\u4f7f\u7528\u91cf", "\u7528\u91cf", "\u5173\u8054\u6570",
        )
        if has_any(sum_keywords) and output_fields:
            return "sum", "\u95ee\u53e5\u5305\u542b\u6c42\u548c\u5173\u952e\u8bcd"

        aggregate_scope_keywords = (
            "\u5168\u7701", "\u5168\u5e02", "\u5168\u53bf", "\u5168\u533a",
            "\u603b", "\u5408\u8ba1", "\u6c47\u603b", "\u4f7f\u7528\u91cf",
            "\u7528\u91cf", "\u5173\u8054\u6570", "\u603b\u91cf",
        )
        for field in output_fields:
            field_name = str(field.get("field", "")).upper()
            if any(token in field_name for token in ("COST", "AMOUNT", "FEE", "BUDGET", "EXPENSE", "COUNT", "TOTAL")):
                return "sum", f"\u8f93\u51fa\u5b57\u6bb5 {field_name} \u547d\u4e2d\u805a\u5408\u5b57\u6bb5\u515c\u5e95"
            if any(token in field_name for token in ("_USE", "USAGE", "CPU_USE", "MEM_USE", "DISK_USE")):
                return "sum", f"\u8f93\u51fa\u5b57\u6bb5 {field_name} \u547d\u4e2d\u8d44\u6e90\u7528\u91cf\u805a\u5408\u515c\u5e95"

        return "detail", "\u672a\u547d\u4e2d\u515c\u5e95\u6761\u4ef6"

    def _describe(self, calc_type: str, params: dict) -> str:
        desc_map = {
            "detail": "\u8fd4\u56de\u660e\u7ec6\u5217\u8868",
            "count": "\u7edf\u8ba1\u6570\u91cf",
            "sum": "\u6c42\u548c",
            "avg": "\u8ba1\u7b97\u5e73\u5747\u503c",
            "rate": "\u8ba1\u7b97\u5360\u6bd4/\u6bd4\u7387",
            "max": "\u53d6\u6700\u5927\u503c",
            "min": "\u53d6\u6700\u5c0f\u503c",
            "group_count": "\u6309\u5206\u7ec4\u7edf\u8ba1\u6570\u91cf",
            "topn": "\u53d6\u6392\u540d\u524dN",
            "custom_sql": "\u4f7f\u7528\u9884\u5b9a\u4e49SQL",
        }
        desc = desc_map.get(calc_type, f"\u81ea\u5b9a\u4e49\u8ba1\u7b97: {calc_type}")
        if params.get("group_by"):
            desc += f"\uff0c\u6309 {params['group_by']} \u5206\u7ec4"
        if params.get("order_by"):
            desc += f"\uff0c\u6309 {params['order_by']} \u6392\u5e8f"
        if params.get("limit"):
            desc += f"\uff0c\u53d6\u524d {params['limit']} \u6761"
        return desc

    def _build_query_spec(
        self,
        entities: list,
        conditions: list,
        extracted_fields: list,
        output_fields: list,
        calc_type: str,
        calc_params: dict,
        raw_question: str = "",
    ) -> dict:
        entity_names = [str(item).strip() for item in entities if str(item).strip()]
        dimensions = self._normalize_group_by(calc_params.get("group_by", []))
        sort = self._build_sort_spec(calc_params)
        limit = self._safe_int(calc_params.get("limit"))
        filters = [
            {
                "entity": cond.get("entity", ""),
                "field": cond.get("field", ""),
                "op": cond.get("op", "="),
                "value": cond.get("value", ""),
            }
            for cond in (conditions or [])
            if isinstance(cond, dict) and str(cond.get("field", "")).strip()
        ]

        if calc_type == "custom_sql":
            return {
                "query_mode": "custom_sql",
                "entities": entity_names,
                "filters": filters,
                "dimensions": dimensions,
                "measures": [],
                "sort": sort,
                "limit": limit,
            }

        if calc_type == "detail":
            return {
                "query_mode": "detail",
                "entities": entity_names,
                "filters": filters,
                "dimensions": dimensions,
                "measures": [],
                "sort": sort,
                "limit": limit,
            }

        if calc_type == "topn" and not dimensions:
            return {
                "query_mode": "detail",
                "entities": entity_names,
                "filters": filters,
                "dimensions": [],
                "measures": [],
                "sort": sort,
                "limit": limit,
            }

        measures = self._build_measures(
            calc_type=calc_type,
            calc_params=calc_params,
            extracted_fields=extracted_fields,
            output_fields=output_fields,
            dimensions=dimensions,
            raw_question=raw_question,
        )
        return {
            "query_mode": "aggregate",
            "entities": entity_names,
            "filters": filters,
            "dimensions": dimensions,
            "measures": measures,
            "sort": sort,
            "limit": limit,
        }

    def _build_measures(
        self,
        calc_type: str,
        calc_params: dict,
        extracted_fields: list,
        output_fields: list,
        dimensions: list,
        raw_question: str = "",
    ) -> list:
        agg_type = str(calc_type or "").strip().lower()
        if agg_type != "group_count":
            if agg_type == "sum":
                numeric_targets = self._pick_numeric_target_fields(extracted_fields, output_fields, dimensions)
                if not numeric_targets:
                    return []
                if self._question_requires_multi_sum(raw_question) and len(numeric_targets) > 1:
                    self.log(f"多指标求和: 命中字段 {numeric_targets}")
                    measures = [{"agg": "sum", "field": numeric_targets[0], "alias": "sum_value"}]
                    for field_name in numeric_targets[1:]:
                        measures.append({"agg": "sum", "field": field_name, "alias": field_name})
                    return measures
            measure = self._build_measure(calc_type, calc_params, extracted_fields, output_fields, dimensions)
            return [measure] if measure else []

        measures = []
        include_count = self._question_mentions_count(raw_question)
        numeric_target = self._pick_numeric_target_field(extracted_fields, output_fields, dimensions)
        if include_count or not numeric_target:
            measures.append({"agg": "count", "field": "*", "alias": "count_value"})
        if numeric_target:
            if include_count:
                self.log(
                    f"多指标构建: group_count 同时输出 COUNT(*) 与 SUM({numeric_target})"
                )
            else:
                self.log(
                    f"兼容映射: legacy calc_type={agg_type} 但输出含数值指标 {numeric_target}，"
                    "query_spec 按 SUM 指标构建"
                )
            measures.append({"agg": "sum", "field": numeric_target, "alias": "sum_value"})
        return measures

    @staticmethod
    def _normalize_group_by(group_by) -> list:
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            return []
        items = []
        seen = set()
        for item in group_by:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        return items

    def _build_sort_spec(self, calc_params: dict) -> list:
        order_by = str(calc_params.get("order_by", "")).strip()
        if not order_by:
            return []
        order_dir = str(calc_params.get("order_dir", "desc")).strip().lower()
        if order_dir not in ("asc", "desc"):
            order_dir = "desc"
        return [{"by": order_by, "dir": order_dir}]

    def _build_measure(
        self,
        calc_type: str,
        calc_params: dict,
        extracted_fields: list,
        output_fields: list,
        dimensions: list,
    ):
        agg_type = str(calc_type or "").strip().lower()
        alias_map = {
            "count": "count_value",
            "group_count": "count_value",
            "sum": "sum_value",
            "avg": "average_value",
            "max": "max_value",
            "min": "min_value",
            "rate": str(calc_params.get("metric_alias", "rate_value")).strip() or "rate_value",
            "topn": "metric_value",
        }

        if agg_type in ("count", "group_count"):
            numeric_target = self._pick_numeric_target_field(extracted_fields, output_fields, dimensions)
            if numeric_target:
                return {"agg": "sum", "field": numeric_target, "alias": alias_map["sum"]}
            return {"agg": "count", "field": "*", "alias": alias_map[agg_type]}

        if agg_type == "rate":
            rate_field = str(calc_params.get("rate_field", "")).strip()
            return {
                "agg": "rate",
                "field": rate_field,
                "alias": alias_map["rate"],
                "options": {
                    "true_values": calc_params.get("rate_true_values", []),
                },
            }

        if agg_type in ("sum", "avg", "max", "min", "topn"):
            target_field = self._pick_numeric_target_field(extracted_fields, output_fields, dimensions)
            if not target_field:
                return None
            return {"agg": "sum" if agg_type == "topn" else agg_type, "field": target_field, "alias": alias_map[agg_type]}
        return None

    @staticmethod
    def _question_mentions_count(question: str) -> bool:
        text = str(question or "")
        count_keywords = (
            "数量", "个数", "多少", "几条", "几项", "几家", "多少个", "有多少", "分布",
        )
        return any(keyword in text for keyword in count_keywords)

    @staticmethod
    def _safe_int(value):
        try:
            if value is None:
                return None
            number = int(value)
            return number if number > 0 else None
        except Exception:
            return None

    def _pick_numeric_target_field(self, extracted_fields: list, output_fields: list, dimensions: list | None = None) -> str:
        fields = self._pick_numeric_target_fields(extracted_fields, output_fields, dimensions)
        return fields[0] if fields else ""

    def _pick_numeric_target_fields(self, extracted_fields: list, output_fields: list, dimensions: list | None = None) -> list:
        candidates = []
        dimension_names = set()
        for dim in dimensions or []:
            dim_text = str(dim).strip()
            if not dim_text:
                continue
            if "." in dim_text:
                dim_text = dim_text.rsplit(".", 1)[1].strip()
            if dim_text:
                dimension_names.add(dim_text.upper())

        for item in extracted_fields or []:
            if isinstance(item, dict):
                candidates.append(item)
        for item in output_fields or []:
            if isinstance(item, dict):
                candidates.append(item)

        result = []
        seen = set()
        for item in candidates:
            field_name = str(item.get("field", "")).strip()
            field_type = str(item.get("type", "")).strip().lower()
            if not field_name:
                continue
            if field_name.upper() in dimension_names:
                continue
            field_upper = field_name.upper()
            if field_type == "number":
                if field_upper not in seen:
                    seen.add(field_upper)
                    result.append(field_name)
                continue
            if any(token in field_upper for token in ("COST", "AMOUNT", "FEE", "BUDGET", "EXPENSE", "TOTAL", "_USE", "USAGE", "COUNT", "NUM")):
                if field_upper not in seen:
                    seen.add(field_upper)
                    result.append(field_name)
        return result

    @staticmethod
    def _question_requires_multi_sum(question: str) -> bool:
        text = str(question or "")
        explicit_tokens = ("分别", "各自")
        return any(token in text for token in explicit_tokens)
