import ifcopenshell
import ifcopenshell.util.placement
import re
import math
from pathlib import Path
from typing import Dict, List, Any, Optional

class IFCReader:
    def __init__(self, ifc_path: str):
        self.ifc_path = Path(ifc_path)
        if not self.ifc_path.exists():
            raise FileNotFoundError(f"IFC file not found: {ifc_path}")
        self.model = ifcopenshell.open(str(self.ifc_path))

    def get_storeys(self) -> List[Dict[str, Any]]:
        storeys = []
        for s in self.model.by_type("IfcBuildingStorey"):
            storeys.append({
                "id": s.GlobalId,
                "name": s.Name or "Unnamed Storey",
                "elevation": getattr(s, "Elevation", 0.0) or 0.0,
                "description": s.Description or ""
            })
        return storeys

    def get_spaces(self, storey_id: Optional[str] = None) -> List[Dict[str, Any]]:
        spaces_data = []
        for sp in self.model.by_type("IfcSpace"):
            # Check storey container if filtered
            storey_match = True
            if storey_id:
                parent_storey = None
                for rel in getattr(sp, "Decomposes", []):
                    if rel.RelatingObject.is_a("IfcBuildingStorey"):
                        parent_storey = rel.RelatingObject.GlobalId
                if parent_storey and parent_storey != storey_id:
                    storey_match = False
            
            if not storey_match:
                continue

            desc = sp.Description or ""
            bounds = None
            has_walls = True
            space_code = sp.Name or sp.GlobalId
            
            # Ưu tiên lấy từ Description nếu có
            if "minX:" in desc:
                try:
                    parts = dict(re.findall(r"(\w+):([^;]+)", desc))
                    bounds = {
                        "min_x": float(parts.get("minX", 0)),
                        "min_y": float(parts.get("minY", 0)),
                        "max_x": float(parts.get("maxX", 10)),
                        "max_y": float(parts.get("maxY", 10)),
                        "height": 3.5
                    }
                    has_walls = parts.get("has_walls", "True").lower() == "true"
                    if "ID" in parts:
                        space_code = parts["ID"]
                except Exception:
                    pass

            # Tính toán bounds thực tế từ IfcRelSpaceBoundary nếu có
            boundaries = [r for r in self.model.by_type("IfcRelSpaceBoundary") if r.RelatingSpace == sp]
            has_walls = len(boundaries) > 0 or len(self.model.by_type("IfcWall")) > 0
            
            # Nếu có boundaries, tính min/max từ tọa độ thực của các tường và cửa của phòng
            space_wall_coords = []
            for rsb in boundaries:
                elem = rsb.RelatedBuildingElement
                if elem and getattr(elem, "ObjectPlacement", None):
                    try:
                        mat = ifcopenshell.util.placement.get_local_placement(elem.ObjectPlacement)
                        scale = 0.001 if (abs(mat[0, 3]) > 100 or abs(mat[1, 3]) > 100) else 1.0
                        space_wall_coords.append((float(mat[0, 3]) * scale, float(mat[1, 3]) * scale))
                    except Exception:
                        pass

            if not bounds and len(space_wall_coords) >= 2:
                xs = [pt[0] for pt in space_wall_coords]
                ys = [pt[1] for pt in space_wall_coords]
                # Thêm biên đệm padding 0.5m để bao trọn mép tường
                bounds = {
                    "min_x": round(min(xs) - 0.5, 2),
                    "min_y": round(min(ys) - 0.5, 2),
                    "max_x": round(max(xs) + 4.5, 2),
                    "max_y": round(max(ys) + 4.5, 2),
                    "height": 3.3
                }
            elif not bounds:
                pos = [0.0, 0.0, 0.0]
                if getattr(sp, "ObjectPlacement", None):
                    try:
                        mat = ifcopenshell.util.placement.get_local_placement(sp.ObjectPlacement)
                        scale = 0.001 if (abs(mat[0, 3]) > 100 or abs(mat[1, 3]) > 100) else 1.0
                        pos = [float(mat[0, 3]) * scale, float(mat[1, 3]) * scale, float(mat[2, 3]) * scale]
                    except Exception:
                        pass
                
                bounds = {
                    "min_x": round(pos[0] - 10.0, 2),
                    "min_y": round(pos[1] - 10.0, 2),
                    "max_x": round(pos[0] + 10.0, 2),
                    "max_y": round(pos[1] + 10.0, 2),
                    "height": 3.3
                }

            area = (bounds["max_x"] - bounds["min_x"]) * (bounds["max_y"] - bounds["min_y"])
            centroid = [
                round((bounds["min_x"] + bounds["max_x"]) / 2.0, 2),
                round((bounds["min_y"] + bounds["max_y"]) / 2.0, 2),
                round(bounds.get("height", 3.0), 2)
            ]

            spaces_data.append({
                "id": sp.GlobalId,
                "code": space_code,
                "name": sp.Name or space_code,
                "long_name": getattr(sp, "LongName", sp.Name or space_code),
                "type": getattr(sp, "ObjectType", "residential"),
                "area": round(abs(area), 2),
                "bounds": bounds,
                "centroid": centroid,
                "has_walls": has_walls
            })
        return spaces_data

    def get_walls(self, space_id: Optional[str] = None) -> List[Dict[str, Any]]:
        walls = []
        
        # 1. ƯU TIÊN: Đọc tường từ IfcRelSpaceBoundary (Chuẩn BIM thực tế)
        rsb_list = self.model.by_type("IfcRelSpaceBoundary")
        if len(rsb_list) > 0:
            for rsb in rsb_list:
                elem = rsb.RelatedBuildingElement
                if not elem or not elem.is_a("IfcWall"):
                    continue

                sp = rsb.RelatingSpace
                sp_code = sp.Name if sp else ""
                if space_id and sp_code != space_id and (sp and sp.GlobalId != space_id):
                    continue

                # Lấy chiều dài tường từ OuterBoundary
                wall_len = 3.5
                try:
                    geom = rsb.ConnectionGeometry
                    if geom and geom.SurfaceOnRelatingElement:
                        srf = geom.SurfaceOnRelatingElement
                        ob = getattr(srf, "OuterBoundary", None)
                        if ob and hasattr(ob, "Points") and len(ob.Points) >= 2:
                            pt0 = ob.Points[0].Coordinates
                            pt1 = ob.Points[1].Coordinates
                            dx = pt1[0] - pt0[0]
                            dy = pt1[1] - pt0[1]
                            raw_len = math.hypot(dx, dy)
                            if raw_len > 100: # đơn vị mm
                                wall_len = round(raw_len / 1000.0, 2)
                except Exception:
                    pass

                # Lấy tọa độ vị trí thực từ ObjectPlacement của tường
                mat = ifcopenshell.util.placement.get_local_placement(elem.ObjectPlacement)
                scale = 0.001 if (abs(mat[0, 3]) > 100 or abs(mat[1, 3]) > 100) else 1.0
                wx = float(mat[0, 3]) * scale
                wy = float(mat[1, 3]) * scale
                
                # Tạo đoạn thẳng p1, p2
                p1 = [round(wx, 2), round(wy, 2)]
                p2 = [round(wx + wall_len, 2), round(wy, 2)]

                wall_type = elem.ObjectType or "brick_wall"
                if "kính" in elem.Name.lower() or "glass" in elem.Name.lower():
                    wall_type = "glass_wall"

                walls.append({
                    "id": elem.GlobalId,
                    "name": elem.Name or "Wall",
                    "type": wall_type,
                    "p1": p1,
                    "p2": p2,
                    "length": wall_len,
                    "space_code": sp_code
                })

        # 2. FALLBACK: Đọc từ Description nếu không có IfcRelSpaceBoundary
        if len(walls) == 0:
            for wall in self.model.by_type("IfcWall"):
                desc = wall.Description or ""
                parts = dict(re.findall(r"(\w+):([^;]+)", desc))
                wall_type = wall.ObjectType or parts.get("type", "brick_wall")
                sp_code = parts.get("space", "")
                p1_str = parts.get("p1", "[0,0]").strip("[]").split(",")
                p2_str = parts.get("p2", "[0,0]").strip("[]").split(",")
                p1 = [float(p1_str[0]), float(p1_str[1])]
                p2 = [float(p2_str[0]), float(p2_str[1])]
                walls.append({
                    "id": wall.GlobalId,
                    "name": wall.Name,
                    "type": wall_type,
                    "p1": p1,
                    "p2": p2,
                    "space_code": sp_code
                })
        return walls

    def get_doors_and_windows(self, space_id: Optional[str] = None) -> List[Dict[str, Any]]:
        elements = []
        
        # 1. ƯU TIÊN: Đọc cửa từ IfcRelSpaceBoundary
        rsb_list = self.model.by_type("IfcRelSpaceBoundary")
        if len(rsb_list) > 0:
            for rsb in rsb_list:
                elem = rsb.RelatedBuildingElement
                if not elem or not (elem.is_a("IfcDoor") or elem.is_a("IfcWindow")):
                    continue
                sp = rsb.RelatingSpace
                sp_code = sp.Name if sp else ""
                
                mat = ifcopenshell.util.placement.get_local_placement(elem.ObjectPlacement)
                scale = 0.001 if (abs(mat[0, 3]) > 100 or abs(mat[1, 3]) > 100) else 1.0
                x = round(float(mat[0, 3]) * scale, 2)
                y = round(float(mat[1, 3]) * scale, 2)
                z = round(float(mat[2, 3]) * scale, 2)

                is_door = elem.is_a("IfcDoor")
                elements.append({
                    "id": elem.GlobalId,
                    "type": "door" if is_door else "window",
                    "name": elem.Name or ("Door" if is_door else "Window"),
                    "position": [x, y, z],
                    "space_code": sp_code,
                    "clearance_radius": 0.8
                })

        # 2. FALLBACK nếu không có IfcRelSpaceBoundary
        if len(elements) == 0:
            for door in self.model.by_type("IfcDoor"):
                desc = door.Description or ""
                parts = dict(re.findall(r"(\w+):([^;]+)", desc))
                x = float(parts.get("X", 0.0))
                y = float(parts.get("Y", 0.0))
                z = float(parts.get("Z", 0.0))
                sp_code = parts.get("space", "")
                elements.append({
                    "id": door.GlobalId,
                    "type": "door",
                    "name": door.Name,
                    "position": [x, y, z],
                    "space_code": sp_code,
                    "clearance_radius": 0.8
                })

            for win in self.model.by_type("IfcWindow"):
                desc = win.Description or ""
                parts = dict(re.findall(r"(\w+):([^;]+)", desc))
                x = float(parts.get("X", 0.0))
                y = float(parts.get("Y", 0.0))
                z = float(parts.get("Z", 1.0))
                elements.append({
                    "id": win.GlobalId,
                    "type": "window",
                    "name": win.Name,
                    "position": [x, y, z],
                    "space_code": parts.get("space", "")
                })
        return elements

    def get_existing_placed_devices(self, storey_id: Optional[str] = None, space_id: Optional[str] = None, ifc_type: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        devices = []
        target_types = ifc_type or ["IfcFlowTerminal", "IfcBuildingElementProxy", "IfcFurniture", "IfcSystemFurnitureElement"]
        
        for t in target_types:
            for elem in self.model.by_type(t):
                desc = elem.Description or ""
                parts = dict(re.findall(r"(\w+):([^;]+)", desc))
                
                pos = [float(parts.get("X", 0)), float(parts.get("Y", 0)), float(parts.get("Z", 0))]
                if pos == [0.0, 0.0, 0.0] and getattr(elem, "ObjectPlacement", None):
                    try:
                        mat = ifcopenshell.util.placement.get_local_placement(elem.ObjectPlacement)
                        scale = 0.001 if abs(mat[0, 3]) > 100 or abs(mat[1, 3]) > 100 else 1.0
                        pos = [round(float(mat[0, 3]) * scale, 2), round(float(mat[1, 3]) * scale, 2), round(float(mat[2, 3]) * scale, 2)]
                    except Exception:
                        pass

                sp = parts.get("space_id", "")
                if not sp:
                    for rel in getattr(elem, "ContainedInStructure", []):
                        sp = getattr(rel.RelatingStructure, "Name", "") or rel.RelatingStructure.GlobalId

                if space_id and sp != space_id:
                    continue
                    
                devices.append({
                    "element_id": elem.GlobalId,
                    "device_type": parts.get("device_type", elem.ObjectType or getattr(elem, "Name", "") or t),
                    "space_id": sp,
                    "position": {"x": pos[0], "y": pos[1], "z": pos[2]},
                    "properties": {
                        "category": parts.get("category", "electrical"),
                        "load_w": float(parts.get("load_w", 0)),
                        "host_type": parts.get("host_type", "wall"),
                        "ifc_type": t,
                        "raw_name": getattr(elem, "Name", "")
                    }
                })
        return devices
