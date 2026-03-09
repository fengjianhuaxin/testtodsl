"""Question split agent: prefer LLM planning with deterministic rule fallback."""
import re

from agents.base_agent import BaseAgent


class QuestionSplitAgent(BaseAgent):
    def __init__(self, llm_client=None, prompt_manager=None):
        super().__init__("\u95ee\u9898\u62c6\u89e3\u667a\u80fd\u4f53", "\u8bc6\u522b\u662f\u5426\u5305\u542b\u591a\u4e2a\u72ec\u7acb\u67e5\u8be2\u76ee\u6807\uff0c\u5e76\u6267\u884c\u4fdd\u5b88\u62c6\u89e3")
        self.llm = llm_client
        self.prompts = prompt_manager

    def run(self, input_data: dict) -> dict:
        question = str(input_data.get("question", "")).strip()
        if not question:
            analysis = self._build_analysis(False, "empty_question", 0.0, [question], execution_mode="single_sql")
            return {**input_data, "split_analysis": analysis}

        split_result = self._plan_with_llm(question) if self.llm else None
        if not split_result:
            split_result = self._split_question(question)

        planned_tasks = split_result.get("tasks", [])
        sub_questions = split_result.get("sub_questions", [question])
        enabled = len(planned_tasks) > 1 if isinstance(planned_tasks, list) and planned_tasks else len(sub_questions) > 1
        analysis = self._build_analysis(
            enabled=enabled,
            reason=split_result.get("reason", "single_intent"),
            confidence=split_result.get("confidence", 0.9),
            sub_questions=sub_questions if sub_questions else [question],
            tasks=planned_tasks if isinstance(planned_tasks, list) else None,
            execution_mode=split_result.get("execution_mode", "multi_sql_independent" if enabled else "single_sql"),
        )

        if enabled:
            self.log(
                f"\u547d\u4e2d\u591a\u95ee\u9898\u62c6\u89e3: {len(analysis['sub_tasks'])}\u4e2a\u5b50\u95ee\u9898, "
                f"reason={analysis.get('reason', '')}, confidence={analysis.get('confidence', 0.0):.2f}"
            )
            for idx, item in enumerate(analysis["sub_tasks"], start=1):
                dep = item.get("depends_on", [])
                dep_text = f", depends_on={dep}" if dep else ""
                self.log(f"  \u5b50\u95ee\u9898{idx}: {item['question']}{dep_text}")
        else:
            self.log("\u672a\u547d\u4e2d\u591a\u95ee\u9898\u62c6\u89e3\uff0c\u6309\u5355\u95ee\u9898\u6267\u884c")

        return {**input_data, "split_analysis": analysis}

    @staticmethod
    def _build_analysis(
        enabled: bool,
        reason: str,
        confidence: float,
        sub_questions: list,
        execution_mode: str = "single_sql",
        tasks: list | None = None,
    ) -> dict:
        normalized_tasks = []
        if isinstance(tasks, list) and tasks:
            for index, item in enumerate(tasks, start=1):
                if isinstance(item, dict):
                    text = str(item.get("question", "")).strip()
                    if not text:
                        continue
                    task_id = str(item.get("task_id", "")).strip() or f"task_{index}"

                    depends_on = item.get("depends_on", [])
                    if isinstance(depends_on, str):
                        depends_on = [depends_on]
                    if not isinstance(depends_on, list):
                        depends_on = []
                    dep_clean = []
                    seen_dep = set()
                    for dep in depends_on:
                        dep_text = str(dep).strip()
                        if not dep_text or dep_text in seen_dep:
                            continue
                        seen_dep.add(dep_text)
                        dep_clean.append(dep_text)

                    bind_output = item.get("bind_output", [])
                    if isinstance(bind_output, dict):
                        bind_output = [bind_output]
                    if not isinstance(bind_output, list):
                        bind_output = []
                    bind_specs = []
                    for bind in bind_output:
                        if not isinstance(bind, dict):
                            continue
                        var_name = str(bind.get("var", "")).strip()
                        if not var_name:
                            continue
                        bind_specs.append(
                            {
                                "from_task": str(bind.get("from_task", "")).strip(),
                                "field": str(bind.get("field", "")).strip(),
                                "var": var_name,
                            }
                        )

                    normalized_tasks.append(
                        {
                            "task_id": task_id,
                            "question": text,
                            "depends_on": dep_clean,
                            "bind_output": bind_specs,
                        }
                    )
                else:
                    text = str(item).strip()
                    if not text:
                        continue
                    normalized_tasks.append(
                        {
                            "task_id": f"task_{index}",
                            "question": text,
                            "depends_on": [],
                            "bind_output": [],
                        }
                    )
        else:
            for index, question in enumerate(sub_questions, start=1):
                text = str(question).strip()
                if not text:
                    continue
                normalized_tasks.append(
                    {
                        "task_id": f"task_{index}",
                        "question": text,
                        "depends_on": [],
                        "bind_output": [],
                    }
                )

        if not normalized_tasks:
            normalized_tasks = [{"task_id": "task_1", "question": "", "depends_on": [], "bind_output": []}]

        mode = str(execution_mode or "single_sql").strip().lower()
        if mode == "multi_sql":
            mode = "multi_sql_independent"
        if mode not in {"single_sql", "multi_sql_independent", "multi_sql_dependent"}:
            mode = "multi_sql_independent" if len(normalized_tasks) > 1 else "single_sql"

        return {
            "enabled": bool(enabled),
            "reason": str(reason or ""),
            "confidence": float(confidence or 0.0),
            "execution_mode": mode,
            "task_count": len(normalized_tasks),
            "sub_tasks": normalized_tasks,
        }

    def _plan_with_llm(self, question: str) -> dict | None:
        rule_result = self._split_question(question)
        system_prompt = self._render_prompt(
            key="question_split_system",
            default_template=(
                "\u4f60\u662f\u4efb\u52a1\u89c4\u5212\u5668\u3002\u8bf7\u5148\u5224\u65ad\u7528\u6237\u95ee\u9898\u662f\u5426\u9700\u8981\u62c6\u6210\u591a\u4e2a\u72ec\u7acb\u67e5\u8be2\u4efb\u52a1\u3002"
                "\u539f\u5219\uff1asingle_sql \u4f18\u5148\uff0c\u53ea\u8981\u80fd\u7528\u4e00\u6761 SQL \u5b8c\u6210\uff0c\u5c31\u4e0d\u8981\u62c6\u3002"
                "\u5f53\u540e\u7eed\u4efb\u52a1\u4f9d\u8d56\u524d\u5e8f\u4efb\u52a1\u7ed3\u679c\u65f6\uff0c\u624d\u4f7f\u7528 multi_sql_dependent\u3002"
                "\u5f53\u4efb\u52a1\u76f8\u4e92\u72ec\u7acb\u4e14\u65e0\u6cd5\u540c SQL \u8868\u8fbe\u65f6\uff0c\u4f7f\u7528 multi_sql_independent\u3002"
                "\u4e25\u683c\u8f93\u51fa JSON\uff1a"
                "{"
                "\\\"is_multi_task\\\": true/false,"
                "\\\"execution_mode\\\": \\\"single_sql\\\" or \\\"multi_sql_independent\\\" or \\\"multi_sql_dependent\\\","
                "\\\"tasks\\\": ["
                "{\\\"task_id\\\":\\\"task_1\\\",\\\"question\\\":\\\"\u5b50\u95ee\u98981\\\",\\\"depends_on\\\":[],\\\"bind_output\\\":[]},"
                "{\\\"task_id\\\":\\\"task_2\\\",\\\"question\\\":\\\"{var}\u7684\u5b50\u95ee\u98982\\\",\\\"depends_on\\\":[\\\"task_1\\\"],"
                "\\\"bind_output\\\":[{\\\"from_task\\\":\\\"task_1\\\",\\\"field\\\":\\\"\u5b57\u6bb5\u540d\\\",\\\"var\\\":\\\"var_name\\\"}]}"
                "],"
                "\\\"reason\\\": \\\"\u7b80\u77ed\u539f\u56e0\\\","
                "\\\"confidence\\\": 0\u52301"
                "}"
            ),
            context={},
        )
        user_prompt = self._render_prompt(
            key="question_split_user",
            default_template="\u539f\u59cb\u95ee\u9898: $question",
            context={"question": question},
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.log(f"\u95ee\u9898\u62c6\u89e3 LLM \u89c4\u5212\u5931\u8d25\uff0c\u56de\u9000\u89c4\u5219\u62c6\u5206: {exc}")
            return None

        normalized = self._normalize_llm_plan(question, parsed)
        if normalized:
            if (
                normalized.get("execution_mode") == "single_sql"
                and len(normalized.get("sub_questions", [])) <= 1
                and isinstance(rule_result, dict)
                and rule_result.get("reason") == "same_period_compare"
                and isinstance(rule_result.get("sub_questions", []), list)
                and len(rule_result.get("sub_questions", [])) == 2
            ):
                self.log("LLM planned single_sql, but rule hit same_period_compare; fallback to multi task")
                return {
                    "sub_questions": rule_result.get("sub_questions", [question]),
                    "reason": "same_period_compare_rule_override",
                    "confidence": max(
                        float(normalized.get("confidence", 0.0) or 0.0),
                        float(rule_result.get("confidence", 0.92) or 0.92),
                    ),
                    "execution_mode": "multi_sql_independent",
                }
            return normalized

        self.log("Question-split LLM plan failed validation; fallback to rule split")
        return rule_result

    def _normalize_llm_plan(self, question: str, payload) -> dict | None:
        if not isinstance(payload, dict):
            return None

        is_multi = bool(payload.get("is_multi_task", False))
        mode = str(payload.get("execution_mode", "")).strip().lower()
        if mode == "multi_sql":
            mode = "multi_sql_independent"
        if mode not in {"single_sql", "multi_sql_independent", "multi_sql_dependent"}:
            mode = "multi_sql_independent" if is_multi else "single_sql"

        tasks_raw = payload.get("tasks", [])
        if isinstance(tasks_raw, str):
            tasks_raw = [tasks_raw]
        if not isinstance(tasks_raw, list):
            tasks_raw = []

        tasks = []
        seen_questions = set()
        seen_task_ids = set()
        for index, item in enumerate(tasks_raw, start=1):
            if isinstance(item, dict):
                text = self._clean_clause(str(item.get("question", "")))
                if not text or text in seen_questions:
                    continue
                seen_questions.add(text)

                task_id = str(item.get("task_id", "")).strip() or f"task_{index}"
                if task_id in seen_task_ids:
                    task_id = f"task_{len(seen_task_ids) + 1}"
                seen_task_ids.add(task_id)

                depends_on = item.get("depends_on", [])
                if isinstance(depends_on, str):
                    depends_on = [depends_on]
                if not isinstance(depends_on, list):
                    depends_on = []
                dep_clean = []
                seen_dep = set()
                for dep in depends_on:
                    dep_text = str(dep).strip()
                    if not dep_text or dep_text in seen_dep:
                        continue
                    seen_dep.add(dep_text)
                    dep_clean.append(dep_text)

                bind_output = item.get("bind_output", [])
                if isinstance(bind_output, dict):
                    bind_output = [bind_output]
                if not isinstance(bind_output, list):
                    bind_output = []
                bind_specs = []
                for bind in bind_output:
                    if not isinstance(bind, dict):
                        continue
                    var_name = str(bind.get("var", "")).strip()
                    if not var_name:
                        continue
                    bind_specs.append(
                        {
                            "from_task": str(bind.get("from_task", "")).strip(),
                            "field": str(bind.get("field", "")).strip(),
                            "var": var_name,
                        }
                    )

                tasks.append(
                    {
                        "task_id": task_id,
                        "question": text,
                        "depends_on": dep_clean,
                        "bind_output": bind_specs,
                    }
                )
            else:
                text = self._clean_clause(str(item))
                if not text or text in seen_questions:
                    continue
                seen_questions.add(text)
                tasks.append(
                    {
                        "task_id": f"task_{len(tasks) + 1}",
                        "question": text,
                        "depends_on": [],
                        "bind_output": [],
                    }
                )

        if mode == "single_sql" or not is_multi:
            tasks = [{"task_id": "task_1", "question": question, "depends_on": [], "bind_output": []}]
            is_multi = False
            mode = "single_sql"
        elif len(tasks) < 2:
            return None

        if is_multi and not self._validate_split_tasks([item["question"] for item in tasks]):
            return None

        if mode == "multi_sql_dependent":
            tasks = self._normalize_dependent_tasks(tasks)

        reason = str(payload.get("reason", "")).strip() or "llm_plan"
        confidence = payload.get("confidence", 0.8)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.8
        confidence = max(0.0, min(1.0, confidence))

        return {
            "tasks": tasks,
            "sub_questions": [item["question"] for item in tasks],
            "reason": reason,
            "confidence": confidence,
            "execution_mode": mode,
        }

    @staticmethod
    def _validate_split_tasks(tasks: list) -> bool:
        if not isinstance(tasks, list) or len(tasks) < 2:
            return False

        for task in tasks:
            text = str(task or "").strip()
            if len(text) < 2:
                return False
            if text.startswith("\u95f4\u6bb5") or text == "\u95f4\u6bb5\u662f\u591a\u5c11":
                return False
            if "\u95f4\u6bb5" in text and "\u65f6\u95f4\u6bb5" not in text:
                return False
        return True

    def _normalize_dependent_tasks(self, tasks: list[dict]) -> list[dict]:
        if not tasks:
            return tasks

        for idx, item in enumerate(tasks):
            if idx == 0:
                item["depends_on"] = []
                item["bind_output"] = []
            elif not item.get("depends_on"):
                item["depends_on"] = [tasks[idx - 1]["task_id"]]

        def _vars_in_question(question_text: str) -> set[str]:
            return set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", str(question_text or "")))

        for idx, item in enumerate(tasks):
            if idx == 0:
                continue

            placeholders = _vars_in_question(item.get("question", ""))
            dep0 = item.get("depends_on", [""])[0] if item.get("depends_on") else ""

            sanitized = []
            seen_vars = set()
            for spec in item.get("bind_output", []):
                if not isinstance(spec, dict):
                    continue
                var_name = str(spec.get("var", "")).strip()
                if not var_name:
                    continue
                from_task = str(spec.get("from_task", "")).strip() or dep0
                if not from_task or from_task == item.get("task_id"):
                    from_task = dep0
                if not from_task:
                    continue
                if placeholders and var_name not in placeholders:
                    continue
                if var_name in seen_vars:
                    continue
                seen_vars.add(var_name)
                sanitized.append(
                    {
                        "from_task": from_task,
                        "field": str(spec.get("field", "")).strip(),
                        "var": var_name,
                    }
                )

            if not sanitized and placeholders and dep0:
                for var_name in sorted(placeholders):
                    sanitized.append(
                        {
                            "from_task": dep0,
                            "field": "",
                            "var": var_name,
                        }
                    )

            item["bind_output"] = sanitized

        return tasks

    def _split_question(self, question: str) -> dict:
        text = self._normalize_text(question)

        if any(token in text for token in ("\u53ca\u5176\u8d39\u7528", "\u53ca\u5176\u5efa\u8bbe\u8d39", "\u53ca\u5176\u8fd0\u7ef4\u8d39", "\u53ca\u5176\u91d1\u989d")):
            return {"sub_questions": [question], "reason": "blacklist_top1_with_value", "confidence": 0.95, "execution_mode": "single_sql"}

        kinship_tasks = self._split_kinship_dependency(text)
        if kinship_tasks:
            return {
                "tasks": kinship_tasks,
                "sub_questions": [item["question"] for item in kinship_tasks],
                "reason": "kinship_dependent_inference",
                "confidence": 0.93,
                "execution_mode": "multi_sql_dependent",
            }

        by_marks = self._split_by_question_marks(text)
        if len(by_marks) >= 2:
            return {"sub_questions": by_marks, "reason": "multi_question_marks", "confidence": 0.95, "execution_mode": "multi_sql_independent"}

        by_comma_dual = self._split_total_and_rank_by_comma(text)
        if len(by_comma_dual) == 2:
            return {"sub_questions": by_comma_dual, "reason": "comma_total_plus_rank", "confidence": 0.90, "execution_mode": "multi_sql_independent"}

        by_same_period = self._split_same_period_compare(text)
        if len(by_same_period) == 2:
            return {"sub_questions": by_same_period, "reason": "same_period_compare", "confidence": 0.92, "execution_mode": "multi_sql_independent"}

        by_connector = self._split_by_connectors(text)
        if len(by_connector) >= 2:
            return {"sub_questions": by_connector, "reason": "connector_multi_target", "confidence": 0.86, "execution_mode": "multi_sql_independent"}

        return {"sub_questions": [question], "reason": "single_intent", "confidence": 0.90, "execution_mode": "single_sql"}

    def _split_kinship_dependency(self, text: str) -> list[dict]:
        clause = str(text or "").strip()
        if not clause:
            return []

        var_map = {
            "\u6bcd\u4eb2": "mother_name",
            "\u7236\u4eb2": "father_name",
            "\u914d\u5076": "spouse_name",
            "\u59bb\u5b50": "wife_name",
            "\u4e08\u592b": "husband_name",
            "\u513f\u5b50": "son_name",
            "\u5973\u513f": "daughter_name",
        }
        kin_tokens = "|".join(var_map.keys())
        pattern = rf"^(?P<subject>.+?)\u7684(?P<kin>{kin_tokens})(?P<tail>.+)$"
        matched = re.match(pattern, clause)
        if not matched:
            return []

        subject = self._clean_clause(matched.group("subject"))
        kin = self._clean_clause(matched.group("kin"))
        tail = self._clean_clause(matched.group("tail"))
        if not subject or not kin or not tail:
            return []
        if tail in {"\u662f\u8c01", "\u53eb\u4ec0\u4e48", "\u53eb\u4ec0\u4e48\u540d\u5b57"}:
            return []

        var_name = var_map.get(kin, "related_name")
        return [
            {
                "task_id": "task_1",
                "question": f"{subject}\u7684{kin}\u662f\u8c01",
                "depends_on": [],
                "bind_output": [],
            },
            {
                "task_id": "task_2",
                "question": f"{{{var_name}}}\u7684{tail}",
                "depends_on": ["task_1"],
                "bind_output": [
                    {
                        "from_task": "task_1",
                        "field": "",
                        "var": var_name,
                    }
                ],
            },
        ]

    @staticmethod
    def _normalize_text(text: str) -> str:
        value = str(text or "").strip()
        value = value.replace("\uff1f", "?").replace("\uff1b", ";").replace("\u3002", ".")
        value = re.sub(r"\s+", "", value)
        return value

    @staticmethod
    def _clean_clause(text: str) -> str:
        cleaned = str(text or "").strip(" ,\uff0c;\uff1b.?\uff1f")
        return cleaned

    def _split_by_question_marks(self, text: str) -> list:
        if "?" not in text:
            return []
        parts = [self._clean_clause(item) for item in text.split("?")]
        parts = [item for item in parts if len(item) >= 3]
        if len(parts) < 2:
            return []
        return parts

    def _split_total_and_rank_by_comma(self, text: str) -> list:
        if "," not in text and "\uff0c" not in text:
            return []
        parts = [self._clean_clause(item) for item in re.split(r"[\uff0c,]", text) if self._clean_clause(item)]
        if len(parts) != 2:
            return []
        first, second = parts[0], parts[1]

        if self._is_total_clause(first) and self._is_rank_clause(second):
            subject_prefix = self._extract_subject_prefix(first)
            second_full = self._enrich_with_subject(second, subject_prefix)
            return [first, second_full]

        if self._is_rank_clause(first) and self._is_total_clause(second):
            subject_prefix = self._extract_subject_prefix(second)
            first_full = self._enrich_with_subject(first, subject_prefix)
            return [second, first_full]

        return []

    def _split_same_period_compare(self, text: str) -> list:
        clause = str(text or "").strip()
        if not clause or "\uff0c" not in clause:
            return []

        pattern = (
            r"^(?P<first>.+?)\uff0c"
            r"(?P<second_year>\d{4})\u5e74?(?:\u540c|\u76f8\u540c|\u540c\u4e00)?\u65f6\u95f4\u6bb5"
            r"(?:\u7684)?(?:\u76ee\u5f55)?(?:\u6570\u91cf)?(?:\u662f)?(?:\u591a\u5c11)?[?\uff1f]?$"
        )
        matched = re.match(pattern, clause)
        if not matched:
            return []

        first = self._clean_clause(matched.group("first"))
        second_year = str(matched.group("second_year")).strip()
        if not first or not second_year:
            return []

        first_year_match = re.search(r"(?P<year>\d{4})\u5e74", first)
        if first_year_match:
            first_year = first_year_match.group("year")
            second = re.sub(rf"{first_year}\u5e74", f"{second_year}\u5e74", first, count=1)
        else:
            second = f"{second_year}\u5e74\u540c\u65f6\u95f4\u6bb5\u662f\u591a\u5c11"

        first_final = first if first.endswith(("?", "\uff1f")) else f"{first}\uff1f"
        second_final = second if second.endswith(("?", "\uff1f")) else f"{second}\uff1f"
        return [first_final, second_final]

    def _split_by_connectors(self, text: str) -> list:
        if "\u5206\u522b" in text and ("\u548c" in text or "\u3001" in text):
            return []

        connector_pattern = r"(?:\u4ee5\u53ca|\u5e76\u4e14|\u540c\u65f6(?!\u95f4)|\u53e6\u5916|\u6b64\u5916|\u518d\u770b|\u518d\u7edf\u8ba1|\u5e76\u8fd4\u56de|\u5e76\u7ed9\u51fa|\u5e76\u627e\u51fa|\u518d\u770b\u4e0b|\u7136\u540e)"
        if not re.search(connector_pattern, text):
            return []

        segments = [self._clean_clause(item) for item in re.split(connector_pattern, text) if self._clean_clause(item)]
        if len(segments) < 2:
            return []

        if not self._looks_like_multi_targets(segments):
            return []

        base_prefix = self._extract_subject_prefix(segments[0])
        result = [segments[0]]
        for seg in segments[1:]:
            result.append(self._enrich_with_subject(seg, base_prefix))
        return result

    @staticmethod
    def _is_total_clause(text: str) -> bool:
        clause = str(text or "")
        ask_keywords = (
            "\u591a\u5c11",
            "\u662f\u591a\u5c11",
            "\u6709\u591a\u5c11",
            "\u603b\u989d",
            "\u603b\u548c",
            "\u5408\u8ba1",
            "\u603b\u8ba1",
            "\u6570\u91cf",
            "\u4e2a\u6570",
            "\u603b\u91cf",
        )
        metric_keywords = (
            "\u8d39\u7528",
            "\u5efa\u8bbe\u8d39",
            "\u8fd0\u7ef4\u8d39",
            "\u6570\u91cf",
            "\u603b\u6570",
            "\u4f7f\u7528\u91cf",
            "\u4e0a\u4e91\u7387",
            "\u5360\u6bd4",
            "\u7f51\u529e\u7387",
        )
        return any(k in clause for k in ask_keywords) and any(k in clause for k in metric_keywords)

    @staticmethod
    def _is_rank_clause(text: str) -> bool:
        clause = str(text or "")
        rank_keywords = ("\u6700\u9ad8", "\u6700\u5927", "\u6700\u4f4e", "\u6700\u5c0f", "\u524d", "\u6392\u884c", "\u6392\u540d")
        ask_keywords = ("\u54ea\u4e2a", "\u54ea\u4e00\u4e2a", "\u662f\u54ea", "\u662f\u4ec0\u4e48")
        has_rank = any(k in clause for k in rank_keywords)
        has_ask = any(k in clause for k in ask_keywords)
        return has_rank and has_ask

    def _looks_like_multi_targets(self, segments: list) -> bool:
        if len(segments) < 2:
            return False
        total_like = sum(1 for seg in segments if self._is_total_clause(seg))
        rank_like = sum(1 for seg in segments if self._is_rank_clause(seg))
        if total_like >= 2:
            return True
        if total_like >= 1 and rank_like >= 1:
            return True
        intent_keywords = (
            "\u591a\u5c11",
            "\u662f\u591a\u5c11",
            "\u6700\u9ad8",
            "\u6700\u4f4e",
            "\u603b\u989d",
            "\u603b\u548c",
            "\u6570\u91cf",
            "\u5360\u6bd4",
            "\u7387",
        )
        hit_count = sum(1 for seg in segments if any(k in seg for k in intent_keywords))
        return hit_count >= 2

    @staticmethod
    def _extract_subject_prefix(clause: str) -> str:
        text = str(clause or "")
        markers = [
            "\u8fd0\u7ef4\u8d39\u7528",
            "\u5efa\u8bbe\u8d39\u7528",
            "\u8d39\u7528",
            "\u6570\u91cf",
            "\u603b\u989d",
            "\u603b\u548c",
            "\u5360\u6bd4",
            "\u7387",
            "\u4f7f\u7528\u91cf",
            "\u6709\u591a\u5c11",
            "\u662f\u591a\u5c11",
            "\u591a\u5c11",
        ]
        positions = [text.find(marker) for marker in markers if text.find(marker) > 0]
        if not positions:
            return ""
        idx = min(positions)
        prefix = text[:idx].strip("\uff0c,\u3002;\uff1b ")
        if prefix.endswith("\u7684"):
            prefix = prefix[:-1]
        return prefix

    @staticmethod
    def _enrich_with_subject(clause: str, subject_prefix: str) -> str:
        seg = str(clause or "").strip()
        subject = str(subject_prefix or "").strip()
        if not seg or not subject:
            return seg
        if subject in seg:
            return seg
        if seg.startswith(("\u54ea\u4e2a", "\u54ea\u4e00\u4e2a", "\u54ea", "\u6700\u9ad8", "\u6700\u4f4e", "\u524d", "Top", "top")):
            joiner = "\u4e2d" if not subject.endswith(("\u4e2d", "\u5185")) else ""
            return f"{subject}{joiner}{seg}"
        return seg

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        return default_template
