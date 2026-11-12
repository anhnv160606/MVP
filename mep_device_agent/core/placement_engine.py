import math
from typing import Dict, List, Any, Optional

class PlacementEngine:
        @staticmethod
        def validate_placements(
            proposals: List[Dict[str, Any]],
            placement_context: Dict[str, Any],
            rules: Dict[str, Any],
            spaces_dict: Dict[str, Dict[str, Any]],
            doors: List[Dict[str, Any]]
        ) -> Dict[str, Any]:
            """
            Tool 9: engine_validate_placements
            Kiểm tra phương án trước khi lưu chính thức:
            - Bounding bounds phòng
            - Tương thích bề mặt (Host compatibility)
            - Va chạm cửa/cửa sổ/vật cản
            - Trùng lặp thiết bị (Duplication)
            - Tiêu chuẩn cao độ Z
            """
            errors = []
            warnings = []
            suggested_fixes = []
            existing_devices = (placement_context or {}).get("existing_devices", [])

            for p in proposals:
                dev_id = p.get("proposed_id") or p.get("id", "UNKNOWN")
                space_id = p.get("space_id", "")
                dev_type = p.get("device_type", "")
                host_type = p.get("host_type", "")
                pos = p.get("position", {})
                x = pos.get("x", 0.0)
                y = pos.get("y", 0.0)
                z = pos.get("z", 0.0)

                # 1. BOUNDS CHECK
                if space_id in spaces_dict:
                    sp = spaces_dict[space_id]
                    b = sp["bounds"]
                    if not (b["min_x"] - 0.2 <= x <= b["max_x"] + 0.2 and b["min_y"] - 0.2 <= y <= b["max_y"] + 0.2):
                        errors.append({
                            "code": "ERR_OUT_OF_BOUNDS",
                            "severity": "CRITICAL",
                            "device_id": dev_id,
                            "message": f"Thiết bị '{dev_type}' tọa độ ({x}, {y}) nằm ngoài phạm vi phòng {sp['name']}."
                        })
                        suggested_fixes.append({
                            "device_id": dev_id,
                            "action": "clamp_to_bounds",
                            "suggested_position": {
                                "x": max(b["min_x"] + 0.5, min(x, b["max_x"] - 0.5)),
                                "y": max(b["min_y"] + 0.5, min(y, b["max_y"] - 0.5)),
                                "z": z
                            },
                            "explanation": "Dịch thiết bị vào bên trong ranh giới phòng tối thiểu 0.5m."
                        })

                # 2. HOST TYPE COMPATIBILITY CHECK
                if host_type == "glass_wall" and dev_type == "socket_standard":
                    errors.append({
                        "code": "ERR_INCOMPATIBLE_HOST",
                        "severity": "CRITICAL",
                        "device_id": dev_id,
                        "message": f"Không được gắn ổ cắm thường '{dev_type}' trên vách kính (glass_wall)."
                    })
                    suggested_fixes.append({
                        "device_id": dev_id,
                        "action": "switch_to_floor_or_remove",
                        "suggested_position": {"x": x, "y": y, "z": 0.05},
                        "explanation": "Chuyển thành ổ cắm âm sàn (socket_floor) hoặc xóa bỏ khỏi vách kính."
                    })

                if dev_type == "socket_floor" and host_type not in ["floor", "floor_slab"]:
                    warnings.append({
                        "code": "WARN_FLOOR_SOCKET_HOST",
                        "severity": "WARNING",
                        "device_id": dev_id,
                        "message": f"Ổ cắm âm sàn '{dev_type}' nên được gắn trên sàn thay vì '{host_type}'."
                    })

                # 3. DOOR COLLISION CHECK
                for door in doors:
                    dp = door["position"]
                    dist = math.dist([x, y], [dp[0], dp[1]])
                    if not dev_type.startswith("switch") and dist < 0.6:
                        errors.append({
                            "code": "ERR_DOOR_COLLISION",
                            "severity": "CRITICAL",
                            "device_id": dev_id,
                            "message": f"Thiết bị '{dev_type}' đè lên vùng mở cửa {door.get('name', '')} (khoảng cách {round(dist, 2)}m < 0.6m)."
                        })
                        suggested_fixes.append({
                            "device_id": dev_id,
                            "action": "relocate_away_from_door",
                            "suggested_position": {"x": round(dp[0] + 1.0, 2), "y": round(dp[1] + 0.5, 2), "z": z},
                            "explanation": "Dời thiết bị ra xa cửa tối thiểu 1.0m."
                        })

                # 4. MOUNTING HEIGHT SANITY CHECK
                if dev_type.startswith("switch") and not (1.0 <= z <= 1.4):
                    warnings.append({
                        "code": "WARN_SWITCH_HEIGHT",
                        "severity": "WARNING",
                        "device_id": dev_id,
                        "message": f"Công tắc '{dev_type}' lắp ở cao độ z={z}m, khuyến nghị chuẩn TCVN là 1.2m."
                    })

                if dev_type == "socket_standard" and z > 1.5:
                    warnings.append({
                        "code": "WARN_SOCKET_UNUSUAL_HEIGHT",
                        "severity": "WARNING",
                        "device_id": dev_id,
                        "message": f"Ổ cắm thường '{dev_type}' lắp ở cao độ z={z}m > 1.5m, cần kiểm tra có phải ổ cắm chuyên dụng không."
                    })

            # 5. DUPLICATE CHECK (Proximity < 0.25m between any two proposals)
            for i in range(len(proposals)):
                for j in range(i + 1, len(proposals)):
                    p1 = proposals[i]
                    p2 = proposals[j]
                    if p1.get("space_id") == p2.get("space_id"):
                        pos1 = p1.get("position", {})
                        pos2 = p2.get("position", {})
                        d = math.dist([pos1.get("x", 0), pos1.get("y", 0)], [pos2.get("x", 0), pos2.get("y", 0)])
                        if d < 0.25:
                            warnings.append({
                                "code": "WARN_DEVICE_PROXIMITY_COLLISION",
                                "severity": "WARNING",
                                "device_id": p1.get("proposed_id"),
                                "message": f"Thiết bị '{p1.get('device_type')}' và '{p2.get('device_type')}' cách nhau quá gần ({round(d, 2)}m < 0.25m)."
                            })

            # Compare AI proposals with the existing model devices supplied by Tool 7.
            for proposal in proposals:
                ppos = proposal.get("position", {})
                for existing in existing_devices:
                    if proposal.get("space_id") != existing.get("space_id"):
                        continue
                    epos = existing.get("position", {})
                    distance = math.dist(
                        [ppos.get("x", 0), ppos.get("y", 0)],
                        [epos.get("x", 0), epos.get("y", 0)]
                    )
                    if distance < 0.25:
                        warnings.append({
                            "code": "WARN_DUPLICATE_EXISTING_DEVICE",
                            "severity": "WARNING",
                            "device_id": proposal.get("proposed_id") or proposal.get("id", "UNKNOWN"),
                            "message": (
                                f"Vị trí đề xuất gần thiết bị hiện có "
                                f"'{existing.get('element_id')}' ({round(distance, 2)}m < 0.25m)."
                            )
                        })

            valid = len(errors) == 0

            return {
                "valid": valid,
                "errors": errors,
                "warnings": warnings,
                "suggested_fixes": suggested_fixes
            }
