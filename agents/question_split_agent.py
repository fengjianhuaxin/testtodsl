"""问题拆解智能体：将单句中的并列查询目标拆解为子问题（保守策略）。"""
import re

from agents.base_agent import BaseAgent


class QuestionSplitAgent(BaseAgent):
    def __init__(self):
        super().__init__("问题拆解智能体", "识别是否包含多个独立查询目标，并进行保守拆解")

    def run(self, input_data: dict) -> dict:
        question = str(input_data.get("question", "")).strip()
        if not question:
            analysis = self._build_analysis(False, "empty_question", 0.0, [question])
            return {**input_data, "split_analysis": analysis}

        split_result = self._split_question(question)
        sub_questions = split_result["sub_questions"]
        enabled = len(sub_questions) > 1
        analysis = self._build_analysis(
            enabled=enabled,
            reason=split_result["reason"],
            confidence=split_result["confidence"],
            sub_questions=sub_questions if sub_questions else [question],
        )

        if enabled:
            self.log(
                f"命中多问题拆解: {len(sub_questions)}个子问题, "
                f"reason={split_result['reason']}, confidence={split_result['confidence']:.2f}"
            )
            for idx, item in enumerate(analysis["sub_tasks"], start=1):
                self.log(f"  子问题{idx}: {item['question']}")
        else:
            self.log("未命中多问题拆解，按单问题执行")

        return {**input_data, "split_analysis": analysis}

    @staticmethod
    def _build_analysis(enabled: bool, reason: str, confidence: float, sub_questions: list) -> dict:
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

        return {
            "enabled": bool(enabled),
            "reason": str(reason or ""),
            "confidence": float(confidence or 0.0),
            "task_count": len(tasks),
            "sub_tasks": tasks,
        }

    def _split_question(self, question: str) -> dict:
        text = self._normalize_text(question)

        # 黑名单：Top1及其指标补充，不拆
        if any(token in text for token in ("及其费用", "及其建设费", "及其运维费", "及其金额")):
            return {"sub_questions": [question], "reason": "blacklist_top1_with_value", "confidence": 0.95}

        # 规则1：多个问号分句 => 多子问题
        by_marks = self._split_by_question_marks(text)
        if len(by_marks) >= 2:
            return {"sub_questions": by_marks, "reason": "multi_question_marks", "confidence": 0.95}

        # 规则2：逗号双分句（总量 + 排名）
        by_comma_dual = self._split_total_and_rank_by_comma(text)
        if len(by_comma_dual) == 2:
            return {"sub_questions": by_comma_dual, "reason": "comma_total_plus_rank", "confidence": 0.90}

        # 规则3：显式连接词（以及/并且/同时/另外）
        by_connector = self._split_by_connectors(text)
        if len(by_connector) >= 2:
            return {"sub_questions": by_connector, "reason": "connector_multi_target", "confidence": 0.86}

        return {"sub_questions": [question], "reason": "single_intent", "confidence": 0.90}

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

    def _split_by_connectors(self, text: str) -> list:
        if "分别" in text and ("和" in text or "、" in text):
            # “分别”在很多场景是一个问题（分组统计），这里保守不拆
            return []

        connector_pattern = r"(?:以及|并且|同时|另外|此外|再看|再统计|并返回|并给出|并找出|再看下|然后)"
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
