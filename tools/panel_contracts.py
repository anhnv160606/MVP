"""Strict Dev C contracts; backend context is never accepted from an agent."""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Identifier = Annotated[
    str, StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
PhaseName = Literal["L1", "L2", "L3"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Position(Contract):
    x: float
    y: float
    z: float


class Orientation(Contract):
    yaw: float = Field(description="Degrees around model-world Z.")


class Location(Contract):
    space_id: str
    host_id: str
    position_m: Position
    orientation: Orientation
    candidate_id: Identifier | None = None
    geometry_revision: str


class Panel(Contract):
    panel_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    role: Literal["main", "distribution"]
    storey_id: str | None = None
    cabinet_catalog_id: Identifier | None = None
    main_breaker_catalog_id: Identifier | None = None
    accessory_catalog_ids: list[Identifier] = Field(default_factory=list, max_length=100)
    location: Location | None = None


class Circuit(Contract):
    circuit_id: Identifier
    panel_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    device_ids: list[Identifier] = Field(max_length=2000)
    voltage_V: Positive | None = None
    breaker_catalog_id: Identifier | None = None


class Feeder(Contract):
    feeder_id: Identifier
    upstream_panel_id: Identifier
    downstream_panel_id: Identifier
    voltage_V: Positive | None = None
    breaker_catalog_id: Identifier | None = None


class ProtectionGroup(Contract):
    protection_group_id: Identifier
    panel_id: Identifier
    circuit_ids: list[Identifier] = Field(min_length=1, max_length=2000)
    catalog_id: Identifier


class PhaseAssignment(Contract):
    circuit_id: Identifier | None = None
    feeder_id: Identifier | None = None
    phases: list[PhaseName] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def one_target(self):
        if (self.circuit_id is None) == (self.feeder_id is None):
            raise ValueError("Exactly one circuit_id or feeder_id is required")
        if len(set(self.phases)) != len(self.phases):
            raise ValueError("Repeated phase assignment")
        return self


class PanelSchedule(Contract):
    schema_version: Literal[2] = 2
    panels: list[Panel] = Field(min_length=1, max_length=100)
    final_circuits: list[Circuit] = Field(max_length=2000)
    feeders: list[Feeder] = Field(default_factory=list, max_length=100)
    protection_groups: list[ProtectionGroup] = Field(default_factory=list, max_length=2000)
    phase_assignments: list[PhaseAssignment] = Field(default_factory=list, max_length=2100)

    @model_validator(mode="after")
    def unique_ids(self):
        all_ids = set()
        for collection, key in (
            (self.panels, "panel_id"),
            (self.final_circuits, "circuit_id"),
            (self.feeders, "feeder_id"),
            (self.protection_groups, "protection_group_id"),
        ):
            ids = [getattr(item, key) for item in collection]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate {key}")
            if all_ids.intersection(ids):
                raise ValueError("Panel/circuit/feeder/protection IDs must be globally unique")
            all_ids.update(ids)
        targets = [(x.circuit_id or x.feeder_id) for x in self.phase_assignments]
        if len(targets) != len(set(targets)):
            raise ValueError("Duplicate phase assignment target")
        return self


class SourceRefs(Contract):
    placement_plan_id: str
    placement_version: int = Field(ge=1)
    source_file_id: str
    model_revision: str = Field(min_length=1)
    questionnaire_revision: int = Field(ge=1)
    playbook_revision: str = Field(min_length=1)
    ruleset_revision: str = Field(min_length=1)


class Device(Contract):
    device_id: Identifier
    ifc_global_id: str | None = None
    requirement_id: str | None = None
    space_id: str | None = None
    storey_id: str | None = None
    position_m: Position | None = None
    load_class_id: str | None = None
    terminal_class_id: str | None = None
    requires_supply: bool
    supply_point_id: Identifier | None = None
    circuit_group: str | None = None
    input_W: Nonnegative | None = None
    input_VA: Nonnegative | None = None
    power_factor: Positive | None = Field(default=None, le=1)
    power_sources: list[JsonValue] = Field(default_factory=list)
    connection_requirements: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def phases(self):
        return self.connection_requirements.get("phases")

    @property
    def voltage_V(self):
        return self.connection_requirements.get("voltage_V")

    @property
    def dedicated_circuit(self):
        return self.connection_requirements.get("dedicated_circuit", False)

    @model_validator(mode="after")
    def connection_types(self):
        values = self.connection_requirements
        if values.get("phases") is not None and (
            type(values["phases"]) is not int or values["phases"] not in (1, 3)
        ):
            raise ValueError("connection_requirements.phases must be 1, 3 or null")
        if values.get("voltage_V") is not None and (
            type(values["voltage_V"]) not in (int, float) or values["voltage_V"] <= 0
        ):
            raise ValueError("connection_requirements.voltage_V must be positive")
        if "dedicated_circuit" in values and type(values["dedicated_circuit"]) is not bool:
            raise ValueError("dedicated_circuit must be boolean")
        return self


class ElectricalSpecs(Contract):
    rated_current_A: Positive | None = None
    rated_voltage_V: Positive | None = None
    poles: int | None = Field(default=None, ge=1, le=4)
    breaking_capacity_kA: Positive | None = None
    residual_current_mA: Positive | None = None
    available_modules: Nonnegative | None = None


class Dimensions(Contract):
    width: Positive
    height: Positive
    depth: Positive


class CatalogItem(Contract):
    catalog_id: Identifier
    category: Literal["cabinet", "mcb", "mccb", "rccb", "rcbo", "accessory"]
    manufacturer: str | None = None
    model: str | None = None
    electrical_specs: ElectricalSpecs = Field(default_factory=ElectricalSpecs)
    dimensions_m: Dimensions | None = None
    mounting_type: str | None = None
    module_width_mm: Positive | None = None
    module_count: Nonnegative | None = None
    source: JsonValue = None
    missing_data: list[JsonValue] = Field(default_factory=list)
    verification_status: Literal["verified", "reference", "unverified"] = "unverified"

    @property
    def rated_current_A(self):
        return self.electrical_specs.rated_current_A

    @property
    def rated_voltage_V(self):
        return self.electrical_specs.rated_voltage_V

    @property
    def poles(self):
        return self.electrical_specs.poles

    @property
    def available_modules(self):
        return self.electrical_specs.available_modules


class SupplyContext(Contract):
    phases: Literal[1, 3] | None = None
    voltage_LN_V: Positive | None = None
    voltage_LL_V: Positive | None = None
    earthing_system: str | None = None
    entry_point: JsonValue = None


class ApprovalPolicy(Contract):
    stage: Literal["panel_design", "final"] = "panel_design"
    # Only these two checks may be deferred, never a failed check.
    defer_to_routing: list[Literal["cable_protection", "voltage_drop"]] = Field(
        default_factory=lambda: ["cable_protection", "voltage_drop"]
    )


class PanelDesignContext(Contract):
    schema_version: Literal[2] = 2
    status: Literal["ok", "partial", "blocked", "no_match"] = "partial"
    project_id: str
    design_context_id: Identifier
    source_refs: SourceRefs
    devices: list[Device]
    catalog: list[CatalogItem]
    supply_context: SupplyContext
    unresolved_requirements: list[JsonValue] = Field(default_factory=list)
    unmapped_devices: list[JsonValue] = Field(default_factory=list)
    warnings: list[JsonValue] = Field(default_factory=list)
    missing_data: list[JsonValue] = Field(default_factory=list)
    assumptions: list[JsonValue] = Field(default_factory=list)
    next_cursor: str | None = None
    rules: list[dict[str, JsonValue]] = Field(default_factory=list)
    approval_policy: ApprovalPolicy = Field(default_factory=ApprovalPolicy)
    space_ids: list[str] = Field(default_factory=list)
    storey_ids: list[str] = Field(default_factory=list)
    inventory_complete: bool = False
    required_spare_percent: Nonnegative | None = Field(default=None, le=100)

    @property
    def supply_phases(self):
        if self.supply_context.phases is None:
            return []
        return {1: ["L1"], 3: ["L1", "L2", "L3"]}.get(self.supply_context.phases, [])

    @model_validator(mode="after")
    def unique_context(self):
        for values in (
            [x.device_id for x in self.devices],
            [x.catalog_id for x in self.catalog],
            self.supply_phases,
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate identity in backend design context")
        return self


def json_copy(value):
    """Reject NaN/Infinity and detach mutable input before hashing or persistence."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def digest(value) -> str:
    text = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schedule_data(value) -> dict:
    parsed = value if isinstance(value, PanelSchedule) else PanelSchedule.model_validate(value)
    data = parsed.model_dump()
    # Entity order is presentation-only. Keep stable IDs and canonicalize before hashing.
    for key, field in (
        ("panels", "panel_id"),
        ("final_circuits", "circuit_id"),
        ("feeders", "feeder_id"),
        ("protection_groups", "protection_group_id"),
    ):
        data[key].sort(key=lambda item: item[field])
        for item in data[key]:
            for name in ("device_ids", "phases", "circuit_ids", "accessory_catalog_ids"):
                if name in item:
                    item[name].sort()
    for item in data["phase_assignments"]:
        item["phases"].sort()
    data["phase_assignments"].sort(key=lambda item: item["circuit_id"] or item["feeder_id"])
    return json_copy(data)


def upgrade_schedule_v1(value) -> dict:
    """Explicit local conversion, never mutate persisted history or its hashes."""
    data = json_copy(value)
    if data.get("schema_version", 1) != 1:
        raise ValueError("Expected a version 1 schedule")
    if "phase_assignments" in data:
        raise ValueError("Mixed v1/v2 phases; resolve explicitly")
    assignments = []
    for group, key in (("final_circuits", "circuit_id"), ("feeders", "feeder_id")):
        for entity in data.get(group, []):
            if "phases" in entity:
                assignments.append({key: entity[key], "phases": entity.pop("phases")})
    data.update(schema_version=2, phase_assignments=assignments)
    return schedule_data(data)


class CheckResult(Contract):
    status: Literal["pass", "fail", "unknown"]
    message: str = ""


class CalculationEntry(Contract):
    circuit_id: Identifier | None = None
    feeder_id: Identifier | None = None
    panel_id: Identifier | None = None
    connected_load_W: Nonnegative | None = None
    connected_load_VA: Nonnegative | None = None
    estimated_demand_W: Nonnegative | None = None
    estimated_demand_VA: Nonnegative | None = None
    design_current_A_by_phase: dict[PhaseName, Nonnegative | None] = Field(default_factory=dict)
    formula_basis: JsonValue = None


class CalculationResult(Contract):
    project_id: str
    design_context_id: str
    source_refs: SourceRefs
    calculation_id: Identifier
    schedule_hash: str
    status: Literal["ok", "partial", "blocked", "no_match"] = "partial"
    circuit_results: list[CalculationEntry] = Field(default_factory=list)
    feeder_results: list[CalculationEntry] = Field(default_factory=list)
    panel_results: list[CalculationEntry] = Field(default_factory=list)
    source_results: dict[str, JsonValue] = Field(default_factory=dict)
    scenario_results: list[dict[str, JsonValue]] = Field(default_factory=list)
    coverage: dict[str, JsonValue] = Field(default_factory=dict)
    missing_data: list[JsonValue] = Field(default_factory=list)
    warnings: list[JsonValue] = Field(default_factory=list)
    assumptions: list[JsonValue] = Field(default_factory=list)
    engineering_checks: dict[str, CheckResult] = Field(default_factory=dict)

    @model_validator(mode="after")
    def result_ids(self):
        for entries, key in (
            (self.circuit_results, "circuit_id"),
            (self.feeder_results, "feeder_id"),
            (self.panel_results, "panel_id"),
        ):
            seen = set()
            for entry in entries:
                identity = getattr(entry, key)
                if (
                    identity is None
                    or identity in seen
                    or sum(
                        getattr(entry, k) is not None
                        for k in ("circuit_id", "feeder_id", "panel_id")
                    )
                    != 1
                ):
                    raise ValueError(f"Missing/duplicate/mismatched {key} in calculation results")
                seen.add(identity)
        return self


class SLDIndexEntry(Contract):
    entity_type: Literal["panel", "circuit", "feeder", "protection_group"]
    entity_id: Identifier


class SLDArtifact(Contract):
    artifact_id: Identifier
    plan_id: str
    version: int = Field(ge=1)
    schedule_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_status: Literal["ready"]
    svg: str = Field(min_length=1, max_length=5_000_000)
    element_index: dict[str, SLDIndexEntry] = Field(min_length=1)
    warnings: list[JsonValue] = Field(default_factory=list)


# Shared mutation contracts: import these in API, tools and Dev A/B adapters.
class ScheduleOperation(Contract):
    op: Literal["add", "update", "remove"]
    collection: Literal[
        "panels", "final_circuits", "feeders", "protection_groups", "phase_assignments"
    ]
    entity_id: Identifier
    value: dict | None = None


class SaveRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    plan_id: str | None = None
    expected_version: int = Field(ge=0)
    proposed_schedule: PanelSchedule
    design_reasons: list[str] = Field(default_factory=list, max_length=100)


class ModifyRequest(Contract):
    request_id: str = Field(min_length=1, max_length=128)
    expected_version: int = Field(ge=1)
    operations: list[ScheduleOperation] = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=2000)
