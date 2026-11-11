"""Calculate preliminary electrical loads for a room, floor, or zone."""

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json

from database.project_store import ProjectSnapshot, read_snapshot, require_id
from ifc_engine.reader import read_spaces
from tools.playbook import Playbook, read_load_catalog

RULESET_ID = "VN_TOWNHOUSE_PRELIM_V1"
CONSUMING_KINDS = {"equipment", "allowance"}
LOAD_KINDS = CONSUMING_KINDS | {"receptacle", "control", "generation"}


def quantity(value, field, *, positive=False):
    if value is None:
        return None
    if type(value) not in (int, float):
        raise ValueError(f"{field} must be a number")
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"Invalid {field}")
    return number


def power(row):
    watts = quantity(row.get("input_W"), "input_W")
    va = quantity(row.get("input_VA"), "input_VA")
    pf = quantity(row.get("power_factor"), "power_factor", positive=True)
    if pf is not None and pf > 1:
        raise ValueError("power_factor must be in (0, 1]")
    if watts is not None or va is not None:
        require_id(row.get("power_source"), "power_source")
    if pf is not None:
        require_id(row.get("pf_source"), "pf_source")
    if watts is not None and va is not None and watts > va:
        raise ValueError("input_W exceeds input_VA")
    if pf is not None and watts is not None and va is not None:
        if abs(watts - va * pf) > max(Decimal("0.01"), watts * Decimal("0.02")):
            raise ValueError("input_W and input_VA disagree with power_factor")
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
            raise ValueError(f"Conflicting records for {field} {key}")
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
            raise ValueError(f"Unknown IFC room {row.get('room_id')}")
        if row.get("load_class_id") not in playbook.classes:
            raise ValueError(f"Unknown load class {row.get('load_class_id')}")
    for pairs in snapshot.zones.values():
        if any(load_class not in playbook.classes for _, load_class in pairs):
            raise ValueError("Unknown load class in zone mapping")

    def in_scope(row):
        pair = (row["room_id"], row["load_class_id"])
        return pair[0] in rooms and (zone_pairs is None or pair in zone_pairs)

    requested = {}
    for key, requirement in requirements.items():
        count = requirement.get("quantity", 1)
        if type(count) is not int or count < 1:
            raise ValueError(f"Invalid quantity for requirement {key}")
        requested[key] = count

    fulfilled = set()
    supplied_outlets = set()
    allowances = set()
    for row in placements.values():
        kind = row.get("kind")
        if kind not in LOAD_KINDS:
            raise ValueError(f"Invalid kind for {row['load_id']}")
        catalog = playbook.classes[row["load_class_id"]]
        if (catalog.id == "TẢI-021") != (kind == "generation"):
            raise ValueError("TẢI-021 must be recorded as generation")
        if kind == "allowance" and catalog.rule_scope != "receptacle_circuit":
            raise ValueError("An outlet allowance needs a receptacle load class")
        outlet = row.get("outlet_id")
        if outlet is not None:
            outlet = require_id(outlet, "outlet_id")
            if kind == "equipment":
                supplied_outlets.add(outlet)
            elif kind == "allowance":
                if outlet in allowances:
                    raise ValueError(f"Multiple allowances for outlet {outlet}")
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
            raise ValueError(f"Invalid requirement link for {row['load_id']}: {key}")
        unit = row.get("requirement_unit", 1 if requested[key] == 1 else None)
        if type(unit) is not int or not 1 <= unit <= requested[key] or (key, unit) in fulfilled:
            raise ValueError(f"Invalid or duplicate allocation for requirement {key}: {unit}")
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
        fields = ("input_W", "input_VA", "power_factor", "power_source", "pf_source")
        if row["kind"] == "equipment" and any(requirement.get(field) is not None for field in fields[:2]):
            if all(row.get(field) is None for field in fields[:2]):
                row = {**row, **{field: requirement[field] for field in fields if field in requirement}}
                row["power_basis"] = "questionnaire"
            elif any(
                row.get(field) is not None and requirement.get(field) is not None
                and row[field] != requirement[field] for field in fields[:3]
            ):
                warnings.append({"code": "POWER_SOURCE_CONFLICT", "load_id": row["load_id"]})
        scoped.append(row)

    scenarios = {}
    for name, active_ids in snapshot.scenarios.items():
        require_id(name, "scenario_id")
        if not isinstance(active_ids, list) or any(load_id not in placements for load_id in active_ids):
            raise ValueError(f"Unknown or invalid load ID in scenario {name}")
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
    """Return a read-only preliminary calculation for the supplied snapshot."""
    if (plan_id, plan_revision) != (snapshot.plan_id, snapshot.plan_revision):
        raise ValueError("Requested plan revision does not match the snapshot")
    if snapshot.playbook_revision is not None and snapshot.playbook_revision != playbook.revision:
        raise ValueError("Snapshot playbook_revision does not match the Playbook file")
    if ruleset_id not in {RULESET_ID, None}:
        raise ValueError(f"Unknown ruleset {ruleset_id}")
    scoped = resolve_loads(snapshot, playbook, scope_type, scope_id)
    warnings = scoped.warnings
    breakdown = []
    consumption = {}
    for row in scoped.placements:
        load_id = row["load_id"]
        kind = row["kind"]
        watts, va = power(row) if kind in CONSUMING_KINDS else (None, None)
        breakdown.append({
            **row, "floor_id": snapshot.model.rooms[row["room_id"]],
            "connected_W": to_number(watts), "connected_VA": to_number(va),
        })
        if kind not in CONSUMING_KINDS:
            continue
        if watts is None and va is None:
            warnings.append({"code": "MISSING_INPUT_POWER", "load_id": load_id})
        elif watts is None or va is None:
            warnings.append({"code": "MISSING_POWER_FACTOR", "load_id": load_id})
        load_class = playbook.classes[row["load_class_id"]]
        factor = load_class.factor if ruleset_id else None
        if kind == "equipment" and load_class.rule_scope == "receptacle_circuit":
            # The socket class can also identify an appliance's point of use.
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
            "Apparent power uses the arithmetic sum of known VA.",
            "Demand covers placed loads in the supplied scenarios only."
            if snapshot.scenarios else "No operating scenarios supplied. All placed loads are active.",
        ],
        "warnings": warnings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", required=True)
    parser.add_argument("--playbook", required=True)
    parser.add_argument("--input", required=True, help="Project snapshot JSON")
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
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    main()
