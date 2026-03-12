"""字段提取智能体(B) - 确定用户需要的输出字段。"""
from agents.base_agent import BaseAgent


class FieldExtractAgent(BaseAgent):
    def __init__(self, ontology_manager):
        super().__init__("字段提取智能体", "提取用户需要的字段，明确输出结果(B步骤)")
        self.ontology = ontology_manager

    def run(self, input_data: dict) -> dict:
        intent = input_data["clarified_intent"]
        output_fields = intent.get("output_fields", [])
        self.log(f"提取输出字段: {len(output_fields)}个")

        extracted_fields = []
        for field_spec in output_fields:
            entity = field_spec.get("entity", "")
            entity_instance = str(field_spec.get("entity_instance", "")).strip()
            raw_field = field_spec.get("field", "")
            label = field_spec.get("label", raw_field)
            field = raw_field

            props = self.ontology.get_entity_properties(entity)
            if entity and raw_field:
                field = self.ontology.resolve_property_name(entity, raw_field)
                if props and field not in props:
                    suggested = self.ontology.suggest_property_name(entity, field)
                    if suggested:
                        self.log(f"输出字段纠错: {entity}.{raw_field} -> {entity}.{suggested}")
                        field = suggested
                    else:
                        self.log(f"输出字段不存在，忽略: {entity}.{raw_field}")
                        continue

            if props and field in props:
                field_item = {
                    "entity": entity,
                    "field": field,
                    "label": label,
                    "type": props[field].get("type", "string"),
                }
            else:
                field_item = {
                    "entity": entity,
                    "field": field,
                    "label": label,
                    "type": "string",
                }

            if entity_instance:
                field_item["entity_instance"] = entity_instance
            extracted_fields.append(field_item)

        field_desc = [f"{field['entity']}.{field['field']}" for field in extracted_fields]
        self.log(f"字段提取完成: {field_desc}")
        return {**input_data, "extracted_fields": extracted_fields}
