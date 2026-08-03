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
  try { d = await (await fetch("/api/status")).json(); } catch (e) { return; }
  const box = $("devices");
  box.textContent = "";
  for (const dev of (d.devices || [])) {
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
  try { runs = await (await fetch("/api/runs?" + params.toString())).json(); } catch (e) { return; }
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
  const operator = prompt("操作人？");
  if (operator === null) return;
  const project = prompt("项目 / 实验 tag？");
  if (project === null) return;
  try {
    await fetch("/api/runs/" + runId + "/tag", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({operator: operator || "", project_tag: project || "", experiment_tag: "", remark: ""})
    });
    loadRuns();
  } catch (e) {
    alert("补录失败，请重试");
  }
}

pollStatus();
loadRuns();
setInterval(pollStatus, 1000);
setInterval(loadRuns, 3000);
