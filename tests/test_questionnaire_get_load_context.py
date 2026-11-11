"""Tests for Tool 2 using a temporary SQLite project store.

Run from the MVP repository root with:
    python -m unittest discover -s tests -p "test_questionnaire_get_load_context.py" -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from database.project_store import ProjectStore
from tools.questionnaire import questionnaire_get_load_context


class TestQuestionnaireGetLoadContext(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "project_state.sqlite"
        self.store = ProjectStore(database_path)
        self.project_id = "tool-2-test-project"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_returns_only_requirements_for_requested_space(self) -> None:
        answers = {
            "per_space_requirements": {
                "space-bedroom": {"needs_air_conditioner": True},
                "space-kitchen": {"needs_special_cooking_device": True},
            },
            "declared_loads": [
                {"space_id": "space-bedroom", "device_type": "air-conditioner"},
                {"space_id": "space-kitchen", "device_type": "induction-cooker"},
            ],
            "special_devices": [
                {"space_id": "space-bedroom", "device_type": "water-heater"},
                {"space_id": "space-kitchen", "device_type": "water-pump"},
            ],
        }
        self.store.save_questionnaire(self.project_id, answers)

        result = questionnaire_get_load_context(
            project_id=self.project_id,
            space_id="space-bedroom",
            answers=None,
            store=self.store,
        )

        self.assertEqual(list(result["per_space_requirements"]), ["space-bedroom"])
        self.assertEqual(result["declared_loads"], [answers["declared_loads"][0]])
        self.assertEqual(result["special_devices"], [answers["special_devices"][0]])
        self.assertEqual(result["missing_data"], [])
        self.assertEqual(result["warnings"], [])
        print("\nTool 2 filtered result:\n" + json.dumps(result, ensure_ascii=False, indent=2))

    def test_raw_phase1_answers_are_not_guessed(self) -> None:
        raw_answers = {"answers": [{"q_id": "Q1", "selected": "option-1"}]}

        result = questionnaire_get_load_context(
            project_id=self.project_id,
            space_id=None,
            answers=raw_answers,
            store=None,
        )

        self.assertEqual(result["missing_data"], ["derived_load_context"])
        self.assertTrue(result["warnings"])
        print("\nTool 2 raw-answer warning:\n" + json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
