"""Tool 4 output check. Run: python tests\\test_playbook_query_devices.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.playbook import playbook_query_devices


def main():
    result = playbook_query_devices(
        SamplePlaybookClient(), category="Áptômát tép", filters={"rated_current_a": 6}
    )
    devices = result["devices"]
    assert all({"module_id", "name", "ifc_class", "specs", "rated_power", "geometry_ref"} <= set(row) for row in devices)
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(devices, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
