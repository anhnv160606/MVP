"""Tool 3 output check. Run: python tests\\test_playbook_get_object_rules.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.phase2 import build_phase2_toolset


def main():
    toolset = build_phase2_toolset(
        project_id="tool-3-test-project", store=None,
        supabase_client=SamplePlaybookClient(),
    )
    result = toolset["execute"]("playbook_get_object_rules", {
        "device_type": "đèn trần", "space_type": "Phòng ngủ",
        "project_standard": "TCVN 9206:2012",
    })
    assert set(result) == {"rules"}
    assert result["rules"]
    required_rule_fields = {
        "rule_id", "quantity_rule", "spacing", "mounting_height", "host_type",
        "constraints", "source", "scope",
    }
    assert all(required_rule_fields <= set(row) for row in result["rules"])
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
