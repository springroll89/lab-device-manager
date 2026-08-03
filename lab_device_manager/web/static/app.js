const $ = (id) => document.getElementById(id);
const fmtDur = (ms) => (ms == null) ? "-" : (ms >= 60000 ? (ms/60000).toFixed(1)+" 分" : (ms/1000).toFixed(0)+" 秒");
const fmtTs = (ms) => (ms == null) ? "-" : new Date(ms).toLocaleString();
const STATE_LABELS = {
  running: "运行中",
  stopped: "已停止",
  paused: "已暂停",
  alarm: "异常",
  offline: "离线",
};
const COMMUNICATION_LABELS = {
  communicating: "通讯正常",
  data_interrupted: "数据中断",
  gateway_offline: "网关离线",
  instrument_unresponsive: "仪器无响应",
  communication_error: "通讯异常",
};
const DEVICE_TYPE_LABELS = {
  tyd02: "注射泵",
  stirrer: "搅拌器",
  viscometer: "粘度计",
  whd46: "温湿度控制器",
};
const DASHBOARD_VIEW_KEY = "puricore-dashboard-view";
let dashboardSession = null;

async function dashboardJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function fmtValue(value, unit, decimals = 1) {
  const number = Number(value);
  if (value == null || value === "" || !Number.isFinite(number)) return "—";
  return `${number.toFixed(decimals)}${unit ? ` ${unit}` : ""}`;
}

function average(values) {
  const valid = values.map(Number).filter(Number.isFinite);
  if (!valid.length) return null;
  return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}

function topologyTone(code) {
  if (["communicating", "online"].includes(code)) return "online";
  if (["data_interrupted", "degraded"].includes(code)) return "warning";
  if (["instrument_unresponsive", "communication_error"].includes(code)) {
    return "error";
  }
  return "offline";
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

function setDashboardView(view, persist = true) {
  const selected = view === "list" ? "list" : "topology";
  $("topologyView").hidden = selected !== "topology";
  $("deviceListView").hidden = selected !== "list";
  $("topologyMode").setAttribute(
    "aria-selected", String(selected === "topology")
  );
  $("listMode").setAttribute("aria-selected", String(selected === "list"));
  if (persist) {
    try { localStorage.setItem(DASHBOARD_VIEW_KEY, selected); } catch (_) {}
  }
}

function wireDashboardView() {
  let preferred = "topology";
  try { preferred = localStorage.getItem(DASHBOARD_VIEW_KEY) || preferred; } catch (_) {}
  setDashboardView(preferred, false);
  $("topologyMode").addEventListener("click", () => setDashboardView("topology"));
  $("listMode").addEventListener("click", () => setDashboardView("list"));
}

function topologyStatus(code, label) {
  const tone = topologyTone(code);
  const badge = element("span", `topology-status is-${tone}`);
  badge.append(
    element("i", "topology-status-dot"),
    document.createTextNode(label || COMMUNICATION_LABELS[code] || "状态未知")
  );
  return badge;
}

function infrastructureNode(kind, title, subtitle, status) {
  const node = element("div", `topology-node topology-${kind}`);
  const icon = element("div", "topology-node-icon");
  icon.setAttribute("aria-hidden", "true");
  const copy = element("div", "topology-node-copy");
  copy.append(
    element("strong", "", title),
    element("span", "", subtitle || "")
  );
  node.append(icon, copy);
  if (status) node.append(topologyStatus(status.code, status.label));
  return node;
}

function deviceEndpoint(device) {
  const connection = device.connection || {};
  if (connection.transport === "tcp") {
    const channel = connection.gateway_port
      ? `串口 ${connection.gateway_port} · `
      : "";
    return `${channel}TCP ${connection.tcp_port || "—"}`;
  }
  const port = String(connection.serial_port || "").split("/").pop();
  return port ? `本机串口 · ${port}` : "本机串口待配置";
}

function buildTopologyDevice(device) {
  const communication = device.communication || {
    code: "gateway_offline",
    label: "网关离线",
  };
  const latest = device.latest || {};
  const metrics = latest.metrics || {};
  const primary = dashboardMetrics(device, latest, metrics)[0] || ["实时数据", "—"];
  const node = element(
    "button",
    `topology-device is-${topologyTone(communication.code)}`
  );
  node.type = "button";
  node.dataset.deviceId = device.id;
  node.setAttribute(
    "aria-label",
    `${device.alias || device.name}，${communication.label || "状态未知"}`
  );
  node.addEventListener("click", () => {
    location.href = device.type === "whd46"
      ? `/sensor/${device.id}`
      : `/device/${device.id}`;
  });
  const header = element("div", "topology-device-head");
  const title = element("div");
  title.append(
    element("strong", "", device.alias || device.name),
    element("span", "", DEVICE_TYPE_LABELS[device.type] || device.type)
  );
  header.append(title, topologyStatus(communication.code, communication.label));
  const endpoint = element("div", "topology-device-endpoint", deviceEndpoint(device));
  const reading = element("div", "topology-device-reading");
  reading.append(
    element("span", "", primary[0]),
    element("strong", "", primary[1])
  );
  node.append(header, endpoint, reading);
  return node;
}

function topologyConnector(tone = "online") {
  const connector = element("div", `topology-connector is-${tone}`);
  connector.setAttribute("aria-hidden", "true");
  return connector;
}

function renderTopologySummary(devices, topology) {
  const host = $("topologySummary");
  host.textContent = "";
  const online = devices.filter(
    device => device.communication?.code === "communicating"
  ).length;
  const abnormal = devices.length - online;
  const values = [
    ["设备", devices.length],
    ["通讯正常", online],
    ["需关注", abnormal],
    ["TCP 网关", topology?.gateways?.length || 0],
  ];
  for (const [label, value] of values) {
    const item = element("div", "topology-summary-item");
    item.append(
      element("span", "", label),
      element("strong", "", String(value))
    );
    host.appendChild(item);
  }
}

function buildTopology(topology, devices) {
  const graph = $("topologyGraph");
  graph.textContent = "";
  const byId = new Map(devices.map(device => [device.id, device]));
  renderTopologySummary(devices, topology);
  if (!topology) {
    graph.appendChild(element("p", "topology-empty", "拓扑信息暂不可用。"));
    return;
  }

  const backbone = element("div", "topology-backbone");
  backbone.appendChild(infrastructureNode(
    "collector",
    topology.collector?.name || "后台采集主机",
    "设备采集与数据服务",
    topology.collector?.status
  ));
  backbone.appendChild(topologyConnector("online"));
  backbone.appendChild(infrastructureNode(
    "network",
    topology.network?.name || "实验室网络",
    topology.network?.model || "交换与汇聚层"
  ));
  graph.appendChild(backbone);

  const branches = element("div", "topology-branches");
  for (const gateway of topology.gateways || []) {
    const tone = topologyTone(gateway.status?.code);
    const branch = element("section", `topology-gateway is-${tone}`);
    branch.appendChild(topologyConnector(tone));
    branch.appendChild(infrastructureNode(
      "gateway",
      gateway.name,
      `${gateway.model || "串口网关"} · ${gateway.host}`,
      gateway.status
    ));
    const deviceGrid = element("div", "topology-device-grid");
    for (const deviceId of gateway.device_ids || []) {
      const device = byId.get(deviceId);
      if (device) deviceGrid.appendChild(buildTopologyDevice(device));
    }
    branch.appendChild(deviceGrid);
    branches.appendChild(branch);
  }

  const directDevices = (topology.direct_device_ids || [])
    .map(deviceId => byId.get(deviceId))
    .filter(Boolean);
  if (directDevices.length) {
    const branch = element("section", "topology-gateway topology-direct is-warning");
    branch.appendChild(topologyConnector("warning"));
    branch.appendChild(infrastructureNode(
      "gateway", "本机串口直连", "未经过 TCP 串口网关"
    ));
    const deviceGrid = element("div", "topology-device-grid");
    directDevices.forEach(device => {
      deviceGrid.appendChild(buildTopologyDevice(device));
    });
    branch.appendChild(deviceGrid);
    branches.appendChild(branch);
  }
  graph.appendChild(branches);
}

function dashboardMetrics(dev, latest, metrics) {
  if (dev.type === "stirrer") {
    return [
      ["实际温度", fmtValue(latest.temp_c, "℃")],
      ["实际转速", fmtValue(metrics.speed ?? latest.flow_rpm, "rpm", 0)],
      ["设定温度", fmtValue(metrics.set_temp, "℃")],
      ["设定转速", fmtValue(metrics.set_speed, "rpm", 0)],
    ];
  }
  if (dev.type === "tyd02") {
    return [
      ["加酸速率", fmtValue(metrics.inject_rate, "mL/min", 2)],
      ["累计加入量", fmtValue(latest.acc_volume, latest.acc_unit || "mL", 2)],
      ["目标加入量", fmtValue(metrics.target_volume, metrics.target_unit || "mL", 2)],
      ["运行进度", fmtValue(latest.progress_pct, "%")],
    ];
  }
  if (dev.type === "viscometer") {
    return [
      ["粘度", fmtValue(metrics.viscosity_mPas, "mPa·s", 2)],
      ["样品温度", fmtValue(latest.temp_c, "℃")],
      ["扭矩", fmtValue(metrics.torque_pct, "%")],
      ["剪切速率", fmtValue(metrics.shear_rate_1s, "1/s")],
    ];
  }
  if (dev.type === "whd46") {
    const channels = metrics.channels || [];
    const temperatures = channels.length
      ? channels.map(channel => channel.temp)
      : [metrics.ch1_temp_c, metrics.ch2_temp_c, metrics.ch3_temp_c];
    const humidity = channels.length
      ? channels.map(channel => channel.humid)
      : [metrics.ch1_humid_rh, metrics.ch2_humid_rh, metrics.ch3_humid_rh];
    return [
      ["平均温度", fmtValue(latest.temp_c ?? average(temperatures), "℃")],
      ["平均湿度", fmtValue(metrics.avg_humid_rh ?? average(humidity), "%RH")],
      ["1 通道温度", fmtValue(temperatures[0], "℃")],
      ["1 通道湿度", fmtValue(humidity[0], "%RH")],
    ];
  }
  return [
    ["工作模式", latest.work_mode || "—"],
    ["温度", fmtValue(latest.temp_c, "℃")],
    ["累计量", fmtValue(latest.acc_volume, latest.acc_unit || "")],
    ["进度", fmtValue(latest.progress_pct, "%")],
  ];
}

function buildDeviceCard(dev, latest, metrics) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.deviceId = dev.id;
  card.onclick = () => {
    location.href = dev.type === "whd46" ? `/sensor/${dev.id}` : `/device/${dev.id}`;
  };

  const head = document.createElement("div");
  head.className = "card-head";
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = dev.alias || dev.name;
  const communication = dev.communication || {
    code: "gateway_offline",
    label: COMMUNICATION_LABELS.gateway_offline,
  };
  const communicationBadge = document.createElement("div");
  communicationBadge.className =
    `communication-badge comm-${communication.code}`;
  communicationBadge.textContent = communication.label
    || COMMUNICATION_LABELS[communication.code]
    || COMMUNICATION_LABELS.communication_error;
  head.append(name, communicationBadge);

  const statusGrid = document.createElement("div");
  statusGrid.className = "card-status-grid";
  const communicationRow = document.createElement("div");
  communicationRow.className = "card-status-row";
  const communicationLabel = document.createElement("span");
  communicationLabel.textContent = "通讯状态";
  const communicationValue = document.createElement("strong");
  communicationValue.className = `comm-${communication.code}`;
  communicationValue.textContent = communicationBadge.textContent;
  communicationRow.append(communicationLabel, communicationValue);

  const operationRow = document.createElement("div");
  operationRow.className = "card-status-row";
  const operationLabel = document.createElement("span");
  operationLabel.textContent = "运行状态";
  const operationValue = document.createElement("strong");
  const operationKnown = communication.code === "communicating";
  operationValue.className = operationKnown
    ? `s-${latest.state || "stopped"}`
    : "s-offline";
  operationValue.textContent = operationKnown
    ? (STATE_LABELS[latest.state] || latest.state || "待机")
    : "状态未知";
  operationRow.append(operationLabel, operationValue);
  statusGrid.append(communicationRow, operationRow);

  const metricGrid = document.createElement("div");
  metricGrid.className = "card-metrics";
  for (const [label, value] of dashboardMetrics(dev, latest, metrics)) {
    const item = document.createElement("div");
    item.className = "card-metric";
    const key = document.createElement("span");
    key.textContent = label;
    const reading = document.createElement("strong");
    reading.textContent = value;
    item.append(key, reading);
    metricGrid.appendChild(item);
  }

  const footer = document.createElement("div");
  footer.className = "card-footer";
  const mode = document.createElement("span");
  mode.textContent = latest.work_mode || dev.type;
  const updated = document.createElement("span");
  updated.textContent = `更新 ${fmtTs(latest.ts_ms)}`;
  footer.append(mode, updated);
  card.append(head, statusGrid, metricGrid, footer);
  return card;
}

async function pollStatus() {
  let d;
  try { d = await dashboardJson("/api/status"); } catch (e) { return; }
  const box = $("devices");
  box.textContent = "";
  const devices = d.devices || [];
  buildTopology(d.topology, devices);
  for (const dev of devices) {
    const L = dev.latest || {};
    const M = L.metrics || {};
    box.appendChild(buildDeviceCard(dev, L, M));
  }
}

async function loadRuns() {
  const params = new URLSearchParams();
  params.set("limit", "30");
  const project = $("fProject").value.trim();
  const operator = $("fOperator").value.trim();
  const status = $("fStatus").value;
  if (project) params.set("project_tag", project);
  if (operator) params.set("operator", operator);
  if (status) params.set("end_status", status);
  let runs = [];
  try { runs = await dashboardJson("/api/runs?" + params.toString()); } catch (e) { return; }
  const tb = $("runs").querySelector("tbody");
  tb.textContent = "";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.onclick = () => { location.href = "/run/" + r.id; };
    const cell = (txt, cls) => { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = (txt == null || txt === "") ? "-" : txt; return td; };
    tr.appendChild(cell(r.started_display));
    tr.appendChild(cell(fmtTs(r.ended_ms)));
    tr.appendChild(cell(r.device_id));
    tr.appendChild(cell(fmtDur(r.duration_ms)));
    const statusCls = "s-" + (r.end_status === "alarm_abort" ? "alarm" : (r.end_status === "completed" ? "running" : "stopped"));
    tr.appendChild(cell(r.end_status, statusCls));
    tr.appendChild(cell((r.result_acc_volume == null ? "-" : r.result_acc_volume) + " " + (r.result_acc_unit || "")));
    tr.appendChild(cell(r.operator));
    tr.appendChild(cell(r.project_tag));
    const act = document.createElement("td");
    if (!r.tagged) {
      const btn = document.createElement("button");
      btn.textContent = "补录";
      btn.onclick = (ev) => { ev.stopPropagation(); tagRun(r.id); };
      act.appendChild(btn);
    } else {
      act.textContent = "✓";
    }
    tr.appendChild(act);
    tb.appendChild(tr);
  }
}

function exportExcel() {
  const params = new URLSearchParams();
  const project = $("fProject").value.trim();
  const operator = $("fOperator").value.trim();
  const status = $("fStatus").value;
  if (project) params.set("project_tag", project);
  if (operator) params.set("operator", operator);
  if (status) params.set("end_status", status);
  window.location = "/api/runs/export.xlsx?" + params.toString();
}

async function tagRun(runId) {
  const project = prompt("项目 / 实验 tag？");
  if (project === null) return;
  try {
    await dashboardJson("/api/runs/" + runId + "/tag", {
      method: "POST", headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": dashboardSession?.csrf_token || ""
      },
      body: JSON.stringify({project_tag: project || "", experiment_tag: "", remark: ""})
    });
    await loadRuns();
  } catch (e) {
    alert("补录失败，请重试");
  }
}

async function repeatAfter(task, delayMs) {
  try {
    await task();
  } finally {
    window.setTimeout(() => repeatAfter(task, delayMs), delayMs);
  }
}

async function startDashboard() {
  wireDashboardView();
  try {
    dashboardSession = await dashboardJson("/api/session");
  } catch (_) {
    return;
  }
  repeatAfter(pollStatus, 1000);
  repeatAfter(loadRuns, 3000);
}

startDashboard();
