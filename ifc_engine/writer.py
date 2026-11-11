"""Write managed electrical device occurrences into an IFC4 model."""

import json
import math
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.pset
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.type
import ifcopenshell.util.element
import ifcopenshell.util.placement
import ifcopenshell.util.representation
import ifcopenshell.util.unit
import numpy as np

from database.placement_application import PlacementApplicationError


_PSET = "MVP_Placement"
_DEVICE_CLASSES = {
    "socket": "IfcOutlet",
    "outlet": "IfcOutlet",
    "switch": "IfcSwitchingDevice",
    "light": "IfcLightFixture",
    "light_fixture": "IfcLightFixture",
    "junction_box": "IfcJunctionBox",
    "appliance": "IfcElectricAppliance",
    "panel": "IfcElectricDistributionBoard",
}
_DEFAULT_DIMENSIONS = {
    "IfcOutlet": (0.08, 0.03, 0.08),
    "IfcSwitchingDevice": (0.08, 0.03, 0.08),
    "IfcLightFixture": (0.30, 0.30, 0.05),
    "IfcJunctionBox": (0.10, 0.06, 0.10),
    "IfcElectricAppliance": (0.20, 0.20, 0.20),
    "IfcElectricDistributionBoard": (0.35, 0.12, 0.45),
}


def open_model(path):
    model = ifcopenshell.open(str(path))
    if model.schema != "IFC4" or len(model.by_type("IfcProject")) != 1:
        raise PlacementApplicationError("Expected an IFC4 file with one IfcProject")
    if ifcopenshell.util.unit.get_project_unit(model, "LENGTHUNIT") is None:
        raise PlacementApplicationError("The IFC project must declare a length unit")
    return model


def _guid(*parts):
    return ifcopenshell.guid.compress(uuid5(NAMESPACE_URL, json.dumps(["MVP", *parts])).hex)


def device_guid(project_id, plan_id, proposed_id):
    return _guid("placement", project_id, plan_id, proposed_id)


def _by_guid(model, guid):
    try:
        return model.by_guid(guid)
    except RuntimeError:
        return None


def _reference(model, guid, expected_class, field):
    item = _by_guid(model, guid)
    if item is None or not item.is_a(expected_class):
        raise PlacementApplicationError(f"{field}={guid!r} must refer to an {expected_class} GlobalId")
    return item


def _number(value, field):
    try:
        if type(value) in (int, float) and math.isfinite(value):
            return float(value)
    except OverflowError:
        pass
    raise PlacementApplicationError(f"{field} must be a finite number")


def _xyz(value, field):
    if isinstance(value, dict) and {"x", "y", "z"} <= value.keys():
        value = [value[axis] for axis in ("x", "y", "z")]
    if not isinstance(value, list) or len(value) != 3:
        raise PlacementApplicationError(f"{field} must contain three coordinates")
    return tuple(_number(number, field) for number in value)


@dataclass(frozen=True)
class DeviceSpec:
    guid: str
    ifc_class: str
    type_guid: str
    type_name: str
    space_id: str
    host_id: str | None
    position: tuple
    yaw: float
    dimensions: tuple
    payload: str

    @property
    def type_class(self):
        return self.ifc_class + "Type"

    @property
    def host_relation_guid(self):
        return _guid("placement-host", self.guid)

    def matrix(self):
        angle = math.radians(self.yaw % 360)
        cos, sin = math.cos(angle), math.sin(angle)
        x, y, z = self.position
        return np.array([[cos, -sin, 0, x], [sin, cos, 0, y], [0, 0, 1, z], [0, 0, 0, 1]])


def device_spec(project_id, plan_id, proposal):
    device_type = proposal["device_type"]
    ifc_class = proposal.get("ifc_class", _DEVICE_CLASSES.get(device_type, device_type))
    if not isinstance(ifc_class, str) or ifc_class not in _DEFAULT_DIMENSIONS:
        raise PlacementApplicationError(f"Unsupported electrical IFC class for {device_type!r}")
    module_id = proposal.get("module_id")
    if module_id is not None and (not isinstance(module_id, str) or not module_id.strip()):
        raise PlacementApplicationError("module_id must be a non-empty catalog identifier")
    orientation = proposal.get("orientation", 0)
    if isinstance(orientation, dict) and orientation.keys() == {"yaw"}:
        orientation = orientation["yaw"]
    dimensions = list(_DEFAULT_DIMENSIONS[ifc_class])
    if "dimensions_m" in proposal:
        dimensions = proposal["dimensions_m"]
    dimensions = _xyz(dimensions, "dimensions_m")
    if min(dimensions) <= 0:
        raise PlacementApplicationError("dimensions_m must be positive")
    type_name = module_id or device_type
    return DeviceSpec(
        guid=device_guid(project_id, plan_id, proposal["proposed_id"]),
        ifc_class=ifc_class,
        type_guid=_guid("placement-type", project_id, ifc_class, "catalog" if module_id else "generic", type_name),
        type_name=type_name,
        space_id=proposal["space_id"],
        host_id=proposal.get("host_id"),
        position=_xyz(proposal["position"], "position"),
        yaw=_number(orientation, "orientation yaw"),
        dimensions=dimensions,
        payload=json.dumps(proposal, sort_keys=True),
    )


def _body_context(model):
    body = ifcopenshell.util.representation.get_context(model, "Model", "Body", "MODEL_VIEW")
    if body is not None:
        return body
    context = ifcopenshell.util.representation.get_context(model, "Model")
    if context is None:
        context = ifcopenshell.api.context.add_context(model, context_type="Model")
    return ifcopenshell.api.context.add_context(
        model, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=context,
    )


def _create_device(model, spec):
    if _by_guid(model, spec.guid) is not None:
        raise PlacementApplicationError(f"Existing IFC entity uses device GlobalId {spec.guid}")
    space = _reference(model, spec.space_id, "IfcSpace", "space_id")
    host = None
    if spec.host_id is not None:
        host = _reference(model, spec.host_id, "IfcBuildingElement", "host_id")
        if _by_guid(model, spec.host_relation_guid) is not None:
            raise PlacementApplicationError("Existing IFC entity uses the placement host relationship ID")
    device_type = _by_guid(model, spec.type_guid)
    if device_type is None:
        device_type = ifcopenshell.api.root.create_entity(model, ifc_class=spec.type_class, name=spec.type_name)
        device_type.GlobalId = spec.type_guid
    elif device_type.is_a() != spec.type_class or device_type.Name != spec.type_name:
        raise PlacementApplicationError(f"Existing IFC entity conflicts with type {spec.type_name!r}")

    device = ifcopenshell.api.root.create_entity(model, ifc_class=spec.ifc_class, name=spec.type_name)
    device.GlobalId = spec.guid
    ifcopenshell.api.type.assign_type(
        model, related_objects=[device], relating_type=device_type, should_map_representations=False,
    )
    ifcopenshell.api.spatial.assign_container(model, products=[device], relating_structure=space)
    ifcopenshell.api.geometry.edit_object_placement(model, product=device, matrix=spec.matrix(), is_si=True)
    x, y, z = spec.dimensions
    body = ifcopenshell.api.geometry.add_wall_representation(
        model, context=_body_context(model), length=x, thickness=y, height=z,
    )
    ifcopenshell.api.geometry.assign_representation(model, product=device, representation=body)
    pset = ifcopenshell.api.pset.add_pset(model, product=device, name=_PSET)
    proposal = json.loads(spec.payload)
    properties = {
        "Proposal": spec.payload,
        "ProposedId": proposal["proposed_id"],
        "DeviceType": proposal["device_type"],
        "SpaceGlobalId": spec.space_id,
    }
    if spec.host_id is not None:
        properties["HostGlobalId"] = spec.host_id
    ifcopenshell.api.pset.edit_pset(
        model, pset=pset,
        properties={name: model.create_entity("IfcText", value) for name, value in properties.items()},
    )
    if host is not None:
        model.create_entity(
            "IfcRelConnectsElements", GlobalId=spec.host_relation_guid, Name="MVP placement host",
            RelatingElement=host, RelatedElement=device,
        )


def _verify_body(device, spec, unit_scale):
    body = ifcopenshell.util.representation.get_representation(device, "Model", "Body", "MODEL_VIEW")
    if body is None or len(body.Items) != 1 or not body.Items[0].is_a("IfcExtrudedAreaSolid"):
        raise PlacementApplicationError(f"IFC geometry is missing or invalid for {spec.guid}")
    solid = body.Items[0]
    profile = solid.SweptArea
    if not profile.is_a("IfcArbitraryClosedProfileDef") or not profile.OuterCurve.is_a("IfcIndexedPolyCurve"):
        raise PlacementApplicationError(f"IFC geometry profile is invalid for {spec.guid}")
    x, y = np.ptp(profile.OuterCurve.Points.CoordList, axis=0)
    dimensions = np.array([x, y, solid.Depth]) * unit_scale
    if not np.allclose(dimensions, spec.dimensions, rtol=0, atol=1e-7):
        raise PlacementApplicationError(f"IFC geometry dimensions differ for {spec.guid}")


def _verify_device(model, spec):
    device = _reference(model, spec.guid, spec.ifc_class, "device")
    pset = ifcopenshell.util.element.get_pset(device, _PSET, should_inherit=False) or {}
    container = ifcopenshell.util.element.get_container(device, should_get_direct=True)
    device_type = ifcopenshell.util.element.get_type(device)
    host_link = _by_guid(model, spec.host_relation_guid)
    if (
        device.is_a() != spec.ifc_class or pset.get("Proposal") != spec.payload
        or container is None or container.GlobalId != spec.space_id
        or device_type is None or device_type.GlobalId != spec.type_guid
        or device.ObjectPlacement is None or not device.ObjectPlacement.is_a("IfcLocalPlacement")
    ):
        raise PlacementApplicationError(f"IFC verification failed for device {spec.guid}")
    if spec.host_id is None:
        valid_host = host_link is None
    else:
        valid_host = (
            host_link is not None and host_link.is_a("IfcRelConnectsElements")
            and host_link.RelatingElement.GlobalId == spec.host_id and host_link.RelatedElement == device
        )
    matrix = ifcopenshell.util.placement.get_local_placement(device.ObjectPlacement)
    unit_scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    matrix[:3, 3] *= unit_scale
    if not valid_host or not np.allclose(matrix, spec.matrix(), rtol=0, atol=1e-7):
        raise PlacementApplicationError(f"IFC position or host verification failed for device {spec.guid}")
    _verify_body(device, spec, unit_scale)
    return device, model.by_id(pset["id"]), host_link


def _remove_device(model, spec):
    device, pset, host_link = _verify_device(model, spec)
    for reference in model.get_inverse(device):
        if reference.is_a() in {"IfcRelDefinesByType", "IfcRelContainedInSpatialStructure"}:
            continue
        if reference.is_a("IfcRelDefinesByProperties") and reference.RelatingPropertyDefinition == pset:
            continue
        if reference == host_link:
            continue
        raise PlacementApplicationError(
            f"Cannot replace or remove {spec.guid}: it has an external {reference.is_a()} reference"
        )
    # Placement-relative children can reference the placement rather than the device.
    if device.ObjectPlacement.ReferencedByPlacements:
        raise PlacementApplicationError(f"Device {spec.guid} has placement-relative children")
    if host_link is not None:
        model.remove(host_link)
    ifcopenshell.api.root.remove_product(model, product=device)


def write_batch(model, previous, current, diff):
    for item_id in diff["removed"] + diff["updated"]:
        _remove_device(model, previous[item_id])
    for item_id in diff["added"] + diff["updated"]:
        _create_device(model, current[item_id])


def verify_batch(model, current, removed):
    for spec in current.values():
        _verify_device(model, spec)
    for spec in removed:
        if _by_guid(model, spec.guid) is not None or _by_guid(model, spec.host_relation_guid) is not None:
            raise PlacementApplicationError(f"Removed device {spec.guid} still exists")
