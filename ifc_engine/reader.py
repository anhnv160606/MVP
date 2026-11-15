from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import ifcopenshell.util.shape
import ifcopenshell.util.unit


class IfcReader:
    def __init__(self, path: Path):
        self.model = ifcopenshell.open(str(path))
        self.length_scale = (
            ifcopenshell.util.unit.calculate_unit_scale(self.model)
        )

    def _get_element(self, global_id: str, ifc_class: str):
        try:
            element = self.model.by_guid(global_id)
        except RuntimeError as error:
            raise ValueError(
                f"Không tìm thấy GlobalId: {global_id}"
            ) from error

        if element is None or not element.is_a(ifc_class):
            raise ValueError(
                f"GlobalId không thuộc loại {ifc_class}: {global_id}"
            )

        return element

    def get_storeys(self) -> list[dict[str, Any]]:
        storeys = self.model.by_type("IfcBuildingStorey")

        result = [
            {
                "id": storey.GlobalId,
                "name": storey.Name,
                "elevation_m": (
                    float(storey.Elevation) * self.length_scale
                    if storey.Elevation is not None
                    else None
                ),
            }
            for storey in storeys
        ]

        return sorted(
            result,
            key=lambda item: (
                item["elevation_m"] is None,
                item["elevation_m"] or 0,
                item["id"],
            ),
        )

    def get_spaces(
        self,
        storey_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if storey_id is None:
            spaces = self.model.by_type("IfcSpace")
        else:
            storey = self._get_element(
                storey_id, "IfcBuildingStorey"
            )
            spaces = [
                element
                for element in (
                    ifcopenshell.util.element.get_decomposition(storey)
                )
                if element.is_a("IfcSpace")
            ]

        return sorted(
            [
                {
                    "id": space.GlobalId,
                    "name": space.Name,
                    "long_name": space.LongName,
                    "ifc_class": space.is_a(),
                }
                for space in spaces
            ],
            key=lambda item: item["id"],
        )

    def get_space_geometry(
        self,
        space_id: str,
    ) -> dict[str, Any]:
        space = self._get_element(space_id, "IfcSpace")

        if space.Representation is None:
            return {
                "status": "unavailable",
                "space_id": space_id,
                "reason": "space_has_no_representation",
            }

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)

        try:
            shape = ifcopenshell.geom.create_shape(settings, space)
            vertices = ifcopenshell.util.shape.get_vertices(
                shape.geometry
            )
        except RuntimeError:
            return {
                "status": "unavailable",
                "space_id": space_id,
                "reason": "geometry_generation_failed",
            }

        if len(vertices) == 0:
            return {
                "status": "unavailable",
                "space_id": space_id,
                "reason": "geometry_has_no_vertices",
            }

        minimum = vertices.min(axis=0)
        maximum = vertices.max(axis=0)

        return {
            "status": "ok",
            "space_id": space_id,
            "unit": "m",
            "coordinate_system": "model_world",
            "bbox": {
                "min": minimum.tolist(),
                "max": maximum.tolist(),
            },
            "bbox_center": ((minimum + maximum) / 2).tolist(),
        }