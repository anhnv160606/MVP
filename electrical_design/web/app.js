const labels = {
  lighting: "Chiếu sáng",
  general_receptacle: "Ổ cắm / điểm cấp điện",
  desk_equipment: "Thiết bị bàn làm việc",
  plug_in_equipment: "Thiết bị cắm điện",
  air_conditioning: "Điều hòa",
  ventilation: "Quạt thông gió",
  water_heater: "Bình nước nóng",
  pump: "Bơm",
  special_equipment: "Thiết bị riêng",
};
const phases = {
  IFC_LOADED: "Đã tải mô hình",
  QUESTIONNAIRE: "Chờ bảng hỏi",
  PLACING_DEVICES: "Đang đề xuất",
  REVIEW_DEVICES: "Chờ kỹ sư duyệt",
  APPLYING_DEVICES: "Đang ghi / chờ thử lại",
  DEVICES_APPROVED: "Đã duyệt & ghi IFC",
};
const deviceNames = {
  light: "Đèn",
  light_fixture: "Đèn",
  IfcLightFixture: "Đèn",
  outlet: "Ổ cắm",
  socket: "Ổ cắm",
  IfcOutlet: "Ổ cắm",
  switch: "Công tắc",
  IfcSwitchingDevice: "Công tắc",
  junction_box: "Hộp nối",
  appliance: "Thiết bị điện",
};
const state = {
  backend: false,
  project: null,
  template: null,
  answers: [],
  drafts: new Map(),
  dirty: new Set(),
  revision: 0,
  review: null,
  geometry: null,
  roomId: null,
  sample: false,
  busy: false,
  socket: null,
  events: 0,
  selected: null,
  token: "",
  geometryRequest: 0,
  checklist: false,
};
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const fmt = (value) =>
  Number.isFinite(Number(value)) && value !== null && value !== ""
    ? Number(value).toFixed(2)
    : "—";
const keyFor = (room, question) => `${room}:${question}`;

export function mergeAnswers(original, drafts, dirty) {
  const replaced = new Set(
    [...dirty].filter((key) => drafts.has(key) && !drafts.get(key).multiple),
  );
  const result = original.filter(
    (a) => !replaced.has(keyFor(a.space_id, a.q_id)),
  );
  for (const key of replaced) {
    const row = drafts.get(key);
    if (!row || row.multiple) continue;
    const answer = {
      answer_item_id: row.answer_item_id,
      q_id: row.q_id,
      space_id: row.space_id,
      selected: row.selected,
    };
    if (row.selected === "needed") {
      if (row.quantity !== "" && row.quantity != null)
        answer.quantity = Number(row.quantity);
      if (row.placement_hint?.trim())
        answer.placement_hint = row.placement_hint.trim();
      if (
        row.q_id !== "general_receptacle" &&
        row.input_W !== "" &&
        row.input_W != null
      ) {
        answer.input_W = Number(row.input_W);
        if (row.power_source?.trim())
          answer.power_source = row.power_source.trim();
      }
      // Preserve previously entered VA/PF; this basic form doesn't edit them.
      for (const field of ["input_VA", "power_factor", "pf_source"])
        if (row[field] != null) answer[field] = row[field];
    }
    result.push(answer);
  }
  return result;
}

export function placementKind(device) {
  const type = String(
    device.device_type || device.ifc_class || "",
  ).toLowerCase();
  return /light|den|đèn/.test(type)
    ? "light"
    : /outlet|socket|ổ cắm/.test(type)
      ? "outlet"
      : /switch|công tắc/.test(type)
        ? "switch"
        : "other";
}

export function canvasTransform(bbox, width = 660, height = 400) {
  if (!bbox || !Array.isArray(bbox.min) || !Array.isArray(bbox.max))
    return null;
  const [x0, y0] = bbox.min,
    [x1, y1] = bbox.max;
  if (![x0, y0, x1, y1].every(Number.isFinite) || x1 <= x0 || y1 <= y0)
    return null;
  const scale = Math.min((width - 150) / (x1 - x0), (height - 115) / (y1 - y0));
  const left = (width - (x1 - x0) * scale) / 2,
    top = (height - (y1 - y0) * scale) / 2;
  return {
    left,
    top,
    roomWidth: (x1 - x0) * scale,
    roomHeight: (y1 - y0) * scale,
    point: (x, y) => [left + (x - x0) * scale, top + (y1 - y) * scale],
  };
}

function notify(message, type = "info") {
  $("notice").textContent = message;
  $("notice").className = `banner ${type}`;
  $("notice").hidden = false;
}

async function api(path, { method = "GET", body, blob = false } = {}) {
  const headers = state.token ? { "X-API-Key": state.token } : {};
  if (body && !(body instanceof FormData))
    headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body:
      body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    let message;
    const raw = await response.text();
    try {
      const detail = JSON.parse(raw).detail;
      message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail.map((e) => `${e.loc?.join(".")}: ${e.msg}`).join("; ")
            : `${detail?.code || response.status}: ${detail?.message || "Yêu cầu không thành công"}`;
    } catch {
      message = `HTTP ${response.status}: ${raw.slice(0, 220) || response.statusText}`;
    }
    if (response.status === 401)
      message =
        "Backend yêu cầu API_TOKEN. Nhấn nút cấu hình ở góc trên để kết nối.";
    throw new Error(message);
  }
  return blob ? response.blob() : response.json();
}

async function busy(action) {
  if (state.busy) return;
  state.busy = true;
  updateButtons();
  try {
    return await action();
  } catch (error) {
    notify(error.message, "error");
  } finally {
    state.busy = false;
    if (state.template) renderQuestions();
    updateButtons();
  }
}

function updateButtons() {
  const real = !!state.project && !state.sample && state.backend;
  $("upload-button").disabled = state.busy || !state.backend;
  $("restore-button").disabled = state.busy || !state.backend;
  $("sample-button").disabled = state.busy || !!state.dirty.size;
  $("exit-sample").disabled = state.busy;
  $("room-select").disabled = !state.template || state.busy;
  $("save-questionnaire").disabled =
    !real || state.busy || state.checklist || !state.dirty.size;
  $("run-agent").disabled =
    !real || state.busy || !state.revision || !!state.dirty.size;
  $("refresh-review").disabled = !real || state.busy;
  const approved = state.project?.phase === "DEVICES_APPROVED";
  const valid = state.review?.validation_result?.valid === true;
  const canApprove =
    real &&
    !state.busy &&
    valid &&
    !state.dirty.size &&
    !approved &&
    state.project.questionnaire_revision === state.revision &&
    ["REVIEW_DEVICES", "APPLYING_DEVICES"].includes(state.project.phase);
  $("review-confirm").disabled = !canApprove;
  $("approve-button").disabled = !canApprove || !$("review-confirm").checked;
  $("approve-button").textContent =
    state.project?.phase === "APPLYING_DEVICES"
      ? "↻ Thử ghi IFC lại"
      : "✓ Duyệt & ghi IFC";
  $("download-button").disabled = !real || state.busy || !approved;
  $("cancel-agent").hidden = !state.socket;
  $("upload-button").textContent =
    state.busy && !state.socket ? "Đang xử lý…" : "↑ Upload & tạo dự án";
  $("sample-banner").hidden = !state.sample;
}

function activeRoom() {
  return state.template?.spaces.find((r) => r.space_id === state.roomId);
}
function roomDevices() {
  return (state.review?.proposals || []).filter(
    (d) => d.space_id === state.roomId,
  );
}

function renderProject() {
  const project = state.project;
  $("project-status").textContent = state.sample
    ? "DỮ LIỆU MẪU"
    : project
      ? "ĐÃ TẢI MÔ HÌNH"
      : "CHƯA CÓ DỰ ÁN";
  $("questionnaire-revision").textContent = state.dirty.size
    ? "CÓ THAY ĐỔI CHƯA LƯU"
    : state.revision
      ? `REVISION ${state.revision}`
      : "CHƯA LƯU";
  $("project-meta").textContent = project
    ? `${project.name || "Dự án"} · ${state.template?.spaces.length || 0} phòng${state.sample ? " · minh họa" : ` · ${project.project_id}`}`
    : "Chưa có mô hình được tải.";
  $("phase-label").textContent = phases[project?.phase] || "Đang chờ dữ liệu";
  $("phase-label").className =
    `status-pill ${project?.phase === "DEVICES_APPROVED" ? "success" : state.review ? "pending" : "neutral"}`;
  const step =
    project?.phase === "DEVICES_APPROVED" || state.review
      ? 4
      : state.revision
        ? 3
        : project
          ? 2
          : 1;
  document.querySelectorAll(".step").forEach((element) => {
    const n = Number(element.dataset.step);
    element.classList.toggle("active", n === step);
    element.classList.toggle("done", n < step);
  });
  updateButtons();
}

function hydrateDrafts() {
  state.drafts.clear();
  state.dirty.clear();
  const groups = new Map();
  for (const answer of state.answers) {
    const key = keyFor(answer.space_id, answer.q_id);
    groups.set(key, [...(groups.get(key) || []), answer]);
  }
  for (const room of state.template.spaces)
    for (const question of state.template.questions) {
      const key = keyFor(room.space_id, question.q_id),
        saved = groups.get(key) || [];
      state.drafts.set(key, {
        answer_item_id: `demo-${room.space_id}-${question.q_id}`,
        space_id: room.space_id,
        q_id: question.q_id,
        selected: "unknown",
        quantity: "",
        input_W: "",
        placement_hint: "",
        power_source: "",
        ...saved[0],
        multiple: saved.length > 1,
      });
    }
}

function renderQuestions() {
  if (!state.template || !state.roomId) return;
  const container = $("questions");
  container.className = "";
  container.innerHTML = state.template.questions
    .map((question) => {
      const row = state.drafts.get(keyFor(state.roomId, question.q_id));
      const disabled =
        state.sample || state.busy || state.checklist || row.multiple;
      const supply = question.q_id === "general_receptacle";
      return `<div class="question-row" data-question="${esc(question.q_id)}"><div class="question-main"><label for="q-${esc(question.q_id)}" title="${esc(question.text)}">${esc(labels[question.q_id] || question.text)}</label><select id="q-${esc(question.q_id)}" data-field="selected" ${disabled ? "disabled" : ""}>${[
        ["unknown", "Chưa rõ"],
        ["not_needed", "Không cần"],
        ["needed", "Cần bố trí"],
      ]
        .map(
          ([v, t]) =>
            `<option value="${v}" ${row.selected === v ? "selected" : ""}>${t}</option>`,
        )
        .join(
          "",
        )}</select></div>${row.multiple ? '<p class="questionnaire-warning">Có nhiều khai báo cho mục này. Bản demo giữ nguyên các khai báo hiện có.</p>' : ""}<div class="question-fields" ${row.selected !== "needed" ? "hidden" : ""}><div><label>Số lượng<input type="number" data-field="quantity" min="1" step="1" value="${esc(row.quantity ?? "")}" placeholder="?" ${disabled ? "disabled" : ""}></label></div>${supply ? '<p class="help">Ổ cắm là điểm cấp điện; không dùng định mức ổ cắm làm công suất tiêu thụ.</p>' : `<div><label>Công suất mỗi thiết bị (W)<input type="number" data-field="input_W" min="0" step="any" value="${esc(row.input_W ?? "")}" placeholder="Chưa biết" ${disabled ? "disabled" : ""}></label></div><div class="wide"><label>Nguồn công suất<input data-field="power_source" value="${esc(row.power_source ?? "")}" placeholder="Ví dụ: nhãn công suất thiết bị" ${disabled ? "disabled" : ""}></label></div>`}<div class="wide"><label>Gợi ý vị trí<input data-field="placement_hint" value="${esc(row.placement_hint ?? "")}" placeholder="Ví dụ: gần bàn làm việc" ${disabled ? "disabled" : ""}></label></div></div></div>`;
    })
    .join("");
}

function renderDrawing() {
  const room = activeRoom(),
    devices = roomDevices();
  $("drawing-room").textContent = room?.name || "Chưa chọn không gian";
  const transform = canvasTransform(state.geometry?.bbox);
  if (!transform) {
    if (state.project)
      $("drawing").innerHTML =
        '<div class="drawing-empty"><span class="empty-graphic">⌑</span><h3>Chưa có hình học để hiển thị</h3><p>Không tìm thấy bounding box phòng.<br>Danh sách thiết bị vẫn có thể xem bên dưới.</p></div>';
    return;
  }
  const { left, top, roomWidth: w, roomHeight: h } = transform;
  const bbox = state.geometry.bbox;
  const markerColors = {
    light: "#c39428",
    outlet: "#007f78",
    switch: "#547eb4",
    other: "#9270b5",
  };
  const marks = devices
    .map((device, index) => {
      const p = device.position;
      if (!p || ![p.x, p.y].every(Number.isFinite)) return "";
      const [x, y] = transform.point(p.x, p.y),
        kind = placementKind(device),
        id = device.proposed_id || device.device_id || String(index);
      const glyph = { light: "L", outlet: "O", switch: "S", other: "E" }[kind];
      return `<g class="device-marker ${state.selected === id ? "selected" : ""}" data-device="${esc(id)}" tabindex="0" role="button" aria-label="${esc(deviceNames[device.device_type] || device.device_type)} ${index + 1}"><title>${esc(id)} · X ${fmt(p.x)}, Y ${fmt(p.y)}, Z ${fmt(p.z)} m</title><circle cx="${x}" cy="${y}" r="15" fill="${markerColors[kind]}" stroke="white" stroke-width="3"/><text x="${x}" y="${y + 4}" text-anchor="middle" font-family="Segoe UI, sans-serif" font-size="11" font-weight="600" fill="white" pointer-events="none">${glyph}</text></g>`;
    })
    .join("");
  $("drawing").innerHTML =
    `<svg viewBox="0 0 660 400" aria-label="Sơ đồ bounding box của phòng với thiết bị đề xuất"><rect x="${left}" y="${top}" width="${w}" height="${h}" fill="#f0f8f7" fill-opacity=".8" stroke="#648d93" stroke-width="4"/><rect x="${left + 7}" y="${top + 7}" width="${Math.max(w - 14, 1)}" height="${Math.max(h - 14, 1)}" fill="none" stroke="#c5dbda" stroke-dasharray="4 4"/><text x="330" y="${top + h / 2 - 8}" text-anchor="middle" fill="#87a6ac" font-size="15" font-family="Segoe UI, sans-serif">${esc(room?.name || "Phòng")}</text><text x="330" y="${top + h / 2 + 14}" text-anchor="middle" fill="#a0b8be" font-size="10" font-family="Segoe UI, sans-serif">${fmt(bbox.max[0] - bbox.min[0])} × ${fmt(bbox.max[1] - bbox.min[1])} m · bounding box</text><path d="M ${left} ${top + h + 23} H ${left + w}" stroke="#a6bbc2"/><text x="330" y="${top + h + 39}" text-anchor="middle" font-size="10" fill="#8fa6af" font-family="monospace">${fmt(bbox.max[0] - bbox.min[0])} m</text><text x="18" y="380" fill="#9cb1ba" font-size="9" font-family="monospace">Gốc model: X ${fmt(bbox.min[0])} · Y ${fmt(bbox.min[1])}</text>${marks}</svg>`;
}

function renderReview() {
  const devices = roomDevices(),
    all = state.review?.proposals || [],
    validation = state.review?.validation_result;
  $("device-count").textContent = state.review ? devices.length : "—";
  $("device-count-note").textContent = state.review
    ? `Phòng này / tổng ${all.length} thiết bị`
    : "Trong phòng đang chọn";
  $("error-count").textContent = validation
    ? (validation.errors || []).length
    : "—";
  $("warning-count").textContent = validation
    ? (validation.warnings || []).length
    : "—";
  $("plan-version").textContent = state.review
    ? `Phiên bản ${state.review.version} · ${all.length} thiết bị toàn phương án`
    : "Chưa có bản nháp";
  $("device-table").innerHTML = devices.length
    ? devices
        .map((device, index) => {
          const id = device.proposed_id || device.device_id || String(index),
            kind = placementKind(device),
            p = device.position || {};
          return `<tr class="device-row ${state.selected === id ? "selected" : ""}" data-device="${esc(id)}"><td><span class="device-label"><i class="legend-dot ${kind}"></i>${esc(deviceNames[device.device_type] || device.device_type)}</span><small>${esc(id)}</small></td><td>${fmt(p.x)} / ${fmt(p.y)} / ${fmt(p.z)}</td><td title="${esc(device.host_id || "")}">${esc(device.host_id ? device.host_id.slice(0, 10) + "…" : "Chưa xác định")}</td></tr>`;
        })
        .join("")
    : '<tr><td colspan="3" class="empty-cell">Chưa có thiết bị đề xuất trong phòng.</td></tr>';
  const issues = [
    ...(validation?.errors || []).map((i) => ({ issue: i, error: true })),
    ...(validation?.warnings || []).map((i) => ({ issue: i, error: false })),
  ];
  $("validation-count").textContent = issues.length;
  $("validation-list").innerHTML = issues.length
    ? issues
        .map(
          ({ issue, error }) =>
            `<div class="validation-issue ${error ? "is-error" : ""}"><strong>${esc(typeof issue === "object" ? issue.code || (error ? "Lỗi" : "Cảnh báo") : error ? "Lỗi" : "Cảnh báo")}</strong>${esc(typeof issue === "object" ? issue.message || JSON.stringify(issue) : issue)}</div>`,
        )
        .join("")
    : `<p class="muted">${validation ? "Không có lỗi hoặc cảnh báo được báo cáo." : "Chưa có kết quả validation."}</p>`;
  $("raw-plan").textContent = state.review
    ? JSON.stringify(state.review, null, 2)
    : "Chưa có phương án.";
  renderDrawing();
  renderProject();
}

async function selectRoom(roomId) {
  state.roomId = roomId;
  state.selected = null;
  state.geometry = null;
  renderQuestions();
  renderReview();
  const request = ++state.geometryRequest;
  if (state.sample) {
    state.geometry = { bbox: { min: [0, 0, 0], max: [6, 4.5, 3] } };
    renderDrawing();
    return;
  }
  $("drawing").innerHTML =
    '<div class="drawing-empty"><h3>Đang đọc hình học phòng…</h3><p>Model IFC lớn có thể cần một chút thời gian.</p></div>';
  try {
    const geometry = await api(
      `/projects/${encodeURIComponent(state.project.project_id)}/spaces/${encodeURIComponent(roomId)}/geometry`,
    );
    if (request !== state.geometryRequest) return;
    state.geometry = geometry;
    renderDrawing();
  } catch (error) {
    if (request === state.geometryRequest) {
      notify(error.message, "error");
      renderDrawing();
    }
  }
}

async function loadProject(project) {
  state.project = project;
  state.template = null;
  state.roomId = null;
  state.geometryRequest++;
  state.drafts.clear();
  state.dirty.clear();
  $("room-select").innerHTML = "<option>Đang đọc danh sách phòng…</option>";
  $("questions").textContent = "Đang đọc bảng hỏi…";
  state.sample = false;
  state.review = null;
  state.geometry = null;
  state.revision = 0;
  state.answers = [];
  state.checklist = false;
  const id = encodeURIComponent(project.project_id);
  renderProject();
  state.template = await api(`/projects/${id}/questionnaire/template`);
  let context;
  try {
    context = await api(
      `/projects/${id}/questionnaire/load-context?include_answers=true`,
    );
  } catch (error) {
    notify(
      `Đã tải dự án, nhưng chưa đọc được bảng hỏi: ${error.message}`,
      "error",
    );
  }
  if (context?.status === "ok") {
    state.revision = context.questionnaire_revision;
    state.answers = context.answers || [];
    state.checklist = !!context.selections;
  }
  hydrateDrafts();
  $("room-select").innerHTML = state.template.spaces
    .map(
      (room) =>
        `<option value="${esc(room.space_id)}">${esc(room.name || room.space_id)}</option>`,
    )
    .join("");
  $("restore-id").value = project.project_id;
  try {
    localStorage.setItem("volt-project-id", project.project_id);
  } catch {}
  renderProject();
  await selectRoom(state.template.spaces[0]?.space_id);
  if (project.plan) await refreshReview();
  if (state.checklist)
    notify(
      "Dự án này dùng checklist. Bản demo chỉ đọc nhu cầu hiện có; chỉnh qua API checklist để giữ nguyên dữ liệu.",
      "info",
    );
}

async function refreshReview() {
  const data = await api(
    `/projects/${encodeURIComponent(state.project.project_id)}/review`,
  );
  state.project = data.state;
  state.review = data.plan;
  $("review-confirm").checked = false;
  renderReview();
}

function logEvent(text) {
  if (!state.events) $("activity-log").replaceChildren();
  const row = document.createElement("div");
  row.className = "log-entry";
  const time = document.createElement("time");
  time.textContent = new Date().toLocaleTimeString("vi-VN");
  row.append(time, document.createTextNode(text));
  $("activity-log").append(row);
  while ($("activity-log").children.length > 100)
    $("activity-log").firstChild.remove();
  state.events++;
  $("activity-count").textContent = `${state.events} sự kiện`;
  $("activity-log").scrollTop = $("activity-log").scrollHeight;
}

function socketProtocols() {
  if (!state.token) return ["volt-demo"];
  const bytes = new TextEncoder().encode(state.token);
  const encoded = btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  return ["volt-demo", `volt-token.${encoded}`];
}

function runAgent() {
  if (state.busy || state.sample || !state.project) return;
  state.busy = true;
  $("review-confirm").checked = false;
  const url = new URL(
    `/projects/${encodeURIComponent(state.project.project_id)}/agent/ws`,
    location.href,
  );
  url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(url, socketProtocols());
  state.socket = socket;
  updateButtons();
  renderQuestions();
  logEvent("Bắt đầu lượt Agent. Đang kết nối…");
  socket.onopen = () => {
    const instruction =
      $("agent-message").value.trim() ||
      "Bố trí thiết bị theo nhu cầu đã khai báo, kiểm tra và lưu phương án nháp. Nêu rõ dữ liệu thiếu và cảnh báo.";
    socket.send(
      JSON.stringify({
        message: `Làm việc với phòng space_id=${state.roomId}. ${instruction} Giữ toàn bộ thiết bị của các phòng khác và các proposed_id hiện có khi lưu lại phương án.`,
        feedback: state.review ? instruction : "",
      }),
    );
  };
  let finished = false;
  socket.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);
      if (data.type === "phase") {
        state.project.phase = data.phase;
        renderProject();
        logEvent(phases[data.phase] || data.phase);
      }
      if (data.type === "message") logEvent(data.text);
      if (data.type === "tool_start") logEvent(`Đang gọi ${data.name}…`);
      if (data.type === "tool_result")
        logEvent(
          data.result?.error
            ? `${data.name}: ${data.result.error.message}`
            : `Hoàn tất ${data.name}${data.result?.status ? ` · ${data.result.status}` : ""}`,
        );
      if (["done", "error", "cancelled"].includes(data.type)) {
        finished = true;
        if (data.type === "done") {
          state.project = data.state;
          notify(
            data.state.phase === "REVIEW_DEVICES"
              ? "Đã lưu bản nháp. Hãy xem phương án và cảnh báo trước khi duyệt."
              : "Agent đã kết thúc lượt. Xem nhật ký để biết dữ liệu cần bổ sung.",
            "info",
          );
        }
        if (data.type === "error")
          notify(
            `Agent gặp lỗi (${data.code}). Xem log ở cửa sổ backend; bản nháp đã lưu vẫn được giữ.`,
            "error",
          );
        if (data.type === "cancelled")
          notify("Đã dừng lượt Agent. Các bản nháp đã lưu được giữ lại.");
        await refreshReview().catch((error) => notify(error.message, "error"));
        socket.close();
      }
    } catch (error) {
      notify(error.message, "error");
    }
  };
  socket.onerror = () =>
    notify(
      "Không kết nối được Agent. Kiểm tra backend, API_TOKEN và cấu hình LLM.",
      "error",
    );
  socket.onclose = () => {
    if (state.socket === socket) {
      state.socket = null;
      state.busy = false;
      renderQuestions();
      updateButtons();
    }
    if (!finished) {
      logEvent(
        "Kết nối đã đóng. Nhấn Cập nhật để xem bản nháp còn lưu trên backend.",
      );
    }
  };
}

async function connect(checkDependencies = false) {
  try {
    const result = await api(
      `/demo/status${checkDependencies ? "?check_dependencies=true" : ""}`,
    );
    state.backend = result.backend_available;
    $("connection-status").textContent = state.backend
      ? "Backend đã kết nối"
      : "Chế độ xem giao diện";
    $("connection-status").className =
      `status-pill ${state.backend ? "success" : "neutral"}`;
    if (!state.backend)
      notify(
        result.message ||
          "Chạy backend để sử dụng dữ liệu thật. Bạn có thể xem dữ liệu mẫu bên dưới.",
      );
    else if (!result.llm_configured)
      notify(
        "Backend đã kết nối. Hãy khai báo LLM_MODEL trong .env và khởi động lại trước khi chạy Agent.",
      );
    if (checkDependencies) {
      const checks = result.dependencies?.checks || [];
      $("readiness-list").replaceChildren();
      for (const check of checks) {
        const line = document.createElement("div");
        line.textContent = `${check.status === "ok" ? "✓" : "!"} ${check.resource}${check.message ? `: ${check.message}` : ""}`;
        $("readiness-list").append(line);
      }
      if (!checks.length)
        $("readiness-list").textContent =
          result.message || "Đã kết nối backend.";
      if (result.dependencies?.status === "ok") {
        notify(
          "Các bảng và bucket đã sẵn sàng cho truy cập đọc. Có thể bắt đầu upload.",
          "success",
        );
        $("settings-dialog").close();
      }
    }
  } catch (error) {
    state.backend = false;
    $("connection-status").textContent = "Chưa kết nối";
    $("connection-status").className = "status-pill error";
    notify(error.message, "error");
    $("readiness-list").textContent = error.message;
  }
  updateButtons();
}

function showSample() {
  state.sample = true;
  state.geometryRequest++;
  state.project = {
    project_id: "sample-only",
    name: "Nhà phố · phòng ngủ mẫu",
    phase: "REVIEW_DEVICES",
  };
  state.template = {
    spaces: [{ space_id: "sample-bedroom", name: "Phòng ngủ 01" }],
    questions: Object.keys(labels).map((q_id) => ({
      q_id,
      text: labels[q_id],
    })),
  };
  state.roomId = "sample-bedroom";
  state.answers = [];
  state.revision = 1;
  hydrateDrafts();
  for (const [q_id, quantity] of [
    ["lighting", 1],
    ["general_receptacle", 3],
    ["air_conditioning", 1],
  ])
    Object.assign(state.drafts.get(keyFor(state.roomId, q_id)), {
      selected: "needed",
      quantity,
      input_W: "",
    });
  state.geometry = { bbox: { min: [0, 0, 0], max: [6, 4.5, 3] } };
  state.review = {
    plan_id: "sample-only",
    version: 1,
    review_status: "pending_review",
    proposals: [
      {
        proposed_id: "light-01",
        device_type: "light",
        space_id: state.roomId,
        host_id: "sample-ceiling",
        position: { x: 3, y: 2.25, z: 2.9 },
      },
      {
        proposed_id: "outlet-01",
        device_type: "outlet",
        space_id: state.roomId,
        host_id: "sample-wall-left",
        position: { x: 0.15, y: 3.5, z: 0.3 },
      },
      {
        proposed_id: "outlet-02",
        device_type: "outlet",
        space_id: state.roomId,
        host_id: "sample-wall-right",
        position: { x: 5.85, y: 3.5, z: 0.3 },
      },
      {
        proposed_id: "outlet-03",
        device_type: "outlet",
        space_id: state.roomId,
        host_id: "sample-wall-right",
        position: { x: 5.85, y: 1, z: 0.3 },
      },
      {
        proposed_id: "switch-01",
        device_type: "switch",
        space_id: state.roomId,
        host_id: "sample-wall-bottom",
        position: { x: 1, y: 0.15, z: 1.2 },
      },
    ],
    validation_result: {
      valid: true,
      complete: false,
      errors: [],
      warnings: [
        {
          code: "SAMPLE_DATA",
          message:
            "Dữ liệu minh họa giao diện, không phải kết quả của Agent hoặc IFC thật.",
        },
        {
          code: "WARN_APPROXIMATE_GEOMETRY",
          message:
            "Sơ đồ là bounding box. Cần kiểm tra bề mặt gắn, cửa và vật cản trong mô hình thực tế.",
        },
      ],
    },
  };
  $("room-select").innerHTML =
    '<option value="sample-bedroom">Phòng ngủ 01 · dữ liệu mẫu</option>';
  $("review-confirm").checked = false;
  $("notice").hidden = true;
  state.selected = null;
  renderQuestions();
  renderReview();
}

function init() {
  $("settings-button").onclick = () => $("settings-dialog").showModal();
  $("close-settings").onclick = () => $("settings-dialog").close();
  $("settings-form").onsubmit = async (event) => {
    event.preventDefault();
    state.token = $("api-token").value.trim();
    await connect(true);
  };
  $("sample-button").onclick = showSample;
  $("exit-sample").onclick = () => {
    state.sample = false;
    state.project = null;
    state.template = null;
    state.review = null;
    state.dirty.clear();
    location.reload();
  };
  $("ifc-file").onchange = () => {
    $("file-name").textContent =
      $("ifc-file").files[0]?.name || "Chọn mô hình kiến trúc IFC";
  };
  for (const eventName of ["dragenter", "dragover"])
    $("dropzone").addEventListener(eventName, () =>
      $("dropzone").classList.add("dragging"),
    );
  for (const eventName of ["dragleave", "drop"])
    $("dropzone").addEventListener(eventName, () =>
      $("dropzone").classList.remove("dragging"),
    );
  $("upload-form").onsubmit = (event) => {
    event.preventDefault();
    busy(async () => {
      const file = $("ifc-file").files[0];
      if (!file || !file.name.toLowerCase().endsWith(".ifc"))
        throw new Error("Chọn tệp .ifc để tiếp tục.");
      if (file.size > 250 * 1024 * 1024)
        throw new Error("Tệp vượt quá giới hạn 250 MB.");
      const body = new FormData();
      body.append("name", $("project-name").value.trim());
      body.append("file", file);
      const project = await api("/projects", { method: "POST", body });
      notify("Upload thành công. Đang đọc danh sách phòng…", "success");
      await loadProject(project);
    });
  };
  $("restore-button").onclick = () =>
    busy(async () => {
      const id = $("restore-id").value.trim();
      if (!id) throw new Error("Nhập ID dự án cần mở.");
      await loadProject(await api(`/projects/${encodeURIComponent(id)}`));
    });
  $("room-select").onchange = (event) => selectRoom(event.target.value);
  $("questions").addEventListener("input", (event) => {
    const field = event.target.dataset.field,
      q = event.target.closest("[data-question]")?.dataset.question;
    if (!field || !q || state.sample || state.busy) return;
    const key = keyFor(state.roomId, q),
      row = state.drafts.get(key);
    row[field] = event.target.value;
    state.dirty.add(key);
    $("review-confirm").checked = false;
    if (field === "selected") renderQuestions();
    renderProject();
  });
  $("questionnaire-form").onsubmit = (event) => {
    event.preventDefault();
    busy(async () => {
      const answers = mergeAnswers(state.answers, state.drafts, state.dirty);
      const result = await api(
        `/projects/${encodeURIComponent(state.project.project_id)}/questionnaire/submit`,
        {
          method: "POST",
          body: {
            template_id: state.template.template_id,
            template_version: state.template.template_version,
            model_revision: state.template.model_revision,
            expected_revision: state.revision,
            answers,
          },
        },
      );
      state.answers = answers;
      state.revision = result.questionnaire_revision;
      state.dirty.clear();
      renderProject();
      notify(
        `Đã lưu bảng hỏi revision ${state.revision}. Có thể chạy Agent cho phòng đang chọn.`,
        "success",
      );
    });
  };
  $("run-agent").onclick = runAgent;
  $("cancel-agent").onclick = () => {
    if (state.socket?.readyState === WebSocket.OPEN) {
      state.socket.send(JSON.stringify({ action: "cancel" }));
      logEvent("Đã gửi yêu cầu dừng. Đợi tool đang chạy hoàn tất.");
    }
  };
  $("refresh-review").onclick = () => busy(refreshReview);
  $("review-confirm").onchange = updateButtons;
  $("approve-button").onclick = () =>
    busy(async () => {
      if (!$("review-confirm").checked)
        throw new Error("Vui lòng xác nhận đã xem phương án và cảnh báo.");
      state.project = await api(
        `/projects/${encodeURIComponent(state.project.project_id)}/review/approve`,
        {
          method: "POST",
          body: {
            plan_id: state.review.plan_id,
            version: state.review.version,
            reviewer_id: crypto.randomUUID(),
          },
        },
      );
      await refreshReview();
      notify(
        "Đã duyệt và ghi IFC thành công. Bạn có thể tải mô hình kết quả.",
        "success",
      );
    });
  $("download-button").onclick = () =>
    busy(async () => {
      const blob = await api(
          `/projects/${encodeURIComponent(state.project.project_id)}/ifc`,
          { blob: true },
        ),
        url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = `${state.project.project_id}-electrical.ifc`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    });
  for (const id of ["drawing", "device-table"]) {
    const select = (event) => {
      const node = event.target.closest("[data-device]");
      if (node) {
        state.selected = node.dataset.device;
        renderReview();
      }
    };
    $(id).addEventListener("click", select);
    $(id).addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        if (event.target.closest("[data-device]")) {
          event.preventDefault();
          select(event);
        }
      }
    });
  }
  try {
    $("restore-id").value = localStorage.getItem("volt-project-id") || "";
  } catch {}
  connect();
  updateButtons();
}

if (typeof document !== "undefined") init();
