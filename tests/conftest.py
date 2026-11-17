"""Small real IFC models and storage doubles; no external services required."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.unit
import numpy as np
import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", help="Run network/Supabase integration tests")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        for item in items:
            if "live" in item.keywords:
                item.add_marker(
                    pytest.mark.skip(reason="Pass --live to enable Supabase integration")
                )


@pytest.fixture
def model_file(tmp_path):
    model = ifcopenshell.api.project.create_file(version="IFC4")
    project = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="Test")
    ifcopenshell.api.unit.assign_unit(model)
    context = ifcopenshell.api.context.add_context(model, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        model,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=context,
    )
    floor = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuildingStorey", name="Ground")
    floor.Elevation = 0.0
    room = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSpace", name="Bedroom")
    ifcopenshell.api.aggregate.assign_object(model, products=[floor], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(model, products=[room], relating_object=floor)
    for element, length, thickness, height in ((room, 4, 3, 3),):
        rep = ifcopenshell.api.geometry.add_wall_representation(
            model, context=body, length=length, thickness=thickness, height=height
        )
        ifcopenshell.api.geometry.assign_representation(model, product=element, representation=rep)
        ifcopenshell.api.geometry.edit_object_placement(
            model, product=element, matrix=np.eye(4), is_si=True
        )
    wall = ifcopenshell.api.root.create_entity(model, ifc_class="IfcWall", name="Mounting wall")
    rep = ifcopenshell.api.geometry.add_wall_representation(
        model, context=body, length=4, thickness=0.2, height=3
    )
    ifcopenshell.api.geometry.assign_representation(model, product=wall, representation=rep)
    ifcopenshell.api.geometry.edit_object_placement(
        model, product=wall, matrix=np.eye(4), is_si=True
    )
    model.create_entity(
        "IfcRelSpaceBoundary",
        GlobalId=ifcopenshell.guid.new(),
        RelatingSpace=room,
        RelatedBuildingElement=wall,
        PhysicalOrVirtualBoundary="PHYSICAL",
        InternalOrExternalBoundary="INTERNAL",
    )
    path = tmp_path / "architecture.ifc"
    model.write(str(path))
    return SimpleNamespace(
        path=path, model=model, room=room.GlobalId, wall=wall.GlobalId, floor=floor.GlobalId
    )


@pytest.fixture
def proposal(model_file):
    return {
        "proposed_id": "outlet-1",
        "device_type": "outlet",
        "space_id": model_file.room,
        "host_id": model_file.wall,
        "position": {"x": 1.0, "y": 0.05, "z": 1.0},
        "orientation": {"yaw": 90.0},
        "properties": {"input_W": 100},
    }


class Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.filters, self.orders = [], []
        self.action, self.values = "select", None
        self.maximum = None
        self.start, self.stop = 0, None
        self.single_row = False

    def select(self, *args):
        return self

    def eq(self, key, value):
        self.filters.append(lambda row: row.get(key) == value)
        return self

    def is_(self, key, value):
        self.filters.append(lambda row: row.get(key) is None)
        return self

    def filter(self, key, op, value):
        assert op == "not.is" and value == "null"
        self.filters.append(lambda row: row.get(key) is not None)
        return self

    def order(self, key, desc=False):
        self.orders.append((key, desc))
        return self

    def limit(self, value):
        self.maximum = value
        return self

    def range(self, start, stop):
        self.start, self.stop = start, stop + 1
        return self

    def single(self):
        self.single_row = True
        return self

    def insert(self, values):
        self.action, self.values = "insert", deepcopy(values)
        return self

    def update(self, values):
        self.action, self.values = "update", deepcopy(values)
        return self

    def execute(self):
        source = self.client.rows.setdefault(self.table, [])
        if self.action == "insert":
            row = {
                "id": str(uuid4()),
                "review_status": "pending_review",
                "reviewed_by": None,
                "reviewed_at": None,
                "applied_at": None,
                "output_file_id": None,
                "removal_details": None,
                "created_at": "2026-11-17T00:00:00Z",
                **self.values,
            }
            source.append(row)
            rows = [row]
        else:
            rows = [r for r in source if all(f(r) for f in self.filters)]
            for key, desc in reversed(self.orders):
                rows.sort(key=lambda r: (r.get(key) is None, r.get(key)), reverse=desc)
            rows = rows[self.start : self.stop]
            if self.maximum is not None:
                rows = rows[: self.maximum]
            if self.action == "update":
                for row in rows:
                    row.update(self.values)
        return SimpleNamespace(data=deepcopy(rows[0] if self.single_row else rows))


class FakeClient:
    def __init__(self):
        self.rows, self.objects = {}, {}

    def table(self, name):
        return Query(self, name)


@pytest.fixture
def storage(model_file, monkeypatch):
    from hashlib import sha256

    from tools import ifc_write

    client = FakeClient()
    project_id, file_id = str(uuid4()), str(uuid4())
    payload = model_file.path.read_bytes()
    client.rows["projects"] = [{"id": project_id}]
    client.rows["project_files"] = [
        {
            "id": file_id,
            "project_id": project_id,
            "file_kind": "architecture",
            "bucket": "ifc-files",
            "object_path": "source.ifc",
            "status": "ready",
            "sha256": sha256(payload).hexdigest(),
            "size_bytes": len(payload),
        }
    ]
    client.objects["source.ifc"] = payload
    monkeypatch.setattr(ifc_write, "_client", lambda: client)
    monkeypatch.setattr(
        ifc_write, "download_ifc", lambda c, b, key, target: target.write_bytes(c.objects[key])
    )
    monkeypatch.setattr(
        ifc_write,
        "upload_ifc",
        lambda c, b, key, source: c.objects.__setitem__(key, source.read_bytes()),
    )
    return SimpleNamespace(client=client, project_id=project_id, file_id=file_id)
