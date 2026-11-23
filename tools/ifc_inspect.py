"""Project-bound IFC discovery tools using IFCOpenShell, without remote state."""

from pathlib import Path

from ifc_engine.reader import IfcReader
from tools.ifc_read import _ifc_element_record, ifc_get_placement_context


class IfcInspection:
    """Keep a model open for a single agent run, in model-world metres."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.reader = IfcReader(self.path)

    def ifc_get_storeys(self) -> list[dict]:
        """List storey GlobalIds, names and elevations in metres, sorted by elevation."""
        return self.reader.get_storeys()

    def ifc_get_spaces(self, storey_id: str | None = None) -> list[dict]:
        """List room GlobalIds/names, optionally below a storey GlobalId. Does not infer room usage."""
        return self.reader.get_spaces(storey_id)

    def ifc_get_space_geometry(self, space_id: str) -> dict:
        """Read a room's world bounding box in metres. Unavailable geometry is explicit; no polygon is inferred."""
        return self.reader.get_space_geometry(space_id)

    def ifc_get_doors_windows(self, space_id: str) -> dict:
        """Read doors/windows linked through room boundaries and openings; missing links are reported, not inferred."""
        context = ifc_get_placement_context(space_id, self.path)
        return {key: context[key] for key in ("doors", "windows", "missing_data")}

    def ifc_get_space_adjacency(self, space_id: str) -> list[dict]:
        """Find neighboring spaces sharing boundary-element GlobalIds; this is topological evidence, not a distance test."""
        self.reader._get_element(space_id, "IfcSpace")
        boundaries = {}
        for rel in self.reader.model.by_type("IfcRelSpaceBoundary"):
            host = rel.RelatedBuildingElement
            if host is not None:
                boundaries.setdefault(host.GlobalId, set()).add(rel.RelatingSpace.GlobalId)
        return [
            {"neighbor_id": other, "shared_boundary_id": host}
            for host, spaces in sorted(boundaries.items())
            if space_id in spaces
            for other in sorted(spaces - {space_id})
        ]

    def ifc_get_building_summary(self) -> dict:
        """Return schema, storeys and room count. Total room area is unavailable unless separately quantified."""
        return {
            "schema": self.reader.model.schema,
            "storeys": self.ifc_get_storeys(),
            "space_count": len(self.ifc_get_spaces()),
            "total_area_m2": None,
        }

    def ifc_search_elements(
        self, ifc_class: str, name: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict:
        """Page products by IFC class and optional case-insensitive name substring. Returns GlobalIds and world metre geometry; limit 1..200."""
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise ValueError("limit must be 1..200; offset must be nonnegative")
        try:
            elements = self.reader.model.by_type(ifc_class)
        except RuntimeError as error:
            raise ValueError(f"Unknown IFC class: {ifc_class}") from error
        elements = sorted(
            (
                e
                for e in elements
                if e.is_a("IfcProduct")
                and (name is None or name.casefold() in (e.Name or "").casefold())
            ),
            key=lambda e: e.GlobalId,
        )
        return {
            "total": len(elements),
            "offset": offset,
            "items": [
                _ifc_element_record(e, self.reader.length_scale)
                for e in elements[offset : offset + limit]
            ],
        }
