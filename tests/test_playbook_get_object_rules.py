"""Tool 3 output check. Run: python tests\\test_playbook_get_object_rules.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.playbook import playbook_get_object_rules


def main():
    result = playbook_get_object_rules(
        SamplePlaybookClient(), device_type="đèn trần",
        space_type="Phòng ngủ", project_standard="TCVN 9206:2012",
    )
    output = {"rules": result["rules"]}
    assert all("rule_id" in row and "source" in row and "scope" in row for row in output["rules"])
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
