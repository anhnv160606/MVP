"""Export the architecture-preserving placement model.

The placement writer starts from architecture and retains its entities. A
second blind merge duplicates the project/site hierarchy and is unnecessary.
Independent MEP models require a separate federation workflow.
"""

from pathlib import Path

import ifcopenshell


def export_combined_model(architecture_path, placement_path, output_path) -> dict:
    """Export only if every architecture root GlobalId survives in the placement model."""
    architecture = ifcopenshell.open(str(architecture_path))
    placement = ifcopenshell.open(str(placement_path))
    if architecture.schema != placement.schema:
        raise ValueError("Model schemas do not match")
    original = {e.GlobalId for e in architecture.by_type("IfcRoot")}
    current = {e.GlobalId for e in placement.by_type("IfcRoot")}
    if original - current:
        raise ValueError("Placement model is not an architecture-preserving snapshot")
    target = Path(output_path)
    if target.resolve() in {Path(architecture_path).resolve(), Path(placement_path).resolve()}:
        raise ValueError("Export to a new path to preserve source snapshots")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(placement_path).read_bytes())
    return {
        "status": "exported",
        "merged_path": str(target.resolve()),
        "size_bytes": target.stat().st_size,
    }
