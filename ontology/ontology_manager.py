"""本体管理器 - 加载和查询本体定义"""
import json
import os
import re
from difflib import SequenceMatcher


class OntologyManager:
    """本体管理器，负责加载、查询本体定义
    
    当前从 JSON 文件加载，后续可替换为 Neo4j 图数据库。
    替换步骤：
    1. 继承此类，实现 Neo4jOntologyManager
    2. 重写 _load / get_entity / get_relations 等方法
    3. 在 config.py 中切换 ONTOLOGY_STORE_TYPE
    """

    def __init__(self, ontology_path: str):
        self.ontology_path = ontology_path
        self.ontology = self._load()

    def _load(self) -> dict:
        with open(self.ontology_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @property
    def entities(self) -> dict:
        return self.ontology.get("entities", {})

    @property
    def relations(self) -> list:
        return self.ontology.get("relations", [])

    def get_entity(self, entity_name: str) -> dict | None:
        """根据实体名（英文）获取实体定义"""
        return self.entities.get(entity_name)

    def get_entity_by_label(self, label: str) -> tuple[str, dict] | None:
        """根据实体中文标签获取实体名和定义"""
        for name, entity in self.entities.items():
            if entity.get("label") == label:
                return name, entity
        return None

    def get_entity_properties(self, entity_name: str) -> dict:
        """获取实体的所有属性定义"""
        entity = self.get_entity(entity_name)
        if entity:
            return entity.get("properties", {})
        return {}

    def resolve_property_name(self, entity_name: str, field_name: str) -> str:
        """将属性名或属性别名解析为本体标准属性名"""
        props = self.get_entity_properties(entity_name)
        if not props:
            return field_name

        if field_name in props:
            return field_name

        field_lower = str(field_name).strip().lower()
        for prop_name, prop_def in props.items():
            aliases = prop_def.get("aliases", [])
            if not isinstance(aliases, list):
                continue
            for alias in aliases:
                if str(alias).strip().lower() == field_lower:
                    return prop_name
        return field_name

    @staticmethod
    def _normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())

    def suggest_property_name(self, entity_name: str, field_name: str, threshold: float = 0.75) -> str | None:
        props = self.get_entity_properties(entity_name)
        if not props:
            return None

        raw = str(field_name or "").strip()
        if not raw:
            return None
        if raw in props:
            return raw

        raw_norm = self._normalize_name(raw)
        if not raw_norm:
            return None

        best_name = None
        best_score = 0.0

        for prop_name, prop_def in props.items():
            candidate_names = [prop_name]
            aliases = prop_def.get("aliases", [])
            if isinstance(aliases, list):
                candidate_names.extend(aliases)

            for candidate in candidate_names:
                candidate_norm = self._normalize_name(candidate)
                if not candidate_norm:
                    continue

                score = SequenceMatcher(None, raw_norm, candidate_norm).ratio()
                if raw_norm in candidate_norm or candidate_norm in raw_norm:
                    score = max(score, 0.9)

                if score > best_score:
                    best_score = score
                    best_name = prop_name

        if best_name and best_score >= threshold:
            return best_name
        return None

    def normalize_property_value(self, entity_name: str, field_name: str, value):
        """按属性 value_aliases 归一化条件值"""
        canonical_field = self.resolve_property_name(entity_name, field_name)
        props = self.get_entity_properties(entity_name)
        prop_def = props.get(canonical_field, {})
        value_aliases = prop_def.get("value_aliases", {})
        if not isinstance(value_aliases, dict) or not value_aliases:
            return canonical_field, value, False

        changed = False

        def _map_one(v):
            nonlocal changed
            if isinstance(v, str):
                key = v.strip()
                if key in value_aliases:
                    changed = True
                    return value_aliases[key]
            return v

        if isinstance(value, list):
            mapped = [_map_one(v) for v in value]
            return canonical_field, mapped, changed

        return canonical_field, _map_one(value), changed

    def get_relations_for(self, entity_name: str) -> list:
        """??????????????"""
        related = []
        for rel in self.relations:
            if rel["from"] == entity_name or rel["to"] == entity_name:
                related.append(rel)
        return related

    @staticmethod
    def _normalize_join_field_list(raw_value) -> list[str]:
        if isinstance(raw_value, list):
            values = raw_value
        else:
            text = str(raw_value or "").strip()
            if not text:
                return []
            values = text.split(",")

        result = []
        for item in values:
            name = str(item or "").strip()
            if name:
                result.append(name)
        return result

    @classmethod
    def _extract_relation_join_pairs(cls, relation: dict, reverse: bool = False) -> list[tuple[str, str]]:
        from_fields = cls._normalize_join_field_list(relation.get("from_field"))
        to_fields = cls._normalize_join_field_list(relation.get("to_field"))
        if not from_fields or not to_fields:
            return []
        if len(from_fields) != len(to_fields):
            return []

        if reverse:
            return [(right, left) for left, right in zip(from_fields, to_fields)]
        return list(zip(from_fields, to_fields))

    def resolve_join_field_pairs(self, left_entity: str, right_entity: str) -> list[tuple[str, str]] | None:
        """????????????????????"""
        for rel in self.relations:
            from_entity = rel.get("from")
            to_entity = rel.get("to")

            if from_entity == left_entity and to_entity == right_entity:
                pairs = self._extract_relation_join_pairs(rel, reverse=False)
                if pairs:
                    return pairs
            elif from_entity == right_entity and to_entity == left_entity:
                pairs = self._extract_relation_join_pairs(rel, reverse=True)
                if pairs:
                    return pairs
        return None

    def resolve_join_fields(self, left_entity: str, right_entity: str) -> tuple[str, str] | None:
        """?????????????????(left_field, right_field)"""
        def _find_key_field(props: dict) -> str | None:
            for pname, pdef in props.items():
                if pdef.get("is_key"):
                    return pname
            return None

        relation_pairs = self.resolve_join_field_pairs(left_entity, right_entity)
        if relation_pairs:
            return relation_pairs[0]

        left_props = self.get_entity_properties(left_entity)
        right_props = self.get_entity_properties(right_entity)
        left_key = _find_key_field(left_props)
        right_key = _find_key_field(right_props)

        for prop_name, prop_def in left_props.items():
            if prop_def.get("is_fk") and prop_def.get("ref_entity") == right_entity:
                if prop_name in right_props:
                    return prop_name, prop_name
                if right_key:
                    return prop_name, right_key

        for prop_name, prop_def in right_props.items():
            if prop_def.get("is_fk") and prop_def.get("ref_entity") == left_entity:
                if prop_name in left_props:
                    return prop_name, prop_name
                if left_key:
                    return left_key, prop_name

        shared_ids = [
            field_name for field_name in left_props.keys()
            if field_name in right_props and str(field_name).endswith("_id")
        ]
        if shared_ids:
            return shared_ids[0], shared_ids[0]

        return None

    def find_path(self, from_entity: str, to_entity: str) -> list:
        """在本体中查找两个实体之间的关系路径（BFS）"""
        if from_entity == to_entity:
            return [from_entity]

        # 构建邻接表
        adj = {}
        for rel in self.relations:
            adj.setdefault(rel["from"], []).append((rel["to"], rel))
            adj.setdefault(rel["to"], []).append((rel["from"], rel))

        visited = {from_entity}
        queue = [(from_entity, [from_entity], [])]
        while queue:
            current, path, rels = queue.pop(0)
            for neighbor, rel in adj.get(current, []):
                if neighbor not in visited:
                    new_path = path + [neighbor]
                    new_rels = rels + [rel]
                    if neighbor == to_entity:
                        return {"entities": new_path, "relations": new_rels}
                    visited.add(neighbor)
                    queue.append((neighbor, new_path, new_rels))
        return None

    def search_concepts(self, keyword: str) -> list:
        """在本体中搜索包含关键词的概念（实体名、标签、属性标签）"""
        results = []
        for name, entity in self.entities.items():
            # 匹配实体名或标签
            if keyword in name.lower() or keyword in entity.get("label", ""):
                results.append({"type": "entity", "name": name, "label": entity["label"]})
            # 匹配属性标签
            for prop_name, prop_def in entity.get("properties", {}).items():
                if keyword in prop_name.lower() or keyword in prop_def.get("label", ""):
                    results.append({
                        "type": "property",
                        "entity": name,
                        "name": prop_name,
                        "label": prop_def["label"]
                    })
        return results

    def get_all_entity_names(self) -> list:
        """返回所有实体名列表"""
        return list(self.entities.keys())

    def get_all_labels(self) -> dict:
        """返回所有实体的中英文标签映射"""
        return {name: e["label"] for name, e in self.entities.items()}

    def to_description(self) -> str:
        """生成本体的文本描述（用于 LLM prompt）"""
        lines = [f"本体名称: {self.ontology.get('name', '未知')}"]
        lines.append(f"描述: {self.ontology.get('description', '')}")
        lines.append("\n实体定义:")
        for name, entity in self.entities.items():
            lines.append(f"  [{name}] ({entity['label']}): {entity.get('description', '')}")
            for prop_name, prop_def in entity.get("properties", {}).items():
                extra = ""
                if prop_def.get("is_key"):
                    extra = " [主键]"
                if prop_def.get("is_fk"):
                    extra = f" [外键->{ prop_def['ref_entity']}]"
                lines.append(f"    - {prop_name} ({prop_def['label']}): {prop_def['type']}{extra}")
        lines.append("\n关系定义:")
        for rel in self.relations:
            join_hint = ""
            if rel.get("from_field") and rel.get("to_field"):
                join_hint = f" ({rel['from_field']}={rel['to_field']})"
            lines.append(f"  {rel['from']} --[{rel['label']}]--> {rel['to']}{join_hint}")
        return "\n".join(lines)
