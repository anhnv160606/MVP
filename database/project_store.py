"""Load a project snapshot supplied by placement or by a local JSON fixture."""

from dataclasses import dataclass
import json
from pathlib import Path

from ifc_engine.reader import SpatialIndex


@dataclass(frozen=True)
class ProjectSnapshot:
    plan_id: str
    plan_revision: str
    plan_status: str
    playbook_revision: str | None
    model: SpatialIndex
    zones: dict[str, set[tuple[str, str]]]
    placements: list[dict]
    requirements: list[dict]
    scenarios: dict[str, list[str]]

    def room_ids(self, scope_type: str, scope_id: str) -> set[str]:
        if scope_type == "room" and scope_id in self.model.rooms:
            return {scope_id}
        if scope_type == "floor" and scope_id in self.model.floors:
            return {room for room, floor in self.model.rooms.items() if floor == scope_id}
        if scope_type == "zone" and scope_id in self.zones:
            return {room for room, _ in self.zones[scope_id]}
        raise ValueError(f"Unknown scope {scope_type}: {scope_id}")


def require_id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def read_snapshot(path: str | Path, model: SpatialIndex) -> ProjectSnapshot:
    return snapshot_from_dict(json.loads(Path(path).read_text(encoding="utf-8")), model)


def snapshot_from_dict(data: dict, model: SpatialIndex) -> ProjectSnapshot:
    if not isinstance(data, dict):
        raise ValueError("Snapshot must be an object")
    if data.get("model_revision", model.revision) != model.revision:
        raise ValueError("Snapshot model_revision does not match the IFC file")
    zones = {}
    memberships = set()
    for zone, entries in data.get("zones", {}).items():
        require_id(zone, "zone_id")
        pairs = set()
        for entry in entries:
            pair = (entry["room_id"], require_id(entry["load_class_id"], "load_class_id"))
            if pair[0] not in model.rooms or pair in memberships:
                raise ValueError(f"Unknown room or repeated zone assignment: {pair}")
            memberships.add(pair)
            pairs.add(pair)
        zones[zone] = pairs
    status = data.get("plan_status", "draft")
    if status not in {"draft", "approved"}:
        raise ValueError("plan_status must be draft or approved")
    return ProjectSnapshot(
        require_id(data.get("plan_id"), "plan_id"),
        require_id(data.get("plan_revision"), "plan_revision"),
        status, data.get("playbook_revision"), model, zones, data.get("placements", []),
        data.get("requirements", []), data.get("scenarios", {}),
    )
