"""Mapping manager - manage ontology/table and field mappings."""
import json
import os


class MappingManager:
    """Read-only mapping manager loaded from *_mapping.json files."""

    def __init__(self, mapping_dir: str):
        self.mapping_dir = mapping_dir
        self.mappings = {}
        self._load_all()

    def _load_all(self):
        for fn in os.listdir(self.mapping_dir):
            if not fn.endswith("_mapping.json"):
                continue
            path = os.path.join(self.mapping_dir, fn)
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
            source_id = str(data.get("source_id", "")).strip()
            if source_id:
                self.mappings[source_id] = data

    def get_source_ids(self) -> list:
        return list(self.mappings.keys())

    def get_source_info(self, source_id: str) -> dict | None:
        mapping = self.mappings.get(source_id)
        if not mapping:
            return None
        return {
            "source_id": mapping.get("source_id", source_id),
            "source_name": mapping.get("source_name", source_id),
            "description": mapping.get("description", ""),
        }

    def get_table_mapping(self, source_id: str, entity_name: str) -> dict | None:
        mapping = self.mappings.get(source_id)
        if not mapping:
            return None
        table_mappings = mapping.get("table_mappings", {})
        if not isinstance(table_mappings, dict):
            return None
        return table_mappings.get(entity_name)

    def get_table_name(self, source_id: str, entity_name: str) -> str | None:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return None
        return table_mapping.get("table_name")

    def get_file_name(self, source_id: str, entity_name: str) -> str | None:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return None
        return table_mapping.get("file_name")

    def get_field_mapping(self, source_id: str, entity_name: str) -> dict:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return {}
        field_mappings = table_mapping.get("field_mappings", {})
        return field_mappings if isinstance(field_mappings, dict) else {}

    def get_field_value_semantics_map(self, source_id: str, entity_name: str) -> dict:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return {}

        raw = table_mapping.get("field_value_semantics", {})
        if not isinstance(raw, dict) or not raw:
            raw = table_mapping.get("value_semantics", {})
        return raw if isinstance(raw, dict) else {}

    def get_field_value_semantics(self, source_id: str, entity_name: str, field_name: str) -> dict | None:
        field_text = str(field_name or "").strip()
        if not field_text:
            return None

        semantics_map = self.get_field_value_semantics_map(source_id, entity_name)
        if not semantics_map:
            return None

        actual_field = self.ontology_field_to_actual(source_id, entity_name, field_text) or ""
        candidate_keys = []
        for key in (field_text, field_text.upper(), actual_field, str(actual_field).upper()):
            key_text = str(key or "").strip()
            if key_text and key_text not in candidate_keys:
                candidate_keys.append(key_text)

        raw_semantics = None
        for key in candidate_keys:
            value = semantics_map.get(key)
            if isinstance(value, dict):
                raw_semantics = value
                break
        if not isinstance(raw_semantics, dict):
            return None

        return self._normalize_field_value_semantics(raw_semantics)

    def get_reverse_field_mapping(self, source_id: str, entity_name: str) -> dict:
        mapping = self.get_field_mapping(source_id, entity_name)
        return {actual: ontology for ontology, actual in mapping.items()}

    def ontology_field_to_actual(self, source_id: str, entity_name: str, ontology_field: str) -> str | None:
        field_mapping = self.get_field_mapping(source_id, entity_name)
        return field_mapping.get(ontology_field)

    def actual_field_to_ontology(self, source_id: str, entity_name: str, actual_field: str) -> str | None:
        reverse_mapping = self.get_reverse_field_mapping(source_id, entity_name)
        return reverse_mapping.get(actual_field)

    def to_description(self, source_id: str = None) -> str:
        source_ids = [source_id] if source_id else self.get_source_ids()
        lines = []
        for sid in source_ids:
            mapping = self.mappings.get(sid)
            if not mapping:
                continue
            lines.append(f"\n数据源: {mapping.get('source_name', sid)} ({sid})")
            lines.append(f"  描述: {mapping.get('description', '')}")
            table_mappings = mapping.get("table_mappings", {})
            if not isinstance(table_mappings, dict):
                continue
            for entity, table_mapping in table_mappings.items():
                table_name = table_mapping.get("table_name", "")
                file_name = table_mapping.get("file_name", "")
                lines.append(f"  实体 {entity} -> 表 {table_name} (文件: {file_name})")
                field_mappings = table_mapping.get("field_mappings", {})
                if not isinstance(field_mappings, dict):
                    continue
                for ontology_field, actual_field in field_mappings.items():
                    lines.append(f"    {ontology_field} -> {actual_field}")
        return "\n".join(lines)

    @staticmethod
    def _normalize_field_value_semantics(payload: dict) -> dict:
        closed_set_raw = payload.get("closed_set", [])
        if isinstance(closed_set_raw, str):
            closed_set_raw = [item.strip() for item in closed_set_raw.split(",") if item.strip()]
        if not isinstance(closed_set_raw, list):
            closed_set_raw = []

        closed_set = []
        seen = set()
        for item in closed_set_raw:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            closed_set.append(text)

        labels_raw = payload.get("labels", {})
        labels = {}
        if isinstance(labels_raw, dict):
            for key, value in labels_raw.items():
                key_text = str(key).strip()
                if not key_text:
                    continue
                labels[key_text] = str(value).strip()

        aliases_raw = payload.get("aliases", {})
        aliases = {}
        if isinstance(aliases_raw, dict):
            for alias, target in aliases_raw.items():
                alias_text = str(alias).strip()
                target_text = str(target).strip()
                if not alias_text or not target_text:
                    continue
                aliases[alias_text] = target_text

        threshold = payload.get("confidence_threshold", 0.65)
        try:
            threshold = float(threshold)
        except Exception:
            threshold = 0.65
        threshold = max(0.0, min(1.0, threshold))

        unknown_policy = str(payload.get("unknown_policy", "fallback")).strip().lower() or "fallback"
        if unknown_policy not in {"fallback", "ask", "reject"}:
            unknown_policy = "fallback"

        return {
            "closed_set": closed_set,
            "labels": labels,
            "aliases": aliases,
            "confidence_threshold": threshold,
            "unknown_policy": unknown_policy,
        }
