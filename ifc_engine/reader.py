"""Spatial index for preliminary load calculations."""

from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path


@dataclass(frozen=True)
class SpatialIndex:
    revision: str
    rooms: dict[str, str | None]
    floors: frozenset[str]


def read_spaces(path: str | Path) -> SpatialIndex:
    import ifcopenshell
    from ifcopenshell.util.element import get_parent

    path = Path(path)
    with path.open("rb") as stream:
        revision = file_digest(stream, "sha256").hexdigest()
    model = ifcopenshell.open(str(path))
    floors = frozenset(floor.GlobalId for floor in model.by_type("IfcBuildingStorey"))
    rooms = {}
    for room in model.by_type("IfcSpace"):
        parent = get_parent(room)
        visited = {room.id()}
        while parent and not parent.is_a("IfcBuildingStorey"):
            if parent.id() in visited:
                raise ValueError(f"Cyclic IFC containment for room {room.GlobalId}")
            visited.add(parent.id())
            parent = get_parent(parent)
        if room.GlobalId in rooms:
            raise ValueError(f"Duplicate IFC room GlobalId {room.GlobalId}")
        rooms[room.GlobalId] = parent.GlobalId if parent else None
    with path.open("rb") as stream:
        if revision != file_digest(stream, "sha256").hexdigest():
            raise ValueError("IFC changed during read")
    return SpatialIndex(revision, rooms, floors)
