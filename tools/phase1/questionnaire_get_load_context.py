"""Tool #3 của Phase 1 và tool #2 của Phase 2.

Đọc revision đã lưu trong PostgreSQL và tạo nhu cầu chuẩn hóa để Phase 2 sử dụng.
Bàn giao nhu cầu sử dụng điện gắn với từng phòng IFC.
"""

import argparse
import json
from typing import Any
from uuid import uuid4

from database.project_store import require_id
from tools.phase1.questionnaire_get_template import (
    QUESTIONS,
)


class QuestionnaireLookupError(LookupError):
    """Phiên bản hoặc phòng không tồn tại trong dữ liệu đã lưu."""


def _derive_context(answers: list[dict], previous_ids: dict[str, str]) -> dict:
    requirements = []
    unresolved = []
    warnings = []
    requirement_ids = {}
    for answer in answers:
        item_id = answer["answer_item_id"]
        requirement_id = previous_ids.get(item_id) or f"req-{uuid4()}"
        requirement_ids[item_id] = requirement_id
        space_id = answer.get("space_id")
        warning_details = {"source_answer_item_id": item_id, "space_id": space_id}

        if answer["selected"] == "unknown":
            warnings.append({"code": "UNCONFIRMED_NEED", **warning_details})
        if answer["selected"] != "needed":
            continue

        category = answer["q_id"]
        intent = QUESTIONS[category][1]
        requirement = {
            "requirement_id": requirement_id,
            "space_id": space_id,
            "device_category": category,
            "intent": intent,
            "quantity": answer.get("quantity"),
            "input_W": answer.get("input_W"),
            "input_VA": answer.get("input_VA"),
            "power_factor": answer.get("power_factor"),
            "source_answer_item_id": item_id,
            "scope_type": answer.get("scope_type", "room"),
            "room_type": answer.get("room_type"),
        }
        for field in ("power_source", "pf_source", "placement_hint"):
            if field in answer:
                requirement[field] = answer[field]

        warning_details["requirement_id"] = requirement_id
        for value_fields, source_field, code, message in (
            (
                ("input_W", "input_VA"),
                "power_source",
                "MISSING_POWER_SOURCE",
                "Đã lưu công suất người dùng nhập; chưa có nguồn xác nhận.",
            ),
            (
                ("power_factor",),
                "pf_source",
                "MISSING_PF_SOURCE",
                "Đã lưu PF người dùng nhập; chưa có nguồn xác nhận.",
            ),
        ):
            if any(answer.get(field) is not None for field in value_fields) and not answer.get(
                source_field
            ):
                warnings.append(
                    {"code": code, "field": source_field, "message": message, **warning_details}
                )
        if space_id is None:
            unresolved.append(requirement)
            warnings.append(
                {
                    "code": "BUILDING_REQUIREMENT_NEEDS_ALLOCATION"
                    if answer.get("scope_type") == "building"
                    else "MISSING_SPACE",
                    **warning_details,
                }
            )
        else:
            requirements.append(requirement)
        if requirement["quantity"] is None:
            warnings.append({"code": "UNKNOWN_QUANTITY", **warning_details})
        if (
            intent == "equipment"
            and requirement["input_W"] is None
            and requirement["input_VA"] is None
        ):
            warnings.append({"code": "UNKNOWN_INPUT_POWER", **warning_details})

    return {
        "requirements": requirements,
        "unresolved_requirements": unresolved,
        "warnings": warnings,
        "requirement_ids": requirement_ids,
    }


def _public_context(
    project_id: str,
    snapshot: dict,
    space_id: str | None = None,
    *,
    include_answers: bool = False,
) -> dict:
    submission = snapshot["submission"]
    context = snapshot["context"]
    result = {
        "status": "ok",
        "project_id": project_id,
        "model_revision": submission["model_revision"],
        "questionnaire_revision": snapshot["questionnaire_revision"],
        "requirements": [
            row
            for row in context["requirements"]
            if space_id is None or row["space_id"] == space_id
        ],
        "unresolved_requirements": context["unresolved_requirements"] if space_id is None else [],
        "warnings": [
            warning
            for warning in context["warnings"]
            if space_id is None or "space_id" not in warning or warning["space_id"] == space_id
        ],
    }
    if "selections" in submission:
        result["playbook_revision"] = submission["playbook_revision"]
        design_input = submission.get("design_input")
        if design_input is not None and space_id is not None:
            design_input = {
                field: [
                    row for row in design_input.get(field, []) if row.get("space_id") == space_id
                ]
                for field in ("rooms", "kitchens", "custom_devices")
            } | {
                "washing_machine_space_id": (
                    space_id if design_input.get("washing_machine_space_id") == space_id else None
                ),
            }
        result["design_input"] = design_input
        result["automatic_requirements"] = [
            row
            for row in context.get("automatic_requirements", [])
            if space_id is None or row["space_id"] == space_id
        ]
        result["template_revision"] = submission["template_revision"]
        if include_answers:
            result["selections"] = [
                row
                for row in submission["selections"]
                if space_id is None or row.get("space_id") == space_id
            ]
    if include_answers:
        result.update(
            template_id=submission["template_id"],
            template_version=submission["template_version"],
            answers=[
                answer
                for answer in submission["answers"]
                if space_id is None or answer.get("space_id") == space_id
            ],
        )
    return result


def questionnaire_get_load_context(
    project_id: str,
    space_id: str | None = None,
    questionnaire_revision: int | None = None,
    *,
    store: Any,
    include_answers: bool = False,
) -> dict:
    """Read normalized electrical needs from a persisted questionnaire revision.

    Bind project_id/store on the backend. questionnaire_revision is a positive
    persisted revision, or None for latest. space_id optionally filters by IFC
    room GlobalId; omit it to retain unallocated building-wide requirements.
    Returns model/questionnaire revisions, requirements, unresolved_requirements
    and warnings. Not submitted returns status=not_submitted. Unknown explicit
    revisions or rooms raise QuestionnaireLookupError. This read never infers
    missing consumption or writes IFC; preserve requirement_id when placing loads.
    """
    require_id(project_id, "project_id")
    if space_id is not None:
        require_id(space_id, "space_id")
    if questionnaire_revision is not None and (
        type(questionnaire_revision) is not int or not 1 <= questionnaire_revision <= 2**63 - 1
    ):
        raise ValueError(
            "questionnaire_revision must be a positive integer within the PostgreSQL BIGINT range"
        )

    snapshot = store.read(project_id, questionnaire_revision)
    if snapshot is None:
        if questionnaire_revision is not None:
            raise QuestionnaireLookupError(
                f"Unknown questionnaire_revision: {questionnaire_revision}"
            )
        return {
            "status": "not_submitted",
            "project_id": project_id,
            "questionnaire_revision": None,
            "requirements": [],
            "unresolved_requirements": [],
            "warnings": [],
        }
    if space_id is not None and space_id not in snapshot["submission"]["space_ids"]:
        raise QuestionnaireLookupError(f"Unknown IFC space_id for this revision: {space_id}")
    return _public_context(project_id, snapshot, space_id, include_answers=include_answers)


def main(argv: list[str] | None = None) -> None:
    from config.settings import Settings
    from database.supabase_client import get_supabase_client
    from tools.phase1.questionnaire_submit import QuestionnaireStore

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", default="demo-house")
    parser.add_argument("--space-id", default=None)
    parser.add_argument("--revision", type=int, default=None)
    args = parser.parse_args(argv)

    store = QuestionnaireStore(get_supabase_client(Settings()))
    context = questionnaire_get_load_context(
        args.project_id,
        space_id=args.space_id,
        questionnaire_revision=args.revision,
        store=store,
        include_answers=True,
    )
    print(json.dumps(context, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
