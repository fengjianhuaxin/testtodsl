"""知识图谱 API - 构建全量图谱数据和高亮子图"""
import json
import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)

from flask import Blueprint, request, jsonify
import config
import pandas as pd

graph_bp = Blueprint("graph", __name__)


def _build_full_graph():
    """从本体和实际数据构建全量图谱"""
    onto_file = os.path.join(config.ONTOLOGY_DIR, "student_mgmt_ontology.json")
    with open(onto_file, "r", encoding="utf-8") as f:
        onto = json.load(f)

    nodes = []
    edges = []
    node_id_counter = 0

    # 为每个数据源构建数据节点
    for source_id, source_dir in config.DATA_SOURCES.items():
        mapping_file = os.path.join(config.MAPPING_DIR, f"{source_id}_mapping.json")
        if not os.path.exists(mapping_file):
            continue
        with open(mapping_file, "r", encoding="utf-8") as f:
            mapping = json.load(f)
        source_name = mapping.get("source_name", source_id)

        entity_rows = {}  # entity_name -> [{row_data}]
        entity_key_map = {}  # (entity, key_value) -> node_id

        # 加载各实体数据
        for entity_name, tm in mapping.get("table_mappings", {}).items():
            fname = tm.get("file_name")
            if not fname:
                continue
            fpath = os.path.join(source_dir, fname)
            if not os.path.exists(fpath):
                continue
            fm = tm.get("field_mappings", {})
            reverse_fm = {v: k for k, v in fm.items()}
            try:
                df = pd.read_excel(fpath)
                # 转换为本体字段名
                df_onto = df.rename(columns=reverse_fm)
                rows = df_onto.fillna("").to_dict(orient="records")
                entity_rows[entity_name] = rows

                # 确定主键字段
                entity_def = onto.get("entities", {}).get(entity_name, {})
                key_field = None
                for pname, pdef in entity_def.get("properties", {}).items():
                    if pdef.get("is_key"):
                        key_field = pname
                        break

                # 创建节点（每行数据一个小节点）
                label_field = "name" if "name" in df_onto.columns else (key_field or df_onto.columns[0])
                for row in rows:
                    nid = node_id_counter
                    node_id_counter += 1
                    label = str(row.get(label_field, f"{entity_name}_{nid}"))
                    nodes.append({
                        "id": nid,
                        "label": label[:12],
                        "title": f"{source_name} | {entity_def.get('label', entity_name)}: {label}",
                        "group": f"{source_id}_{entity_name}",
                        "entity": entity_name,
                        "source": source_id,
                        "data": row
                    })
                    if key_field:
                        entity_key_map[(entity_name, str(row.get(key_field, "")))] = nid
            except Exception:
                pass

        # 根据外键关系创建边
        for rel in onto.get("relations", []):
            from_entity = rel["from"]
            to_entity = rel["to"]
            if from_entity not in entity_rows or to_entity not in entity_rows:
                continue

            from_def = onto["entities"].get(from_entity, {})
            # 找外键属性
            fk_field = None
            ref_entity = None
            for pname, pdef in from_def.get("properties", {}).items():
                if pdef.get("is_fk") and pdef.get("ref_entity") == to_entity:
                    fk_field = pname
                    ref_entity = to_entity
                    break

            if fk_field:
                from_key = None
                for pname, pdef in from_def.get("properties", {}).items():
                    if pdef.get("is_key"):
                        from_key = pname
                        break
                for row in entity_rows[from_entity]:
                    fk_val = str(row.get(fk_field, ""))
                    from_id = entity_key_map.get((from_entity, str(row.get(from_key, ""))))
                    to_id = entity_key_map.get((to_entity, fk_val))
                    if from_id is not None and to_id is not None:
                        edges.append({
                            "from": from_id,
                            "to": to_id,
                            "label": rel.get("label", ""),
                            "relation_type": rel.get("type", ""),
                            "source": source_id
                        })

    return {"nodes": nodes, "edges": edges}


@graph_bp.route("/graph/full")
def full_graph():
    """返回全量图谱"""
    graph = _build_full_graph()
    return jsonify(graph)


@graph_bp.route("/graph/highlight", methods=["POST"])
def highlight_graph():
    """根据查询涉及的实体返回需高亮的节点 ID 列表"""
    data = request.get_json()
    entities = data.get("entities", [])
    conditions = data.get("conditions", [])

    graph = _build_full_graph()
    highlight_ids = set()

    for node in graph["nodes"]:
        if node["entity"] in entities:
            # 检查条件匹配
            if conditions:
                match = True
                for cond in conditions:
                    field = cond.get("field", "")
                    value = str(cond.get("value", ""))
                    node_val = str(node.get("data", {}).get(field, ""))
                    if cond.get("op") == "=" and node_val != value:
                        match = False
                    elif cond.get("op") == "contains" and value not in node_val:
                        match = False
                if match:
                    highlight_ids.add(node["id"])
            else:
                highlight_ids.add(node["id"])

    # 添加关联边的对端节点
    related_ids = set()
    for edge in graph["edges"]:
        if edge["from"] in highlight_ids:
            related_ids.add(edge["to"])
        if edge["to"] in highlight_ids:
            related_ids.add(edge["from"])

    highlight_ids |= related_ids

    return jsonify({"highlight_ids": list(highlight_ids)})
