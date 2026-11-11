"""Tool 2 output check. Run: python tests\\test_questionnaire_get_load_context.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.questionnaire import questionnaire_get_load_context


class SampleQuestionnaireStore:
    def get_questionnaire(self, project_id: str):
        return {
            "derived_load_context": {
                "per_space_requirements": {
                    "bedroom-02": {"air_conditioner": True, "outlets": "standard"},
                    "kitchen": {"special_cooking_device": True},
                },
                "declared_loads": [
                    {"space_id": "bedroom-02", "device_type": "air_conditioner"},
                    {"space_id": "kitchen", "device_type": "induction_cooker"},
                ],
                "special_devices": [
                    {"space_id": "bedroom-02", "device_type": "water_heater"},
                ],
            }
        }


def main() -> None:
    result = questionnaire_get_load_context(
        project_id="tool-2-test-project",
        space_id="bedroom-02",
        store=SampleQuestionnaireStore(),
    )
    output = {key: result[key] for key in (
        "per_space_requirements", "declared_loads", "special_devices"
    )}
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
