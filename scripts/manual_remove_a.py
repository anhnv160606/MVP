"""Run the Phase 2 removal walkthrough on A.ifc with a disposable database."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from agent.orchestrator import apply_approved_placement_plan
from database.placement_application import approve_placement_plan, get_project_ifc_path, register_project_ifc
from database.project_store import ProjectStore
from ifc_engine.writer import open_model
from tools.ifc_write import draft_save_placements, ifc_remove_device


def demo_validator(*, proposals, placement_context, rules):
    room = placement_context["space_id"]
    count = sum(item["space_id"] == room and item["device_type"] == "light" for item in proposals)
    errors = [] if count >= rules["minimum_lights"] else [{"code": "minimum_lights"}]
    return {"valid": not errors, "errors": errors, "warnings": [], "suggested_fixes": []}


def main(source):
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    test_dir = Path(tempfile.mkdtemp(prefix="mvp-remove-a-"))
    os.environ["MVP_DATABASE_URL"] = os.environ["MVP_TEST_DATABASE_URL"]
    os.environ["MVP_IFC_SNAPSHOT_DIR"] = str(test_dir / "snapshots")
    project = f"manual-remove-{uuid4().hex}"
    space = "2cpX_k3PjBxxi1pGo0wUjc"
    context = {"space_id": space}
    rules = {"minimum_lights": 1}

    print("Đăng ký bản sao IFC...")
    register_project_ifc(project, source)
    a = {"proposed_id": "A", "device_type": "light", "space_id": space, "position": [-7.5, -3.5, 2.7]}
    b = {**a, "proposed_id": "B", "position": [-6.5, -3.5, 2.7]}
    c = {**a, "proposed_id": "C", "position": [-6.5, -2.5, 2.7]}
    proposals = [a, b, c]
    initial = demo_validator(proposals=proposals, placement_context=context, rules=rules)
    plan = draft_save_placements(project, proposals, initial)["plan_id"]
    options = dict(project_id=project, plan_id=plan, validate_placements=demo_validator,
                   placement_context=context, rules=rules)
    store = ProjectStore()

    draft = ifc_remove_device("C", "Đèn C dư trong bản nháp", expected_version=1, **options)
    assert draft["status"] == "pending_review" and draft["version"] == 2, draft
    assert store.get_placement_plan(project, plan, 1)["proposals"] == proposals
    assert store.get_placement_plan(project, plan, 2)["proposals"] == [a, b]
    assert store.get_placement_removal(project, plan, 2)["reason"] == "Đèn C dư trong bản nháp"
    print("Xóa C khỏi bản nháp:", draft["status"], draft["version"])

    approve_placement_plan(project, plan, 2, reviewer_id="engineer-demo")
    first = apply_approved_placement_plan(plan, 2, project)
    assert first["status"] == "applied" and first["model_version"] == 1, first
    guid_a, guid_b = first["created_ids"]
    before = get_project_ifc_path(project, 1)
    before_hash = hashlib.sha256(before.read_bytes()).hexdigest()
    staged = ifc_remove_device(guid_a, "Bỏ đèn A theo phương án mới", target="ifc", expected_version=2, **options)
    assert staged["status"] == "pending_review" and staged["version"] == 3, staged
    assert staged["removed_id"] == guid_a and staged["affected_references"]
    assert all(not item["blocks_removal"] for item in staged["affected_references"])
    assert store.get_placement_removal(project, plan, 3)["reason"] == "Bỏ đèn A theo phương án mới"
    print("Yêu cầu xóa A trong IFC:", staged["status"], staged["version"])
    print("Tham chiếu được xử lý:", [item["ifc_class"] for item in staged["affected_references"]])
    assert apply_approved_placement_plan(plan, 3, project)["status"] == "failed"
    assert get_project_ifc_path(project) == before

    approve_placement_plan(project, plan, 3, reviewer_id="engineer-demo")
    second = apply_approved_placement_plan(plan, 3, project)
    assert second["status"] == "applied" and second["model_version"] == 2, second
    assert second["removed_ids"] == [guid_a] and not second["created_ids"] and not second["updated_ids"]
    after = get_project_ifc_path(project, 2)
    model = open_model(after)
    lights = {device.GlobalId for device in model.by_type("IfcLightFixture")}
    assert guid_a not in lights and guid_b in lights
    last = ifc_remove_device(guid_b, "Thử bỏ đèn cuối", target="ifc", expected_version=3, **options)
    assert last["status"] == "validation_failed", last
    assert store.get_placement_plan(project, plan)["version"] == 3
    assert store.get_placement_removal(project, plan, 4) is None
    print("Xóa đèn cuối:", last["status"], last["validation_result"]["errors"])
    assert hashlib.sha256(before.read_bytes()).hexdigest() == before_hash
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    print("ĐẠT. Đã bỏ đèn A, giữ đèn B:", guid_b)
    print("IFC trước khi xóa A:", before)
    print("IFC sau khi xóa A:", after)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to the IFC4 A.ifc file")
    main(parser.parse_args().source)
