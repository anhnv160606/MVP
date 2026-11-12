import sys
sys.stdout.reconfigure(encoding="utf-8")

from agent.agent_part_b import PartBAgent


def main():
    print("=" * 80)
    print("DEMO PART B: TOOL 7 -> PROPOSAL PROVIDER -> TOOL 9")
    print("=" * 80)
    agent = PartBAgent()
    placement_context = {"standard_profile": "office"}

    def proposal_provider(existing_devices, context):
        # Điểm tích hợp AI: truyền existing_devices, context, hình học,
        # rules và catalog vào AI; trả về ai_result["proposals"].
        print(f"\n[TOOL 7] Tìm thấy {len(existing_devices)} thiết bị MEP hiện có.")
        for device in existing_devices[:20]:
            pos = device["position"]
            print(f" • [{device['element_id']}] {device['device_type']} | "
                  f"Space: {device['space_id']} | "
                  f"({pos['x']}, {pos['y']}, {pos['z']})")
        return [{
            "proposed_id": "SAMPLE_SOCKET_01",
            "device_type": "socket_standard",
            "space_id": "space_01",
            "host_id": "wall_01",
            "host_type": "brick_wall",
            "position": {"x": 2.0, "y": 1.0, "z": 0.3},
            "orientation": {"x": 0.0, "y": 0.0, "z": 90.0},
            "rule_ids": ["socket_near_workstation"],
        }]

    result = agent.execute_pipeline(
        proposal_provider=proposal_provider,
        placement_context=placement_context,
    )
    validation = result["validation"]
    print("\n[TOOL 9] Kết quả kiểm định")
    print(f"Valid: {validation['valid']}")
    print(f"Errors: {len(validation['errors'])}; Warnings: {len(validation['warnings'])}")
    for error in validation["errors"]:
        print(f" [ERROR] {error}")
    for warning in validation["warnings"]:
        print(f" [WARN] {warning}")
    for fix in validation["suggested_fixes"]:
        print(f" [FIX] {fix}")


if __name__ == "__main__":
    main()
