"""Tool 6 output check. Run: python tests\\test_playbook_get_terminal_class.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.playbook import playbook_get_terminal_class


def main():
    result = playbook_get_terminal_class(
        SamplePlaybookClient(), device_type="Điểm đèn trần",
        connection_requirements={"voltage_v": 220, "phases": 1},
    )
    matches = result["terminal_classes"]
    if len(matches) != 1:
        raise AssertionError(f"Mong đợi đúng một lớp đầu cuối, nhận được {len(matches)}.")
    row = matches[0]
    output = {"terminal_class_id": row["terminal_class_id"], "voltage": row["voltage"],
              "phases": row["phases"], "connection_specs": row["connection_specs"]}
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
