"""IfcOpenShell adapter for approved Phase 2 placement changes.

The API does not fabricate symbols: the application must inject a
``geometry_factory`` that resolves a catalog geometry_ref into an IFC
representation. Batch edits run inside an IfcOpenShell transaction and are
written through a same-directory temporary file before replacing the model.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


PRODUCT_TYPES = {
    "đèn": ("IfcLightFixture", "IfcLightFixtureType"),
    "light": ("IfcLightFixture", "IfcLightFixtureType"),
    "ổ cắm": ("IfcOutlet", "IfcOutletType"),
    "outlet": ("IfcOutlet", "IfcOutletType"),
    "công tắc": ("IfcSwitchingDevice", "IfcSwitchingDeviceType"),
    "switch": ("IfcSwitchingDevice", "IfcSwitchingDeviceType"),
    "điểm cấp điện": ("IfcOutlet", "IfcOutletType"),
    "appliance": ("IfcElectricAppliance", "IfcElectricApplianceType"),
}
PSET_NAME = "Pset_AgentPlacement"


class IfcOpenShellPlacementAdapter:
    """Application adapter expected by ``tools.ifc_write``.

    ``geometry_factory(model, proposal)`` returns an IfcShapeRepresentation or
    raises when the catalog reference cannot be resolved. ``output_path`` is
    optional for in-memory use; when supplied it is atomically replaced.
    """
    def __init__(self, model: Any, *, geometry_factory: Callable[[Any, dict[str, Any]], Any] | None = None,
                 output_path: str | Path | None = None):
        self.model = model
        self.geometry_factory = geometry_factory
        self.output_path = Path(output_path) if output_path else None

    @staticmethod
    def _api(usecase: str, model: Any, **kwargs: Any) -> Any:
        try:
            import ifcopenshell.api
        except ImportError as exc:
            raise RuntimeError("IfcOpenShell with ifcopenshell.api is required for IFC writes.") from exc
        return ifcopenshell.api.run(usecase, model, **kwargs)

    def _product_ids(self) -> list[str]:
        ids = []
        for ifc_class in ("IfcLightFixture", "IfcOutlet", "IfcSwitchingDevice", "IfcElectricAppliance", "IfcFlowTerminal"):
            try:
                ids.extend(str(x.GlobalId) for x in self.model.by_type(ifc_class))
            except Exception:
                continue
        return ids

    def _psets(self, product: Any) -> dict[str, Any]:
        try:
            import ifcopenshell.util.element
            return ifcopenshell.util.element.get_psets(product)
        except Exception:
            return {}

    def _properties(self, product: Any) -> dict[str, Any]:
        result = self._psets(product).get(PSET_NAME, {})
        return {k: v for k, v in result.items() if k not in {"id", "type"}}

    def _add_or_get_pset(self, product: Any) -> Any:
        values = self._psets(product).get(PSET_NAME, {})
        pset_id = values.get("id") if isinstance(values, dict) else None
        if pset_id:
            try:
                return self.model.by_id(int(pset_id))
            except Exception:
                pass
        return self._api("pset.add_pset", self.model, product=product, name=PSET_NAME)

    def _by_global_id(self, global_id: str) -> Any:
        try:
            return self.model.by_guid(global_id)
        except Exception:
            return None

    def list_devices(self) -> list[dict[str, Any]]:
        records = []
        classes = ("IfcLightFixture", "IfcOutlet", "IfcSwitchingDevice", "IfcElectricAppliance")
        for ifc_class in classes:
            try:
                products = self.model.by_type(ifc_class)
            except Exception:
                continue
            for product in products:
                props = self._properties(product)
                if not props:
                    continue
                location = product.ObjectPlacement
                try:
                    import ifcopenshell.util.placement
                    matrix = ifcopenshell.util.placement.get_local_placement(location)
                    from ifcopenshell.util.unit import calculate_unit_scale
                    scale = calculate_unit_scale(self.model)
                    position = {"x": float(matrix[0][3])*scale, "y": float(matrix[1][3])*scale, "z": float(matrix[2][3])*scale}
                except Exception:
                    position = None
                records.append({"element_id": str(product.GlobalId), "device_type": props.get("DeviceType") or ifc_class,
                                "space_id": props.get("SpaceId"), "storey_id": props.get("StoreyId"),
                                "position": position, "rated_power_W": props.get("RatedPowerW"),
                                "load_class_id": props.get("LoadClassId"), "load_type": props.get("LoadType"),
                                "demand_factor": props.get("DemandFactor"), "terminal_class_id": props.get("TerminalClassId"),
                                "voltage_v": props.get("VoltageV"), "phases": props.get("Phases"),
                                "module_id": props.get("ModuleId"), "properties": props})
        return records

    @staticmethod
    def _types(proposal: dict[str, Any]) -> tuple[str, str]:
        declared = proposal.get("ifc_class")
        declared_types = {"IfcLightFixture": "IfcLightFixtureType", "IfcOutlet": "IfcOutletType",
                          "IfcSwitchingDevice": "IfcSwitchingDeviceType", "IfcElectricAppliance": "IfcElectricApplianceType"}
        if declared:
            if declared not in declared_types:
                raise ValueError(f"ifc_class không hỗ trợ hoặc không phải lớp thiết bị điện: {declared}")
            return declared, declared_types[declared]
        dtype = str(proposal.get("device_type", "")).casefold()
        for token, classes in PRODUCT_TYPES.items():
            if token in dtype:
                return classes
        raise ValueError(f"Không ánh xạ được device_type '{dtype}' sang lớp IFC; cần cấu hình rõ.")

    def _space(self, space_id: str) -> Any:
        try:
            return self.model.by_guid(space_id)
        except Exception:
            try:
                return self.model.by_id(int(space_id))
            except Exception as exc:
                raise ValueError(f"Không tìm thấy IfcSpace {space_id}.") from exc

    def _placement_matrix(self, proposal: dict[str, Any]) -> Any:
        try:
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("NumPy is required by IfcOpenShell placement editing.") from exc
        p = proposal["position"]
        orientation = proposal.get("orientation") or {}
        angle = float(orientation.get("yaw_rad", 0.0)) if isinstance(orientation, dict) else 0.0
        c, s = math.cos(angle), math.sin(angle)
        matrix = np.eye(4)
        matrix[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
        matrix[:3, 3] = [float(p["x"]), float(p["y"]), float(p["z"])]
        return matrix

    def _find_proposal(self, proposed_id: str) -> Any:
        for global_id in self._product_ids():
            product = self._by_global_id(global_id)
            if product is not None and self._properties(product).get("ProposedId") == proposed_id:
                return product
        return None

    def _find_or_create_type(self, ifc_type: str, proposal: dict[str, Any]) -> Any:
        name = str(proposal.get("module_id") or proposal.get("device_type"))
        entity = None
        for candidate in self.model.by_type(ifc_type):
            if getattr(candidate, "Name", None) == name:
                entity = candidate
                break
        if entity is None:
            entity = self._api("root.create_entity", self.model, ifc_class=ifc_type, name=name)
        pset = self._add_or_get_pset(entity)
        self._api("pset.edit_pset", self.model, pset=pset, properties={
            "DeviceType": str(proposal.get("device_type", "")), "ModuleId": str(proposal.get("module_id", "")),
            "GeometryRef": str(proposal.get("geometry_ref", "")), "RatedPowerW": proposal.get("rated_power_W"),
            "LoadClassId": str(proposal.get("load_class_id", "")), "LoadType": str(proposal.get("load_type", "")),
            "DemandFactor": proposal.get("demand_factor"), "TerminalClassId": str(proposal.get("terminal_class_id", "")),
            "VoltageV": proposal.get("voltage_v"), "Phases": proposal.get("phases"),
        })
        return entity

    def _write_batch(self, proposals: list[dict[str, Any]], idempotency_key: str,
                     removed_ids: list[str] | None = None) -> dict[str, Any]:
        created, updated, removed = [], [], []
        self.model.begin_transaction()
        temporary = None
        try:
            for proposal in proposals:
                proposed_id = str(proposal.get("proposed_id") or "")
                if not proposed_id:
                    raise ValueError("Mỗi proposal cần proposed_id bền vững để chống ghi trùng.")
                instance_class, type_class = self._types(proposal)
                product = self._find_proposal(proposed_id)
                existed = product is not None
                if product is None:
                    if self.geometry_factory is None:
                        raise RuntimeError("Chưa cấu hình geometry_factory; không tạo instance IFC không có hình học catalog.")
                    product = self._api("root.create_entity", self.model, ifc_class=instance_class, name=proposal.get("device_type"))
                    relating_type = self._find_or_create_type(type_class, proposal)
                    self._api("type.assign_type", self.model, related_objects=[product], relating_type=relating_type)
                    representation = self.geometry_factory(self.model, proposal)
                    if representation is None:
                        raise ValueError(f"Catalog geometry rỗng cho đề xuất {proposed_id}.")
                    self._api("geometry.assign_representation", self.model, product=product, representation=representation)
                    self._api("spatial.assign_container", self.model, products=[product], relating_structure=self._space(str(proposal["space_id"])))
                else:
                    old_props = self._properties(product)
                    type_changed = (product.is_a() != instance_class
                                    or old_props.get("DeviceType") != str(proposal.get("device_type", ""))
                                    or old_props.get("ModuleId") != str(proposal.get("module_id", ""))
                                    or old_props.get("GeometryRef") != str(proposal.get("geometry_ref", "")))
                    if product.is_a() != instance_class:
                        product = self._api("root.reassign_class", self.model, product=product, ifc_class=instance_class)
                    if type_changed:
                        if self.geometry_factory is None:
                            raise RuntimeError("Chưa cấu hình geometry_factory; không đổi loại/hình học instance IFC.")
                        relating_type = self._find_or_create_type(type_class, proposal)
                        self._api("type.assign_type", self.model, related_objects=[product], relating_type=relating_type)
                        shape = getattr(product, "Representation", None)
                        if shape is not None:
                            for representation in list(shape.Representations or []):
                                self._api("geometry.unassign_representation", self.model, product=product, representation=representation)
                        representation = self.geometry_factory(self.model, proposal)
                        if representation is None:
                            raise ValueError(f"Catalog geometry rỗng cho đề xuất {proposed_id}.")
                        self._api("geometry.assign_representation", self.model, product=product, representation=representation)
                self._api("geometry.edit_object_placement", self.model, product=product,
                          matrix=self._placement_matrix(proposal), is_si=True)
                pset = self._add_or_get_pset(product)
                values = {"ProposedId": proposed_id, "IdempotencyKey": idempotency_key,
                          "DeviceType": str(proposal.get("device_type", "")), "SpaceId": str(proposal.get("space_id", "")),
                          "StoreyId": str(proposal.get("storey_id", "")),
                          "HostId": str(proposal.get("host_id", "")), "HostType": str(proposal.get("host_type", "")),
                          "ModuleId": str(proposal.get("module_id", "")), "LoadClassId": str(proposal.get("load_class_id", "")),
                          "GeometryRef": str(proposal.get("geometry_ref", "")),
                          "TerminalClassId": str(proposal.get("terminal_class_id", "")),
                          "RatedPowerW": proposal.get("rated_power_W"),
                          "LoadType": str(proposal.get("load_type", "")), "DemandFactor": proposal.get("demand_factor"),
                          "VoltageV": proposal.get("voltage_v"), "Phases": proposal.get("phases"),
                          "ConnectionSpecs": json.dumps(proposal.get("connection_specs") or {}, ensure_ascii=False),
                          "RuleIds": json.dumps(proposal.get("rule_ids", []), ensure_ascii=False),
                          "Orientation": json.dumps(proposal.get("orientation") or {}, ensure_ascii=False)}
                self._api("pset.edit_pset", self.model, pset=pset, properties=values)
                (updated if existed else created).append(str(product.GlobalId))
            for proposed_id in removed_ids or []:
                product = self._find_proposal(str(proposed_id))
                if product is not None:
                    removed.append(str(product.GlobalId))
                    self._api("root.remove_product", self.model, product=product)
            if self.output_path:
                self.output_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(prefix=self.output_path.stem + ".", suffix=self.output_path.suffix, dir=self.output_path.parent)
                os.close(fd)
                self.model.write(temporary)
                os.replace(temporary, self.output_path)
                temporary = None
            self.model.end_transaction()
            return {"status": "success", "created_ids": created, "updated_ids": updated,
                    "removed_ids": removed, "model_version": idempotency_key}
        except Exception:
            self.model.discard_transaction()
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            raise

    def apply_batch(self, records: list[dict[str, Any]], idempotency_key: str,
                    removed_ids: list[str] | None = None) -> dict[str, Any]:
        return self._write_batch(records, idempotency_key, removed_ids)

    def modify_device(self, device_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        product = self._by_global_id(device_id)
        if product is None:
            raise KeyError(f"Không tìm thấy IFC device {device_id}.")
        self.model.begin_transaction()
        try:
            props = self._properties(product)
            try:
                old_orientation = json.loads(props.get("Orientation", "{}"))
            except (TypeError, ValueError):
                old_orientation = {}
            if changes.get("orientation") is not None:
                old_orientation.update(changes["orientation"])
            try:
                import ifcopenshell.util.placement
                from ifcopenshell.util.unit import calculate_unit_scale
                old_matrix = ifcopenshell.util.placement.get_local_placement(product.ObjectPlacement)
                scale = calculate_unit_scale(self.model)
                old_position = {"x": float(old_matrix[0][3])*scale, "y": float(old_matrix[1][3])*scale, "z": float(old_matrix[2][3])*scale}
            except Exception:
                old_position = None
            proposal = {"device_type": props.get("DeviceType"), "space_id": props.get("SpaceId"),
                        "position": changes.get("position") or old_position, "orientation": old_orientation}
            if proposal["position"] is not None and (changes.get("position") is not None or changes.get("orientation") is not None):
                self._api("geometry.edit_object_placement", self.model, product=product,
                          matrix=self._placement_matrix(proposal), is_si=True)
            updated = dict(props)
            updated.update(changes.get("properties", {}))
            if changes.get("device_type"):
                updated["DeviceType"] = changes["device_type"]
            updated["Orientation"] = json.dumps(old_orientation, ensure_ascii=False)
            pset = self._add_or_get_pset(product)
            self._api("pset.edit_pset", self.model, pset=pset, properties=updated)
            self._persist()
            self.model.end_transaction()
            return {"element_id": device_id, "properties": updated, "changes": changes}
        except Exception:
            self.model.discard_transaction()
            raise

    def remove_device(self, device_id: str, reason: str = "") -> list[dict[str, Any]]:
        product = self._by_global_id(device_id)
        if product is None:
            raise KeyError(f"Không tìm thấy IFC device {device_id}.")
        affected = [{"ifc_id": x.id(), "ifc_class": x.is_a()} for x in self.model.get_inverse(product)]
        self.model.begin_transaction()
        try:
            self._api("root.remove_product", self.model, product=product)
            self._persist()
            self.model.end_transaction()
            return affected
        except Exception:
            self.model.discard_transaction()
            raise

    def _persist(self) -> None:
        if not self.output_path:
            return
        fd, temporary = tempfile.mkstemp(prefix=self.output_path.stem + ".", suffix=self.output_path.suffix, dir=self.output_path.parent)
        os.close(fd)
        try:
            self.model.write(temporary)
            os.replace(temporary, self.output_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
