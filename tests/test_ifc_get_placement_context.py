"""Simple Tool 1 smoke test: call with space_id and print the tool output only.

Run from the MVP repository root with:
    python tests\\test_ifc_get_placement_context.py

Set A_IFC_PATH to use a different IFC file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import ifcopenshell


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.phase2 import build_phase2_toolset


DEFAULT_IFC_PATH = Path(__file__).resolve().parents[2] / "A.ifc"
IFC_PATH = Path(os.environ.get("A_IFC_PATH", str(DEFAULT_IFC_PATH)))
OUTPUT_FIELDS = {
    "space_type", "geometry", "walls", "ceiling", "doors", "windows",
    "obstacles", "coordinate_system", "missing_data",
}


def main() -> None:
    if not IFC_PATH.is_file():
        raise FileNotFoundError(f"Không tìm thấy IFC: {IFC_PATH}")

    model = ifcopenshell.open(str(IFC_PATH))
    spaces = model.by_type("IfcSpace")
    if not spaces:
        raise AssertionError(f"IFC không có IfcSpace: {IFC_PATH}")

    # The dispatcher injects the IFC model. The public tool call has one input.
    toolset = build_phase2_toolset(
        project_id="tool-1-test-project",
        store=None,
        supabase_client=None,
        ifc_model_provider=lambda: model,
    )
    space_id = str(spaces[0].GlobalId)
    result = toolset["execute"](
        "ifc_get_placement_context",
        {"space_id": space_id},
    )

    assert set(result) == OUTPUT_FIELDS, f"Output fields không đúng: {sorted(result)}"
    assert result["space_type"], "Không nhận diện được loại phòng."
    estimated_area = result["geometry"].get("area_bbox_estimate_m2")
    assert estimated_area is not None and estimated_area > 0.01, (
        "Hộp bao phòng thiếu kích thước hợp lệ hoặc bị đổi đơn vị sai."
    )

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, OSError):
        pass
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
