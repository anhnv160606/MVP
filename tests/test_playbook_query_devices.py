"""Tool 4 output check. Run: python tests\\test_playbook_query_devices.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.phase2 import build_phase2_toolset


def main():
    toolset = build_phase2_toolset(
        project_id="tool-4-test-project", store=None,
        supabase_client=SamplePlaybookClient(),
    )
    devices = toolset["execute"]("playbook_query_devices", {
        "category": "Áptômát tép", "filters": {"rated_current_a": 6}
    })
    assert isinstance(devices, list)
    expected_device_fields = {
        "module_id", "name", "ifc_class", "specs", "rated_power", "geometry_ref"
    }
    assert all(set(row) == expected_device_fields for row in devices)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(devices, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
