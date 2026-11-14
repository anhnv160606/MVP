"""Chạy thử luồng bảng tick và thiết bị tùy chỉnh qua HTTP."""
import argparse
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", default="checklist-demo")
    args = parser.parse_args()
    base = f"{args.url}/projects/{args.project_id}/questionnaire"
    def call(path, body=None):
        request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    template = call("/checklist-template")
    if template["has_legacy_answers"]:
        raise SystemExit("Use a separate project for this example; manual answers already exist.")
    group = next(g for g in template["load_groups"] if g["group_name"] == "Động cơ gia dụng")
    device = call("/custom-devices", {"request_id": "checklist-demo-group-fan-v1", "name": "Quạt hút bổ sung",
                                      "group_name": group["group_name"], "group_revision": group["group_revision"]})
    template = call("/checklist-template")  # Catalog revision includes the new entry.
    design = template["saved_design_input"] or template["suggested_design_input"]
    if not template["spaces"]:
        raise SystemExit("The IFC has no rooms.")
    chosen = next((s for s in template["spaces"] if s["room_type"] == "bedroom"), template["spaces"][0])
    device_id = device["device"]["load_class_id"]
    custom = [d for d in design["custom_devices"] if d["load_class_id"] != device_id]
    design["custom_devices"] = custom + [{"load_class_id": device_id, "space_id": chosen["space_id"], "quantity": 1}]
    body = {k: template[k] for k in ("template_id", "template_version", "template_revision", "model_revision", "playbook_revision", "expected_revision")}
    body["design_input"] = design
    result = call("/checklist-submit", body)
    reloaded = call("/load-context")
    assert reloaded == result["derived_load_context"]
    print(json.dumps(reloaded, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except HTTPError as error:
        raise SystemExit(error.read().decode()) from None
