"""Exercise real geometry/writer with isolated storage, including review and replay."""

from copy import deepcopy
from uuid import uuid4

import ifcopenshell
import pytest

from ifc_engine import writer
from tools import ifc_write
from tools.ifc_inspect import IfcInspection
from tools.ifc_read import ifc_get_placement_context, ifc_list_placed_devices
from tools.validation import engine_validate_placements


def validation(path, proposals):
    return engine_validate_placements(proposals, {}, ifc_file_path=path)


def test_native_readers_and_validation(model_file, proposal):
    inspection = IfcInspection(model_file.path)
    assert inspection.ifc_get_storeys()[0]["id"] == model_file.floor
    assert inspection.ifc_get_spaces(model_file.floor)[0]["id"] == model_file.room
    geometry = inspection.ifc_get_space_geometry(model_file.room)
    assert geometry["bbox"]["max"] == pytest.approx([4, 3, 3])  # source is millimetres
    context = ifc_get_placement_context(model_file.room, model_file.path)
    assert context["geometry"]["storey_name"] == "Ground"
    assert context["walls"][0]["element_id"] == model_file.wall
    assert inspection.ifc_get_doors_windows(model_file.room)["doors"] == []
    assert inspection.ifc_get_space_adjacency(model_file.room) == []
    assert inspection.ifc_search_elements("IfcWall")["total"] == 1
    assert inspection.ifc_get_building_summary()["space_count"] == 1
    assert validation(model_file.path, [proposal])["valid"]
    invalid = deepcopy(proposal)
    invalid["position"]["x"] = 100
    assert not validation(model_file.path, [invalid])["valid"]
    invalid = {**proposal, "orientation": {"x": 0, "y": 0, "z": 1}}
    assert any(
        e["code"] == "ERR_INVALID_ORIENTATION"
        for e in validation(model_file.path, [invalid])["errors"]
    )
    assert not validation(model_file.path, [proposal, proposal])["valid"]


def test_complete_write_edit_remove_replay(model_file, proposal, storage, tmp_path):
    project = storage.project_id
    original = model_file.path.read_bytes()
    saved = ifc_write.draft_save_placements(
        project, [proposal], validation(model_file.path, [proposal]), source_file_id=storage.file_id
    )
    plan_id = saved["plan_id"]
    row = ifc_write.get_placement_plan(project, plan_id)
    device_id = row["proposals"][0]["device_id"]
    assert ifc_write.ifc_apply_placements_batch(plan_id, 1, project)["status"] == "failed"
    reviewer = str(uuid4())
    ifc_write.approve_placement_plan(project, plan_id, 1, reviewer_id=reviewer)
    result = ifc_write.ifc_apply_placements_batch(plan_id, 1, project)
    assert result["status"] == "applied", result
    guid = writer.device_guid(project, plan_id, proposal["proposed_id"])
    assert result["created_ids"] == [guid]
    assert ifc_write.ifc_apply_placements_batch(plan_id, 1, project)["status"] == "already_applied"
    output_row = next(
        r for r in storage.client.rows["project_files"] if r["id"] == result["model_version"]
    )
    output = tmp_path / "electrical.ifc"
    output.write_bytes(storage.client.objects[output_row["object_path"]])
    devices = ifc_list_placed_devices(
        output, space_id=model_file.room, storey_id=model_file.floor, ifc_types=["IfcOutlet"]
    )
    assert devices[0]["element_id"] == guid
    assert devices[0]["position"] == pytest.approx(proposal["position"])
    assert model_file.path.read_bytes() == original

    with ifc_write.placement_review_context(project, plan_id, 1):
        edit = ifc_write.ifc_modify_device(device_id, {"position": {"x": 2.0, "y": 0.05, "z": 1.0}})
    assert edit["status"] == "pending_review", edit
    assert edit["version"] == 2
    with pytest.raises(ifc_write.PlacementConflict):
        ifc_write.approve_placement_plan(project, plan_id, 1, reviewer_id=reviewer)
    ifc_write.approve_placement_plan(project, plan_id, 2, reviewer_id=reviewer)
    changed = ifc_write.ifc_apply_placements_batch(plan_id, 2, project)
    assert changed["status"] == "applied", changed
    assert changed["updated_ids"] == [guid]
    with ifc_write.placement_review_context(project, plan_id, 2):
        removed = ifc_write.ifc_remove_device(device_id, "Engineer removed duplicate outlet")
    assert removed["status"] == "pending_review", removed
    assert ifc_write.get_placement_removal(project, plan_id, 3)["reason"]
    ifc_write.approve_placement_plan(project, plan_id, 3, reviewer_id=reviewer)
    final = ifc_write.ifc_apply_placements_batch(plan_id, 3, project)
    assert final["status"] == "applied", final
    assert final["removed_ids"] == [guid]


def test_forged_validation_rejected_on_review(model_file, proposal, storage):
    bad = deepcopy(proposal)
    bad["position"]["x"] = 1000
    forged = {"valid": True, "errors": [], "warnings": [], "suggested_fixes": []}
    saved = ifc_write.draft_save_placements(
        storage.project_id, [bad], forged, source_file_id=storage.file_id
    )
    with pytest.raises(ifc_write.PlacementError):
        ifc_write.approve_placement_plan(
            storage.project_id, saved["plan_id"], 1, reviewer_id=str(uuid4())
        )


def test_write_failure_preserves_current_ifc(model_file, proposal, storage, monkeypatch):
    saved = ifc_write.draft_save_placements(
        storage.project_id,
        [proposal],
        validation(model_file.path, [proposal]),
        source_file_id=storage.file_id,
    )
    ifc_write.approve_placement_plan(
        storage.project_id, saved["plan_id"], 1, reviewer_id=str(uuid4())
    )

    def fail(*args):
        raise OSError("Simulated storage outage")

    monkeypatch.setattr(ifc_write, "upload_ifc", fail)
    result = ifc_write.ifc_apply_placements_batch(saved["plan_id"], 1, storage.project_id)
    assert result["status"] == "failed"
    assert (
        ifc_write.get_placement_plan(storage.project_id, saved["plan_id"])["output_file_id"] is None
    )
    assert ifcopenshell.open(str(model_file.path)).by_type("IfcOutlet") == []
