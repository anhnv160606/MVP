"""Tool 5 output check. Run: python tests\\test_playbook_get_load_class.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from tools.phase2 import build_phase2_toolset


def main():
    toolset = build_phase2_toolset(
        project_id="tool-5-test-project", store=None,
        supabase_client=None,
    )
    output = toolset["execute"]("playbook_get_load_class", {
        "space_type": "Phòng ngủ", "device_category": "ổ cắm"
    })
    assert set(output) == {"load_class_id", "load_class_name", "load_type"}
    assert output["load_class_id"] == "TẢI-003"
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
