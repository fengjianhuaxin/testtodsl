"""映射管理器 - 管理本体到各数据源的字段映射"""
import json
import os


class MappingManager:
    """映射管理器，管理本体实体/属性与实际数据库表/字段的映射关系"""

    def __init__(self, mapping_dir: str):
        self.mapping_dir = mapping_dir
        self.mappings = {}
        self._load_all()

    def _load_all(self):
        """加载目录下所有映射配置"""
        for fn in os.listdir(self.mapping_dir):
            if fn.endswith("_mapping.json"):
                path = os.path.join(self.mapping_dir, fn)
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.mappings[data["source_id"]] = data

    def get_source_ids(self) -> list:
        """返回所有数据源ID"""
        return list(self.mappings.keys())

    def get_source_info(self, source_id: str) -> dict:
        """获取数据源基本信息"""
        m = self.mappings.get(source_id)
        if m:
            return {"source_id": m["source_id"], "source_name": m["source_name"], "description": m["description"]}
        return None

    def get_table_mapping(self, source_id: str, entity_name: str) -> dict | None:
        """获取指定数据源中某个本体实体对应的表映射"""
        m = self.mappings.get(source_id)
        if m:
            return m.get("table_mappings", {}).get(entity_name)
        return None

    def get_table_name(self, source_id: str, entity_name: str) -> str | None:
        """获取实际表名"""
        tm = self.get_table_mapping(source_id, entity_name)
        return tm["table_name"] if tm else None

    def get_file_name(self, source_id: str, entity_name: str) -> str | None:
        """获取数据文件名（Sheet 模式用）"""
        tm = self.get_table_mapping(source_id, entity_name)
        return tm["file_name"] if tm else None

    def get_field_mapping(self, source_id: str, entity_name: str) -> dict:
        """获取字段映射：本体属性名 -> 实际字段名"""
        tm = self.get_table_mapping(source_id, entity_name)
        return tm.get("field_mappings", {}) if tm else {}

    def get_reverse_field_mapping(self, source_id: str, entity_name: str) -> dict:
        """获取反向字段映射：实际字段名 -> 本体属性名"""
        fm = self.get_field_mapping(source_id, entity_name)
        return {v: k for k, v in fm.items()}

    def ontology_field_to_actual(self, source_id: str, entity_name: str, ontology_field: str) -> str | None:
        """将本体属性名转换为实际字段名"""
        fm = self.get_field_mapping(source_id, entity_name)
        return fm.get(ontology_field)

    def actual_field_to_ontology(self, source_id: str, entity_name: str, actual_field: str) -> str | None:
        """将实际字段名转换为本体属性名"""
        rfm = self.get_reverse_field_mapping(source_id, entity_name)
        return rfm.get(actual_field)

    def to_description(self, source_id: str = None) -> str:
        """生成映射配置的文本描述"""
        sources = [source_id] if source_id else self.get_source_ids()
        lines = []
        for sid in sources:
            m = self.mappings[sid]
            lines.append(f"\n数据源: {m['source_name']} ({sid})")
            lines.append(f"  描述: {m['description']}")
            for entity, tm in m["table_mappings"].items():
                lines.append(f"  实体 {entity} -> 表 {tm['table_name']} (文件: {tm['file_name']})")
                for onto_f, actual_f in tm["field_mappings"].items():
                    lines.append(f"    {onto_f} -> {actual_f}")
        return "\n".join(lines)
