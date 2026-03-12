"""Quality check agent - validate result integrity and obvious anomalies."""

from agents.base_agent import BaseAgent


class QualityCheckAgent(BaseAgent):
    def __init__(self):
        super().__init__("质检验证智能体", "验证计算结果的完整性和准确性")

    def run(self, input_data: dict) -> dict:
        compute_result = input_data.get("compute_result", [])
        extracted_fields = input_data.get("extracted_fields", [])
        calc_rule = input_data.get("calc_rule", {})
        self.log(f"质检验证: {len(compute_result)}条结果...")

        issues = []

        if not compute_result:
            issues.append("查询结果为空，可能是条件过严或无匹配数据")

        if compute_result:
            expected_fields = self._build_expected_fields(calc_rule, extracted_fields)
            actual_fields = set(compute_result[0].keys()) if compute_result else set()
            missing = expected_fields - actual_fields - {"_source"}
            if missing:
                issues.append(f"缺少字段: {missing}")

        for record in compute_result:
            for key, value in record.items():
                if isinstance(value, (int, float)):
                    if key in ("score", "fenshu") and (value < 0 or value > 100):
                        issues.append(f"分数值异常: {key}={value}")

        passed = len(issues) == 0
        self.log(
            f"质检{'通过' if passed else '发现问题'}: {issues if issues else '无问题'}"
        )

        return {
            **input_data,
            "quality_check": {
                "passed": passed,
                "record_count": len(compute_result),
                "issues": issues,
            },
        }

    @staticmethod
    def _build_expected_fields(calc_rule: dict, extracted_fields: list) -> set:
        field_display_map = {}
        for item in extracted_fields or []:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field", "")).strip()
            if not field_name:
                continue
            label = str(item.get("label", "")).strip()
            field_display_map[field_name.upper()] = label or field_name

        def _display_name(field_name: str) -> str:
            text = str(field_name or "").strip()
            if not text:
                return ""
            return field_display_map.get(text.upper(), text)

        expected = set()
        for item in extracted_fields or []:
            if not isinstance(item, dict):
                continue
            field_name = str(item.get("field", "")).strip()
            if field_name:
                expected.add(_display_name(field_name))

        calc_type = str((calc_rule or {}).get("type", "detail")).strip().lower()
        params = (calc_rule or {}).get("params", {})
        if not isinstance(params, dict):
            params = {}

        group_by = params.get("group_by", [])
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list):
            group_by = []
        group_fields = set()
        for item in group_by:
            text = str(item).strip()
            if not text:
                continue
            if "." in text:
                text = text.rsplit(".", 1)[1].strip()
            if text:
                group_fields.add(_display_name(text))

        agg_alias = {
            "count": set(),
            "sum": {"sum_value"},
            "avg": {"average_value"},
            "max": {"max_value"},
            "min": {"min_value"},
            "group_count": {"count_value", "count"},
            "rate": {
                str(params.get("metric_alias", "rate_value")).strip() or "rate_value"
            },
        }

        if calc_type in agg_alias:
            expected = set(group_fields) if group_fields else set()
            if calc_type == "count":
                expected.add("count_value" if group_fields else "total_count")
            else:
                expected.update(agg_alias.get(calc_type, set()))
            if calc_type in {"sum", "avg", "max", "min"}:
                group_upper = {name.upper() for name in group_fields}
                for item in extracted_fields or []:
                    if not isinstance(item, dict):
                        continue
                    field_name = str(item.get("field", "")).strip()
                    if field_name and field_name.upper() not in group_upper:
                        expected.add(_display_name(field_name))

        return expected
