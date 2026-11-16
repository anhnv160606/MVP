"""API bảng hỏi Phase 1 và máy chủ demo cục bộ."""

import argparse
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from postgrest.exceptions import APIError

from config.settings import Settings
from database.supabase_client import get_supabase_client
from ifc_engine.reader import SpatialIndex, read_spaces

from tools.phase1.questionnaire_get_template import (
    BUILDING_TYPE,
    CustomDeviceInput,
    FormSubmission,
    QuestionnaireModelConflict,
    load_groups,
    questionnaire_get_template,
    read_checklist_catalog,
)
from tools.phase1.questionnaire_submit import (
    CustomDeviceConflict,
    PlaybookStore,
    QuestionnaireRevisionConflict,
    QuestionnaireStore,
    questionnaire_submit,
)
from tools.phase1.questionnaire_get_load_context import (
    QuestionnaireLookupError,
    questionnaire_get_load_context,
)


class QuestionnaireSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template_id: str
    template_version: str
    model_revision: str
    expected_revision: int
    answers: list[dict]


def _call_tool(tool, *args, **kwargs):
    try:
        return tool(*args, **kwargs)
    except QuestionnaireRevisionConflict as error:
        raise HTTPException(409, {
            "code": "QUESTIONNAIRE_REVISION_CONFLICT",
            "current_revision": error.current_revision,
            "message": str(error),
        }) from error
    except QuestionnaireModelConflict as error:
        raise HTTPException(409, {
            "code": "MODEL_REVISION_CONFLICT",
            "current_model_revision": error.current_model_revision,
            "message": str(error),
        }) from error
    except QuestionnaireLookupError as error:
        raise HTTPException(404, str(error)) from error
    except CustomDeviceConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


CHECKLIST_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Chọn thiết bị cần cấp điện</title>

<style>
:root{--bg:#EDF0F3;--surface:#fff;--ink:#16222C;--muted:#5B6B78;--line:#CBD3DA;--accent:#0B6E99;--accent-ink:#fff;--hint:#FFF1CC;--hint-ink:#6E4A00;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#10181E;--surface:#18222A;--ink:#E6ECF0;--muted:#93A3B0;--line:#2B3944;--accent:#4DB6E0;--accent-ink:#06222E;--hint:#3A2E10;--hint-ink:#F0C96A}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#10181E;--surface:#18222A;--ink:#E6ECF0;--muted:#93A3B0;--line:#2B3944;--accent:#4DB6E0;--accent-ink:#06222E;--hint:#3A2E10;--hint-ink:#F0C96A}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
*,*::before,*::after{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:400 15px/1.5 "IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:980px;margin:0 auto;padding:28px 16px 40px}
h1{font-size:26px;line-height:1.2;margin:0 0 6px;font-weight:600;letter-spacing:-.01em}
h2{font-size:16px;margin:0 0 4px;font-weight:600}
p{margin:0}
.lead{color:var(--muted);max-width:68ch;margin-bottom:20px}
.auto{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--accent);padding:12px 14px;margin-bottom:22px;border-radius:6px}
.auto b{font-weight:600}
.auto span{color:var(--muted)}
section{margin-bottom:26px}
.sec-note{color:var(--muted);font-size:13px;margin-bottom:10px}
.legend{display:inline-block;background:var(--hint);color:var(--hint-ink);padding:1px 8px;border-radius:4px;font-size:12px}
.tw{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;min-width:560px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-weight:600;font-size:13px;color:var(--muted);background:var(--surface)}
tr:last-child td{border-bottom:0}
td.fl{font-weight:600;background:var(--bg);font-size:13px}
td.c,th.c{text-align:center}
td.sug{background:var(--hint)}
td.na{color:var(--muted);text-align:center}
.room small{display:block;color:var(--muted);font-size:12px}
input[type=checkbox],input[type=radio]{width:18px;height:18px;accent-color:var(--accent);vertical-align:middle;margin:0 6px 0 0}
select{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:5px 8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
fieldset{margin:0;border:1px solid var(--line);border-radius:8px;background:var(--surface);padding:12px 14px}
legend{font-weight:600;padding:0 6px}
label{display:block;padding:4px 0;cursor:pointer}
.sub{margin:4px 0 0 24px;padding-left:12px;border-left:2px solid var(--line)}
.sub[hidden]{display:none}
.hint{color:var(--muted);font-size:13px;margin-top:6px}
.bar{position:sticky;bottom:0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;box-shadow:0 -4px 14px rgba(0,0,0,.08)}
#sum{font-size:14px;max-width:60ch}
.btns{display:flex;gap:8px;flex-wrap:wrap}
button{font:inherit;font-weight:500;border-radius:6px;padding:8px 14px;cursor:pointer;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
button.pri{background:var(--accent);color:var(--accent-ink);border-color:var(--accent)}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
#out{margin-top:18px}
#out[hidden]{display:none}
pre{margin:8px 0 0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;font:13px/1.5 ui-monospace,Menlo,Consolas,monospace;max-height:320px}

input[type=text],input[type=number]{width:100%;font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px}
.control{display:grid;gap:4px}
[hidden]{display:none!important}
#status{margin:12px 0;color:var(--muted)}
#status[data-error=true]{color:var(--ink);background:var(--hint);padding:8px 12px;border-radius:6px}
.actions{margin-top:12px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button:disabled{opacity:.6;cursor:default}
#custom-list{margin:12px 0;display:grid;gap:8px}
.device{border:1px solid var(--line);border-radius:6px;padding:10px 12px;background:var(--surface)}
.device .grid{margin-top:6px}
@media(max-width:450px){main{padding:18px 12px 32px}.bar{position:static}}
</style>
</head>
<body>
<main>
<h1>Chọn thiết bị cần cấp điện</h1>
<p class="lead">Kiểm tra gợi ý theo từng phòng, rồi bổ sung thiết bị theo nhu cầu của công trình.</p>
<div class="auto"><b>Bố trí theo Playbook</b> <span>Chiếu sáng, công tắc, ổ cắm và quạt hút WC được chuyển sang bước bố trí.</span></div>
<p id="status" role="status" aria-live="polite">Đang tải bảng hỏi…</p>
<button id="reload" type="button" hidden>Tải lại bảng hỏi</button>
<div id="questionnaire" hidden>
<section>
<h2>Theo từng phòng</h2>
<p class="sec-note">Ô <span class="legend">nền vàng</span> đánh dấu gợi ý theo loại phòng.</p>
<div class="tw"><table><thead><tr><th>Phòng</th><th class="c">Điều hòa (số máy)</th><th class="c">Bình nước nóng</th></tr></thead><tbody id="rows"></tbody></table></div>
<label><input type="checkbox" id="shared">Các WC cùng tầng dùng chung một bình nước nóng</label>
</section>
<section class="grid">
<div id="kitchens"></div>
<fieldset><legend>Máy bơm nước</legend>
<label><input type="radio" name="pump" value="yes" id="pump">Có dùng máy bơm</label>
<label><input type="radio" name="pump" value="no" id="no-pump">Không dùng</label></fieldset>
<fieldset><legend>Thiết bị khác</legend>
<label class="control">Máy giặt <select id="washer"></select></label>
<p class="hint">Chọn phòng đặt máy giặt. Tủ lạnh chọn trong mục Bếp.</p>
</fieldset>
</section>
<section>
<h2>Thiết bị tùy chỉnh</h2>
<p class="sec-note">Thêm máy lọc không khí, máy rửa bát hoặc thiết bị khác vào Playbook của dự án.</p>
<div id="custom-list"></div>
<label class="control">Chọn thiết bị đã thêm <select id="reuse"></select></label>
<button type="button" id="reuse-add" class="actions">Thêm vào bảng hỏi</button>
<form id="custom-form" style="margin-top:14px">
<fieldset><legend>Thêm thiết bị vào Playbook</legend>
<div class="grid">
<label class="control">Tên thiết bị <input id="device-name" type="text" required maxlength="200" placeholder="Máy lọc không khí"></label>
<label class="control">Nhóm phụ tải <select id="group" required></select></label>
<label class="control">Công suất thực tế (W) <input id="watts" type="number" min="0" step="any" placeholder="Ví dụ: 60"></label>
<label class="control">Phòng lắp đặt <select id="device-room"></select></label>
</div>
<p class="hint" id="range" aria-live="polite"></p>
<p class="hint" id="power-note">Có thể để trống W. Nếu đã biết, nhập công suất điện ghi trên nhãn thiết bị.</p>
<div class="actions"><button class="pri" id="create" type="submit">Thêm vào Playbook và chọn</button><span id="custom-status" role="status"></span></div>
</fieldset></form>
</section>
<div class="bar"><div id="sum" aria-live="polite"></div><div class="btns">
<button type="button" id="download-json" hidden>Tải JSON</button><button type="button" id="reset">Chọn lại theo gợi ý</button><button type="button" id="save" class="pri">Lưu bảng hỏi</button>
</div></div>
<details id="out" hidden><summary>JSON đã lưu</summary><pre id="json"></pre></details>
</div>
</main>
<script>
"use strict";
const el = id => document.getElementById(id);
const api = new URL(".", location.href).pathname.replace(/\/$/, "");
const numeric = new Intl.NumberFormat("vi-VN");
const option = (value, label) => new Option(label, value);
const metadata = ["template_id", "template_version", "template_revision", "model_revision", "playbook_revision", "expected_revision"];
let template, design, pendingCreate, savedContext;
const groups = () => template.load_groups.filter(group => group.intent);
const deviceCatalog = () => template.catalog.filter(item => item.source.kind === "user_declared");
const roomLabel = room => `${room.name} (${room.storey_name ?? "Chưa rõ tầng"})`;

function node(tag, content = "", className = "") {
  const element = document.createElement(tag);
  element.textContent = content;
  element.className = className;
  return element;
}

function select(options, value, label) {
  const field = node("select");
  field.replaceChildren(...options.map(([key, text]) => option(key, text)));
  field.value = value ?? "";
  field.setAttribute("aria-label", label);
  return field;
}

function check(label, value, onChange) {
  const container = node("label");
  const field = node("input");
  field.type = "checkbox";
  field.checked = Boolean(value);
  field.addEventListener("change", () => { onChange(field.checked); summarize(); });
  container.append(field, label);
  return container;
}

function getRooms(firstLabel) {
  return [["", firstLabel], ...template.spaces.map(room => [room.space_id, roomLabel(room)])];
}

function status(message, error = false) {
  el("status").textContent = message;
  el("status").dataset.error = error;
}

async function request(path, body) {
  const response = await fetch(api + path, body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail;
    const message = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map(row => row.msg).join(". ") : detail?.message;
    throw new Error(message || `Lỗi API (${response.status})`);
  }
  return data;
}

function roomDeviceOptions(choice, room) {
  return template.room_device_options[choice.room_type ?? room.room_type] ?? [];
}

function renderRooms() {
  const rows = el("rows");
  rows.replaceChildren();
  const ordered = [...template.spaces].sort((a, b) =>
    (a.storey_name ?? "").localeCompare(b.storey_name ?? "", "vi", {numeric: true})
    || a.name.localeCompare(b.name, "vi", {numeric: true}));
  let floor;
  for (const room of ordered) {
    if (floor !== room.storey_id) {
      floor = room.storey_id;
      const header = rows.insertRow().insertCell();
      header.colSpan = 3;
      header.className = "fl";
      header.textContent = room.storey_name ?? "Chưa rõ tầng";
    }
    const current = design.rooms.find(choice => choice.space_id === room.space_id);
    const suggested = template.suggested_design_input.rooms.find(choice => choice.space_id === room.space_id);
    const row = rows.insertRow();
    const name = row.insertCell();
    name.className = "room";
    name.append(room.name, node("small", template.room_groups.find(group => group.room_type === current.room_type)?.label ?? ""));
    const available = roomDeviceOptions(current, room);
    const ac = row.insertCell();
    if (available.includes("air_conditioners")) {
      ac.className = suggested?.air_conditioners ? "c sug" : "c";
      const count = select([[0, "0"], [1, "1"], [2, "2"]], current.air_conditioners, "Số máy điều hòa, " + roomLabel(room));
      count.addEventListener("change", () => { current.air_conditioners = Number(count.value); summarize(); });
      ac.append(count);
    } else {
      ac.className = "na";
      ac.textContent = "Không áp dụng";
    }
    const heater = row.insertCell();
    heater.className = suggested?.water_heater ? "c sug" : "c";
    if (available.includes("water_heater")) {
      const control = check("", current.water_heater, value => { current.water_heater = value; });
      control.firstChild.setAttribute("aria-label", "Bình nước nóng, " + roomLabel(room));
      heater.append(control);
    } else {
      heater.className = "na";
      heater.textContent = "Không áp dụng";
    }
  }
}

function renderKitchens() {
  const root = el("kitchens");
  root.replaceChildren();
  if (!design.kitchens.length) {
    const empty = node("fieldset", "Chưa có phòng được nhận diện là bếp.");
    empty.prepend(node("legend", "Bếp"));
    root.append(empty);
  }
  for (const kitchen of design.kitchens) {
    const space = template.spaces.find(room => room.space_id === kitchen.space_id);
    const box = node("fieldset");
    box.append(node("legend", "Bếp · " + roomLabel(space)));
    const stove = select([["gas", "Bếp gas"], ["electric", "Bếp điện"]], kitchen.stove, "Loại bếp");
    const sub = node("div", "", "sub");
    const burners = select([["single", "Bếp đơn"], ["double", "Bếp đôi"]], kitchen.burners, "Số mặt bếp");
    sub.hidden = kitchen.stove !== "electric";
    burners.addEventListener("change", () => { kitchen.burners = burners.value; summarize(); });
    sub.append(burners, check("Có lò nướng âm tủ", kitchen.oven, value => { kitchen.oven = value; }));
    stove.addEventListener("change", () => {
      kitchen.stove = stove.value;
      kitchen.burners = stove.value === "electric" ? "double" : null;
      if (stove.value === "gas") kitchen.oven = false;
      renderKitchens();
      summarize();
    });
    box.append(stove, sub, check("Tủ lạnh", kitchen.fridge, value => { kitchen.fridge = value; }));
    root.append(box);
  }
}

function rangeText(range) {
  if (range?.min == null && range?.max == null) return "Chưa có khoảng tham khảo";
  if (range.min == null) return "Tối đa " + numeric.format(range.max) + " W";
  if (range.max == null) return "Từ " + numeric.format(range.min) + " W";
  return `${numeric.format(range.min)}–${numeric.format(range.max)} W`;
}

function renderCustom() {
  const list = el("custom-list");
  list.replaceChildren();
  const catalog = deviceCatalog();
  el("reuse").replaceChildren(option("", "Chọn thiết bị"), ...catalog.map(item => option(item.load_class_id, item.label)));
  el("reuse-add").disabled = !catalog.length;
  for (const [index, selection] of design.custom_devices.entries()) {
    const item = catalog.find(entry => entry.load_class_id === selection.load_class_id);
    if (!item) continue;
    const card = node("div", "", "device");
    card.append(node("strong", item.label), node("p",
      `${item.group} · Tham khảo: ${rangeText(item.reference_power_W)}`
      + (item.input_W == null ? " · Chưa biết công suất thực tế" : ` · Thực tế: ${numeric.format(item.input_W)} W`), "hint"));
    const controls = node("div", "", "grid");
    const room = select(getRooms("Toàn công trình / chưa gán phòng"), selection.space_id, "Phòng đặt " + item.label);
    const quantity = node("input");
    quantity.type = "number";
    quantity.min = "1";
    quantity.step = "1";
    quantity.value = selection.quantity;
    quantity.setAttribute("aria-label", "Số lượng " + item.label);
    quantity.addEventListener("change", () => {
      if (Number.isInteger(quantity.valueAsNumber) && quantity.valueAsNumber >= 1) selection.quantity = quantity.valueAsNumber;
      quantity.value = selection.quantity;
      summarize();
    });
    room.addEventListener("change", () => {
      const id = room.value || null;
      if (design.custom_devices.some(other => other !== selection && other.load_class_id === selection.load_class_id && other.space_id === id)) {
        status("Thiết bị này đã có ở phạm vi đã chọn. Có thể sửa số lượng trên dòng hiện có.", true);
        room.value = selection.space_id ?? "";
        return;
      }
      selection.space_id = id;
    });
    const remove = node("button", "Bỏ chọn");
    remove.type = "button";
    remove.onclick = () => { design.custom_devices.splice(index, 1); renderCustom(); summarize(); };
    controls.append(room, quantity, remove);
    card.append(controls);
    list.append(card);
  }
}

function summarize() {
  const ac = design.rooms.reduce((sum, room) => sum + room.air_conditioners, 0);
  const heaters = design.rooms.filter(room => room.water_heater);
  const shared = new Set(heaters.map(room => template.spaces.find(space => space.space_id === room.space_id)?.storey_id ?? room.space_id));
  const count = design.shared_water_heaters_by_storey ? shared.size : heaters.length;
  const custom = design.custom_devices.reduce((n, item) => n + item.quantity, 0);
  el("sum").textContent = `${ac} điều hòa · ${count} bình nước nóng · ${design.pump ? "Có máy bơm" : "Không dùng máy bơm"} · ${custom} thiết bị tùy chỉnh`;
}

function updateRange() {
  const group = groups().find(item => item.group_name === el("group").value);
  el("range").textContent = group ? "Khoảng tham khảo: " + rangeText(group.reference_power_W) : "Chọn nhóm để xem khoảng công suất tham khảo.";
  el("watts").disabled = !group || group.intent !== "equipment";
  el("power-note").hidden = !!group && group.intent !== "equipment";
}

function render() {
  el("shared").checked = design.shared_water_heaters_by_storey;
  el("pump").checked = design.pump;
  el("no-pump").checked = !design.pump;
  el("washer").replaceChildren(...getRooms("Không dùng máy giặt").map(pair => option(...pair)));
  el("washer").value = design.washing_machine_space_id ?? "";
  el("device-room").replaceChildren(...getRooms("Toàn công trình / chưa gán phòng").map(pair => option(...pair)));
  el("group").replaceChildren(option("", "Chọn nhóm phụ tải"), ...groups().map(item => option(item.group_name, item.group_name)));
  renderRooms();
  renderKitchens();
  renderCustom();
  updateRange();
  summarize();
}

function addChoice(id, room) {
  if (!design.custom_devices.some(item => item.load_class_id === id && item.space_id === room)) {
    design.custom_devices.push({load_class_id: id, space_id: room, quantity: 1});
  }
  renderCustom();
  summarize();
}

el("group").onchange = updateRange;
el("shared").onchange = () => { design.shared_water_heaters_by_storey = el("shared").checked; summarize(); };
for (const id of ["pump", "no-pump"]) el(id).onchange = () => { design.pump = el("pump").checked; summarize(); };
el("washer").onchange = () => { design.washing_machine_space_id = el("washer").value || null; };
el("reuse-add").onclick = () => { if (el("reuse").value) addChoice(el("reuse").value, null); };
el("reset").onclick = () => { design = structuredClone(template.suggested_design_input); render(); status("Đã chọn lại theo gợi ý. Bấm Lưu bảng hỏi để xác nhận."); };
el("custom-form").onsubmit = async event => {
  event.preventDefault();
  const group = groups().find(item => item.group_name === el("group").value);
  const name = el("device-name").value.trim();
  const watts = el("watts").disabled || el("watts").value === "" ? null : el("watts").valueAsNumber;
  if (!name || !group || (watts !== null && (!Number.isFinite(watts) || watts <= 0))) {
    el("custom-status").textContent = "Nhập tên, chọn nhóm và nhập số W lớn hơn 0 nếu đã biết.";
    return;
  }
  const payload = {name, group_name: group.group_name, group_revision: group.group_revision, input_W: watts};
  const fingerprint = JSON.stringify(payload);
  if (pendingCreate?.fingerprint !== fingerprint) pendingCreate = {fingerprint, id: crypto.randomUUID()};
  const room = el("device-room").value || null;
  el("create").disabled = el("save").disabled = true;
  el("custom-status").textContent = "Đang lưu thiết bị…";
  try {
    const data = await request("/custom-devices", {...payload, request_id: pendingCreate.id});
    template.playbook_revision = data.playbook_revision;
    if (!template.catalog.some(item => item.load_class_id === data.device.load_class_id)) template.catalog.push(data.device);
    addChoice(data.device.load_class_id, room);
    el("custom-form").reset();
    pendingCreate = null;
    updateRange();
    el("custom-status").textContent = "Đã thêm vào Playbook và chọn trong bảng hỏi.";
  } catch (error) {
    el("custom-status").textContent = error.message;
  } finally {
    el("create").disabled = el("save").disabled = false;
  }
};

el("save").onclick = async () => {
  el("save").disabled = el("create").disabled = true;
  status("Đang lưu bảng hỏi…");
  try {
    const revisions = Object.fromEntries(metadata.map(key => [key, template[key]]));
    const result = await request("/checklist-submit", {...revisions, design_input: design});
    template.expected_revision = result.questionnaire_revision;
    savedContext = result.derived_load_context;
    el("json").textContent = JSON.stringify(savedContext, null, 2);
    el("download-json").hidden = false;
    el("out").hidden = false;
    status("Đã lưu bảng hỏi · Phiên bản " + result.questionnaire_revision);
  } catch (error) {
    status(error.message, true);
  } finally {
    el("save").disabled = el("create").disabled = false;
  }
};

el("download-json").onclick = async () => {
  const button = el("download-json");
  const revision = template.expected_revision;
  button.disabled = true;
  try {
    const context = savedContext?.questionnaire_revision === revision
      ? savedContext
      : await request("/load-context?questionnaire_revision=" + encodeURIComponent(revision));
    const json = JSON.stringify(context, null, 2) + "\n";
    const url = URL.createObjectURL(new Blob([json], {type: "application/json;charset=utf-8"}));
    const link = document.createElement("a");
    link.href = url;
    link.download = `phase1-${encodeURIComponent(context.project_id)}-rev-${context.questionnaire_revision}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) {
    status("Không tải được JSON: " + error.message, true);
  } finally {
    button.disabled = false;
  }
};

async function load() {
  try {
    template = await request("/checklist-template");
    const currentModel = template.saved_model_revision === template.model_revision;
    design = structuredClone((currentModel && template.saved_design_input) || template.suggested_design_input);
    let removedUnavailable = false;
    for (const choice of design.rooms) {
      const room = template.spaces.find(space => space.space_id === choice.space_id);
      const available = roomDeviceOptions(choice, room);
      if (choice.air_conditioners && !available.includes("air_conditioners")) {
        choice.air_conditioners = 0;
        removedUnavailable = true;
      }
      if (choice.water_heater && !available.includes("water_heater")) {
        choice.water_heater = false;
        removedUnavailable = true;
      }
    }
    render();
    el("reload").hidden = true;
    el("questionnaire").hidden = false;
    savedContext = undefined;
    el("download-json").hidden = !template.saved_design_input;
    status(template.saved_design_input && !currentModel ? "IFC đã thay đổi. Kiểm tra gợi ý cho danh sách phòng mới trước khi lưu."
      : template.has_legacy_answers ? "Dự án có bảng hỏi nhập tay. Mở demo với project-id khác để lưu bảng tick."
      : removedUnavailable ? "Bản nháp đã bỏ lựa chọn cũ không phù hợp với loại phòng. Kiểm tra lại trước khi lưu."
      : template.saved_design_input ? "Đã tải bảng hỏi · Phiên bản " + template.expected_revision : "Đang hiển thị gợi ý. Bạn có thể chỉnh trước khi lưu.");
  } catch (error) {
    status(error.message, true);
    el("reload").hidden = false;
  }
}
el("reload").onclick = load;
load();
</script>
</body>
</html>"""


def create_questionnaire_router(
    store: QuestionnaireStore,
    resolve_model: Callable[[str], SpatialIndex | None],
    playbook_path: Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/projects/{project_id}/questionnaire", tags=["questionnaire"])

    def require_project_model(project_id: str) -> SpatialIndex:
        model = resolve_model(project_id)
        if model is None:
            raise HTTPException(404, f"Unknown project_id: {project_id}")
        return model

    playbook_store = PlaybookStore(store) if playbook_path is not None else None

    def catalog(project_id: str):
        if playbook_path is None:
            raise HTTPException(503, "Checklist Playbook is not configured")
        try:
            return playbook_store.catalog(project_id, read_checklist_catalog(playbook_path))
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(503, "Checklist Playbook cannot be loaded") from error

    @router.get("/demo", include_in_schema=False)
    def checklist_demo(project_id: str):
        require_project_model(project_id)
        return HTMLResponse(content=CHECKLIST_HTML, media_type="text/html")

    @router.get("/checklist-template")
    def get_checklist(project_id: str):
        model = require_project_model(project_id)
        return _call_tool(
            questionnaire_get_template, project_id, BUILDING_TYPE, model.revision,
            model=model, catalog=catalog(project_id), store=store,
        )

    @router.post("/checklist-submit")
    def submit_checklist(project_id: str, submission: FormSubmission):
        model = require_project_model(project_id)
        return _call_tool(
            questionnaire_submit, project_id, submission=submission,
            model=model, catalog=catalog(project_id), store=store,
        )

    @router.get("/load-groups")
    def get_load_groups(project_id: str):
        require_project_model(project_id)
        current = catalog(project_id)
        return {"playbook_revision": current.revision, "groups": load_groups(current)}

    @router.post("/custom-devices")
    def create_custom(project_id: str, device: CustomDeviceInput):
        require_project_model(project_id)
        if playbook_store is None:
            raise HTTPException(503, "Checklist Playbook is not configured")
        current = catalog(project_id)
        item = _call_tool(playbook_store.create_custom_device, project_id, **device.model_dump(), catalog=current)
        current = catalog(project_id)
        return {"status": "saved", "device": item, "playbook_revision": current.revision}

    @router.get("/template")
    def get_template(
        project_id: str, building_type: str = BUILDING_TYPE, model_revision: str | None = None,
    ) -> dict:
        model = require_project_model(project_id)
        return _call_tool(
            questionnaire_get_template, project_id, building_type,
            model.revision if model_revision is None else model_revision, model=model,
        )

    @router.post("/submit")
    def submit(project_id: str, submission: QuestionnaireSubmission) -> dict:
        model = require_project_model(project_id)
        return _call_tool(
            questionnaire_submit, project_id, **submission.model_dump(), model=model, store=store,
        )

    @router.get("/load-context")
    def get_load_context(
        project_id: str,
        space_id: str | None = None,
        questionnaire_revision: int | None = None,
        include_answers: bool = False,
    ) -> dict:
        require_project_model(project_id)
        return _call_tool(
            questionnaire_get_load_context, project_id, space_id, questionnaire_revision,
            store=store, include_answers=include_answers,
        )

    return router


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ifc", required=True, type=Path)
    parser.add_argument("--project-id", default="demo-house")
    parser.add_argument("--playbook", type=Path, default=Path(__file__).resolve().parents[2] / "database/Playbook_dien_nha_pho_Viet_Nam_2-5_tang.xlsx")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if not args.project_id.strip():
        parser.error("--project-id must be non-empty")

    try:
        store = QuestionnaireStore(get_supabase_client(Settings()))
    except ValueError as error:
        parser.error(str(error))
    except APIError:
        parser.exit(1, "Không truy cập được Supabase. Kiểm tra cấu hình và quyền của Client.\n")
    model = read_spaces(args.ifc)
    app = FastAPI(title="MVP Phase 1 Questionnaire")
    app.include_router(create_questionnaire_router(
        store,
        lambda project_id: model if project_id == args.project_id else None,
        playbook_path=args.playbook,
    ))
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
