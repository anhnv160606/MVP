const SVG_NS = "http://www.w3.org/2000/svg";
const ALLOWED_TAGS = new Set([
  "svg", "title", "desc", "defs", "marker", "path", "rect", "line", "g",
  "text", "circle",
]);
const ALLOWED_ATTRIBUTES = new Set([
  "xmlns", "viewBox", "width", "height", "role", "aria-label", "class",
  "id", "data-entity-type", "data-entity-id", "tabindex", "d", "markerWidth",
  "markerHeight", "refX", "refY", "orient", "fill", "stroke", "rx", "x",
  "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "text-anchor",
  "marker-end",
]);
const EXPORTED_SVG_STYLE = `
.sld-background{fill:#f8fbfc}
.sld-title{fill:#31515c;font:700 15px Arial,sans-serif;letter-spacing:1px}
.sld-subtitle,.sld-panel-meta,.sld-panel-load,.sld-circuit-meta,.sld-empty-label{fill:#80959e;font:10px Arial,sans-serif}
.sld-panel-card{fill:#fff;stroke:#d4e2e5;stroke-width:1.5}
.sld-panel-name{fill:#31515c;font:650 14px Arial,sans-serif}
.sld-wire,.sld-feeder-wire{fill:none;stroke:#6e8790;stroke-width:1.8}
.sld-busbar{stroke:#007f78;stroke-width:4}
.sld-protected-busbar{stroke-width:2.5}
.sld-breaker-box,.sld-protection-box{fill:#f7fbfa;stroke:#4d7e7d;stroke-width:1.4}
.sld-main-breaker{fill:#eaf6f3;stroke-width:1.8}
.sld-breaker-label,.sld-protection-label{fill:#365a63;font:9px Arial,sans-serif}
.sld-circuit-label{fill:#294a55;font:600 10px Arial,sans-serif}
.sld-feeder-label{fill:#5f7881;font:9px Arial,sans-serif}
.sld-device-dot{fill:#d09b35;stroke:#fff;stroke-width:1}
`;

function sanitizeSvg(source) {
  if (typeof source !== "string" || !source.trim())
    throw new Error("Không có SVG để hiển thị.");
  const parsed = new DOMParser().parseFromString(source, "image/svg+xml");
  const sourceRoot = parsed.documentElement;
  if (
    sourceRoot.localName !== "svg" ||
    sourceRoot.namespaceURI !== SVG_NS ||
    parsed.querySelector("parsererror")
  )
    throw new Error("SVG không hợp lệ.");

  const copy = (node) => {
    if (node.nodeType === Node.TEXT_NODE)
      return document.createTextNode(node.nodeValue || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    if (node.namespaceURI !== SVG_NS || !ALLOWED_TAGS.has(node.localName))
      return null;
    const result = document.createElementNS(SVG_NS, node.localName);
    for (const attribute of node.attributes) {
      if (!ALLOWED_ATTRIBUTES.has(attribute.name)) continue;
      const value = attribute.value;
      if (/javascript:|<|>|\bon\w+\s*=/i.test(value)) continue;
      if (attribute.name === "marker-end" && !/^url\(#sld-arrow\)$/.test(value))
        continue;
      result.setAttribute(attribute.name, value);
    }
    for (const child of node.childNodes) {
      const safeChild = copy(child);
      if (safeChild) result.append(safeChild);
    }
    return result;
  };
  return copy(sourceRoot);
}

function detailFor(indexEntry, sldData) {
  if (!indexEntry || !sldData) return null;
  return sldData[indexEntry.entity_type]?.[indexEntry.entity_id] || null;
}

function showDetails(container, indexEntry, data) {
  container.replaceChildren();
  if (!indexEntry) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Chọn tủ, feeder, mạch hoặc thiết bị trên sơ đồ để xem thông tin.";
    container.append(empty);
    return;
  }
  const title = document.createElement("h3");
  title.textContent = data?.name || data?.catalog_id || indexEntry.entity_id;
  const kind = document.createElement("p");
  kind.className = "sld-detail-kind";
  kind.textContent = `${indexEntry.entity_type} · ${indexEntry.entity_id}`;
  const body = document.createElement("pre");
  body.textContent = JSON.stringify(data || indexEntry, null, 2);
  container.append(title, kind, body);
}

export function mountSldViewer(container, detailContainer, artifact, onSelect = () => {}) {
  if (!container || !detailContainer) throw new Error("Thiếu vùng hiển thị SLD.");
  if (!artifact || !artifact.element_index || !artifact.sld_data)
    throw new Error("Artifact SLD thiếu element_index hoặc sld_data.");

  const svg = sanitizeSvg(artifact.svg);
  let selected = null;
  const abort = new AbortController();
  container.replaceChildren(svg);
  showDetails(detailContainer, null, null);

  const pick = (target) => {
    const element = target.closest?.("[data-entity-type][data-entity-id]");
    if (!element || !container.contains(element)) return;
    const entry = artifact.element_index[element.id];
    if (!entry) return;
    selected = { type: entry.entity_type, id: entry.entity_id };
    for (const node of svg.querySelectorAll(".sld-entity")) {
      const active =
        node.dataset.entityType === selected.type &&
        node.dataset.entityId === selected.id;
      node.classList.toggle("is-selected", active);
      node.setAttribute("aria-pressed", String(active));
    }
    showDetails(detailContainer, entry, detailFor(entry, artifact.sld_data));
    onSelect(entry, detailFor(entry, artifact.sld_data));
  };

  container.addEventListener("click", (event) => pick(event.target), {
    signal: abort.signal,
  });
  container.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const element = event.target.closest?.("[data-entity-type][data-entity-id]");
    if (!element) return;
    event.preventDefault();
    pick(element);
  }, { signal: abort.signal });

  return {
    selectEntity(type, id) {
      const found = [...svg.querySelectorAll(".sld-entity")].find(
        (node) => node.dataset.entityType === type && node.dataset.entityId === id,
      );
      if (found) pick(found);
      return Boolean(found);
    },
    selectDevice(deviceId) {
      const direct = [...svg.querySelectorAll(".sld-entity")].find(
        (node) =>
          node.dataset.entityType === "device" &&
          node.dataset.entityId === deviceId,
      );
      if (direct) {
        pick(direct);
        return true;
      }
      const circuit = [...svg.querySelectorAll('.sld-entity[data-entity-type="circuit"]')].find(
        (node) => {
          const data = artifact.sld_data.circuit?.[node.dataset.entityId];
          const ids = [
            ...(data?.device_ids || []),
            ...(data?.devices || []).map((device) => device.device_id || device.id),
          ];
          return ids.includes(deviceId);
        },
      );
      if (circuit) pick(circuit);
      return Boolean(circuit);
    },
    downloadSvg() {
      const staticSvg = svg.cloneNode(true);
      const style = document.createElementNS(SVG_NS, "style");
      style.textContent = EXPORTED_SVG_STYLE;
      staticSvg.insertBefore(style, staticSvg.firstChild);
      for (const node of staticSvg.querySelectorAll("[tabindex], [role], [data-entity-type], [data-entity-id]")) {
        node.removeAttribute("tabindex");
        node.removeAttribute("role");
        node.removeAttribute("data-entity-type");
        node.removeAttribute("data-entity-id");
        node.removeAttribute("aria-pressed");
      }
      for (const node of staticSvg.querySelectorAll(".is-selected"))
        node.classList.remove("is-selected");
      const xml = new XMLSerializer().serializeToString(staticSvg);
      const blob = new Blob([xml], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `sld-${String(artifact.plan_id || "schedule").replace(/[^a-z0-9_-]/gi, "_")}-v${artifact.version || 1}.svg`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    },
    getSelected() {
      return selected;
    },
    destroy() {
      abort.abort();
      container.replaceChildren();
      detailContainer.replaceChildren();
    },
  };
}
