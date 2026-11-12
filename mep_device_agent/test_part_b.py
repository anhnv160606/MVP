import sys
sys.stdout.reconfigure(encoding="utf-8")

from agent.agent_part_b import PartBAgent


def run_part_b_check():
    agent = PartBAgent()
    placement_context = {"standard_profile": "office"}

    def sample_proposal_provider(existing_devices, context):
        print(f"[TOOL 7] Thiết bị MEP hiện có: {len(existing_devices)}")
        for device in existing_devices[:20]:
            print(f" • {device['element_id']} | {device['device_type']} | "
                  f"Space: {device['space_id']} | Position: {device['position']}")
        # Proposal mẫu; thay provider này bằng hàm gọi AI khi tích hợp.
        return [{
            "proposed_id": "SAMPLE_SOCKET_01",
            "device_type": "socket_standard",
            "space_id": "space_01",
            "host_id": "wall_01",
            "host_type": "brick_wall",
            "position": {"x": 2.0, "y": 1.0, "z": 0.3},
            "orientation": {"x": 0.0, "y": 0.0, "z": 90.0},
            "rule_ids": [],
        }]

    result = agent.execute_pipeline(
        proposal_provider=sample_proposal_provider,
        placement_context=placement_context,
    )
    validation = result["validation"]
    print(f"[TOOL 9] Valid: {validation['valid']}")
    print(f"Errors: {len(validation['errors'])}; Warnings: {len(validation['warnings'])}")
    for error in validation["errors"]:
        print(f" [ERROR] {error}")
    for warning in validation["warnings"]:
        print(f" [WARN] {warning}")


if __name__ == "__main__":
    run_part_b_check()
