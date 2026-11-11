"""Create a small IFC4 project for integration tests and manual checks."""

from pathlib import Path
from sys import argv

import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import numpy as np


def make_ifc_fixture(path, *, schema="IFC4"):
    model = ifcopenshell.api.project.create_file(version=schema)
    project = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="MVP sample")
    ifcopenshell.api.unit.assign_unit(model)  # Default length unit is millimetres.
    context = ifcopenshell.api.context.add_context(model, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=context,
    )
    site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite")
    building = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuilding")
    storey = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuildingStorey")
    space = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSpace", name="Test room")
    for parent, child in ((project, site), (site, building), (building, storey), (storey, space)):
        ifcopenshell.api.aggregate.assign_object(model, relating_object=parent, products=[child])
    matrix = np.array([[0., -1., 0., 5.], [1., 0., 0., -2.], [0., 0., 1., 3.], [0., 0., 0., 1.]])
    ifcopenshell.api.geometry.edit_object_placement(model, product=space, matrix=matrix)

    wall = ifcopenshell.api.root.create_entity(model, ifc_class="IfcWall", name="Test wall")
    ifcopenshell.api.spatial.assign_container(model, products=[wall], relating_structure=storey)
    ifcopenshell.api.geometry.edit_object_placement(model, product=wall, matrix=matrix)
    geometry = ifcopenshell.api.geometry.add_wall_representation(
        model, context=body, length=4, thickness=0.12, height=2.8,
    )
    ifcopenshell.api.geometry.assign_representation(model, product=wall, representation=geometry)

    model.write(str(path))
    return space.GlobalId, wall.GlobalId


if __name__ == "__main__":
    space_id, host_id = make_ifc_fixture(Path(argv[1]))
    print("space_id:", space_id)
    print("host_id: ", host_id)
