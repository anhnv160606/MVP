"""Real-model read/write smoke test; this is a technical fixture, not a design."""

from hashlib import sha256
from pathlib import Path

import pytest

from ifc_engine import writer
from ifc_engine.merger import export_combined_model
from tools.ifc_read import ifc_get_placement_context, ifc_list_placed_devices


@pytest.mark.ifc
def test_ctr_roundtrip_preserves_original(tmp_path):
    source = Path(__file__).resolve().parents[1] / "ctr.ifc"
    if not source.exists():
        pytest.skip("ctr.ifc is not present")
    original_hash = sha256(source.read_bytes()).hexdigest()
    model = writer.open_model(source)
    room = model.by_type("IfcSpace")[0]
    context = ifc_get_placement_context(room.GlobalId, source)
    hosts = context["walls"] or context["ceiling"]
    assert hosts and context["geometry"]["bbox"]
    # Deliberately only a writer fixture: no claim that the bbox midpoint is
    # an engineering-approved mount or that placement rules are satisfied.
    center = context["geometry"]["centroid"]
    proposal = {
        "proposed_id": "roundtrip-fixture",
        "device_type": "outlet",
        "space_id": room.GlobalId,
        "host_id": hosts[0]["element_id"],
        "position": dict(zip("xyz", center)),
        "orientation": {"yaw": 30.0},
        "properties": {"test_fixture": True},
    }
    spec = writer.device_spec("roundtrip-project", "roundtrip-plan", proposal)
    writer.create_device(model, spec)
    output = tmp_path / "ctr.devices-test.ifc"
    model.write(str(output))
    reopened = writer.open_model(output)
    writer.verify_batch(reopened, {"roundtrip-fixture": spec}, [])
    devices = ifc_list_placed_devices(output, space_id=room.GlobalId, ifc_types=["IfcOutlet"])
    placed = next(device for device in devices if device["element_id"] == spec.guid)
    assert placed["position"] == pytest.approx(proposal["position"])
    export_combined_model(source, output, tmp_path / "combined.ifc")
    writer.remove_device(reopened, spec)
    writer.verify_batch(reopened, {}, [spec])
    assert sha256(source.read_bytes()).hexdigest() == original_hash
