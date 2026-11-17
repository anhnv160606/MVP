"""Bảng tick Phase 1 trên terminal, dùng cùng API với bản web."""

import argparse
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import uuid4

from .questionnaire_get_template import choose

REVISION_FIELDS = (
    "template_id",
    "template_version",
    "template_revision",
    "model_revision",
    "playbook_revision",
    "expected_revision",
)


def api_client(url, project_id):
    base = f"{url.rstrip('/')}/projects/{quote(project_id, safe='')}/questionnaire"

    def call(path, body=None):
        request = Request(
            base + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            detail = json.load(error).get("detail")
            raise ValueError(f"API {error.code}: {detail}") from None
        except (URLError, TimeoutError) as error:
            raise ValueError(f"Không kết nối được API tại {url}: {error}") from None

    return call


def tick(label, initial):
    marker = "x" if initial else " "
    while True:
        answer = input(f"[{marker}] {label} (y/n, Enter giữ nguyên): ").strip().lower()
        if answer in {"", "y", "n"}:
            return initial if not answer else answer == "y"
        print("Nhập y hoặc n.")


def select_room(spaces, prompt, default=None):
    rooms = [(None, "Chưa chọn phòng")]
    rooms.extend((room["space_id"], room_label(room)) for room in spaces)
    return choose(prompt, rooms, default)


def room_label(room):
    return f"{room['name']} ({room['storey_name'] or 'Chưa rõ tầng'})"


def edit_room(template, design):
    spaces = sorted(template["spaces"], key=lambda room: (room["storey_name"] or "", room["name"]))
    space_id = choose("Chọn phòng", [(room["space_id"], room_label(room)) for room in spaces])
    choice = next(room for room in design["rooms"] if room["space_id"] == space_id)
    options = template["room_device_options"].get(choice["room_type"], [])
    if "air_conditioners" in options:
        choice["air_conditioners"] = choose(
            "Số máy điều hòa", [(n, str(n)) for n in range(3)], choice["air_conditioners"]
        )
    if "water_heater" in options:
        choice["water_heater"] = tick("Bình nước nóng", choice["water_heater"])
    if not options:
        print("Phòng này không có thiết bị mặc định để tick. Dùng mục thiết bị tùy chỉnh nếu cần.")


def edit_building(template, design):
    design["pump"] = tick("Máy bơm nước", design["pump"])
    design["shared_water_heaters_by_storey"] = tick(
        "Các WC cùng tầng dùng chung một bình",
        design["shared_water_heaters_by_storey"],
    )
    design["washing_machine_space_id"] = select_room(
        template["spaces"],
        "Chọn phòng đặt máy giặt hoặc bỏ chọn",
        design["washing_machine_space_id"],
    )
    spaces = {room["space_id"]: room for room in template["spaces"]}
    for kitchen in design["kitchens"]:
        print(room_label(spaces[kitchen["space_id"]]))
        kitchen["stove"] = choose(
            "Loại bếp", [("gas", "Bếp gas"), ("electric", "Bếp điện")], kitchen["stove"]
        )
        if kitchen["stove"] == "electric":
            kitchen["burners"] = choose(
                "Số mặt bếp",
                [("single", "Bếp đơn"), ("double", "Bếp đôi")],
                kitchen["burners"] or "double",
            )
            kitchen["oven"] = tick("Lò nướng âm tủ", kitchen["oven"])
        else:
            kitchen.update(burners=None, oven=False)
        kitchen["fridge"] = tick("Tủ lạnh", kitchen["fridge"])


def add_custom(call, template, design, pending):
    catalog = [item for item in template["catalog"] if item["source"]["kind"] == "user_declared"]
    mode = choose(
        "Thiết bị tùy chỉnh", [("new", "Thêm vào Playbook"), ("saved", "Chọn từ Playbook")]
    )
    if mode == "saved":
        if not catalog:
            print("Chưa có thiết bị tùy chỉnh trong Playbook.")
            return
        item = choose("Chọn thiết bị", [(item, item["label"]) for item in catalog])
    else:
        name = input("Tên thiết bị: ").strip()
        if not name:
            raise ValueError("Tên thiết bị là bắt buộc.")
        group = choose(
            "Nhóm phụ tải", [(g, g["group_name"]) for g in template["load_groups"] if g["intent"]]
        )
        power_range = group["reference_power_W"]
        print(f"Công suất tham khảo min/max (W): {power_range['min']} / {power_range['max']}")
        watts = None
        if group["intent"] == "equipment":
            raw = input("Công suất thực tế (W), Enter nếu chưa biết: ").strip()
            if raw:
                watts = float(raw.replace(",", "."))
                if not math.isfinite(watts) or watts <= 0:
                    raise ValueError("Công suất thực tế phải là số hữu hạn lớn hơn 0.")
        body = dict(
            name=name,
            group_name=group["group_name"],
            group_revision=group["group_revision"],
            input_W=watts,
        )
        key = tuple(body.values())
        request_id = pending.setdefault(key, str(uuid4()))
        response = call("/custom-devices", dict(body, request_id=request_id))
        pending.pop(key)
        item = response["device"]
        template["playbook_revision"] = response["playbook_revision"]
        if not any(row["load_class_id"] == item["load_class_id"] for row in template["catalog"]):
            template["catalog"].append(item)
        print(f"Đã lưu vào Playbook: {item['label']} ({item['load_class_id']})")
    room_id = select_room(template["spaces"], "Chọn phòng lắp đặt")
    if not any(
        row["load_class_id"] == item["load_class_id"] and row["space_id"] == room_id
        for row in design["custom_devices"]
    ):
        design["custom_devices"].append(
            dict(load_class_id=item["load_class_id"], space_id=room_id, quantity=1)
        )


def export_context(context, output_dir):
    project = quote(context["project_id"], safe="")
    revision = context["questionnaire_revision"]
    path = output_dir / f"phase1-{project}-rev-{revision}.json"
    payload = json.dumps(context, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output_dir, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(payload)
            file.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path.resolve()


def run(call, output_dir=None):
    output_dir = Path.cwd() if output_dir is None else output_dir
    template = call("/checklist-template")
    pending = {}
    if template["has_legacy_answers"]:
        raise ValueError("Dự án có bảng hỏi nhập tay. Dùng project-id của bảng tick.")
    saved = template["saved_design_input"]
    if saved and template["saved_model_revision"] != template["model_revision"]:
        print("IFC đã đổi. Kiểm tra gợi ý của mô hình mới trước khi lưu.")
        saved = None
    design = saved or template["suggested_design_input"]
    for room in design["rooms"]:
        space = next(space for space in template["spaces"] if space["space_id"] == room["space_id"])
        room["room_type"] = room.get("room_type") or space["room_type"]
        options = template["room_device_options"].get(room["room_type"], [])
        if "air_conditioners" not in options:
            room["air_conditioners"] = 0
        if "water_heater" not in options:
            room["water_heater"] = False
    print(f"Bảng tick · {template['project_id']} · revision {template['expected_revision']}")
    while True:
        action = choose(
            "\nThao tác",
            [
                ("room", "Tick theo phòng"),
                ("building", "Bếp, máy bơm, máy giặt và bình dùng chung"),
                ("custom", "Thêm / chọn thiết bị tùy chỉnh"),
                ("remove", "Bỏ chọn thiết bị tùy chỉnh"),
                ("review", "Xem lựa chọn JSON"),
                ("save", "Lưu bảng hỏi và xuất JSON"),
                ("quit", "Thoát"),
            ],
        )
        try:
            if action == "quit":
                return
            if action == "room" and template["spaces"]:
                edit_room(template, design)
            elif action == "building":
                edit_building(template, design)
            elif action == "custom":
                add_custom(call, template, design, pending)
            elif action == "remove" and design["custom_devices"]:
                names = {item["load_class_id"]: item["label"] for item in template["catalog"]}
                rooms = {room["space_id"]: room_label(room) for room in template["spaces"]}
                choices = [
                    (
                        i,
                        f"{names.get(row['load_class_id'], row['load_class_id'])} · {rooms.get(row['space_id'], 'Chưa gán phòng')}",
                    )
                    for i, row in enumerate(design["custom_devices"])
                ]
                design["custom_devices"].pop(choose("Bỏ chọn mục nào", choices))
            elif action == "review":
                print(json.dumps(design, ensure_ascii=False, indent=2))
            elif action == "save":
                body = {key: template[key] for key in REVISION_FIELDS}
                result = call("/checklist-submit", dict(body, design_input=design))
                template["expected_revision"] = result["questionnaire_revision"]
                print(f"Đã lưu bảng hỏi · Phiên bản {result['questionnaire_revision']}")
                try:
                    path = export_context(result["derived_load_context"], output_dir)
                except OSError as error:
                    print(f"Bảng hỏi đã lưu. Không ghi được file JSON: {error}")
                else:
                    print(f"Đã xuất JSON: {path}")
        except ValueError as error:
            print(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--project-id", default="checklist-demo")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Thư mục lưu JSON (mặc định: thư mục hiện tại)",
    )
    args = parser.parse_args()
    try:
        run(api_client(args.url, args.project_id), args.output_dir.expanduser())
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except (EOFError, KeyboardInterrupt):
        print("\nĐã thoát.")


if __name__ == "__main__":
    main()
