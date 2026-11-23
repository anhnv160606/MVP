"""One validated JSON-schema contract shared by all LLM providers."""

import inspect
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import ConfigDict, Field, create_model

PARAMETER_DESCRIPTIONS = {
    "space_id": "IfcSpace.GlobalId in the current project snapshot; not a room name or STEP ID.",
    "storey_id": "IfcBuildingStorey.GlobalId; omit to query the whole current model.",
    "ifc_class": "IFC entity class name, for example IfcWall, IfcOutlet or IfcSpace.",
    "device_id": "Stable UUID from the placement draft; distinct from the IFC GlobalId.",
    "proposals": "Complete list of devices in the desired plan. Keep proposed_id stable; omitted existing entries request removal. All positions are model-world metres.",
    "device_type": "Semantic device category; writer aliases include outlet, light, switch, junction_box and appliance.",
    "device_category": "Device/load category such as lighting, outlet, air conditioner or water heater.",
    "space_type": "Room usage label such as Phòng ngủ/bedroom; not an IFC identifier.",
    "project_standard": "Project's requested standard for provenance; source rules may not assert applicability.",
    "connection_requirements": "Electrical constraints such as phases, voltage and connection_type; missing/ambiguous catalog data remains explicit.",
    "rules": "Supported validation rules. min_separation_m is a nonnegative distance in metres; other rules generate unchecked warnings.",
    "reason": "Engineer/agent explanation recorded in the removal revision for audit.",
    "offset": "Zero-based result offset. Continue paging until all items are read.",
    "limit": "Maximum records in this page; use small pages to avoid provider output truncation.",
    "element_id": "IFC numeric STEP ID from this exact loaded model, not an IFC GlobalId.",
}


@dataclass
class RegisteredTool:
    function: Any
    arguments: Any
    bound: dict
    schema: dict


class ToolRegistry:
    """Expose explicit capabilities; backend-bound parameters never appear in schemas."""

    def __init__(self, context_factory=None):
        self.tools: dict[str, RegisteredTool] = {}
        self.context_factory = context_factory or nullcontext

    def add(self, function, *, name=None, description=None, bound=None):
        name = name or function.__name__
        if name in self.tools:
            raise ValueError(f"Duplicate tool: {name}")
        bound = bound or {}
        hints = get_type_hints(function)
        fields = {}
        for key, parameter in inspect.signature(function).parameters.items():
            if key in bound:
                continue
            if parameter.kind in (parameter.VAR_KEYWORD, parameter.VAR_POSITIONAL):
                raise ValueError("Tools require explicit parameters")
            default = ... if parameter.default is inspect.Parameter.empty else parameter.default
            field_options = (
                {"description": PARAMETER_DESCRIPTIONS[key]}
                if key in PARAMETER_DESCRIPTIONS
                else {}
            )
            if key == "proposals":
                field_options["max_length"] = 2000
            fields[key] = (hints.get(key, Any), Field(default=default, **field_options))
        arguments = create_model(
            name + "Arguments", __config__=ConfigDict(extra="forbid", strict=True), **fields
        )
        description = description or inspect.getdoc(function)
        if not description or len(description) < 40:
            raise ValueError(f"Tool needs a detailed description: {name}")
        schema = {
            "name": name,
            "description": description,
            "parameters": arguments.model_json_schema(),
        }
        if "proposals" in fields:
            schema["parameters"]["properties"]["proposals"]["items"] = {
                "type": "object",
                "additionalProperties": True,
                "required": ["proposed_id", "device_type", "space_id", "host_id", "position"],
                "properties": {
                    "proposed_id": {
                        "type": "string",
                        "description": "Stable agent-defined identifier within this plan.",
                    },
                    "device_type": {"type": "string"},
                    "space_id": {
                        "type": "string",
                        "description": PARAMETER_DESCRIPTIONS["space_id"],
                    },
                    "host_id": {
                        "type": "string",
                        "description": "GlobalId of the wall/slab/building element used as mount.",
                    },
                    "position": {
                        "type": "object",
                        "required": ["x", "y", "z"],
                        "properties": {
                            axis: {"type": "number", "description": "Absolute model-world metres."}
                            for axis in "xyz"
                        },
                    },
                    "orientation": {
                        "type": "object",
                        "properties": {
                            "yaw": {
                                "type": "number",
                                "description": "Rotation around model Z in degrees.",
                            }
                        },
                        "required": ["yaw"],
                    },
                    "rule_ids": {"type": "array", "items": {"type": "string"}},
                    "properties": {"type": "object", "additionalProperties": True},
                },
            }
        self.tools[name] = RegisteredTool(function, arguments, bound, schema)

    def schemas(self) -> list[dict]:
        return [tool.schema for tool in self.tools.values()]

    def execute(self, name: str, arguments: dict):
        if name not in self.tools:
            raise ValueError(f"Unknown or forbidden tool: {name}")
        tool = self.tools[name]
        validated = tool.arguments.model_validate(arguments)
        with self.context_factory():
            return tool.function(**tool.bound, **validated.model_dump())
