"""Tool 6 output check. Run: python tests\\test_playbook_get_terminal_class.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from tools.phase2 import build_phase2_toolset


def main():
    toolset = build_phase2_toolset(
        project_id="tool-6-test-project", store=None,
        supabase_client=None,
    )
    output = toolset["execute"]("playbook_get_terminal_class", {
        "device_type": "Điểm đèn trần",
        "connection_requirements": {"voltage": 220},
    })
    assert set(output) == {"terminal_class_id", "voltage", "phases", "connection_specs"}
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
