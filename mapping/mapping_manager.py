"""Mapping manager - manage ontology/table and field mappings."""
from collections import deque
import json
import os


class MappingManager:
    """Read-only mapping manager loaded from *_mapping.json files.

    Backward compatible:
    - legacy single-table entity mapping:
      {table_name,file_name,field_mappings,...}
    - multi-table (entity-internal) extension:
      {secondary_tables,field_sources,...}
    """

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

    @staticmethod
    def _lookup_by_key_case_insensitive(payload: dict, key: str):
        if not isinstance(payload, dict):
            return None
        if key in payload:
            return payload.get(key)

        target = str(key or "").strip().lower()
        if not target:
            return None
        for item_key, item_value in payload.items():
            if str(item_key or "").strip().lower() == target:
                return item_value
        return None

    @staticmethod
    def _lookup_key_case_insensitive(payload: dict, key: str) -> str:
        if not isinstance(payload, dict):
            return ""
        if key in payload:
            return str(key)

        target = str(key or "").strip().lower()
        if not target:
            return ""
        for item_key in payload.keys():
            if str(item_key or "").strip().lower() == target:
                return str(item_key)
        return ""

    def get_table_mapping(self, source_id: str, entity_name: str) -> dict | None:
        mapping = self.mappings.get(source_id)
        if not mapping:
            return None
        table_mappings = mapping.get("table_mappings", {})
        if not isinstance(table_mappings, dict):
            return None
        value = self._lookup_by_key_case_insensitive(table_mappings, entity_name)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _resolve_primary_table(table_mapping: dict) -> dict:
        if not isinstance(table_mapping, dict):
            return {
                "table_name": "",
                "file_name": "",
            }

        primary = table_mapping.get("primary_table", {})
        if not isinstance(primary, dict):
            primary = {}

        table_name = str(primary.get("table_name", "")).strip() or str(table_mapping.get("table_name", "")).strip()
        file_name = str(primary.get("file_name", "")).strip() or str(table_mapping.get("file_name", "")).strip()
        return {
            "table_name": table_name,
            "file_name": file_name,
        }

    def get_table_name(self, source_id: str, entity_name: str) -> str | None:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return None
        primary = self._resolve_primary_table(table_mapping)
        primary_table = str(primary.get("table_name", "")).strip()
        if primary_table:
            return primary_table

        field_sources = self.get_field_sources_map(source_id, entity_name)
        for item in field_sources.values():
            if not isinstance(item, dict):
                continue
            table_name = str(item.get("table_name", "") or item.get("table", "")).strip()
            if table_name:
                return table_name

        secondary = self.get_secondary_tables(source_id, entity_name)
        if secondary:
            table_name = str(secondary[0].get("table_name", "")).strip()
            if table_name:
                return table_name
        return None

    def get_file_name(self, source_id: str, entity_name: str) -> str | None:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return None
        primary = self._resolve_primary_table(table_mapping)
        primary_file = str(primary.get("file_name", "")).strip()
        if primary_file:
            return primary_file

        anchor_table = self.get_table_name(source_id, entity_name)
        if not anchor_table:
            return None
        return self._find_table_file_name(source_id, anchor_table)

    def _find_table_file_name(self, source_id: str, table_name: str) -> str:
        table_text = str(table_name or "").strip()
        if not table_text:
            return ""

        mapping = self.mappings.get(source_id, {})
        table_mappings = mapping.get("table_mappings", {}) if isinstance(mapping, dict) else {}
        if not isinstance(table_mappings, dict):
            table_mappings = {}

        target = table_text.lower()
        for table_mapping in table_mappings.values():
            if not isinstance(table_mapping, dict):
                continue
            primary = self._resolve_primary_table(table_mapping)
            primary_table = str(primary.get("table_name", "")).strip().lower()
            if primary_table == target:
                file_name = str(primary.get("file_name", "")).strip()
                if file_name:
                    return file_name

            raw_secondary = table_mapping.get("secondary_tables", [])
            if isinstance(raw_secondary, dict):
                raw_secondary = [item for item in raw_secondary.values() if isinstance(item, dict)]
            if not isinstance(raw_secondary, list):
                raw_secondary = []
            for item in raw_secondary:
                sec_table = str(item.get("table_name", "") or item.get("table", "")).strip().lower()
                if sec_table != target:
                    continue
                file_name = str(item.get("file_name", "")).strip()
                if file_name:
                    return file_name

        return f"{table_text}.xlsx"

    @staticmethod
    def _normalize_join_pairs(raw_join_pairs) -> list[tuple[str, str]]:
        if isinstance(raw_join_pairs, dict):
            raw_join_pairs = [raw_join_pairs]
        if not isinstance(raw_join_pairs, list):
            return []

        pairs = []
        seen = set()
        for item in raw_join_pairs:
            if isinstance(item, dict):
                left_field = str(
                    item.get("left", "")
                    or item.get("left_field", "")
                    or item.get("primary_field", "")
                ).strip()
                right_field = str(
                    item.get("right", "")
                    or item.get("right_field", "")
                    or item.get("secondary_field", "")
                ).strip()
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                left_field = str(item[0]).strip()
                right_field = str(item[1]).strip()
            else:
                continue
            if not left_field or not right_field:
                continue
            key = (left_field, right_field)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
        return pairs

    @staticmethod
    def _normalize_join_type(raw_value) -> str:
        text = str(raw_value or "").strip().lower()
        if text in {"left", "left_join", "left join"}:
            return "left"
        return "inner"

    def get_secondary_tables(self, source_id: str, entity_name: str) -> list[dict]:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return []

        raw_secondary = table_mapping.get("secondary_tables", table_mapping.get("entity_tables", []))
        if isinstance(raw_secondary, dict):
            items = []
            for key, value in raw_secondary.items():
                if isinstance(value, dict):
                    merged = dict(value)
                    merged.setdefault("table_key", str(key).strip())
                    items.append(merged)
            raw_secondary = items

        if not isinstance(raw_secondary, list):
            return []

        result = []
        seen = set()
        for item in raw_secondary:
            if not isinstance(item, dict):
                continue
            table_name = str(item.get("table_name", "") or item.get("table", "")).strip()
            if not table_name:
                continue
            table_key = str(item.get("table_key", "")).strip() or table_name
            key = (table_name.lower(), table_key.lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "table_name": table_name,
                    "table_key": table_key,
                    "file_name": str(item.get("file_name", "")).strip(),
                    "join_pairs": self._normalize_join_pairs(
                        item.get("join_pairs", item.get("join_on", item.get("join_keys", [])))
                    ),
                    "join_type": self._normalize_join_type(item.get("join_type", "left")),
                }
            )
        return result

    @staticmethod
    def _normalize_table_relations_payload(raw_relations) -> list[dict]:
        if isinstance(raw_relations, dict):
            raw_relations = list(raw_relations.values())
        if not isinstance(raw_relations, list):
            return []

        result = []
        seen = set()
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            left_table = str(
                item.get("left_table", "")
                or item.get("from_table", "")
                or item.get("table_a", "")
                or item.get("left", "")
            ).strip()
            right_table = str(
                item.get("right_table", "")
                or item.get("to_table", "")
                or item.get("table_b", "")
                or item.get("right", "")
            ).strip()
            if not left_table or not right_table:
                continue

            join_pairs = MappingManager._normalize_join_pairs(
                item.get("join_pairs", item.get("join_on", item.get("pairs", [])))
            )
            if not join_pairs:
                left_field = str(item.get("left_field", "")).strip()
                right_field = str(item.get("right_field", "")).strip()
                if left_field and right_field:
                    join_pairs = [(left_field, right_field)]
            if not join_pairs:
                continue

            join_type = MappingManager._normalize_join_type(item.get("join_type", "left"))
            pair_key = tuple((str(left).lower(), str(right).lower()) for left, right in join_pairs)
            unique_key = (left_table.lower(), right_table.lower(), pair_key, join_type)
            if unique_key in seen:
                continue
            seen.add(unique_key)
            result.append({
                "left_table": left_table,
                "right_table": right_table,
                "join_pairs": join_pairs,
                "join_type": join_type,
            })
        return result

    def get_table_relations(self, source_id: str) -> list[dict]:
        mapping = self.mappings.get(source_id, {})
        if not isinstance(mapping, dict):
            return []

        relations = self._normalize_table_relations_payload(mapping.get("table_relations", []))

        table_mappings = mapping.get("table_mappings", {})
        if not isinstance(table_mappings, dict):
            table_mappings = {}
        legacy_relations = []
        for entity_name in table_mappings.keys():
            table_mapping = self.get_table_mapping(source_id, str(entity_name))
            if not isinstance(table_mapping, dict):
                continue
            primary = self._resolve_primary_table(table_mapping)
            primary_table = str(primary.get("table_name", "")).strip()
            if not primary_table:
                continue
            secondary_tables = self.get_secondary_tables(source_id, str(entity_name))
            for item in secondary_tables:
                secondary_table = str(item.get("table_name", "")).strip()
                join_pairs = item.get("join_pairs", [])
                if not secondary_table or not join_pairs:
                    continue
                legacy_relations.append({
                    "left_table": primary_table,
                    "right_table": secondary_table,
                    "join_pairs": join_pairs,
                    "join_type": self._normalize_join_type(item.get("join_type", "left")),
                })

        merged = relations + legacy_relations
        return self._normalize_table_relations_payload(merged)

    def find_table_join_path(self, source_id: str, start_table: str, target_table: str) -> list[dict]:
        start = str(start_table or "").strip()
        target = str(target_table or "").strip()
        if not start or not target:
            return []
        if start.lower() == target.lower():
            return []

        relations = self.get_table_relations(source_id)
        if not relations:
            return []

        graph = {}
        for relation in relations:
            left_table = str(relation.get("left_table", "")).strip()
            right_table = str(relation.get("right_table", "")).strip()
            join_pairs = relation.get("join_pairs", [])
            join_type = self._normalize_join_type(relation.get("join_type", "left"))
            if not left_table or not right_table or not join_pairs:
                continue

            left_key = left_table.lower()
            right_key = right_table.lower()
            graph.setdefault(left_key, []).append({
                "left_table": left_table,
                "right_table": right_table,
                "join_pairs": join_pairs,
                "join_type": join_type,
            })
            graph.setdefault(right_key, []).append({
                "left_table": right_table,
                "right_table": left_table,
                "join_pairs": [(right, left) for left, right in join_pairs],
                "join_type": join_type,
            })

        start_key = start.lower()
        target_key = target.lower()
        if start_key not in graph:
            return []

        queue = deque([(start_key, [])])
        visited = {start_key}
        while queue:
            table_key, path = queue.popleft()
            for edge in graph.get(table_key, []):
                next_key = str(edge.get("right_table", "")).strip().lower()
                if not next_key or next_key in visited:
                    continue
                next_path = path + [edge]
                if next_key == target_key:
                    return next_path
                visited.add(next_key)
                queue.append((next_key, next_path))
        return []

    def get_field_mapping(self, source_id: str, entity_name: str) -> dict:
        """Return primary-table field mapping only (legacy behavior)."""
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return {}
        field_mappings = table_mapping.get("field_mappings", {})
        return field_mappings if isinstance(field_mappings, dict) else {}

    def get_field_sources_map(self, source_id: str, entity_name: str) -> dict:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return {}
        field_sources = table_mapping.get("field_sources", table_mapping.get("field_source_mappings", {}))
        return field_sources if isinstance(field_sources, dict) else {}

    def resolve_field_binding(self, source_id: str, entity_name: str, ontology_field: str) -> dict:
        """Resolve ontology field to physical column and source table.

        Returns:
        {
          ontology_field,
          actual_field,
          table_name,
          file_name,
          join_pairs: [(left_actual,right_actual), ...],  # primary->secondary
          join_type: inner|left,
          source: primary_field_mapping|field_sources|fallback
        }
        """
        field_text = str(ontology_field or "").strip()
        table_mapping = self.get_table_mapping(source_id, entity_name)
        primary = self._resolve_primary_table(table_mapping or {})
        primary_table = str(primary.get("table_name", "")).strip()
        primary_file = str(primary.get("file_name", "")).strip()
        if not field_text:
            return {
                "ontology_field": "",
                "actual_field": "",
                "table_name": primary_table,
                "file_name": primary_file,
                "join_pairs": [],
                "join_type": "inner",
                "source": "fallback",
            }

        field_mapping = self.get_field_mapping(source_id, entity_name)
        source_key = self._lookup_key_case_insensitive(field_mapping, field_text)
        if source_key:
            return {
                "ontology_field": source_key,
                "actual_field": str(field_mapping.get(source_key, "")).strip(),
                "table_name": primary_table,
                "file_name": primary_file,
                "join_pairs": [],
                "join_type": "inner",
                "source": "primary_field_mapping",
            }

        field_sources = self.get_field_sources_map(source_id, entity_name)
        field_source_key = self._lookup_key_case_insensitive(field_sources, field_text)
        if not field_source_key:
            return {
                "ontology_field": field_text,
                "actual_field": field_text,
                "table_name": primary_table,
                "file_name": primary_file,
                "join_pairs": [],
                "join_type": "inner",
                "source": "fallback",
            }

        source_item = field_sources.get(field_source_key)
        if isinstance(source_item, str):
            source_item = {"actual_field": str(source_item).strip()}
        if not isinstance(source_item, dict):
            source_item = {}

        actual_field = str(source_item.get("actual_field", "") or source_item.get("field", "")).strip()
        table_name = str(source_item.get("table_name", "") or source_item.get("table", "")).strip()
        table_key = str(source_item.get("table_key", "")).strip()

        secondary_tables = self.get_secondary_tables(source_id, entity_name)
        secondary_lookup = {}
        for item in secondary_tables:
            table_name_key = str(item.get("table_name", "")).strip().lower()
            table_key_key = str(item.get("table_key", "")).strip().lower()
            if table_name_key:
                secondary_lookup[table_name_key] = item
            if table_key_key:
                secondary_lookup[table_key_key] = item

        secondary_meta = None
        if table_name and table_name.lower() in secondary_lookup:
            secondary_meta = secondary_lookup.get(table_name.lower())
        elif table_key and table_key.lower() in secondary_lookup:
            secondary_meta = secondary_lookup.get(table_key.lower())

        if secondary_meta:
            table_name = str(secondary_meta.get("table_name", "")).strip() or table_name
            file_name = str(secondary_meta.get("file_name", "")).strip()
            join_pairs = secondary_meta.get("join_pairs", [])
            join_type = secondary_meta.get("join_type", "left")
        else:
            file_name = str(source_item.get("file_name", "")).strip()
            join_pairs = []
            join_type = "left"

        # Field-level overrides have higher priority.
        if not actual_field:
            actual_field = str(source_item.get("actual", "")).strip()
        if not table_name:
            table_name = primary_table
        if not file_name and table_name == primary_table:
            file_name = primary_file
        if not file_name and table_name:
            file_name = self._find_table_file_name(source_id, table_name)

        join_pairs_override = self._normalize_join_pairs(
            source_item.get("join_pairs", source_item.get("join_on", source_item.get("join_keys", [])))
        )
        if join_pairs_override:
            join_pairs = join_pairs_override
        join_type = self._normalize_join_type(source_item.get("join_type", join_type))

        return {
            "ontology_field": field_source_key,
            "actual_field": actual_field or field_text,
            "table_name": table_name,
            "file_name": file_name,
            "join_pairs": join_pairs,
            "join_type": join_type,
            "source": "field_sources",
        }

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
            lowered_targets = {
                str(key or "").strip().lower()
                for key in candidate_keys
                if str(key or "").strip()
            }
            if lowered_targets:
                fallback_hits = []
                for key, value in semantics_map.items():
                    key_text = str(key or "").strip()
                    if not key_text or not isinstance(value, dict):
                        continue
                    if key_text.lower() in lowered_targets:
                        fallback_hits.append(value)
                if fallback_hits:
                    raw_semantics = fallback_hits[0]
        if not isinstance(raw_semantics, dict):
            return None

        return self._normalize_field_value_semantics(raw_semantics)

    def get_self_join_policy(self, source_id: str, entity_name: str) -> dict:
        table_mapping = self.get_table_mapping(source_id, entity_name)
        if not table_mapping:
            return {
                "enabled": False,
                "mode": "disabled",
                "candidate_pairs": [],
            }

        raw_policy = table_mapping.get("self_join_policy", {})
        if isinstance(raw_policy, bool):
            return {
                "enabled": bool(raw_policy),
                "mode": "llm" if raw_policy else "disabled",
                "candidate_pairs": [],
            }
        if not isinstance(raw_policy, dict):
            raw_policy = {}

        enabled = bool(raw_policy.get("enabled", False))
        mode = str(raw_policy.get("mode", "llm" if enabled else "disabled")).strip().lower()
        if mode not in {"disabled", "llm", "explicit_only"}:
            mode = "llm" if enabled else "disabled"
        if not enabled:
            mode = "disabled"

        raw_pairs = raw_policy.get("candidate_pairs", raw_policy.get("pairs", []))
        if isinstance(raw_pairs, dict):
            raw_pairs = [raw_pairs]
        if not isinstance(raw_pairs, list):
            raw_pairs = []

        candidate_pairs = []
        seen = set()
        for item in raw_pairs:
            if not isinstance(item, dict):
                continue
            left_field = str(item.get("left_field", "")).strip().upper()
            right_field = str(item.get("right_field", "")).strip().upper()
            if not left_field or not right_field:
                continue
            pair_key = (left_field, right_field)
            if pair_key in seen:
                continue
            seen.add(pair_key)
            candidate_pairs.append({
                "left_field": left_field,
                "right_field": right_field,
                "source": str(item.get("source", "mapping_policy")).strip() or "mapping_policy",
                "reason": str(item.get("reason", "configured")).strip() or "configured",
            })

        return {
            "enabled": enabled,
            "mode": mode,
            "candidate_pairs": candidate_pairs,
        }

    def get_reverse_field_mapping(self, source_id: str, entity_name: str) -> dict:
        # Keep reverse-mapping based on primary table only.
        mapping = self.get_field_mapping(source_id, entity_name)
        return {actual: ontology for ontology, actual in mapping.items()}

    def ontology_field_to_actual(self, source_id: str, entity_name: str, ontology_field: str) -> str | None:
        resolved = self.resolve_field_binding(source_id, entity_name, ontology_field)
        actual_field = str(resolved.get("actual_field", "")).strip()
        return actual_field or None

    def actual_field_to_ontology(self, source_id: str, entity_name: str, actual_field: str) -> str | None:
        reverse_mapping = self.get_reverse_field_mapping(source_id, entity_name)
        return self._lookup_by_key_case_insensitive(reverse_mapping, actual_field)

    def to_description(self, source_id: str = None) -> str:
        source_ids = [source_id] if source_id else self.get_source_ids()
        lines = []
        for sid in source_ids:
            mapping = self.mappings.get(sid)
            if not mapping:
                continue
            lines.append(f"\n数据源: {mapping.get('source_name', sid)} ({sid})")
            lines.append(f"  描述: {mapping.get('description', '')}")
            table_relations = self.get_table_relations(sid)
            for relation in table_relations:
                lines.append(
                    f"  [table_relation] {relation.get('left_table','')} -> {relation.get('right_table','')} "
                    f"join={relation.get('join_pairs', [])} type={relation.get('join_type', 'left')}"
                )
            table_mappings = mapping.get("table_mappings", {})
            if not isinstance(table_mappings, dict):
                continue
            for entity, table_mapping in table_mappings.items():
                primary = self._resolve_primary_table(table_mapping if isinstance(table_mapping, dict) else {})
                table_name = primary.get("table_name", "")
                file_name = primary.get("file_name", "")
                lines.append(f"  实体 {entity} -> 主表 {table_name} (文件: {file_name})")

                field_mappings = self.get_field_mapping(sid, entity)
                for ontology_field, actual_field in field_mappings.items():
                    lines.append(f"    {ontology_field} -> {actual_field}")

                secondary_tables = self.get_secondary_tables(sid, entity)
                for item in secondary_tables:
                    lines.append(
                        f"    [secondary] {item.get('table_name','')} "
                        f"join={item.get('join_pairs', [])} type={item.get('join_type', 'left')}"
                    )

                field_sources = self.get_field_sources_map(sid, entity)
                for field_name in field_sources.keys():
                    resolved = self.resolve_field_binding(sid, entity, field_name)
                    lines.append(
                        f"    [field_source] {field_name} -> {resolved.get('table_name','')}.{resolved.get('actual_field','')}"
                    )
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

        rate_true_values_raw = payload.get("rate_true_values", [])
        if isinstance(rate_true_values_raw, str):
            rate_true_values_raw = [item.strip() for item in rate_true_values_raw.split(",") if item.strip()]
        if not isinstance(rate_true_values_raw, list):
            rate_true_values_raw = []
        rate_true_values = []
        seen_true = set()
        for item in rate_true_values_raw:
            text = str(item).strip()
            if not text or text in seen_true:
                continue
            seen_true.add(text)
            rate_true_values.append(text)

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
            "rate_true_values": rate_true_values,
            "confidence_threshold": threshold,
            "unknown_policy": unknown_policy,
        }
