import sys
sys.stdout.reconfigure(encoding='utf-8')

from typing import Dict, List, Any, Callable
from tools_part_b import ifc_list_placed_devices, engine_validate_placements

class PartBAgent:
    """
    Bộ công cụ Part B: đọc thiết bị hiện có (Tool 7) và kiểm định
    phương án do AI agent/kỹ sư cung cấp (Tool 9). Không tự sinh phương án.
    """
    def __init__(self, ifc_path: str = None):
        self.ifc_path = ifc_path
        self.current_validation = {}
        self.existing_devices = []

    def list_existing_devices(self, space_id: str = None, storey_id: str = None) -> List[Dict[str, Any]]:
        """Tool 7: trả danh sách thiết bị MEP đã có để AI tránh đề xuất trùng."""
        self.existing_devices = ifc_list_placed_devices(
            space_id=space_id, storey_id=storey_id, ifc_file_path=self.ifc_path
        )
        return self.existing_devices

    def validate_proposals(
        self, proposals: List[Dict[str, Any]], placement_context: Dict[str, Any],
        rules: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Tool 9: kiểm định các vị trí AI/kỹ sư đã tạo; không tính tọa độ."""
        self.current_validation = engine_validate_placements(
            proposals=proposals,
            placement_context={**(placement_context or {}), "existing_devices": self.existing_devices},
            rules=rules,
            ifc_file_path=self.ifc_path
        )
        return self.current_validation

    @staticmethod
    def _check_proposal_schema(proposals: Any) -> List[Dict[str, Any]]:
        """Kiểm tra contract đầu vào trước khi gọi Tool 9."""
        issues = []
        if not isinstance(proposals, list):
            return [{"code": "ERR_INVALID_PROPOSALS", "message": "proposals phải là một danh sách."}]

        required = ("device_type", "space_id", "host_type", "position")
        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, dict):
                issues.append({"code": "ERR_INVALID_PROPOSAL", "index": index,
                               "message": "Mỗi proposal phải là một object."})
                continue
            missing = [key for key in required if key not in proposal]
            if not (proposal.get("proposed_id") or proposal.get("id")):
                missing.append("proposed_id (hoặc id)")
            if missing:
                issues.append({"code": "ERR_MISSING_FIELDS", "index": index,
                               "message": f"Thiếu trường: {', '.join(missing)}."})
                continue
            position = proposal.get("position")
            if not isinstance(position, dict) or any(
                not isinstance(position.get(axis), (int, float)) or isinstance(position.get(axis), bool)
                for axis in ("x", "y", "z")
            ):
                issues.append({"code": "ERR_INVALID_POSITION", "index": index,
                               "message": "position phải có x, y, z là số."})
        return issues

    def execute_pipeline(
        self,
        proposal_provider: Callable[[List[Dict[str, Any]], Dict[str, Any]], List[Dict[str, Any]]],
        placement_context: Dict[str, Any],
        rules: Dict[str, Any] = None,
        space_id: str = None,
        storey_id: str = None,
    ) -> Dict[str, Any]:
        """Tool 7 -> proposal_provider -> schema check -> Tool 9.

        Provider hiện có thể là hàm trả proposal mẫu. Khi tích hợp AI, thay
        provider bằng hàm gọi AI, nhận existing_devices/context và trả proposals.
        """
        existing = self.list_existing_devices(space_id=space_id, storey_id=storey_id)
        proposals = proposal_provider(existing, placement_context or {})
        schema_errors = self._check_proposal_schema(proposals)
        if schema_errors:
            validation = {
                "valid": False,
                "errors": schema_errors,
                "warnings": [],
                "suggested_fixes": [],
            }
        else:
            validation = self.validate_proposals(
                proposals=proposals,
                placement_context=placement_context or {},
                rules=rules or {},
            )
        return {
            "existing_devices": existing,
            "proposals": proposals,
            "validation": validation,
        }

    def validate_manual_device(self, new_device: Dict[str, Any]) -> Dict[str, Any]:
        """Kỹ sư muốn thử đặt thêm 1 thiết bị thủ công hoặc tinh chỉnh toạ độ"""
        return self.validate_proposals(
            proposals=[new_device], placement_context={"standard_profile": "office"}
        )
