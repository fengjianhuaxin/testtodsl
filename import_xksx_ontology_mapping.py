"""Import ontology/mapping from Excel.

Supported Excel modes:
1) Table-structure mode (preferred):
   columns = 中文表名, 英文表名, 中文字段名, 英文字段名, 数据类型, 备注
   -> generates entities + table mappings + empty data table files.
2) Legacy two-sheet mode:
   sheet1(entity/property), sheet2(relations), then tries to adapt to existing source tables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from typing import Any

import pandas as pd

import config


@dataclass
class TableMeta:
    table_name: str
    file_name: str
    columns: list[str]
    upper_to_actual: dict[str, str]


TABLE_STRUCTURE_FIELDS = ("table_cn", "table_en", "field_cn", "field_en", "data_type", "remark")
TABLE_HEADER_ALIAS = {
    "中文表名": "table_cn",
    "英文表名": "table_en",
    "中文字段名": "field_cn",
    "英文字段名": "field_en",
    "数据类型": "data_type",
    "备注": "remark",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_header(value: Any) -> str:
    text = _clean_text(value)
    return re.sub(r"\s+", "", text).lower()


def _dedupe_keep_order(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = _clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _infer_type(field_name: str) -> str:
    upper = str(field_name or "").upper()
    if any(token in upper for token in ("DATE", "TIME")):
        return "date"
    if any(
        token in upper
        for token in (
            "COUNT",
            "NUM",
            "AMOUNT",
            "COST",
            "TOTAL",
            "RATE",
            "PERCENT",
            "USE",
            "SIZE",
            "CAPACITY",
            "QUANTITY",
            "FEE",
            "PRICE",
            "VALUE",
        )
    ):
        return "number"
    return "string"


def _map_data_type(raw_data_type: str, field_name: str) -> str:
    text = str(raw_data_type or "").strip().lower()
    if not text:
        return _infer_type(field_name)

    if any(token in text for token in ("date", "time", "year", "timestamp", "datetime")):
        return "date"
    if any(token in text for token in ("bool", "boolean", "bit")):
        return "boolean"
    if any(
        token in text
        for token in ("int", "integer", "number", "numeric", "decimal", "float", "double", "real", "money")
    ):
        return "number"
    return "string"


def _is_primary_key(remark: str) -> bool:
    text = str(remark or "").strip().lower()
    if not text:
        return False
    if "主键" in text:
        return True
    return bool(re.search(r"\bpk\b", text, flags=re.IGNORECASE))


def _standardize_table_structure_frame(frame: pd.DataFrame) -> pd.DataFrame | None:
    if frame is None or frame.empty:
        return None

    norm_to_actual = {_normalize_header(col): col for col in frame.columns}
    normalized = pd.DataFrame()

    # header-based mode
    for cn_name, key in TABLE_HEADER_ALIAS.items():
        norm = _normalize_header(cn_name)
        if norm in norm_to_actual:
            normalized[key] = frame[norm_to_actual[norm]]

    # positional fallback (requires at least 6 cols to avoid mis-detecting legacy sheets)
    if len(normalized.columns) < 4:
        if frame.shape[1] < 6:
            return None
        subset = frame.iloc[:, :6].copy()
        subset.columns = list(TABLE_STRUCTURE_FIELDS)
        normalized = subset
    else:
        for field in TABLE_STRUCTURE_FIELDS:
            if field not in normalized.columns:
                normalized[field] = ""
        normalized = normalized.loc[:, TABLE_STRUCTURE_FIELDS]

    for col in TABLE_STRUCTURE_FIELDS:
        normalized[col] = normalized[col].map(_clean_text)

    normalized["table_cn"] = normalized["table_cn"].replace("", pd.NA).ffill().fillna("")
    normalized["table_en"] = normalized["table_en"].replace("", pd.NA).ffill().fillna("")

    normalized = normalized[
        (normalized["table_en"] != "")
        & (normalized["field_en"] != "")
    ].copy()
    if normalized.empty:
        return None

    # avoid false positives by requiring mostly identifier-like english names
    table_ok = normalized["table_en"].map(lambda x: bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(x))))
    field_ok = normalized["field_en"].map(lambda x: bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(x))))
    if table_ok.mean() < 0.6 or field_ok.mean() < 0.6:
        return None

    return normalized


def _load_table_structure_book(excel_file: pd.ExcelFile) -> tuple[str, OrderedDict[str, dict[str, Any]]] | None:
    for sheet_name in excel_file.sheet_names:
        frame = excel_file.parse(sheet_name)
        normalized = _standardize_table_structure_frame(frame)
        if normalized is None:
            continue

        tables: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for _, row in normalized.iterrows():
            table_cn = _clean_text(row.get("table_cn"))
            table_en = _clean_text(row.get("table_en"))
            field_cn = _clean_text(row.get("field_cn"))
            field_en = _clean_text(row.get("field_en"))
            raw_data_type = _clean_text(row.get("data_type"))
            remark = _clean_text(row.get("remark"))
            if not table_en or not field_en:
                continue

            entity_key = table_en.upper()
            field_key = field_en.upper()
            if entity_key not in tables:
                tables[entity_key] = {
                    "label": table_cn or table_en,
                    "table_name": table_en,
                    "fields": OrderedDict(),
                }

            field_info = tables[entity_key]["fields"].get(field_key)
            if not field_info:
                tables[entity_key]["fields"][field_key] = {
                    "actual_name": field_en,
                    "label": field_cn or field_en,
                    "raw_type": raw_data_type,
                    "type": _map_data_type(raw_data_type, field_en),
                    "remark": remark,
                    "is_key": _is_primary_key(remark),
                }
                continue

            # fill missing values on duplicates
            if not field_info.get("label") and field_cn:
                field_info["label"] = field_cn
            if not field_info.get("raw_type") and raw_data_type:
                field_info["raw_type"] = raw_data_type
                field_info["type"] = _map_data_type(raw_data_type, field_en)
            if (not field_info.get("remark")) and remark:
                field_info["remark"] = remark
            if _is_primary_key(remark):
                field_info["is_key"] = True

        if tables:
            return sheet_name, tables
    return None


def _build_from_table_structure(
    tables: OrderedDict[str, dict[str, Any]],
    *,
    excel_path: str,
    sheet_name: str,
    source_id: str,
    source_name: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, int]]:
    ontology_entities: OrderedDict[str, dict[str, Any]] = OrderedDict()
    table_mappings: OrderedDict[str, dict[str, Any]] = OrderedDict()
    table_specs: list[dict[str, Any]] = []

    field_count = 0
    primary_key_field_count = 0

    for entity_key, table_info in tables.items():
        table_name = str(table_info.get("table_name", "")).strip()
        entity_label = str(table_info.get("label", "")).strip() or table_name or entity_key
        fields = table_info.get("fields", {})
        if not table_name or not isinstance(fields, OrderedDict) or not fields:
            continue

        properties = OrderedDict()
        field_mappings = OrderedDict()
        actual_columns = []

        for property_name, field_info in fields.items():
            actual_name = _clean_text(field_info.get("actual_name"))
            if not actual_name:
                continue
            aliases = _dedupe_keep_order([actual_name, actual_name.lower(), actual_name.upper()])
            prop_def = {
                "aliases": aliases,
                "label": _clean_text(field_info.get("label")) or property_name,
                "type": _clean_text(field_info.get("type")) or "string",
                "value_aliases": {},
            }
            if field_info.get("is_key"):
                prop_def["is_key"] = True
                primary_key_field_count += 1

            properties[property_name] = prop_def
            field_mappings[property_name] = actual_name
            actual_columns.append(actual_name)
            field_count += 1

        if not properties:
            continue

        ontology_entities[entity_key] = {
            "label": entity_label,
            "description": f"Imported from {os.path.basename(excel_path)}::{sheet_name}",
            "aliases": _dedupe_keep_order([table_name, entity_label]),
            "properties": properties,
        }
        table_mappings[entity_key] = {
            "table_name": table_name,
            "file_name": f"{table_name}.xlsx",
            "field_mappings": field_mappings,
            "field_value_semantics": {},
        }
        table_specs.append(
            {
                "entity_key": entity_key,
                "table_name": table_name,
                "file_name": f"{table_name}.xlsx",
                "columns": actual_columns,
            }
        )

    relation_items = []
    identity_join_priority = [
        ("CERT_TYPE", "CERT_NO"),
        ("CERT_NO",),
    ]

    entity_names = list(ontology_entities.keys())
    for i in range(len(entity_names)):
        for j in range(i + 1, len(entity_names)):
            left_entity = entity_names[i]
            right_entity = entity_names[j]
            left_props = ontology_entities[left_entity].get("properties", {})
            right_props = ontology_entities[right_entity].get("properties", {})

            selected_fields: tuple[str, ...] | None = None
            for candidate_fields in identity_join_priority:
                if all(field in left_props and field in right_props for field in candidate_fields):
                    selected_fields = candidate_fields
                    break

            if not selected_fields:
                continue

            relation_items.append(
                {
                    "from": left_entity,
                    "to": right_entity,
                    "type": "identity_match",
                    "label": "证件关联",
                    "from_field": ",".join(selected_fields),
                    "to_field": ",".join(selected_fields),
                }
            )

    ontology_json = {
        "name": f"{source_id}_ontology",
        "version": "1.0",
        "description": f"Imported from {os.path.basename(excel_path)} (sheet={sheet_name})",
        "entities": ontology_entities,
        "relations": relation_items,
    }

    mapping_json = {
        "source_name": source_name,
        "source_id": source_id,
        "description": f"Imported from {os.path.basename(excel_path)} (sheet={sheet_name})",
        "table_mappings": table_mappings,
    }

    stats = {
        "entity_count": len(ontology_entities),
        "field_count": field_count,
        "primary_key_field_count": primary_key_field_count,
        "table_count": len(table_specs),
        "relation_count": len(relation_items),
    }
    return ontology_json, mapping_json, table_specs, stats


def _materialize_table_files(source_dir: str, table_specs: list[dict[str, Any]]) -> list[str]:
    generated = []
    if not source_dir:
        return generated

    os.makedirs(source_dir, exist_ok=True)
    for spec in table_specs:
        file_name = _clean_text(spec.get("file_name"))
        columns = spec.get("columns", [])
        if not file_name or not isinstance(columns, list) or not columns:
            continue
        file_path = os.path.join(source_dir, file_name)
        pd.DataFrame(columns=columns).to_excel(file_path, index=False)
        generated.append(file_path)
    return generated


def _load_entity_rows(entity_sheet_df: pd.DataFrame) -> OrderedDict[str, OrderedDict[str, dict[str, Any]]]:
    frame = entity_sheet_df.copy()
    if frame.shape[1] < 3:
        raise ValueError("Legacy sheet1 requires at least 3 columns.")

    frame = frame.iloc[:, :3].rename(
        columns={
            frame.columns[0]: "entity_cn",
            frame.columns[1]: "property_cn",
            frame.columns[2]: "property_en",
        }
    )
    frame["entity_cn"] = frame["entity_cn"].ffill()

    entities: OrderedDict[str, OrderedDict[str, dict[str, Any]]] = OrderedDict()
    for _, row in frame.iterrows():
        entity_cn = _clean_text(row.get("entity_cn"))
        property_cn = _clean_text(row.get("property_cn"))
        property_en = _clean_text(row.get("property_en")).upper()
        if not entity_cn or not property_en:
            continue

        if entity_cn not in entities:
            entities[entity_cn] = OrderedDict()

        if property_en not in entities[entity_cn]:
            entities[entity_cn][property_en] = {
                "label": property_cn or property_en,
                "type": _infer_type(property_en),
            }
        elif property_cn and not entities[entity_cn][property_en].get("label"):
            entities[entity_cn][property_en]["label"] = property_cn

    return entities


def _load_relation_rows(relation_sheet_df: pd.DataFrame) -> list[dict[str, str]]:
    frame = relation_sheet_df.copy()
    if frame.shape[1] < 4:
        return []

    from_col = frame.columns[1]
    rel_col = frame.columns[2]
    to_col = frame.columns[3]

    relations = []
    seen = set()
    for _, row in frame.iterrows():
        from_entity = _clean_text(row.get(from_col))
        rel_label = _clean_text(row.get(rel_col))
        to_entity = _clean_text(row.get(to_col))
        if not from_entity or not to_entity:
            continue

        rel_type = rel_label or "related"
        key = (from_entity, to_entity, rel_type)
        if key in seen:
            continue
        seen.add(key)
        relations.append(
            {
                "from_cn": from_entity,
                "to_cn": to_entity,
                "type": rel_type,
                "label": rel_label or rel_type,
                "from_field": "",
                "to_field": "",
            }
        )

    return relations


def _load_source_tables(source_dir: str) -> list[TableMeta]:
    tables: list[TableMeta] = []
    if not os.path.isdir(source_dir):
        return tables

    for file_name in sorted(os.listdir(source_dir)):
        if not file_name.lower().endswith(".xlsx"):
            continue
        if file_name.startswith("~$"):
            continue
        table_name = file_name[:-5]
        file_path = os.path.join(source_dir, file_name)
        try:
            header_df = pd.read_excel(file_path, nrows=0)
        except Exception:
            continue
        columns = [str(column).strip() for column in header_df.columns if str(column).strip()]
        upper_to_actual = {column.upper(): column for column in columns}
        tables.append(
            TableMeta(
                table_name=table_name,
                file_name=file_name,
                columns=columns,
                upper_to_actual=upper_to_actual,
            )
        )
    return tables


def _score_entity_table_match(
    properties: OrderedDict[str, dict[str, Any]],
    tables: list[TableMeta],
) -> tuple[TableMeta | None, list[str], float]:
    if not tables:
        return None, [], 0.0

    property_names = list(properties.keys())
    property_uppers = [name.upper() for name in property_names]

    column_freq = defaultdict(int)
    for table in tables:
        for column_upper in table.upper_to_actual.keys():
            column_freq[column_upper] += 1

    best_table = None
    best_overlap: list[str] = []
    best_score = -1.0

    for table in tables:
        overlap = [name for name, upper in zip(property_names, property_uppers) if upper in table.upper_to_actual]
        if not overlap:
            continue

        weighted_score = 0.0
        for property_name in overlap:
            property_upper = property_name.upper()
            weighted_score += 1.0 / max(column_freq[property_upper], 1)

        score = weighted_score + (len(overlap) * 0.01)
        if score > best_score:
            best_score = score
            best_table = table
            best_overlap = overlap

    return best_table, best_overlap, best_score


def _accept_mapping(overlap: list[str], total_properties: int, table_meta: TableMeta | None) -> bool:
    if not table_meta:
        return False
    overlap_count = len(overlap)
    if overlap_count >= 2:
        return True
    if overlap_count == 1 and total_properties <= 3:
        return True
    return False


def _build_ontology_and_mapping(
    entities: OrderedDict[str, OrderedDict[str, dict[str, Any]]],
    relations: list[dict[str, str]],
    source_tables: list[TableMeta],
    ontology_name: str,
    ontology_description: str,
    source_id: str,
    source_name: str,
    mapping_description: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    match_result: dict[str, dict[str, Any]] = {}
    for entity_cn, properties in entities.items():
        table_meta, overlap, score = _score_entity_table_match(properties, source_tables)
        accepted = _accept_mapping(overlap, len(properties), table_meta)
        match_result[entity_cn] = {
            "table_meta": table_meta if accepted else None,
            "overlap": overlap if accepted else [],
            "score": score if accepted else 0.0,
        }

    used_keys = set()
    entity_key_by_cn: dict[str, str] = {}
    for entity_cn in entities.keys():
        matched_table = match_result[entity_cn]["table_meta"]
        candidate_key = matched_table.table_name if matched_table else entity_cn
        if candidate_key in used_keys:
            candidate_key = entity_cn
        if candidate_key in used_keys:
            index = 2
            while f"{candidate_key}_{index}" in used_keys:
                index += 1
            candidate_key = f"{candidate_key}_{index}"
        used_keys.add(candidate_key)
        entity_key_by_cn[entity_cn] = candidate_key

    for relation in relations:
        for endpoint in ("from_cn", "to_cn"):
            entity_cn = relation[endpoint]
            if entity_cn in entity_key_by_cn:
                continue
            candidate_key = entity_cn
            if candidate_key in used_keys:
                index = 2
                while f"{candidate_key}_{index}" in used_keys:
                    index += 1
                candidate_key = f"{candidate_key}_{index}"
            used_keys.add(candidate_key)
            entity_key_by_cn[entity_cn] = candidate_key
            entities[entity_cn] = OrderedDict()
            match_result[entity_cn] = {"table_meta": None, "overlap": [], "score": 0.0}

    ontology_entities: OrderedDict[str, dict[str, Any]] = OrderedDict()
    mapping_table_mappings: OrderedDict[str, dict[str, Any]] = OrderedDict()

    for entity_cn, properties in entities.items():
        entity_key = entity_key_by_cn[entity_cn]
        property_defs = OrderedDict()
        for property_name, property_meta in properties.items():
            property_defs[property_name] = {
                "aliases": [],
                "label": property_meta.get("label", property_name),
                "type": property_meta.get("type", "string"),
                "value_aliases": {},
            }

        ontology_entities[entity_key] = {
            "label": entity_cn,
            "description": "Imported from legacy Excel sheet1",
            "aliases": [entity_cn] if entity_key != entity_cn else [],
            "properties": property_defs,
        }

        matched_table = match_result[entity_cn]["table_meta"]
        overlap = match_result[entity_cn]["overlap"]
        if not matched_table or not overlap:
            continue

        field_mappings = OrderedDict()
        for property_name in overlap:
            actual_column = matched_table.upper_to_actual.get(property_name.upper())
            if actual_column:
                field_mappings[property_name] = actual_column

        if not field_mappings:
            continue

        mapping_table_mappings[entity_key] = {
            "table_name": matched_table.table_name,
            "file_name": matched_table.file_name,
            "field_mappings": field_mappings,
            "field_value_semantics": {},
        }

    ontology_relations = []
    seen_relation_keys = set()
    for relation in relations:
        from_key = entity_key_by_cn.get(relation["from_cn"], relation["from_cn"])
        to_key = entity_key_by_cn.get(relation["to_cn"], relation["to_cn"])
        relation_key = (from_key, to_key, relation["type"], relation["label"])
        if relation_key in seen_relation_keys:
            continue
        seen_relation_keys.add(relation_key)
        ontology_relations.append(
            {
                "from": from_key,
                "to": to_key,
                "type": relation["type"],
                "label": relation["label"],
                "from_field": "",
                "to_field": "",
            }
        )

    ontology_json = {
        "name": ontology_name,
        "version": "1.0",
        "description": ontology_description,
        "entities": ontology_entities,
        "relations": ontology_relations,
    }

    mapping_json = {
        "source_name": source_name,
        "source_id": source_id,
        "description": mapping_description,
        "table_mappings": mapping_table_mappings,
    }
    return ontology_json, mapping_json, match_result


def import_ontology_mapping(
    excel_path: str,
    ontology_out: str | None = None,
    mapping_out: str | None = None,
    source_dir: str | None = None,
    source_id: str = "xksx",
    source_name: str = "xksx",
) -> dict[str, Any]:
    if not os.path.exists(excel_path):
        raise FileNotFoundError(f"Excel file not found: {excel_path}")

    ontology_out = ontology_out or os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
    mapping_out = mapping_out or os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
    source_dir = source_dir if source_dir is not None else config.DATA_SOURCES.get(source_id, "")

    excel_file = pd.ExcelFile(excel_path)
    table_structure_payload = _load_table_structure_book(excel_file)

    # Mode A: table-structure import (preferred for current project)
    if table_structure_payload is not None:
        sheet_name, tables = table_structure_payload
        ontology_json, mapping_json, table_specs, stats = _build_from_table_structure(
            tables=tables,
            excel_path=excel_path,
            sheet_name=sheet_name,
            source_id=source_id,
            source_name=source_name,
        )

        os.makedirs(os.path.dirname(ontology_out), exist_ok=True)
        os.makedirs(os.path.dirname(mapping_out), exist_ok=True)
        with open(ontology_out, "w", encoding="utf-8") as ontology_file:
            json.dump(ontology_json, ontology_file, ensure_ascii=False, indent=4)
        with open(mapping_out, "w", encoding="utf-8") as mapping_file:
            json.dump(mapping_json, mapping_file, ensure_ascii=False, indent=4)

        generated_files = _materialize_table_files(source_dir, table_specs)
        return {
            "mode": "table_structure",
            "excel_path": excel_path,
            "sheet": sheet_name,
            "ontology_out": ontology_out,
            "mapping_out": mapping_out,
            "source_dir": source_dir,
            "generated_table_files": generated_files,
            **stats,
        }

    # Mode B: legacy two-sheet import
    if len(excel_file.sheet_names) < 2:
        raise ValueError("Excel format unsupported: expected table-structure sheet or legacy 2-sheet format.")

    entity_sheet_name = excel_file.sheet_names[0]
    relation_sheet_name = excel_file.sheet_names[1]

    entity_sheet_df = excel_file.parse(entity_sheet_name)
    relation_sheet_df = excel_file.parse(relation_sheet_name)

    entities = _load_entity_rows(entity_sheet_df)
    relations = _load_relation_rows(relation_sheet_df)
    source_tables = _load_source_tables(source_dir)

    ontology_json, mapping_json, match_result = _build_ontology_and_mapping(
        entities=entities,
        relations=relations,
        source_tables=source_tables,
        ontology_name="xksx_ontology",
        ontology_description=(
            f"Imported from {os.path.basename(excel_path)} "
            f"(sheet1={entity_sheet_name}, sheet2={relation_sheet_name})"
        ),
        source_id=source_id,
        source_name=source_name,
        mapping_description=f"Ontology-first mapping imported from {os.path.basename(excel_path)}",
    )

    os.makedirs(os.path.dirname(ontology_out), exist_ok=True)
    os.makedirs(os.path.dirname(mapping_out), exist_ok=True)

    with open(ontology_out, "w", encoding="utf-8") as ontology_file:
        json.dump(ontology_json, ontology_file, ensure_ascii=False, indent=4)

    with open(mapping_out, "w", encoding="utf-8") as mapping_file:
        json.dump(mapping_json, mapping_file, ensure_ascii=False, indent=4)

    mapped_entities = [
        entity_name
        for entity_name, matched in match_result.items()
        if matched.get("table_meta") is not None
    ]
    unmapped_entities = [
        entity_name
        for entity_name, matched in match_result.items()
        if matched.get("table_meta") is None
    ]

    return {
        "mode": "legacy_two_sheet",
        "excel_path": excel_path,
        "sheet1": entity_sheet_name,
        "sheet2": relation_sheet_name,
        "ontology_out": ontology_out,
        "mapping_out": mapping_out,
        "entity_count": len(ontology_json["entities"]),
        "relation_count": len(ontology_json["relations"]),
        "mapped_entity_count": len(mapped_entities),
        "unmapped_entity_count": len(unmapped_entities),
        "mapped_entities": mapped_entities,
        "unmapped_entities": unmapped_entities,
    }


def main():
    parser = argparse.ArgumentParser(description="Import ontology and mapping from Excel.")
    parser.add_argument("--excel", required=True, help="Excel path.")
    parser.add_argument(
        "--ontology-out",
        default=os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json"),
        help="Output ontology JSON path.",
    )
    parser.add_argument(
        "--mapping-out",
        default=os.path.join(config.MAPPING_DIR, "xksx_mapping.json"),
        help="Output mapping JSON path.",
    )
    parser.add_argument(
        "--source-dir",
        default=config.DATA_SOURCES.get("xksx", ""),
        help="Data source directory for generated/linked table files.",
    )
    parser.add_argument("--source-id", default="xksx")
    parser.add_argument("--source-name", default="xksx")
    args = parser.parse_args()

    result = import_ontology_mapping(
        excel_path=args.excel,
        ontology_out=args.ontology_out,
        mapping_out=args.mapping_out,
        source_dir=args.source_dir,
        source_id=args.source_id,
        source_name=args.source_name,
    )

    print(f"[OK] mode={result.get('mode')}")
    print(f"[OK] Excel: {result['excel_path']}")
    print(f"[OK] Ontology -> {result['ontology_out']}")
    print(f"[OK] Mapping  -> {result['mapping_out']}")
    if result.get("mode") == "table_structure":
        print(
            f"[OK] entities={result.get('entity_count', 0)}, "
            f"fields={result.get('field_count', 0)}, "
            f"primary_keys={result.get('primary_key_field_count', 0)}"
        )
        print(f"[OK] generated tables={len(result.get('generated_table_files', []))}")
    else:
        print(
            f"[OK] entities={result.get('entity_count', 0)}, "
            f"relations={result.get('relation_count', 0)}, "
            f"mapped={result.get('mapped_entity_count', 0)}"
        )


if __name__ == "__main__":
    main()
