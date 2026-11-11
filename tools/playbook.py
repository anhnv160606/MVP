"""Read the load catalog used by the preliminary load tool."""

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class LoadClass:
    id: str
    name: str
    factor: float | None
    rule_scope: str
    rule_source: str


@dataclass(frozen=True)
class Playbook:
    revision: str
    classes: dict[str, LoadClass]


def read_load_catalog(path: str | Path) -> Playbook:
    data = Path(path).read_bytes()
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        rows = workbook["Lớp phụ tải"].iter_rows(min_row=4, max_col=11, values_only=True)
        header = next(rows)
        if (header[0], header[6], header[9]) != (
            "Mã lớp phụ tải", "Hệ số nhu cầu", "Nguồn quy tắc"
        ):
            raise ValueError("Unexpected headers in Lớp phụ tải")
        classes = {}
        for row_number, row in enumerate(rows, start=5):
            key, name, _, _, _, _, factor, _, _, source, _ = row
            if not key:
                continue
            if key in classes:
                raise ValueError(f"Duplicate load class {key}")
            if factor is not None and (
                type(factor) not in (int, float) or not 0 <= factor <= 1
            ):
                raise ValueError(f"Invalid factor for {key}")
            # The numeric cells are editable. Eligibility follows the approved MVP
            # mapping, independently of the wording of the notes in the workbook.
            if key in {"TẢI-002", "TẢI-003", "TẢI-004", "TẢI-005"}:
                scope = "receptacle_circuit"
            elif key in {"TẢI-001", "TẢI-006"}:
                scope = "full_load_scenario"
            else:
                scope = "equipment_scenario"
            classes[key] = LoadClass(key, name, factor, scope, f"G{row_number}/J{row_number}: {source}")
        if not classes:
            raise ValueError("Playbook has no load classes")
        return Playbook(sha256(data).hexdigest(), classes)
    finally:
        workbook.close()
