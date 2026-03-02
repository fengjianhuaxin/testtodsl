"""领域知识管理器 - 术语检索"""
import json
import os
import re


class KnowledgeManager:
    """管理领域知识，避免将全量知识直接注入上下文"""

    def __init__(self, knowledge_file: str):
        self.knowledge_file = knowledge_file
        self.data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.knowledge_file):
            return {
                "term_knowledge": [],
                "rewrite_rules": [],
                "sql_metric_rules": [],
            }
        with open(self.knowledge_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("term_knowledge", [])
        data.setdefault("rewrite_rules", [])
        data.setdefault("sql_metric_rules", [])
        return data

    def retrieve_term_knowledge(self, query: str, top_k: int = 3) -> list:
        """按关键词匹配检索少量相关术语知识片段"""
        if not query:
            return []
        query_text = str(query).strip()
        if not query_text:
            return []

        hits = []
        for item in self.data.get("term_knowledge", []):
            term = str(item.get("term", "")).strip()
            keywords = item.get("keywords", [])
            keywords = keywords if isinstance(keywords, list) else []
            candidates = [term] + [str(k).strip() for k in keywords if str(k).strip()]

            score = 0
            matched = []
            for kw in candidates:
                if kw and kw in query_text:
                    score += max(2, len(kw))
                    matched.append(kw)

            if score <= 0:
                continue

            # 去重保持顺序
            seen = set()
            uniq_matched = []
            for m in matched:
                if m not in seen:
                    seen.add(m)
                    uniq_matched.append(m)

            hits.append({
                "score": score,
                "id": item.get("id", ""),
                "term": term,
                "content": item.get("content", ""),
                "matched_keywords": uniq_matched
            })

        hits.sort(key=lambda x: x["score"], reverse=True)
        return hits[:top_k]

    def get_rewrite_rules(self, enabled_only: bool = True) -> list:
        rules = self.data.get("rewrite_rules", [])
        if not isinstance(rules, list):
            return []

        normalized = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            if not source or not target:
                continue
            enabled = bool(item.get("enabled", True))
            if enabled_only and not enabled:
                continue
            normalized.append({
                "id": str(item.get("id", "")).strip(),
                "source": source,
                "target": target,
                "enabled": enabled,
                "description": str(item.get("description", "")).strip(),
            })

        normalized.sort(key=lambda item: len(item["source"]), reverse=True)
        return normalized

    def normalize_question(self, question: str) -> tuple[str, list]:
        text = str(question or "")
        if not text:
            return text, []

        normalized = text
        hits = []
        for rule in self.get_rewrite_rules(enabled_only=True):
            source = rule["source"]
            target = rule["target"]
            count = normalized.count(source)
            if count <= 0:
                continue
            normalized = normalized.replace(source, target)
            hits.append({
                "id": rule.get("id", ""),
                "source": source,
                "target": target,
                "count": count,
                "description": rule.get("description", ""),
            })

        return normalized, hits

    @staticmethod
    def _normalize_sql_text(sql: str) -> str:
        text = str(sql or "").strip()
        if text.startswith("```sql"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.endswith(";"):
            text = text[:-1].strip()
        return text

    @classmethod
    def _is_safe_select_sql(cls, sql: str) -> bool:
        text = cls._normalize_sql_text(sql)
        if not text:
            return False
        if not re.match(r"^\s*select\b", text, flags=re.IGNORECASE):
            return False
        if ";" in text:
            return False
        if re.search(r"\b(insert|update|delete|drop|alter|truncate|create|replace)\b", text, flags=re.IGNORECASE):
            return False
        return True

    def get_sql_metric_rules(self, enabled_only: bool = True) -> list:
        rules = self.data.get("sql_metric_rules", [])
        if not isinstance(rules, list):
            return []

        normalized = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            sql = self._normalize_sql_text(item.get("sql", ""))
            if not name or not sql:
                continue
            if not self._is_safe_select_sql(sql):
                continue

            keywords = item.get("keywords", [])
            if isinstance(keywords, str):
                keywords = [x.strip() for x in keywords.split(",") if x.strip()]
            if not isinstance(keywords, list):
                keywords = []
            keywords = [str(x).strip() for x in keywords if str(x).strip()]
            if not keywords:
                keywords = [name]

            target_entities = item.get("target_entities", [])
            if isinstance(target_entities, str):
                target_entities = [x.strip() for x in target_entities.split(",") if x.strip()]
            if not isinstance(target_entities, list):
                target_entities = []
            target_entities = [str(x).strip() for x in target_entities if str(x).strip()]

            try:
                priority = int(item.get("priority", 100))
            except Exception:
                priority = 100

            enabled = bool(item.get("enabled", True))
            if enabled_only and not enabled:
                continue

            normalized.append({
                "id": str(item.get("id", "")).strip(),
                "name": name,
                "keywords": keywords,
                "sql": sql,
                "target_entities": target_entities,
                "data_source": str(item.get("data_source", "")).strip() or "xksx",
                "enabled": enabled,
                "priority": priority,
                "description": str(item.get("description", "")).strip(),
            })

        normalized.sort(key=lambda x: (x["priority"], max((len(k) for k in x["keywords"]), default=0)), reverse=True)
        return normalized

    def match_metric_sql_rule(self, question: str) -> dict | None:
        text = str(question or "").strip()
        if not text:
            return None

        for rule in self.get_sql_metric_rules(enabled_only=True):
            matched_keywords = [keyword for keyword in rule.get("keywords", []) if keyword and keyword in text]
            if not matched_keywords:
                continue
            return {
                **rule,
                "matched_keywords": matched_keywords,
            }
        return None
