"""Import ontology and rebuild xksx mapping from Excel (ontology-first)."""
from __future__ import annotations

import argparse
import json
import os
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


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def _infer_type(field_name: str) -> str:
    upper = field_name.upper()
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


def _load_entity_rows(entity_sheet_df: pd.DataFrame) -> OrderedDict[str, OrderedDict[str, dict[str, Any]]]:
    frame = entity_sheet_df.copy()
    if frame.shape[1] < 3:
        raise ValueError("实体sheet至少需要3列：实体、属性中文名、属性英文名")

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
            "description": "Imported from Excel sheet1 (ontology-first)",
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
        raise FileNotFoundError(f"Excel文件不存在: {excel_path}")

    ontology_out = ontology_out or os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
    mapping_out = mapping_out or os.path.join(config.MAPPING_DIR, "xksx_mapping.json")
    source_dir = source_dir if source_dir is not None else config.DATA_SOURCES.get(source_id, "")

    excel_file = pd.ExcelFile(excel_path)
    if len(excel_file.sheet_names) < 2:
        raise ValueError("Excel至少需要2个sheet（sheet1实体属性、sheet2关系）")

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
        ontology_description=f"Imported from {os.path.basename(excel_path)} (sheet1={entity_sheet_name}, sheet2={relation_sheet_name})",
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
    parser = argparse.ArgumentParser(description="Import ontology and mapping from Excel (ontology-first).")
    parser.add_argument(
        "--excel",
        required=True,
        help="Excel path (sheet1: entity/property list, sheet2: relations).",
    )
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
        help="Data source directory for table adaptation.",
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

    print(f"[OK] Excel: {result['excel_path']}")
    print(f"[OK] Sheet1={result['sheet1']}, Sheet2={result['sheet2']}")
    print(f"[OK] 实体总数: {result['entity_count']}, 关系总数: {result['relation_count']}")
    print(f"[OK] 映射实体数: {result['mapped_entity_count']}, 未映射实体数: {result['unmapped_entity_count']}")
    print(f"[OK] Ontology -> {result['ontology_out']}")
    print(f"[OK] Mapping  -> {result['mapping_out']}")


if __name__ == "__main__":
    main()
