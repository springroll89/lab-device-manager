// Device-detail page. All rendering is DOM-only (textContent) — no innerHTML
// with server data, so device/run fields cannot inject markup.
const $ = (id) => document.getElementById(id);
const fmtDur = (ms) => (ms == null) ? "-" : (ms >= 60000 ? (ms/60000).toFixed(1)+" 分" : (ms/1000).toFixed(0)+" 秒");
const fmtTs = (ms) => (ms == null) ? "-" : new Date(ms).toLocaleString();
const orDash = (v) => ((v == null || v === "") ? "-" : v);
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

// device id comes from the URL path: /device/<id>
const DEVICE_ID = Number(location.pathname.split("/").pop());

function kv(k, v) {
  const wrap = document.createElement("div");
  const kk = document.createElement("span"); kk.className = "k"; kk.textContent = k;
  const vv = document.createElement("span"); vv.className = "val"; vv.textContent = orDash(v);
  wrap.append(kk, vv);
  return wrap;
}

function fmtValue(value, unit, decimals = 1) {
  const number = Number(value);
  if (value == null || value === "" || !Number.isFinite(number)) return "—";
  return `${number.toFixed(decimals)}${unit ? ` ${unit}` : ""}`;
}

function deviceReadings(dev, latest, metrics) {
  if (dev.type === "stirrer") {
    return [
      ["实际温度", fmtValue(latest.temp_c, "℃")],
      ["实际转速", fmtValue(metrics.speed ?? latest.flow_rpm, "rpm", 0)],
      ["设定转速", fmtValue(metrics.set_speed, "rpm", 0)],
    ];
  }
  if (dev.type === "viscometer") {
    return [
      ["粘度", fmtValue(metrics.viscosity_mPas, "mPa·s", 2)],
      ["样品温度", fmtValue(latest.temp_c, "℃")],
      ["扭矩", fmtValue(metrics.torque_pct, "%")],
    ];
  }
  return [
    ["加酸速率", fmtValue(metrics.inject_rate, "mL/min", 2)],
    ["累计加入量", fmtValue(latest.acc_volume, latest.acc_unit || "mL", 2)],
    ["运行进度", fmtValue(latest.progress_pct, "%")],
  ];
}

function renderStatus(dev, latest, communication) {
  const box = $("status"); box.textContent = "";
  const L = latest || {};
  const M = L.metrics || {};
  const comm = communication || {
    code: "gateway_offline",
    label: COMMUNICATION_LABELS.gateway_offline,
  };
  const operationKnown = comm.code === "communicating";
  const readings = [
    ["通讯状态", comm.label || COMMUNICATION_LABELS[comm.code], "communication"],
    [
      "运行状态",
      operationKnown
        ? (STATE_LABELS[L.state] || L.state || "待机")
        : "状态未知",
      operationKnown ? "state" : "unknown",
    ],
    ...deviceReadings(dev, L, M),
  ];
  for (const [label, value, kind] of readings) {
    const card = document.createElement("div");
    card.className = "card reading-card";
    const key = document.createElement("div");
    key.className = "k";
    key.textContent = label;
    const reading = document.createElement("div");
    reading.className = kind === "communication"
      ? `v comm-${comm.code}`
      : `v${kind === "state" ? ` s-${L.state || "stopped"}` : kind === "unknown" ? " s-offline" : ""}`;
    reading.textContent = value;
    const updated = document.createElement("div");
    updated.className = "reading-updated";
    updated.textContent = kind === "communication"
      ? (comm.detail || `更新 ${fmtTs(comm.updated_at_ms)}`)
      : kind === "state" || kind === "unknown"
        ? `更新 ${fmtTs(L.ts_ms)}`
        : (L.work_mode || dev.type || "设备读数");
    card.append(key, reading, updated);
    box.appendChild(card);
  }
}

function renderConfig(dev, latest, runs) {
  const box = $("config"); box.textContent = "";
  const M = (latest && latest.metrics) || {};
  const card = document.createElement("div"); card.className = "card";
  const title = document.createElement("div"); title.className = "k"; title.textContent = "来自最近一次读数";
  const grid = document.createElement("div"); grid.className = "kv";
  // Prefer live metrics; fall back to the most recent run's setpoints.
  let sp = {};
  if (runs && runs.length) {
    try { sp = runs[0].setpoints || {}; } catch (e) { sp = {}; }
  }
  if (dev.type === "stirrer") {
    grid.append(
      kv("工作模式", latest && latest.work_mode),
      kv("设定温度", fmtValue(M.set_temp, "℃")),
      kv("设定转速", fmtValue(M.set_speed, "rpm", 0)),
      kv("当前温度", fmtValue(latest && latest.temp_c, "℃"))
    );
  } else if (dev.type === "viscometer") {
    grid.append(
      kv("工作模式", latest && latest.work_mode),
      kv("粘度", fmtValue(M.viscosity_mPas, "mPa·s", 2)),
      kv("扭矩", fmtValue(M.torque_pct, "%")),
      kv("剪切速率", fmtValue(M.shear_rate_1s, "1/s"))
    );
  } else {
    grid.append(
      kv("模式", latest && latest.work_mode),
      kv("注射器", M.syringe_name != null ? M.syringe_name : (sp.syringe_name || sp.syringe_code)),
      kv("目标液量", M.target_volume != null ? (M.target_volume + " " + (M.target_unit || "")) : (sp.target_volume != null ? sp.target_volume : null)),
      kv("注入速率", M.inject_rate != null ? (M.inject_rate + " " + (M.inject_rate_unit || "")) : (sp.inject_rate != null ? sp.inject_rate : null)),
      kv("暂停间隔(ms)", M.pause_delay_ms != null ? M.pause_delay_ms : sp.pause_delay_ms),
      kv("重复次数", M.repeat_count != null ? M.repeat_count : sp.repeat_count),
      kv("推力", M.force != null ? M.force : sp.force),
      kv("步长(μL/步)", M.step_length_ul_per_step != null ? M.step_length_ul_per_step.toFixed(4) : (sp.step_length_ul_per_step != null ? sp.step_length_ul_per_step : null)),
      kv("步长(mm/步)", M.step_length_mm_per_step != null ? M.step_length_mm_per_step.toFixed(6) : (sp.step_length_mm_per_step != null ? sp.step_length_mm_per_step : null))
    );
  }
  card.append(title, grid); box.appendChild(card);
}

function renderLifetime(dev, latest, runs) {
  const box = $("lifetime"); box.textContent = "";
  const card = document.createElement("div"); card.className = "card";
  const title = document.createElement("div"); title.className = "k";
  // Lifetime total comes from the pump's accumulator, not from summing runs.
  const L = latest || {};
  const lifetimeTotal = L.acc_volume;
  const lifetimeUnit = L.acc_unit || "";
  let count = 0; let alarms = 0;
  for (const r of (runs || [])) {
    if (r.end_status === "completed") count += 1;
    if (r.alarm_count != null) alarms += r.alarm_count;
  }
  const tot = document.createElement("div"); tot.className = "v";
  if (dev.type === "tyd02") {
    title.textContent = "累计液量（寿命）";
    tot.textContent = (lifetimeTotal == null ? "-" : lifetimeTotal) + " " + lifetimeUnit;
  } else {
    title.textContent = "历史运行批次";
    tot.textContent = String((runs || []).length);
  }
  const done = document.createElement("div"); done.className = "k"; done.textContent = "已完成运行 " + count;
  const alm = document.createElement("div"); alm.className = "k"; alm.textContent = "累计报警 " + alarms;
  card.append(title, tot, done, alm); box.appendChild(card);
}

function renderRuns(runs) {
  const tb = $("runs").querySelector("tbody"); tb.textContent = "";
  const empty = $("empty");
  if (!runs || !runs.length) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  for (const r of runs) {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.onclick = () => { location.href = "/run/" + r.id; };
    const cell = (txt, cls) => { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = (txt == null || txt === "") ? "-" : txt; return td; };
    tr.appendChild(cell(r.started_display));
    tr.appendChild(cell(fmtTs(r.ended_ms)));
    tr.appendChild(cell(fmtDur(r.duration_ms)));
    tr.appendChild(cell(PuricoreRunPresentation.statusLabel(r), PuricoreRunPresentation.statusClass(r)));
    tr.appendChild(cell(PuricoreRunPresentation.formatMetric(r.primary_metric)));
    tr.appendChild(cell(r.operator));
    tr.appendChild(cell(r.project_tag));
    tb.appendChild(tr);
  }
}

async function load() {
  if (!DEVICE_ID || !Number.isFinite(DEVICE_ID)) { return; }
  let data;
  try {
    const response = await fetch("/api/devices/" + DEVICE_ID);
    data = await response.json();
    if (!response.ok) throw new Error(data.error || "请求失败");
  } catch (e) { return; }
  const dev = data.device || { id: DEVICE_ID };
  $("title").textContent = dev.alias || dev.name || ("设备 " + DEVICE_ID);
  renderStatus(dev, data.latest, data.communication);
  renderConfig(dev, data.latest, data.runs);
  renderLifetime(dev, data.latest, data.runs);
  renderRuns(data.runs);
}

async function pollDevice() {
  await load();
  window.setTimeout(pollDevice, 2000);
}

pollDevice();
