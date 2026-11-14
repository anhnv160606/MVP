"""Tính phụ tải điện sơ bộ theo phòng, tầng hoặc zone."""

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path

from openpyxl import load_workbook

from database.project_store import ProjectSnapshot, read_snapshot, require_id
from ifc_engine.reader import read_spaces

# ──────────────────────────────────────────────────────────────────────
# Kiểm tra dữ liệu công suất
# ──────────────────────────────────────────────────────────────────────

def quantity(value, field, *, positive=False):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a number")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"Giá trị {field} không hợp lệ")
    return number


def validate_power_values(row):
    """Kiểm tra giá trị điện và giữ nguyên dữ liệu chưa biết."""
    watts = quantity(row.get("input_W"), "input_W")
    va = quantity(row.get("input_VA"), "input_VA")
    pf = quantity(row.get("power_factor"), "power_factor", positive=True)
    if pf is not None and pf > 1:
        raise ValueError("power_factor phải nằm trong khoảng (0, 1]")
    if watts is not None and va is not None and watts > va:
        raise ValueError("input_W không được lớn hơn input_VA")
    if pf is not None and watts is not None and va is not None:
        if abs(watts - va * pf) > max(Decimal("0.01"), watts * Decimal("0.02")):
            raise ValueError("input_W và input_VA không khớp với power_factor")
    return watts, va, pf

# ──────────────────────────────────────────────────────────────────────
# Đọc lớp phụ tải từ Playbook
# ──────────────────────────────────────────────────────────────────────

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
    """Đọc danh mục lớp phụ tải cho phép tính sơ bộ."""
    data = Path(path).read_bytes()
    workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    try:
        rows = workbook["Lớp phụ tải"].iter_rows(min_row=4, max_col=11, values_only=True)
        header = next(rows)
        if (header[0], header[6], header[9]) != (
            "Mã lớp phụ tải", "Hệ số nhu cầu", "Nguồn quy tắc"
        ):
            raise ValueError("Tiêu đề trong trang tính Lớp phụ tải không đúng định dạng")
        classes = {}
        for row_number, row in enumerate(rows, start=5):
            key, name, _, _, _, _, factor, _, _, source, _ = row
            if not key:
                continue
            if key in classes:
                raise ValueError(f"Mã lớp phụ tải bị trùng: {key}")
            if factor is not None and (
                type(factor) not in (int, float) or not 0 <= factor <= 1
            ):
                raise ValueError(f"Hệ số nhu cầu không hợp lệ cho {key}")
            # Ô số có thể chỉnh sửa. Phạm vi áp dụng theo ánh xạ của MVP.
            if key in {"TẢI-002", "TẢI-003", "TẢI-004", "TẢI-005"}:
                scope = "receptacle_circuit"
            elif key in {"TẢI-001", "TẢI-006"}:
                scope = "full_load_scenario"
            else:
                scope = "equipment_scenario"
            classes[key] = LoadClass(key, name, factor, scope, f"G{row_number}/J{row_number}: {source}")
        if not classes:
            raise ValueError("Playbook không có lớp phụ tải")
        return Playbook(sha256(data).hexdigest(), classes)
    finally:
        workbook.close()

# ──────────────────────────────────────────────────────────────────────
# Tính phụ tải sơ bộ
# ──────────────────────────────────────────────────────────────────────

RULESET_ID = "VN_TOWNHOUSE_PRELIM_V1"
CONSUMING_KINDS = {"equipment", "allowance"}
LOAD_KINDS = CONSUMING_KINDS | {"receptacle", "control", "generation"}


def power(row):
    """Chỉ quy đổi công suất khi đã có nguồn cho các giá trị cần dùng."""
    watts, va, pf = validate_power_values(row)
    if watts is not None or va is not None:
        require_id(row.get("power_source"), "power_source")
    if pf is not None:
        require_id(row.get("pf_source"), "pf_source")
    if watts is None and va is not None and pf is not None:
        watts = va * pf
    if va is None and watts is not None and pf is not None:
        va = watts / pf
    return watts, va


def indexed(rows, field):
    result = {}
    for row in rows:
        key = require_id(row.get(field), field)
        if key in result and result[key] != row:
            raise ValueError(f"Bản ghi {field} bị xung đột: {key}")
        result[key] = row
    return result


@dataclass(frozen=True)
class ScopedLoads:
    placements: list[dict]
    pending: list[dict]
    scenarios: dict[str, set[str]]
    warnings: list[dict]


def resolve_loads(snapshot, playbook, scope_type, scope_id):
    rooms = snapshot.room_ids(scope_type, scope_id)
    zone_pairs = snapshot.zones.get(scope_id) if scope_type == "zone" else None
    placements = indexed(
        [row for row in snapshot.placements if row.get("status") != "deleted"], "load_id"
    )
    requirements = indexed(snapshot.requirements, "requirement_id")
    for row in [*placements.values(), *requirements.values()]:
        if row.get("room_id") not in snapshot.model.rooms:
            raise ValueError(f"Không tìm thấy phòng IFC: {row.get('room_id')}")
        if row.get("load_class_id") not in playbook.classes:
            raise ValueError(f"Không tìm thấy lớp phụ tải: {row.get('load_class_id')}")
    for pairs in snapshot.zones.values():
        if any(load_class not in playbook.classes for _, load_class in pairs):
            raise ValueError("Ánh xạ zone tham chiếu lớp phụ tải không tồn tại")

    def in_scope(row):
        pair = (row["room_id"], row["load_class_id"])
        return pair[0] in rooms and (zone_pairs is None or pair in zone_pairs)

    requested = {}
    for key, requirement in requirements.items():
        count = requirement.get("quantity", 1)
        if type(count) is not int or count < 1:
            raise ValueError(f"Số lượng yêu cầu không hợp lệ: {key}")
        requested[key] = count

    fulfilled = set()
    supplied_outlets = set()
    allowances = set()
    for row in placements.values():
        kind = row.get("kind")
        if kind not in LOAD_KINDS:
            raise ValueError(f"Loại tải không hợp lệ: {row['load_id']}")
        catalog = playbook.classes[row["load_class_id"]]
        if (catalog.id == "TẢI-021") != (kind == "generation"):
            raise ValueError("TẢI-021 phải được khai báo là nguồn phát")
        if kind == "allowance" and catalog.rule_scope != "receptacle_circuit":
            raise ValueError("Suất tải ổ cắm phải dùng lớp tải ổ cắm")
        outlet = row.get("outlet_id")
        if outlet is not None:
            outlet = require_id(outlet, "outlet_id")
            if kind == "equipment":
                supplied_outlets.add(outlet)
            elif kind == "allowance":
                if outlet in allowances:
                    raise ValueError(f"Có nhiều suất tải cho cùng ổ cắm: {outlet}")
                allowances.add(outlet)
        key = row.get("requirement_id")
        if key is None:
            continue
        requirement = requirements.get(key)
        if (
            requirement is None
            or row["room_id"] != requirement["room_id"]
            or row["load_class_id"] != requirement["load_class_id"]
            or kind not in CONSUMING_KINDS | {"generation"}
        ):
            raise ValueError(f"Liên kết yêu cầu không hợp lệ cho {row['load_id']}: {key}")
        unit = row.get("requirement_unit", 1 if requested[key] == 1 else None)
        if type(unit) is not int or not 1 <= unit <= requested[key] or (key, unit) in fulfilled:
            raise ValueError(f"Phân bổ yêu cầu không hợp lệ hoặc bị trùng {key}: {unit}")
        fulfilled.add((key, unit))

    pending = []
    for key, requirement in requirements.items():
        if in_scope(requirement):
            missing = [unit for unit in range(1, requested[key] + 1) if (key, unit) not in fulfilled]
            if missing:
                pending.append({**requirement, "pending_units": missing})
    warnings = []
    scoped = []
    for row in placements.values():
        if not in_scope(row):
            continue
        if row["kind"] == "allowance" and row.get("outlet_id") in supplied_outlets:
            warnings.append({"code": "ALLOWANCE_REPLACED", "load_id": row["load_id"]})
            continue
        requirement = requirements.get(row.get("requirement_id"), {})
        power_fields = ("input_W", "input_VA", "power_factor", "power_source", "pf_source")
        if row["kind"] == "equipment" and requirement:
            merged = dict(row)
            copied_power = False
            has_conflict = False
            for field in power_fields:
                requirement_value = requirement.get(field)
                placement_value = row.get(field)
                if placement_value is None and requirement_value is not None:
                    merged[field] = requirement_value
                    if field in {"input_W", "input_VA"}:
                        copied_power = True
                elif (
                    placement_value is not None
                    and requirement_value is not None
                    and placement_value != requirement_value
                ):
                    has_conflict = True
            if copied_power:
                merged["power_basis"] = "questionnaire"
            if has_conflict:
                warnings.append({"code": "POWER_SOURCE_CONFLICT", "load_id": row["load_id"]})
            row = merged
        scoped.append(row)

    scenarios = {}
    for name, active_ids in snapshot.scenarios.items():
        require_id(name, "scenario_id")
        if not isinstance(active_ids, list) or any(load_id not in placements for load_id in active_ids):
            raise ValueError(f"Mã tải trong kịch bản không tồn tại hoặc không hợp lệ: {name}")
        scenarios[name] = set(active_ids)
    if not scenarios:
        scenarios["all_placed_loads"] = set(placements)
    if pending:
        warnings.append({"code": "PENDING_REQUIREMENTS", "count": len(pending)})
    return ScopedLoads(scoped, pending, scenarios, warnings)


def to_number(value):
    return float(round(value, 6)) if value is not None else None


def total(values, *, require_all=False):
    known = [value for value in values if value is not None]
    if not values:
        return 0, "complete"
    if not known:
        return None, "unavailable"
    complete = len(known) == len(values)
    value = sum(known, Decimal(0))
    return (value if complete or not require_all else None), ("complete" if complete else "partial")


def calc_preliminary_load(
    plan_id: str, plan_revision: str, scope_type: str, scope_id: str, *,
    snapshot: ProjectSnapshot, playbook: Playbook, ruleset_id: str | None = RULESET_ID,
) -> dict:
    """Trả kết quả tính sơ bộ từ snapshot hiện tại."""
    if (plan_id, plan_revision) != (snapshot.plan_id, snapshot.plan_revision):
        raise ValueError("Revision phương án yêu cầu không khớp snapshot")
    if snapshot.playbook_revision is not None and snapshot.playbook_revision != playbook.revision:
        raise ValueError("playbook_revision trong snapshot không khớp file Playbook")
    if ruleset_id not in {RULESET_ID, None}:
        raise ValueError(f"Không tìm thấy bộ quy tắc: {ruleset_id}")
    scoped = resolve_loads(snapshot, playbook, scope_type, scope_id)
    warnings = scoped.warnings
    breakdown = []
    consumption = {}
    for row in scoped.placements:
        load_id = row["load_id"]
        kind = row["kind"]
        watts, va = None, None
        if kind in CONSUMING_KINDS:
            validate_power_values(row)
            for field in ("power_source", "pf_source"):
                if row.get(field) is not None:
                    require_id(row[field], field)
            has_power = row.get("input_W") is not None or row.get("input_VA") is not None
            missing_source = has_power and row.get("power_source") is None
            missing_pf_source = row.get("power_factor") is not None and row.get("pf_source") is None
            if missing_source:
                warnings.append({"code": "MISSING_POWER_SOURCE", "load_id": load_id})
            if missing_pf_source:
                warnings.append({"code": "MISSING_PF_SOURCE", "load_id": load_id})
            if not missing_source:
                # Giữ W/VA khai báo trực tiếp và bỏ PF chưa có nguồn khi quy đổi.
                usable = {**row, "power_factor": None} if missing_pf_source else row
                watts, va = power(usable)
        breakdown.append({
            **row, "floor_id": snapshot.model.rooms[row["room_id"]],
            "connected_W": to_number(watts), "connected_VA": to_number(va),
        })
        if kind not in CONSUMING_KINDS:
            continue
        if row.get("input_W") is None and row.get("input_VA") is None:
            warnings.append({"code": "MISSING_INPUT_POWER", "load_id": load_id})
        elif not missing_source and (watts is None or va is None):
            warnings.append({"code": "MISSING_POWER_FACTOR", "load_id": load_id})
        load_class = playbook.classes[row["load_class_id"]]
        factor = load_class.factor if ruleset_id else None
        if kind == "equipment" and load_class.rule_scope == "receptacle_circuit":
            # Lớp ổ cắm cũng có thể biểu thị điểm cấp điện của thiết bị.
            factor = 1 if ruleset_id else None
            rule = "scenario_equipment"
            source = "MVP assumption: known equipment at full input when running"
        else:
            rule = load_class.rule_scope
            source = load_class.rule_source
        if kind == "allowance" and not row.get("circuit_id"):
            factor = None
            warnings.append({"code": "MISSING_RECEPTACLE_CIRCUIT", "load_id": load_id})
        if factor is None:
            warnings.append({"code": "MISSING_DEMAND_RULE", "load_id": load_id})
        consumption[load_id] = (watts, va, factor, rule, source)

    values = list(consumption.values())
    connected_w, coverage_w = total([row[0] for row in values])
    connected_va, coverage_va = total([row[1] for row in values])
    scenarios = {}
    for scenario, active in scoped.scenarios.items():
        rows = []
        demand_w = []
        demand_va = []
        for load_id, (watts, va, factor, rule, source) in consumption.items():
            if load_id not in active:
                continue
            multiplier = Decimal(str(factor)) if factor is not None else None
            w = watts * multiplier if watts is not None and multiplier is not None else None
            s = va * multiplier if va is not None and multiplier is not None else None
            demand_w.append(w)
            demand_va.append(s)
            rows.append({
                "load_id": load_id, "factor": factor, "rule": rule, "source": source,
                "demand_W": to_number(w), "demand_VA": to_number(s),
            })
        w, status_w = total(demand_w, require_all=True)
        s, status_va = total(demand_va, require_all=True)
        scenarios[scenario] = (w, s, status_w, status_va, rows)

    def peak(value_index, status_index):
        statuses = [entry[status_index] for entry in scenarios.values()]
        if any(status != "complete" for status in statuses):
            return None, None, "unavailable" if all(s == "unavailable" for s in statuses) else "partial"
        name = max(scenarios, key=lambda scenario: scenarios[scenario][value_index])
        return to_number(scenarios[name][value_index]), name, "complete"

    demand_w, peak_w, demand_coverage_w = peak(0, 2)
    demand_va, peak_va, demand_coverage_va = peak(1, 3)
    return {
        "plan_id": plan_id, "plan_revision": plan_revision, "plan_status": snapshot.plan_status,
        "scope_type": scope_type, "scope_id": scope_id,
        "input_revisions": {"ifc_sha256": snapshot.model.revision, "playbook_sha256": playbook.revision},
        "ruleset_id": ruleset_id,
        "connected_load_W": to_number(connected_w),
        "connected_load_VA": to_number(connected_va),
        "estimated_demand_W": demand_w, "estimated_demand_VA": demand_va,
        "peak_scenario_W": peak_w, "peak_scenario_VA": peak_va,
        "coverage": {
            "connected_load_W": coverage_w, "connected_load_VA": coverage_va,
            "estimated_demand_W": demand_coverage_w, "estimated_demand_VA": demand_coverage_va,
        },
        "load_breakdown": breakdown,
        "demand_breakdown": {
            name: {"demand_W": to_number(w), "demand_VA": to_number(s),
                   "coverage_W": cw, "coverage_VA": cs, "loads": rows}
            for name, (w, s, cw, cs, rows) in scenarios.items()
        },
        "pending_requirements": scoped.pending,
        "assumptions": [
            "Tổng công suất biểu kiến là tổng số học của các giá trị VA đã biết.",
            "Giữ lại giá trị không có nguồn nhưng không cộng vào tổng. "
            "Không dùng PF thiếu nguồn để quy đổi.",
            "Phụ tải tính toán chỉ bao gồm tải đã bố trí trong các kịch bản được cung cấp."
            if snapshot.scenarios else "Không có kịch bản hoạt động. Xem tất cả tải đã bố trí là đang hoạt động.",
        ],
        "warnings": warnings,
    }


# ──────────────────────────────────────────────────────────────────────
# Lệnh chạy trên terminal
# ──────────────────────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", required=True)
    parser.add_argument("--playbook", required=True)
    parser.add_argument("--input", required=True, help="Tệp JSON snapshot của phương án")
    parser.add_argument("--scope-type", required=True, choices=("room", "floor", "zone"))
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--connected-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        model = read_spaces(args.ifc)
        snapshot = read_snapshot(args.input, model)
        playbook = read_load_catalog(args.playbook)
        result = calc_preliminary_load(
            snapshot.plan_id, snapshot.plan_revision, args.scope_type, args.scope_id,
            snapshot=snapshot, playbook=playbook, ruleset_id=None if args.connected_only else RULESET_ID,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, InvalidOperation) as error:
        parser.exit(2, f"Lỗi: {error}\n")


if __name__ == "__main__":
    main()
