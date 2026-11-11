"""Tool 5 output check. Run: python tests\\test_playbook_get_load_class.py"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from playbook_fixture import SamplePlaybookClient
from tools.playbook import playbook_get_load_class


def main():
    result = playbook_get_load_class(
        SamplePlaybookClient(), space_type="Phòng ngủ", device_category="Chiếu sáng chung"
    )
    matches = result["load_classes"]
    if len(matches) != 1:
        raise AssertionError(f"Mong đợi đúng một lớp phụ tải, nhận được {len(matches)}.")
    row = matches[0]
    output = {"load_class_id": row["load_class_id"],
              "load_class_name": row["load_class_name"], "load_type": row["load_type"]}
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
