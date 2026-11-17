"""Explicit reference-path utility, excluded from the device-placement registry."""

import math

from ifc_engine.writer import coordinates


def route_calculate_path(device_position, panel_position, routing_elevation_m: float) -> dict:
    """Construct a reference orthogonal path in model-world metres.

    Inputs are x/y/z dictionaries or triples, plus a user-chosen routing plane.
    This utility does not solve obstacles, cable selection or conduit routing;
    it must not be interpreted as an approved circuit design or written to IFC.
    """
    start = coordinates(device_position, "device_position")
    end = coordinates(panel_position, "panel_position")
    if type(routing_elevation_m) not in (int, float) or not math.isfinite(routing_elevation_m):
        raise ValueError("routing_elevation_m must be finite")
    z = float(routing_elevation_m)
    candidates = [start, (start[0], start[1], z), (end[0], start[1], z), (end[0], end[1], z), end]
    points = []
    for point in candidates:
        if not points or point != points[-1]:
            points.append(point)
    return {
        "path_points": [list(p) for p in points],
        "length_m": sum(math.dist(a, b) for a, b in zip(points, points[1:])),
        "validated": False,
        "warnings": ["Reference path only; obstacles and routing rules are not checked."],
    }
