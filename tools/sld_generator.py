"""Render a saved panel schedule as an interactive, revision-pinned SLD artifact.

The caller loads the exact persisted plan/version and passes its schedule here.
This module does not size equipment, validate electrical safety, persist data,
or approve a schedule. Stable SVG element IDs are derived from stable schedule
entity IDs; display circuit numbers are labels only.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from typing import Any

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
SVG_STYLE = """
.sld-background{fill:#f8fbfc}
.sld-title{fill:#31515c;font:700 15px Arial,sans-serif;letter-spacing:1px}
.sld-subtitle,.sld-panel-meta,.sld-panel-load,.sld-circuit-meta,.sld-empty-label{fill:#80959e;font:10px Arial,sans-serif}
.sld-panel-card{fill:#fff;stroke:#d4e2e5;stroke-width:1.5}
.sld-panel-name{fill:#31515c;font:650 14px Arial,sans-serif}
.sld-wire,.sld-feeder-wire{fill:none;stroke:#6e8790;stroke-width:1.8}
.sld-busbar{stroke:#007f78;stroke-width:4}
.sld-protected-busbar{stroke-width:2.5}
.sld-breaker-box,.sld-protection-box{fill:#f7fbfa;stroke:#4d7e7d;stroke-width:1.4}
.sld-main-breaker{fill:#eaf6f3;stroke-width:1.8}
.sld-breaker-label,.sld-protection-label{fill:#365a63;font:9px Arial,sans-serif}
.sld-circuit-label{fill:#294a55;font:600 10px Arial,sans-serif}
.sld-feeder-label{fill:#5f7881;font:9px Arial,sans-serif}
.sld-device-dot{fill:#d09b35;stroke:#fff;stroke-width:1}
""".strip()


def _tag(name: str) -> str:
    return f"{{{SVG_NS}}}{name}"


def _required_id(row: dict, field: str, label: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{field} must be a non-empty stable ID")
    return value.strip()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _display(value: Any, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "—"
    if number.is_integer():
        return f"{int(number):,}{suffix}"
    return f"{number:,.2f}{suffix}"


def _circuit_device_ids(circuit: dict) -> list[str]:
    ids = [value for value in circuit.get("device_ids", []) if isinstance(value, str) and value]
    for device in circuit.get("devices", []):
        if isinstance(device, dict):
            value = device.get("device_id") or device.get("id")
            if isinstance(value, str) and value and value not in ids:
                ids.append(value)
    return ids


def _circuit_row_height(circuit: dict) -> int:
    device_count = len(_circuit_device_ids(circuit))
    device_rows = (device_count + 8) // 9
    return 54 + max(0, device_rows - 1) * 12


def _stable_svg_id(entity_type: str, entity_id: str, context: str = "") -> str:
    digest = hashlib.sha256(f"{entity_type}\0{entity_id}\0{context}".encode("utf-8")).hexdigest()[:20]
    return f"sld-{entity_type}-{digest}"


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sld_generate(plan_id: str, version: int, schedule: dict[str, Any]) -> dict[str, Any]:
    """Render one exact saved schedule revision.

    Args:
        plan_id: Stable ID of the saved panel schedule.
        version: Positive saved schedule revision number.
        schedule: JSON-compatible object with ``panels`` and optional
            ``feeders``, ``protection_groups`` and ``devices``. Each panel has
            a stable ``panel_id`` and nested circuits with stable ``circuit_id``
            values. A circuit may include a breaker object and ``device_ids``.
            Protection groups contain a stable ``protection_id`` and
            ``circuit_ids``. Feeder rows identify upstream/downstream panels.

    Returns an artifact ID, schedule hash, inline-safe SVG, an SVG element
    index, display data keyed by entity type/ID, render status and warnings.
    Input records are treated as untrusted text and serialized by ElementTree.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    if type(version) is not int or version < 1:
        raise ValueError("version must be a positive integer")
    if not isinstance(schedule, dict):
        raise ValueError("schedule must be an object")
    try:
        # Work on an isolated JSON snapshot so rendering cannot mutate the
        # caller's persisted schedule while filling display-only fields.
        schedule = json.loads(json.dumps(schedule, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("schedule must contain only finite JSON values") from error
    schedule_hash = _canonical_hash(schedule)

    panels = schedule.get("panels")
    top_level_circuits = schedule.get("final_circuits", schedule.get("circuits", []))
    feeders = schedule.get("feeders", [])
    protection_groups = schedule.get("protection_groups", [])
    devices = schedule.get("devices", [])
    if not isinstance(panels, list) or not panels:
        raise ValueError("schedule.panels must contain at least one panel")
    if not isinstance(top_level_circuits, list):
        raise ValueError("final_circuits must be a list")
    if not isinstance(feeders, list) or not isinstance(protection_groups, list):
        raise ValueError("feeders and protection_groups must be lists")
    if not isinstance(devices, list):
        raise ValueError("devices must be a list")

    panel_by_id: dict[str, dict] = {}
    circuits_by_panel: dict[str, list[dict]] = {}
    top_level_by_panel: dict[str, list[dict]] = defaultdict(list)
    for circuit in top_level_circuits:
        if not isinstance(circuit, dict):
            raise ValueError("Each final_circuit must be an object")
        owner_id = _required_id(circuit, "panel_id", "final_circuit")
        top_level_by_panel[owner_id].append(circuit)
    protection_by_id: dict[str, dict] = {}
    device_by_id: dict[str, dict] = {}
    warnings: list[str] = []
    for panel in panels:
        if not isinstance(panel, dict):
            raise ValueError("Each panel must be an object")
        panel_id = _required_id(panel, "panel_id", "panel")
        if panel_id in panel_by_id:
            raise ValueError(f"Duplicate panel_id: {panel_id}")
        nested_circuits = panel.get("circuits", [])
        if not isinstance(nested_circuits, list):
            raise ValueError(f"panel {panel_id} circuits must be a list")
        circuits = [*nested_circuits, *top_level_by_panel.get(panel_id, [])]
        if panel.get("circuits") and top_level_by_panel.get(panel_id):
            raise ValueError(f"Provide nested circuits or final_circuits, not both, for panel {panel_id}")
        circuit_ids = set()
        for circuit in circuits:
            if not isinstance(circuit, dict):
                raise ValueError(f"Every circuit in panel {panel_id} must be an object")
            circuit_id = _required_id(circuit, "circuit_id", f"panel {panel_id} circuit")
            if circuit_id in circuit_ids:
                raise ValueError(f"Duplicate circuit_id in panel {panel_id}: {circuit_id}")
            circuit_ids.add(circuit_id)
            for field in ("devices", "device_ids"):
                if field in circuit and not isinstance(circuit[field], list):
                    raise ValueError(f"{field} for circuit {circuit_id} must be a list")
            breaker = circuit.get("breaker")
            if breaker is None:
                breaker = circuit.get("selected_breaker")
                if breaker is not None:
                    circuit["breaker"] = breaker
            if breaker is not None:
                if not isinstance(breaker, dict):
                    raise ValueError(f"breaker for circuit {circuit_id} must be an object")
                breaker_id = breaker.get("breaker_id") or breaker.get("id") or f"{circuit_id}:breaker"
                breaker = {**breaker, "breaker_id": str(breaker_id)}
                circuit["breaker"] = breaker
        panel_by_id[panel_id] = panel
        circuits_by_panel[panel_id] = sorted(
            circuits,
            key=lambda row: (
                row.get("circuit_no") if type(row.get("circuit_no")) is int else 2**31,
                row["circuit_id"],
            ),
        )

    unknown_panel_circuits = set(top_level_by_panel) - set(panel_by_id)
    if unknown_panel_circuits:
        raise ValueError(f"final_circuits reference unknown panels: {sorted(unknown_panel_circuits)}")

        main_breaker = panel.get("main_breaker")
        if main_breaker is not None:
            if not isinstance(main_breaker, dict):
                raise ValueError(f"main_breaker for panel {panel_id} must be an object")
            main_id = main_breaker.get("breaker_id") or main_breaker.get("id") or f"{panel_id}:main"
            main_breaker = {**main_breaker, "breaker_id": str(main_id)}
            panel["main_breaker"] = main_breaker

    all_circuit_ids = [
        circuit["circuit_id"]
        for circuits in circuits_by_panel.values()
        for circuit in circuits
    ]
    if len(all_circuit_ids) != len(set(all_circuit_ids)):
        raise ValueError("circuit_id values must be unique across the schedule")
    circuit_owner = {
        circuit["circuit_id"]: panel_id
        for panel_id, circuits in circuits_by_panel.items()
        for circuit in circuits
    }
    for group in protection_groups:
        if not isinstance(group, dict):
            raise ValueError("Each protection group must be an object")
        protection_id = _required_id(group, "protection_id", "protection group")
        if protection_id in protection_by_id:
            raise ValueError(f"Duplicate protection_id: {protection_id}")
        group_circuits = group.get("circuit_ids", [])
        if not isinstance(group_circuits, list):
            raise ValueError(f"circuit_ids for protection group {protection_id} must be a list")
        if any(not isinstance(circuit_id, str) or not circuit_id for circuit_id in group_circuits):
            raise ValueError(f"Invalid circuit_id in protection group {protection_id}")
        if len(group_circuits) != len(set(group_circuits)):
            raise ValueError(f"Duplicate circuit_id in protection group {protection_id}")
        unknown = [circuit_id for circuit_id in group_circuits if circuit_id not in circuit_owner]
        if unknown:
            raise ValueError(f"Unknown circuits in protection group {protection_id}: {unknown}")
        group_panel_ids = {circuit_owner[circuit_id] for circuit_id in group_circuits}
        if len(group_panel_ids) > 1:
            raise ValueError(f"Protection group {protection_id} spans multiple panels")
        protection_by_id[protection_id] = group
        group["circuit_ids"] = group_circuits

    for device in devices:
        if not isinstance(device, dict):
            raise ValueError("Each device must be an object")
        device_id = device.get("device_id") or device.get("id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Each device must have a stable device_id")
        if device_id in device_by_id:
            raise ValueError(f"Duplicate device_id: {device_id}")
        device_by_id[device_id] = device

    for panel_id, circuits in circuits_by_panel.items():
        for circuit in circuits:
            protection_id = circuit.get("protection_id") or circuit.get("protection_group_id")
            if protection_id is not None:
                if protection_id not in protection_by_id:
                    raise ValueError(f"Unknown protection group for circuit {circuit['circuit_id']}")
                group = protection_by_id[protection_id]
                if circuit["circuit_id"] not in group["circuit_ids"]:
                    raise ValueError(f"Circuit {circuit['circuit_id']} is not listed in protection group")
            for device in circuit.get("devices", []):
                if not isinstance(device, dict):
                    continue
                device_id = device.get("device_id") or device.get("id")
                if isinstance(device_id, str) and device_id:
                    device_by_id.setdefault(device_id, device)
            for device_id in circuit.get("device_ids", []):
                if not isinstance(device_id, str) or not device_id:
                    raise ValueError(f"Invalid device_id in circuit {circuit['circuit_id']}")
                device_by_id.setdefault(device_id, {"device_id": device_id, "name": device_id})

    feeder_ids = set()
    parent_by_panel: dict[str, str] = {}
    feeders_by_parent: dict[str, list[dict]] = defaultdict(list)
    for feeder in feeders:
        if not isinstance(feeder, dict):
            raise ValueError("Each feeder must be an object")
        feeder_id = _required_id(feeder, "feeder_id", "feeder")
        if feeder_id in feeder_ids:
            raise ValueError(f"Duplicate feeder_id: {feeder_id}")
        feeder_ids.add(feeder_id)
        upstream = _required_id(feeder, "upstream_panel_id", f"feeder {feeder_id}")
        downstream = _required_id(feeder, "downstream_panel_id", f"feeder {feeder_id}")
        if upstream not in panel_by_id or downstream not in panel_by_id or upstream == downstream:
            raise ValueError(f"Feeder {feeder_id} references an invalid panel")
        if downstream in parent_by_panel:
            raise ValueError(f"Panel {downstream} has more than one upstream feeder")
        parent_by_panel[downstream] = upstream
        feeders_by_parent[upstream].append(feeder)

    # Reject cycles and render a deterministic depth-first panel order.
    roots = sorted(set(panel_by_id) - set(parent_by_panel))
    if not roots:
        raise ValueError("Feeder topology must have at least one root panel")
    ordered_panel_ids: list[str] = []
    depth_by_panel: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(panel_id: str, depth: int) -> None:
        if panel_id in visiting:
            raise ValueError("Feeder topology contains a cycle")
        if panel_id in depth_by_panel:
            return
        visiting.add(panel_id)
        depth_by_panel[panel_id] = depth
        ordered_panel_ids.append(panel_id)
        for feeder in sorted(feeders_by_parent[panel_id], key=lambda row: row["downstream_panel_id"]):
            visit(feeder["downstream_panel_id"], depth + 1)
        visiting.remove(panel_id)

    for root in roots:
        visit(root, 0)
    if len(ordered_panel_ids) != len(panel_by_id):
        raise ValueError("Feeder topology contains an unreachable panel or cycle")

    # Stable panel columns make element IDs independent of render positions.
    panel_x = {panel_id: 56 + index * 520 for index, panel_id in enumerate(ordered_panel_ids)}
    panel_y = {panel_id: 145 for panel_id in ordered_panel_ids}
    panel_width = 480
    panel_heights: dict[str, int] = {}
    circuit_group: dict[str, dict] = {}
    for panel_id, circuits in circuits_by_panel.items():
        for group in protection_groups:
            ids = [cid for cid in group["circuit_ids"] if circuit_owner[cid] == panel_id]
            if any(cid in circuit_group for cid in ids):
                raise ValueError("A circuit cannot belong to more than one protection group")
            if ids:
                circuit_group.update({cid: group for cid in ids})
        grouped: list[tuple[dict | None, list[dict]]] = []
        for circuit in circuits:
            group = circuit_group.get(circuit["circuit_id"])
            entry = next((row for row in grouped if row[0] is group), None)
            if entry is None:
                entry = (group, [])
                grouped.append(entry)
            entry[1].append(circuit)
        height = 162
        for group, rows in grouped:
            height += (48 if group else 8) + sum(_circuit_row_height(row) for row in rows)
        panel_heights[panel_id] = max(height, 200)

    svg_width = max(640, 56 + len(ordered_panel_ids) * 520)
    svg_height = max(panel_heights.values(), default=200) + 205
    root = ET.Element(
        _tag("svg"),
        {
            "viewBox": f"0 0 {svg_width} {svg_height}",
            "width": str(svg_width),
            "height": str(svg_height),
            "role": "img",
            "aria-label": "Sơ đồ đơn tuyến tương tác",
            "class": "sld-svg",
        },
    )
    ET.SubElement(root, _tag("title")).text = f"Sơ đồ đơn tuyến · phiên bản {version}"
    ET.SubElement(root, _tag("desc")).text = (
        "Sơ đồ thể hiện các tủ, thiết bị bảo vệ, mạch điện và feeder. "
        "Chọn phần tử để xem dữ liệu đã lưu của phương án."
    )
    ET.SubElement(root, _tag("style")).text = SVG_STYLE
    defs = ET.SubElement(root, _tag("defs"))
    marker = ET.SubElement(
        defs,
        _tag("marker"),
        {"id": "sld-arrow", "markerWidth": "8", "markerHeight": "8", "refX": "6", "refY": "3", "orient": "auto"},
    )
    ET.SubElement(marker, _tag("path"), {"d": "M0,0 L0,6 L7,3 z", "fill": "#56727d"})
    ET.SubElement(root, _tag("rect"), {"class": "sld-background", "x": "0", "y": "0", "width": str(svg_width), "height": str(svg_height), "rx": "12"})
    ET.SubElement(root, _tag("text"), {"class": "sld-title", "x": "24", "y": "32"}).text = "SƠ ĐỒ ĐƠN TUYẾN"
    ET.SubElement(root, _tag("text"), {"class": "sld-subtitle", "x": "24", "y": "54"}).text = f"Phương án {plan_id} · phiên bản {version} · {len(panel_by_id)} tủ"

    element_index: dict[str, dict[str, Any]] = {}
    sld_data: dict[str, dict[str, dict[str, Any]]] = {
        "panel": {}, "circuit": {}, "feeder": {}, "breaker": {},
        "protection": {}, "device": {}, "busbar": {},
    }

    def entity_group(parent, entity_type: str, entity_id: str, detail: dict, *, context: str = ""):
        svg_id = _stable_svg_id(entity_type, entity_id, context)
        if svg_id in element_index:
            raise ValueError(f"Two rendered elements map to the same entity: {entity_type} {entity_id}")
        group = ET.SubElement(
            parent,
            _tag("g"),
            {
                "id": svg_id,
                "class": f"sld-entity sld-{entity_type}",
                "data-entity-type": entity_type,
                "data-entity-id": entity_id,
                "role": "button",
                "tabindex": "0",
                "aria-label": f"{entity_type}: {detail.get('name') or entity_id}",
            },
        )
        ET.SubElement(group, _tag("title")).text = str(detail.get("name") or entity_id)
        element_index[svg_id] = {
            "entity_type": entity_type,
            "entity_id": entity_id,
            **({"context": context} if context else {}),
        }
        sld_data[entity_type].setdefault(entity_id, detail)
        return group

    # Feeders are drawn first so panel cards remain on top of connector lines.
    for feeder in feeders:
        upstream = feeder["upstream_panel_id"]
        downstream = feeder["downstream_panel_id"]
        x1 = panel_x[upstream] + panel_width / 2
        x2 = panel_x[downstream] + panel_width / 2
        y = 95
        group = entity_group(root, "feeder", feeder["feeder_id"], feeder)
        ET.SubElement(
            group,
            _tag("path"),
            {
                "class": "sld-wire sld-feeder-wire",
                "d": f"M {x1} 145 V {y} H {x2} V 137",
                "marker-end": "url(#sld-arrow)",
            },
        )
        label = feeder.get("name") or feeder.get("feeder_id")
        ET.SubElement(group, _tag("text"), {"class": "sld-feeder-label", "x": str((x1 + x2) / 2), "y": str(y - 7), "text-anchor": "middle"}).text = str(label)
        sld_data["feeder"][feeder["feeder_id"]] = feeder

    for panel_id in ordered_panel_ids:
        panel = panel_by_id[panel_id]
        x, y = panel_x[panel_id], panel_y[panel_id]
        circuits = circuits_by_panel[panel_id]
        panel_detail = {**panel, "circuits": circuits}
        panel_group = entity_group(root, "panel", panel_id, panel_detail)
        ET.SubElement(panel_group, _tag("rect"), {"class": "sld-panel-card", "x": str(x), "y": str(y), "width": str(panel_width), "height": str(panel_heights[panel_id]), "rx": "10"})
        ET.SubElement(panel_group, _tag("text"), {"class": "sld-panel-name", "x": str(x + 18), "y": str(y + 25)}).text = str(panel.get("name") or panel_id)
        meta = " · ".join(str(part) for part in (panel.get("role"), panel.get("storey_name") or panel.get("storey_id")) if part)
        ET.SubElement(panel_group, _tag("text"), {"class": "sld-panel-meta", "x": str(x + 18), "y": str(y + 44)}).text = meta or "Tủ phân phối"
        ET.SubElement(panel_group, _tag("text"), {"class": "sld-panel-load", "x": str(x + panel_width - 16), "y": str(y + 25), "text-anchor": "end"}).text = f"{_display(panel.get('total_load_W'), ' W')}  /  {_display(panel.get('total_load_VA'), ' VA')}"

        main = panel.get("main_breaker")
        main_id = None
        if main:
            main_id = main["breaker_id"]
            breaker_group = entity_group(panel_group, "breaker", main_id, main, context=f"main:{panel_id}")
            bx, by = x + 24, y + 64
            ET.SubElement(breaker_group, _tag("line"), {"class": "sld-wire", "x1": str(x + 40), "y1": str(y + 48), "x2": str(x + 40), "y2": str(by)})
            ET.SubElement(breaker_group, _tag("rect"), {"class": "sld-breaker-box sld-main-breaker", "x": str(bx), "y": str(by), "width": "150", "height": "28", "rx": "4"})
            ET.SubElement(breaker_group, _tag("line"), {"class": "sld-wire", "x1": str(x + 40), "y1": str(by + 28), "x2": str(x + 40), "y2": str(y + 94)})
            breaker_label = main.get("name") or main.get("catalog_id") or "Main breaker"
            current = _display(main.get("rated_current_A"), " A")
            ET.SubElement(breaker_group, _tag("text"), {"class": "sld-breaker-label", "x": str(bx + 8), "y": str(by + 18)}).text = f"MAIN · {breaker_label} · {current}"
            sld_data["breaker"][main_id] = main
        bus_id = f"{panel_id}:busbar"
        busbar_group = entity_group(panel_group, "busbar", bus_id, {"name": f"Busbar {panel.get('name') or panel_id}", "panel_id": panel_id}, context=f"main-bus:{panel_id}")
        bus_x = x + 40
        first_row_y = y + 137
        ET.SubElement(busbar_group, _tag("line"), {"class": "sld-busbar", "x1": str(bus_x), "y1": str(y + 94), "x2": str(bus_x), "y2": str(max(first_row_y, y + panel_heights[panel_id] - 25))})
        sld_data["busbar"][bus_id] = {"name": f"Busbar {panel.get('name') or panel_id}", "panel_id": panel_id}

        grouped: list[tuple[dict | None, list[dict]]] = []
        for circuit in circuits:
            protection = circuit_group.get(circuit["circuit_id"])
            entry = next((row for row in grouped if row[0] is protection), None)
            if entry is None:
                entry = (protection, [])
                grouped.append(entry)
            entry[1].append(circuit)
        current_y = first_row_y
        for protection, rows in grouped:
            if protection:
                protection_id = protection["protection_id"]
                protection_group = entity_group(panel_group, "protection", protection_id, protection, context=panel_id)
                pbox_y = current_y
                ET.SubElement(protection_group, _tag("line"), {"class": "sld-wire", "x1": str(bus_x), "y1": str(pbox_y + 14), "x2": str(x + 66), "y2": str(pbox_y + 14)})
                ET.SubElement(protection_group, _tag("rect"), {"class": "sld-protection-box", "x": str(x + 66), "y": str(pbox_y), "width": "182", "height": "28", "rx": "4"})
                label = protection.get("name") or protection.get("catalog_id") or "RCD"
                rating = _display(protection.get("rated_current_A"), " A")
                residual = _display(protection.get("residual_current_mA"), " mA")
                ET.SubElement(protection_group, _tag("text"), {"class": "sld-protection-label", "x": str(x + 74), "y": str(pbox_y + 18)}).text = f"{label} · {rating} · IΔn {residual}"
                sub_bus_x = x + 260
                subbus_id = f"{protection_id}:busbar"
                subbus = entity_group(panel_group, "busbar", subbus_id, {"name": f"Busbar sau {label}", "panel_id": panel_id, "protection_id": protection_id}, context=f"protected-bus:{panel_id}:{protection_id}")
                ET.SubElement(subbus, _tag("line"), {"class": "sld-busbar sld-protected-busbar", "x1": str(x + 248), "y1": str(pbox_y + 14), "x2": str(sub_bus_x), "y2": str(pbox_y + 14)})
                group_height = sum(_circuit_row_height(row) for row in rows)
                ET.SubElement(subbus, _tag("line"), {"class": "sld-busbar sld-protected-busbar", "x1": str(sub_bus_x), "y1": str(pbox_y + 14), "x2": str(sub_bus_x), "y2": str(pbox_y + 14 + group_height)})
                sld_data["protection"][protection_id] = protection
                sld_data["busbar"][subbus_id] = {"name": f"Busbar sau {label}", "panel_id": panel_id, "protection_id": protection_id}
                row_start = current_y + 40
            else:
                sub_bus_x = bus_x
                row_start = current_y
            row_offset = 0
            for circuit in rows:
                circuit_id = circuit["circuit_id"]
                circuit_y = row_start + row_offset
                circuit_group_node = entity_group(panel_group, "circuit", circuit_id, circuit, context=panel_id)
                ET.SubElement(circuit_group_node, _tag("line"), {"class": "sld-wire", "x1": str(sub_bus_x), "y1": str(circuit_y), "x2": str(x + 282), "y2": str(circuit_y)})
                breaker = circuit.get("breaker")
                if breaker:
                    breaker_id = breaker["breaker_id"]
                    breaker_node = entity_group(circuit_group_node, "breaker", breaker_id, breaker, context=circuit_id)
                    ET.SubElement(breaker_node, _tag("rect"), {"class": "sld-breaker-box", "x": str(x + 282), "y": str(circuit_y - 13), "width": "56", "height": "26", "rx": "4"})
                    breaker_name = breaker.get("name") or breaker.get("catalog_id") or "CB"
                    ET.SubElement(breaker_node, _tag("text"), {"class": "sld-breaker-label", "x": str(x + 310), "y": str(circuit_y + 4), "text-anchor": "middle"}).text = str(breaker.get("display_label") or _display(breaker.get("rated_current_A"), " A"))
                    breaker_node.set("aria-label", f"CB: {breaker_name}")
                    sld_data["breaker"][breaker_id] = breaker
                ET.SubElement(circuit_group_node, _tag("line"), {"class": "sld-wire", "x1": str(x + 338), "y1": str(circuit_y), "x2": str(x + 354), "y2": str(circuit_y)})
                no = circuit.get("circuit_no")
                circuit_name = circuit.get("name") or circuit_id
                label = f"M{no} · {circuit_name}" if no is not None else str(circuit_name)
                ET.SubElement(circuit_group_node, _tag("text"), {"class": "sld-circuit-label", "x": str(x + 360), "y": str(circuit_y - 3)}).text = str(label)
                detail = f"{_display(circuit.get('load_W'), ' W')} / {_display(circuit.get('load_VA'), ' VA')} · {circuit.get('phase') or 'Pha —'}"
                ET.SubElement(circuit_group_node, _tag("text"), {"class": "sld-circuit-meta", "x": str(x + 360), "y": str(circuit_y + 12)}).text = detail
                sld_data["circuit"][circuit_id] = circuit
                circuit["panel_id"] = panel_id
                circuit["protection_id"] = protection.get("protection_id") if protection else None
                device_ids = _circuit_device_ids(circuit)
                for device_index, device_id in enumerate(device_ids):
                    device = device_by_id.get(device_id, {"device_id": device_id, "name": device_id})
                    device_node = entity_group(circuit_group_node, "device", device_id, device, context=circuit_id)
                    device_name = device.get("name") or device.get("device_type") or device_id
                    ET.SubElement(device_node, _tag("circle"), {"class": "sld-device-dot", "cx": str(x + 370 + (device_index % 9) * 12), "cy": str(circuit_y + 27 + (device_index // 9) * 12), "r": "5"})
                    ET.SubElement(device_node, _tag("title")).text = str(device_name)
                    sld_data["device"][device_id] = device
                row_offset += _circuit_row_height(circuit)
            current_y = row_start + row_offset + 8

        if not circuits:
            ET.SubElement(panel_group, _tag("text"), {"class": "sld-empty-label", "x": str(x + 24), "y": str(y + 128)}).text = "Chưa có mạch trong phương án"
        sld_data["panel"][panel_id] = panel_detail

    svg = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    artifact_id = "sld-" + hashlib.sha256(
        f"{plan_id}\0{version}\0{schedule_hash}".encode("utf-8")
    ).hexdigest()[:24]
    if len(panels) > 1 and not feeders:
        warnings.append("Sơ đồ không có feeder; kiểm tra topology và nguồn cấp của từng tủ.")
    if not any(panel.get("main_breaker") for panel in panels):
        warnings.append("Không có main breaker trong dữ liệu schedule.")
    if any(not circuits_by_panel[panel_id] for panel_id in panel_by_id):
        warnings.append("Có tủ chưa có mạch.")
    return {
        "artifact_id": artifact_id,
        "plan_id": plan_id,
        "version": version,
        "schedule_hash": schedule_hash,
        "svg": svg,
        "element_index": element_index,
        "sld_data": sld_data,
        "render_status": "partial" if warnings else "complete",
        "warnings": warnings,
    }
