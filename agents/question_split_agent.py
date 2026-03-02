"""问题拆解智能体：优先用 LLM 规划任务，规则仅用于校验与兜底。"""
import re

from agents.base_agent import BaseAgent


class QuestionSplitAgent(BaseAgent):
    def __init__(self, llm_client=None, prompt_manager=None):
        super().__init__("问题拆解智能体", "识别是否包含多个独立查询目标，并执行保守拆解")
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

        sub_questions = split_result.get("sub_questions", [question])
        enabled = len(sub_questions) > 1
        analysis = self._build_analysis(
            enabled=enabled,
            reason=split_result.get("reason", "single_intent"),
            confidence=split_result.get("confidence", 0.9),
            sub_questions=sub_questions if sub_questions else [question],
            execution_mode=split_result.get("execution_mode", "multi_sql" if enabled else "single_sql"),
        )

        if enabled:
            self.log(
                f"命中多问题拆解: {len(sub_questions)}个子问题, "
                f"reason={analysis.get('reason', '')}, confidence={analysis.get('confidence', 0.0):.2f}"
            )
            for idx, item in enumerate(analysis["sub_tasks"], start=1):
                self.log(f"  子问题{idx}: {item['question']}")
        else:
            self.log("未命中多问题拆解，按单问题执行")

        return {**input_data, "split_analysis": analysis}

    @staticmethod
    def _build_analysis(enabled: bool, reason: str, confidence: float, sub_questions: list, execution_mode: str = "single_sql") -> dict:
        tasks = []
        for index, question in enumerate(sub_questions, start=1):
            text = str(question).strip()
            if not text:
                continue
            tasks.append({
                "task_id": f"task_{index}",
                "question": text,
            })
        if not tasks:
            tasks = [{"task_id": "task_1", "question": ""}]

        mode = str(execution_mode or "single_sql").strip().lower()
        if mode not in {"single_sql", "multi_sql"}:
            mode = "multi_sql" if len(tasks) > 1 else "single_sql"

        return {
            "enabled": bool(enabled),
            "reason": str(reason or ""),
            "confidence": float(confidence or 0.0),
            "execution_mode": mode,
            "task_count": len(tasks),
            "sub_tasks": tasks,
        }

    def _plan_with_llm(self, question: str) -> dict | None:
        rule_result = self._split_question(question)
        system_prompt = self._render_prompt(
            key="question_split_system",
            default_template=(
                "你是任务规划器。请先判断用户问题是否需要拆成多个独立查询任务。"
                "原则：single_sql 优先，只要能用一条 SQL 完成，就不要拆。"
                "只有确实独立且无法同 SQL 表达时，才用 multi_sql。"
                "严格输出 JSON："
                "{"
                "\"is_multi_task\": true/false,"
                "\"execution_mode\": \"single_sql\" or \"multi_sql\","
                "\"tasks\": [\"子问题1\", \"子问题2\"],"
                "\"reason\": \"简短原因\","
                "\"confidence\": 0到1"
                "}"
            ),
            context={},
        )
        user_prompt = self._render_prompt(
            key="question_split_user",
            default_template="原始问题: $question",
            context={"question": question},
        )

        try:
            parsed = self.llm.chat_json(system_prompt, user_prompt)
        except Exception as exc:
            self.log(f"问题拆解 LLM 规划失败，回退规则拆分: {exc}")
            return None

        normalized = self._normalize_llm_plan(question, parsed)
        if normalized:
            # 规则校验兜底：如果 LLM 误判为单任务，但规则高置信命中“同比同时间段”，回拉为多任务。
            if (
                normalized.get("execution_mode") == "single_sql"
                and len(normalized.get("sub_questions", [])) <= 1
                and isinstance(rule_result, dict)
                and rule_result.get("reason") == "same_period_compare"
                and isinstance(rule_result.get("sub_questions", []), list)
                and len(rule_result.get("sub_questions", [])) == 2
            ):
                self.log("LLM 拆解为单任务，但规则命中 same_period_compare，回拉为多任务")
                return {
                    "sub_questions": rule_result.get("sub_questions", [question]),
                    "reason": "same_period_compare_rule_override",
                    "confidence": max(
                        float(normalized.get("confidence", 0.0) or 0.0),
                        float(rule_result.get("confidence", 0.92) or 0.92),
                    ),
                    "execution_mode": "multi_sql",
                }
            return normalized

        self.log("问题拆解 LLM 规划结果未通过校验，回退规则拆分")
        return rule_result

    def _normalize_llm_plan(self, question: str, payload) -> dict | None:
        if not isinstance(payload, dict):
            return None

        is_multi = bool(payload.get("is_multi_task", False))
        mode = str(payload.get("execution_mode", "")).strip().lower()
        if mode not in {"single_sql", "multi_sql"}:
            mode = "multi_sql" if is_multi else "single_sql"

        tasks_raw = payload.get("tasks", [])
        if isinstance(tasks_raw, str):
            tasks_raw = [tasks_raw]
        if not isinstance(tasks_raw, list):
            tasks_raw = []

        tasks = []
        for item in tasks_raw:
            text = self._clean_clause(str(item))
            if not text:
                continue
            if text not in tasks:
                tasks.append(text)

        # 单SQL优先
        if mode == "single_sql" or not is_multi:
            tasks = [question]
            is_multi = False
            mode = "single_sql"
        elif len(tasks) < 2:
            return None

        # 规则校验兜底，防残句
        if is_multi and not self._validate_split_tasks(tasks):
            return None

        reason = str(payload.get("reason", "")).strip() or "llm_plan"
        confidence = payload.get("confidence", 0.8)
        try:
            confidence = float(confidence)
        except Exception:
            confidence = 0.8
        confidence = max(0.0, min(1.0, confidence))

        return {
            "sub_questions": tasks,
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
            if len(text) < 6:
                return False
            # 典型残句：由“同时间段”误切导致
            if text.startswith("间段") or text == "间段是多少":
                return False
            if "间段" in text and "时间段" not in text:
                return False
        return True

    def _split_question(self, question: str) -> dict:
        text = self._normalize_text(question)

        # 黑名单：Top1及其指标补充，不拆
        if any(token in text for token in ("及其费用", "及其建设费", "及其运维费", "及其金额")):
            return {"sub_questions": [question], "reason": "blacklist_top1_with_value", "confidence": 0.95, "execution_mode": "single_sql"}

        # 规则1：多个问号分句 => 多子问题
        by_marks = self._split_by_question_marks(text)
        if len(by_marks) >= 2:
            return {"sub_questions": by_marks, "reason": "multi_question_marks", "confidence": 0.95, "execution_mode": "multi_sql"}

        # 规则2：逗号双分句（总量 + 排名）
        by_comma_dual = self._split_total_and_rank_by_comma(text)
        if len(by_comma_dual) == 2:
            return {"sub_questions": by_comma_dual, "reason": "comma_total_plus_rank", "confidence": 0.90, "execution_mode": "multi_sql"}

        # 规则2B：同比同时间段（兜底）
        by_same_period = self._split_same_period_compare(text)
        if len(by_same_period) == 2:
            return {"sub_questions": by_same_period, "reason": "same_period_compare", "confidence": 0.92, "execution_mode": "multi_sql"}

        # 规则3：显式连接词（以及/并且/同时/另外）
        by_connector = self._split_by_connectors(text)
        if len(by_connector) >= 2:
            return {"sub_questions": by_connector, "reason": "connector_multi_target", "confidence": 0.86, "execution_mode": "multi_sql"}

        return {"sub_questions": [question], "reason": "single_intent", "confidence": 0.90, "execution_mode": "single_sql"}

    @staticmethod
    def _normalize_text(text: str) -> str:
        value = str(text or "").strip()
        value = value.replace("？", "?").replace("；", ";").replace("。", ".")
        value = re.sub(r"\s+", "", value)
        return value

    @staticmethod
    def _clean_clause(text: str) -> str:
        cleaned = str(text or "").strip(" ,，;；.?？")
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
        if "," not in text and "，" not in text:
            return []
        parts = [self._clean_clause(item) for item in re.split(r"[，,]", text) if self._clean_clause(item)]
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
        if not clause or "，" not in clause:
            return []

        pattern = (
            r"^(?P<first>.+?)，"
            r"(?P<second_year>\d{4})年?(?:同|相同|同一)?时间段"
            r"(?:的)?(?:目录)?(?:数量)?(?:是)?(?:多少)?[?？]?$"
        )
        matched = re.match(pattern, clause)
        if not matched:
            return []

        first = self._clean_clause(matched.group("first"))
        second_year = str(matched.group("second_year")).strip()
        if not first or not second_year:
            return []

        first_year_match = re.search(r"(?P<year>\d{4})年", first)
        if first_year_match:
            first_year = first_year_match.group("year")
            second = re.sub(rf"{first_year}年", f"{second_year}年", first, count=1)
        else:
            second = f"{second_year}年同时间段是多少"

        first_final = first if first.endswith(("?", "？")) else f"{first}？"
        second_final = second if second.endswith(("?", "？")) else f"{second}？"
        return [first_final, second_final]

    def _split_by_connectors(self, text: str) -> list:
        if "分别" in text and ("和" in text or "、" in text):
            # “分别”在很多场景是一个问题（分组统计），这里保守不拆
            return []

        # 避免把“同时间段”误识别成连接词“同时”
        connector_pattern = r"(?:以及|并且|同时(?!间)|另外|此外|再看|再统计|并返回|并给出|并找出|再看下|然后)"
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
        ask_keywords = ("多少", "是多少", "有多少", "总额", "总和", "合计", "总计", "数量", "个数", "总量")
        metric_keywords = ("费用", "建设费", "运维费", "数量", "总数", "使用量", "上云率", "占比", "网办率")
        return any(k in clause for k in ask_keywords) and any(k in clause for k in metric_keywords)

    @staticmethod
    def _is_rank_clause(text: str) -> bool:
        clause = str(text or "")
        rank_keywords = ("最高", "最大", "最低", "最小", "前", "排行", "排名")
        ask_keywords = ("哪个", "哪一个", "是哪", "是什么")
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
        # 兜底：多个分句且至少两个分句都带统计/比较意图
        intent_keywords = ("多少", "是多少", "最高", "最低", "总额", "总和", "数量", "占比", "率")
        hit_count = sum(1 for seg in segments if any(k in seg for k in intent_keywords))
        return hit_count >= 2

    @staticmethod
    def _extract_subject_prefix(clause: str) -> str:
        text = str(clause or "")
        markers = ["运维费用", "建设费用", "费用", "数量", "总额", "总和", "占比", "率", "使用量", "有多少", "是多少", "多少"]
        positions = [text.find(marker) for marker in markers if text.find(marker) > 0]
        if not positions:
            return ""
        idx = min(positions)
        prefix = text[:idx].strip("，,。;； ")
        if prefix.endswith("的"):
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
        if seg.startswith(("哪个", "哪一个", "哪", "最高", "最低", "前", "Top", "top")):
            joiner = "中" if not subject.endswith(("中", "内")) else ""
            return f"{subject}{joiner}{seg}"
        return seg

    def _render_prompt(self, key: str, default_template: str, context: dict) -> str:
        if self.prompts:
            return self.prompts.render(key, context=context, fallback_template=default_template)
        return default_template
