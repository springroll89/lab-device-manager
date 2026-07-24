const byId = (id) => document.getElementById(id);
const pathMatch = location.pathname.match(/^\/experiments\/(\d+)$/);
const experimentId = pathMatch ? Number(pathMatch[1]) : null;

const MAIN_STEPS = [
  "R201-01","R201-02","R201-03","R201-04","R201-10","R201-20","R201-30",
  "R201-31","R201-32","R201-40","R201-50","R201-60","R201-70","R201-80","R201-90"
];

const STEP_GUIDES = {
  "R201-01":"确认环境温湿度、设备编号、点检有效期与实时通讯状态。",
  "R201-02":"逐项确认两口烧瓶、冷凝管、搅拌子、容器与注射器均干燥无水。",
  "R201-03":"核对体系、物料批号、有效期、理论量、实际量与外观。",
  "R201-04":"记录盐酸稀释计算，严格执行“酸入水”，完成定容与复核。",
  "R201-10":"放入 TEOS 与功能硅烷，开始预混并保持本批参数快照规定的转速。",
  "R201-20":"加入无水乙醇继续搅拌，记录实际用量、转速与体系外观。",
  "R201-30":"建立冰浴，确认反应温度、注射器、目标体积/速率并排尽管路气泡。",
  "R201-31":"确认管路无气泡后开始加酸；系统自动记录累计量、速度和用时。",
  "R201-32":"加酸结束后继续平衡；实际时长由开始与结束标记自动计算。",
  "R201-40":"先确认冷凝水下进上出、无泄漏、回流正常及防吸湿措施，再开始升温。",
  "R201-50":"保持 40℃陈化、搅拌与冷凝回流；粘度测量是本步骤中的重复子任务。",
  "R201-60":"达到稳定终点后停止加热并持续搅拌，降至室温后才能停止搅拌。",
  "R201-70":"完成过滤、容器称量、转序复测、外观和标签确认。",
  "R201-80":"确认自动生成的数据、时间轴和已处置异常，然后锁定本批实验记录。",
  "R201-90":"实验记录已锁定，可导出批记录和完整设备数据。"
};

const STEP_ACTIONS = {
  "R201-01":{start:"开始环境确认并打标",complete:"确认环境与设备，进入下一步"},
  "R201-02":{start:"开始器皿确认并打标",complete:"器皿均已干燥，进入下一步"},
  "R201-03":{start:"开始物料确认并打标",complete:"确认物料，进入下一步"},
  "R201-04":{start:"开始配制酸水并打标",complete:"酸水配制完成并打标"},
  "R201-10":{start:"开始预混并打标",complete:"预混完成并打标"},
  "R201-20":{start:"加入乙醇并打标",complete:"乙醇搅拌完成并打标"},
  "R201-30":{start:"进入冰浴并打标",complete:"冰浴与泵准备完成"},
  "R201-31":{start:"开始加酸并打标",complete:"加酸完成并打标"},
  "R201-32":{start:"开始平衡并打标",complete:"平衡完成并打标"},
  "R201-40":{start:"开始升温并打标",complete:"到达反应温度并打标"},
  "R201-50":{start:"开始恒温陈化并打标",complete:"确认粘度终点并进入降温"},
  "R201-60":{start:"开始降温并打标",complete:"降至室温并打标"},
  "R201-70":{start:"开始过滤出料并打标",complete:"出料完成并打标"}
};

const AUTO_STEP_FIELDS = {
  "R201-01":new Set(["environment_temp_c","environment_humidity_rh","device_checks"]),
  "R201-10":new Set(["actual_rpm"]),
  "R201-20":new Set(["actual_rpm"]),
  "R201-30":new Set(["reaction_temp_c","syringe_spec","target_volume_ml","target_rate_ml_min"]),
  "R201-31":new Set(["acc_volume_start","acc_volume_end","target_volume_ml","acc_volume_unit"]),
  "R201-32":new Set(["balance_minutes"]),
  "R201-40":new Set(["reached_temp_c"]),
  "R201-60":new Set(["cooling_minutes"]),
  "R201-70":new Set(["transfer_viscosity_mpas"])
};

const STEP_DEVICE_TYPES = {
  "R201-01":["whd46"],
  "R201-10":["stirrer"],
  "R201-20":["stirrer"],
  "R201-30":["stirrer","tyd02"],
  "R201-31":["tyd02"],
  "R201-32":["stirrer"],
  "R201-40":["stirrer"],
  "R201-50":["stirrer","viscometer"],
  "R201-60":["stirrer","whd46"],
  "R201-70":["viscometer"]
};

const STEP_FIELDS = {
  "R201-04":[
    ["hcl_c1_mol_l","浓盐酸 C1 mol/L","number"],["hcl_c2_mol_l","目标浓度 C2 mol/L","number"],
    ["hcl_v1_ml","计算取用 V1 mL","number"],["hcl_v2_ml","最终定容 V2 mL","number"],
    ["acid_into_water","已确认酸入水","checkbox"]
  ],
  "R201-10":[["actual_rpm","实际转速 rpm","number"],["appearance","体系外观","text"]],
  "R201-20":[
    ["ethanol_actual_ml","乙醇实际量 mL","number"],["actual_rpm","实际转速 rpm","number"],["appearance","体系外观","text"]
  ],
  "R201-30":[
    ["ice_bath_confirmed","冰浴已就位","checkbox"],["reaction_temp_c","反应温度 ℃","number"],
    ["syringe_spec","注射器规格","text"],["target_volume_ml","目标体积 mL","number"],
    ["target_rate_ml_min","设定加酸速率 mL/min","number"],["line_purged","管路已排气","checkbox"]
  ],
  "R201-31":[
    ["acc_volume_start","设备累计量起始值 mL","number"],["acc_volume_end","设备累计量结束值 mL","number"],
    ["target_volume_ml","目标加入量 mL","number"],["acc_volume_unit","累计量单位","text"]
  ],
  "R201-32":[["balance_minutes","实际平衡时长 min","number"]],
  "R201-40":[
    ["condenser_confirmed","冷凝回流检查通过","checkbox"],["moisture_protection_confirmed","防吸湿措施已就位","checkbox"],
    ["reached_temp_c","稳定到达温度 ℃","number"]
  ],
  "R201-50":[["endpoint_confirmed","确认粘度终点","checkbox"]],
  "R201-60":[["room_temp_confirmed","已降至室温","checkbox"],["cooling_minutes","降温时长 min","number"]],
  "R201-70":[
    ["filter_spec","过滤规格","text"],["container_tare_g","容器皮重 g","number"],
    ["container_gross_g","容器总重 g","number"],["transfer_viscosity_mpas","转序复测粘度 mPa·s","number"],
    ["appearance","溶胶外观","text"],["label_confirmed","容器标签已完成","checkbox"]
  ]
};

const FIELD_LABELS = Object.fromEntries(
  Object.values(STEP_FIELDS).flat().map(([key,label]) => [key,label])
);
Object.assign(FIELD_LABELS, {
  environment_temp_c:"环境温度",
  environment_humidity_rh:"环境湿度",
  device_checks:"设备在线状态",
  viscosity_mpas:"粘度",
  sample_temp_c:"样品温度",
  reaction_temp_c:"反应温度",
  rotor:"转子",
  rpm:"转速",
  torque_pct:"扭矩"
});

const MATERIAL_PRESETS = {
  CEM:["TEOS","MPTES","无水乙醇","盐酸","去离子水"],
  AEM:["TEOS","TMAPS","无水乙醇","盐酸","去离子水"]
};

const EVENT_LABELS = {
  experiment_created:"创建批次",
  step_started:"开始步骤",
  step_completed:"完成步骤",
  data_source_bound:"连接数据源",
  process_device_selected:"选择本批设备",
  measurement_recorded:"记录测量",
  viscosity_recorded:"记录粘度读数",
  deviation_opened:"记录偏差",
  deviation_resolved:"处置偏差",
  experiment_submitted:"提交批次",
  experiment_reviewed:"完成审核"
};

const STATUS_LABELS = {
  draft:"待开始",
  in_progress:"进行中",
  pending_review:"待确认",
  released:"已完成",
  returned:"已退回",
  terminated:"已终止",
  parallel_validation:"纸电并行验证",
  running:"运行中",
  paused:"已暂停",
  stopped:"已停止",
  alarm:"报警",
  offline:"离线"
};

const TRACE_STATUS_LABELS = {
  active:"可用",
  stored:"暂存中",
  consumed:"已消耗",
  completed:"已完成",
  disposed:"已报废"
};

const TRACE_TYPE_LABELS = {
  batch:"实验批次",
  intermediate:"中间品",
  final_product:"最终成品"
};

const DEVICE_TYPE_LABELS = {
  tyd02:"TYD02 注射泵",
  stirrer:"HMS-C 搅拌器",
  viscometer:"粘度计",
  whd46:"WHD46 温湿度控制器"
};

const PROCESS_ROLE_BY_DEVICE_TYPE = {
  tyd02:"acid_pump",
  stirrer:"stirrer",
  viscometer:"viscometer",
  whd46:"environment"
};

let state = null;
let currentOperator = "本机操作员";
let nextBatchId = "";
let clockOffsetMs = null;
let clockStatus = "unknown";
let toastTimer = null;
let flushingOutbox = false;
let liveConnecting = false;
let viewedStepCode = null;
const openDevicePickers = new Set();
const pendingDeviceSelections = new Map();
const stepFormUiState = new Map();
const OUTBOX_KEY = "r201-event-outbox-v1";
const STEP_DRAFT_PREFIX = "r201-step-draft-v1";

function uid(prefix = "evt") {
  if (crypto.randomUUID) return `${prefix}-${crypto.randomUUID()}`;
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: {"Content-Type":"application/json", ...(options.headers || {})}
  });
  let data = null;
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) {
    const error = new Error(data?.error || `请求失败 (${response.status})`);
    error.httpStatus = response.status;
    error.details = data?.details || null;
    throw error;
  }
  return data;
}

function readOutbox() {
  try {
    const value = JSON.parse(localStorage.getItem(OUTBOX_KEY) || "[]");
    return Array.isArray(value) ? value : [];
  } catch (_) {
    return [];
  }
}

function writeOutbox(entries) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(entries)); } catch (_) {}
  updateOutboxStatus(entries.length);
}

function updateOutboxStatus(count = readOutbox().length) {
  const box = byId("outboxStatus");
  if (!box) return;
  box.textContent = count ? `待同步事件 ${count} 条` : "事件已同步";
  box.style.color = count ? "var(--warn)" : "";
}

function removeOutboxEntry(clientEventId) {
  writeOutbox(
    readOutbox().filter(entry => entry.client_event_id !== clientEventId)
  );
}

async function mutate(url, payload) {
  if (!payload.client_event_id) {
    throw new Error("写操作缺少 client_event_id，已阻止提交");
  }
  const entries = readOutbox();
  if (!entries.some(entry => entry.client_event_id === payload.client_event_id)) {
    entries.push({
      client_event_id: payload.client_event_id,
      url,
      payload,
      queued_at_ms: Date.now()
    });
    writeOutbox(entries);
  }
  try {
    const result = await api(url, {
      method:"POST",
      body:JSON.stringify(payload)
    });
    removeOutboxEntry(payload.client_event_id);
    return result;
  } catch (error) {
    if (error.httpStatus) {
      removeOutboxEntry(payload.client_event_id);
      throw error;
    }
    updateOutboxStatus();
    throw new Error("网络中断：操作已保存在本机，恢复连接后会用同一幂等键自动重试");
  }
}

async function flushOutbox() {
  if (flushingOutbox || !navigator.onLine) return;
  flushingOutbox = true;
  let synced = 0;
  try {
    for (const entry of readOutbox()) {
      try {
        await api(entry.url, {
          method:"POST",
          body:JSON.stringify(entry.payload)
        });
        removeOutboxEntry(entry.client_event_id);
        synced += 1;
      } catch (error) {
        if (!error.httpStatus) break;
        removeOutboxEntry(entry.client_event_id);
        toast(`待同步操作未被接受：${error.message}`, true);
      }
    }
  } finally {
    flushingOutbox = false;
    updateOutboxStatus();
  }
  if (synced && experimentId) {
    toast(`已恢复连接并同步 ${synced} 条事件。`);
    await loadDetail();
  } else if (synced) {
    toast(`已恢复连接并同步 ${synced} 条事件。`);
    await loadList();
  }
}

function toast(message, error = false) {
  const box = byId("toast");
  box.textContent = message;
  box.className = `toast${error ? " error" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add("hidden"), 3800);
}

function setCreateStatus(message = "", type = "") {
  const box = byId("createStatus");
  if (!box) return;
  box.textContent = message;
  box.className = message
    ? `form-status${type ? ` ${type}` : ""}`
    : "form-status hidden";
}

function setTraceStatus(id, message = "", type = "") {
  const box = byId(id);
  if (!box) return;
  box.textContent = message;
  box.className = message
    ? `form-status${type ? ` ${type}` : ""}`
    : "form-status hidden";
}

function normalizeTraceCode(value) {
  const normalized = String(value || "").trim().toUpperCase();
  return normalized.startsWith("PURICORE:")
    ? normalized.split(":", 2)[1]
    : normalized;
}

function eventPayload(actor, prefix) {
  return {
    client_event_id: uid(prefix),
    occurred_at_client_ms: Date.now(),
    client_clock_offset_ms: clockOffsetMs,
    clock_sync_status: clockStatus,
    actor
  };
}

async function syncClock() {
  const start = Date.now();
  try {
    const data = await api("/api/time");
    const end = Date.now();
    const rtt = end - start;
    const midpoint = (start + end) / 2;
    clockOffsetMs = Math.round(midpoint - data.server_ms);
    clockStatus = rtt <= 2000 ? "trusted" : "untrusted";
    const text = `时钟${clockStatus === "trusted" ? "已校准" : "延迟偏高"} · RTT ${rtt}ms · 偏移 ${clockOffsetMs}ms`;
    if (byId("clockSync")) byId("clockSync").textContent = text;
  } catch (_) {
    clockStatus = "unknown";
    if (byId("clockSync")) byId("clockSync").textContent = "时钟未校准，事件将标记待复核";
  }
}

function fmtTime(ms) {
  if (!ms) return "—";
  return new Date(ms).toLocaleString("zh-CN", {hour12:false});
}

function badge(text, cls = "") {
  const span = document.createElement("span");
  span.className = `chip ${cls}`;
  span.textContent = text;
  return span;
}

function stepDraftKey(step) {
  return `${STEP_DRAFT_PREFIX}:${experimentId}:${step}`;
}

function saveStepDraft(step, form) {
  if (!step || !form || form.dataset.skipDraftSave === "true") return;
  const values = {};
  for (const input of form.elements) {
    if (!input.name) continue;
    values[input.name] = input.type === "checkbox"
      ? {checked:input.checked}
      : {value:input.value};
  }
  try {
    sessionStorage.setItem(stepDraftKey(step), JSON.stringify(values));
  } catch (_) {}
}

function restoreStepDraft(step, form) {
  let values = null;
  try {
    values = JSON.parse(sessionStorage.getItem(stepDraftKey(step)) || "null");
  } catch (_) {}
  if (!values || typeof values !== "object") return;
  for (const input of form.elements) {
    if (!input.name || !values[input.name]) continue;
    if (input.type === "checkbox") input.checked = Boolean(values[input.name].checked);
    else if (typeof values[input.name].value === "string") input.value = values[input.name].value;
  }
}

function clearStepDraft(step) {
  try { sessionStorage.removeItem(stepDraftKey(step)); } catch (_) {}
}

function captureStepFormUiState(form) {
  if (!form?.dataset.stepCode) return;
  const focused = form.contains(document.activeElement)
    ? document.activeElement
    : null;
  const selectionSupported = focused
    && typeof focused.selectionStart === "number"
    && typeof focused.selectionEnd === "number";
  stepFormUiState.set(form.dataset.stepCode, {
    manualFallbackOpen:Boolean(form.querySelector(".manual-fallback")?.open),
    focusedName:focused?.name || null,
    selectionStart:selectionSupported ? focused.selectionStart : null,
    selectionEnd:selectionSupported ? focused.selectionEnd : null
  });
}

function restoreStepFormUiState(step, form) {
  const saved = stepFormUiState.get(step);
  if (!saved) return;
  const fallback = form.querySelector(".manual-fallback");
  if (fallback) fallback.open = saved.manualFallbackOpen;
  if (!saved.focusedName) return;
  const focused = Array.from(form.elements).find(
    input => input.name === saved.focusedName
  );
  if (!focused) return;
  focused.focus({preventScroll:true});
  if (
    saved.selectionStart !== null
    && typeof focused.setSelectionRange === "function"
  ) {
    focused.setSelectionRange(saved.selectionStart, saved.selectionEnd);
  }
}

function preserveStepFormDuringLiveUpdate() {
  const form = byId("stepResultForm");
  if (!form) return false;
  return (
    form.contains(document.activeElement)
    || Boolean(form.querySelector(".manual-fallback")?.open)
  );
}

function processValue(label, value) {
  const box = document.createElement("div");
  box.className = "process-value";
  const caption = document.createElement("span");
  caption.textContent = label;
  const reading = document.createElement("b");
  reading.textContent = value ?? "—";
  box.append(caption, reading);
  return box;
}

function exactBoundValue(process) {
  if (!process?.bound || !process.latest) return null;
  const key = process.binding.metric_key;
  return process.latest[key] ?? process.latest.metrics?.[key] ?? null;
}

function appendProcessSummary(parent, step) {
  if (!["R201-31","R201-40","R201-50"].includes(step)) return;
  const summary = document.createElement("div");
  summary.className = "process-summary";
  if (step === "R201-31") {
    const pump = state.process_status?.acid_pump;
    const temperature = state.process_status?.reaction_temp;
    const latest = pump?.latest;
    summary.append(
      processValue("注射泵", pump?.bound ? (pump.device?.alias || pump.device?.name) : "未绑定"),
      processValue("泵状态", latest?.state || "无数据"),
      processValue("设定加酸速率", latest?.metrics?.inject_rate == null ? "—" : `${latest.metrics.inject_rate} mL/min`),
      processValue("生命周期累计量", latest?.acc_volume == null ? "—" : `${latest.acc_volume} ${latest.acc_unit || ""}`),
      processValue("泵报警", latest?.alarm || latest?.metrics?.alarm || "无"),
      processValue(
        "反应温度（绑定源）",
        exactBoundValue(temperature) == null ? "未绑定/无数据" : `${exactBoundValue(temperature)} ℃`
      )
    );
  } else {
    const temperature = state.process_status?.reaction_temp;
    const source = temperature?.bound
      ? `${temperature.device?.alias || temperature.device?.name} · ${temperature.binding.metric_key}`
      : "未绑定明确通道";
    summary.append(
      processValue("反应温度源", source),
      processValue("当前温度", exactBoundValue(temperature) == null ? "—" : `${exactBoundValue(temperature)} ℃`),
      processValue("20 min 检查点", `${state.temperature_checkpoints?.length || 0} 个`),
      processValue("自动到温", state.reached_temperature ? fmtTime(state.reached_temperature.effective_at_ms) : "未满足/未配置"),
      processValue("数据完整性", `${state.telemetry_integrity_status || "未知"} · 缺口 ${state.telemetry_gaps?.length || 0}`)
    );
  }
  parent.appendChild(summary);
  if (step === "R201-40" || step === "R201-50") {
    const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chart.setAttribute("class", "trend");
    chart.setAttribute("viewBox", "0 0 600 150");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "绑定反应温度趋势");
    parent.appendChild(chart);
    drawTrend(
      chart,
      (state.temperature_series || []).map(point => ({
        x:point.ts_ms,
        y:Number(point.value),
        valid:true
      })),
      "℃",
      [
        state.experiment.spec_snapshot?.reaction_temp_min_c,
        state.experiment.spec_snapshot?.reaction_temp_max_c
      ]
    );
  }
}

function drawTrend(svg, points, unit, bounds = []) {
  if (!svg) return;
  svg.textContent = "";
  const ns = "http://www.w3.org/2000/svg";
  if (!points.length) {
    const empty = document.createElementNS(ns, "text");
    empty.setAttribute("x", "300");
    empty.setAttribute("y", "78");
    empty.setAttribute("text-anchor", "middle");
    empty.textContent = "尚无趋势数据";
    svg.appendChild(empty);
    return;
  }
  const xValues = points.map(point => point.x);
  const yValues = points.map(point => point.y).filter(Number.isFinite);
  const configuredBounds = bounds.map(Number).filter(Number.isFinite);
  yValues.push(...configuredBounds);
  const minX = Math.min(...xValues);
  const maxX = Math.max(...xValues);
  let minY = Math.min(...yValues);
  let maxY = Math.max(...yValues);
  if (minY === maxY) { minY -= 1; maxY += 1; }
  const padY = (maxY - minY) * 0.12;
  minY -= padY;
  maxY += padY;
  const x = value => 45 + ((value - minX) / Math.max(1, maxX - minX)) * 535;
  const y = value => 125 - ((value - minY) / Math.max(0.001, maxY - minY)) * 105;
  const axisX = document.createElementNS(ns, "line");
  axisX.setAttribute("class", "axis");
  axisX.setAttribute("x1", "45"); axisX.setAttribute("y1", "125");
  axisX.setAttribute("x2", "580"); axisX.setAttribute("y2", "125");
  const axisY = document.createElementNS(ns, "line");
  axisY.setAttribute("class", "axis");
  axisY.setAttribute("x1", "45"); axisY.setAttribute("y1", "20");
  axisY.setAttribute("x2", "45"); axisY.setAttribute("y2", "125");
  svg.append(axisX, axisY);
  if (configuredBounds.length === 2) {
    const band = document.createElementNS(ns, "rect");
    const top = y(Math.max(...configuredBounds));
    const bottom = y(Math.min(...configuredBounds));
    band.setAttribute("x", "45");
    band.setAttribute("y", String(top));
    band.setAttribute("width", "535");
    band.setAttribute("height", String(Math.max(1, bottom - top)));
    band.setAttribute("fill", "rgba(73,215,177,.08)");
    svg.insertBefore(band, axisX);
  }
  const polyline = document.createElementNS(ns, "polyline");
  polyline.setAttribute("class", "line");
  polyline.setAttribute("points", points.map(point => `${x(point.x)},${y(point.y)}`).join(" "));
  svg.appendChild(polyline);
  points.forEach(point => {
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("class", "point");
    circle.setAttribute("cx", String(x(point.x)));
    circle.setAttribute("cy", String(y(point.y)));
    circle.setAttribute("r", point.valid === false ? "4" : "3");
    if (point.valid === false) circle.setAttribute("fill", "var(--danger)");
    svg.appendChild(circle);
  });
  const label = document.createElementNS(ns, "text");
  label.setAttribute("x", "8"); label.setAttribute("y", "18");
  label.textContent = `${maxY.toFixed(1)} ${unit}`;
  const bottomLabel = document.createElementNS(ns, "text");
  bottomLabel.setAttribute("x", "8"); bottomLabel.setAttribute("y", "128");
  bottomLabel.textContent = `${minY.toFixed(1)} ${unit}`;
  svg.append(label, bottomLabel);
}

async function loadList() {
  const list = byId("experimentList");
  list.textContent = "";
  try {
    const experiments = await api("/api/experiments");
    if (!experiments.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "还没有 R-201 批次。创建后，人工事件和设备数据将汇入同一条时间轴。";
      list.appendChild(empty);
      return;
    }
    for (const item of experiments) {
      const card = document.createElement("a");
      card.className = "experiment-card";
      card.href = `/experiments/${item.id}`;
      const text = document.createElement("div");
      const batch = document.createElement("div");
      batch.className = "batch";
      batch.textContent = item.batch_id;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = `${item.membrane_system} · ${item.recipe_no}/${item.recipe_version} · 当前 ${item.current_step_code}`;
      text.append(batch, meta);
      const status = document.createElement("span");
      status.className = `status ${item.status}`;
      status.textContent = STATUS_LABELS[item.status] || item.status;
      card.append(text, status);
      list.appendChild(card);
    }
  } catch (error) {
    toast(error.message, true);
  }
}

function localDateCode() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0")
  ].join("");
}

async function loadSession() {
  try {
    const data = await api("/api/session");
    currentOperator = data.operator || "本机操作员";
    if (byId("operatorDisplay")) {
      byId("operatorDisplay").textContent = currentOperator;
    }
    return data;
  } catch (error) {
    currentOperator = "本机操作员";
    if (byId("operatorDisplay")) {
      byId("operatorDisplay").textContent = "读取失败";
    }
    if (error.httpStatus === 401) {
      location.replace(`/login?next=${encodeURIComponent(location.pathname)}`);
      return false;
    }
    setCreateStatus(
      "操作员读取失败，已临时使用“本机操作员”。你仍可刷新或重新登录。",
      "error"
    );
    return null;
  }
}

async function refreshNextBatchId() {
  const system = byId("system").value;
  nextBatchId = "";
  byId("batchId").textContent = "正在生成…";
  try {
    const query = new URLSearchParams({
      membrane_system:system,
      date:localDateCode()
    });
    const data = await api(`/api/experiments/next-batch-id?${query}`);
    nextBatchId = data.batch_id;
    byId("batchId").textContent = nextBatchId;
    return nextBatchId;
  } catch (error) {
    byId("batchId").textContent = "生成失败";
    throw error;
  }
}

function parseSpec() {
  const raw = byId("specJson").value.trim();
  if (!raw) return {};
  try { return JSON.parse(raw); } catch (_) { throw new Error("批次参数快照不是有效 JSON"); }
}

function parseRecipeParameters() {
  const raw = byId("recipeParametersJson").value.trim();
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    if (!Array.isArray(value)) throw new Error();
    return value;
  } catch (_) {
    throw new Error("配方计算参数必须是有效的 JSON 数组");
  }
}

async function createExperiment(event) {
  event.preventDefault();
  const button = byId("createButton");
  button.disabled = true;
  button.textContent = "正在创建…";
  setCreateStatus("正在校验批次信息…");
  try {
    const system = byId("system").value;
    const recipeNo = byId("recipeNo").value.trim();
    const recipeVersion = byId("recipeVersion").value.trim();
    if (!nextBatchId) await refreshNextBatchId();
    if (!recipeNo) throw new Error("请选择或填写配方编号");
    if (!recipeVersion) throw new Error("请填写配方版本");
    const data = {
      batch_id: nextBatchId,
      membrane_system: system,
      recipe_no: recipeNo,
      recipe_version: recipeVersion,
      sop_code: "SOP-SOL-GEL-CEM-AEM-01",
      sop_version: byId("sopVersion").value.trim(),
      target_viscosity_min_mpas: Number(byId("visMin").value),
      target_viscosity_max_mpas: Number(byId("visMax").value),
      operator: currentOperator,
      reviewer: "",
      spec_snapshot: parseSpec(),
      recipe_parameters: parseRecipeParameters(),
      ...eventPayload(currentOperator, "create")
    };
    const created = await mutate("/api/experiments", data);
    setCreateStatus(`批次 ${created.batch_id} 创建成功，正在进入实验…`, "success");
    location.href = `/experiments/${created.id}`;
  } catch (error) {
    if (error.httpStatus === 409) {
      try {
        const refreshed = await refreshNextBatchId();
        setCreateStatus(
          `原批次号已被占用，系统已刷新为 ${refreshed}。请再次点击创建。`,
          "error"
        );
      } catch (_) {
        setCreateStatus("批次号已被占用，且暂时无法生成新批次号。请刷新页面。", "error");
      }
    } else {
      setCreateStatus(error.message, "error");
    }
    toast(error.message, true);
  } finally {
    button.disabled = !nextBatchId;
    button.textContent = "创建并进入实验";
  }
}

async function loadDetail() {
  try {
    const [detail, traceability] = await Promise.all([
      api(`/api/experiments/${experimentId}`),
      api(`/api/experiments/${experimentId}/traceability`)
    ]);
    state = detail;
    state.traceability = traceability;
    renderDetail();
    connectLive();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderDetail() {
  const exp = state.experiment;
  byId("detailBatch").textContent = exp.batch_id;
  byId("reportLink").href = `/api/experiments/${exp.id}/report.pdf`;
  const hero = byId("heroMeta");
  hero.textContent = "";
  hero.append(
    badge(`${exp.membrane_system} · ${exp.recipe_no}/${exp.recipe_version}`),
    badge(`SOP ${exp.sop_version}`),
    badge(`目标 ${exp.target_viscosity_min_mpas}–${exp.target_viscosity_max_mpas} mPa·s`),
    badge(STATUS_LABELS[exp.status] || exp.status),
    badge(STATUS_LABELS[exp.validation_mode] || exp.validation_mode)
  );
  renderProgress();
  renderCurrentStep();
  renderDevices(state.available_devices || []);
  renderTimeline();
  renderMeasurements();
  renderDeviations();
  renderTraceability();
  byId("viscosityPanel").classList.toggle("hidden", exp.current_step_code !== "R201-50");
  byId("reviewControls").classList.toggle("hidden", exp.status !== "pending_review");
}

function renderTraceability() {
  const traceability = state.traceability || {items:[], locations:[], print_jobs:[]};
  const items = traceability.items || [];
  const locations = traceability.locations || [];
  const list = byId("traceItems");
  const summary = byId("traceSummary");
  const physicalItems = items.filter(item => item.item_type !== "batch");
  summary.textContent = `${physicalItems.length} 个实物 · ${locations.length} 个位置`;
  const options = byId("traceLocationOptions");
  options.textContent = "";
  locations.forEach(location => {
    const option = document.createElement("option");
    option.value = location.location_code;
    option.label = `${location.display_name}${location.storage_condition ? ` · ${location.storage_condition}` : ""}`;
    options.appendChild(option);
  });
  list.textContent = "";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "trace-empty";
    empty.textContent = "暂无实物身份。批次标签将在首次打印时自动生成。";
    list.appendChild(empty);
    return;
  }
  items.forEach(item => {
    const row = document.createElement("div");
    row.className = "trace-item";
    const main = document.createElement("div");
    main.className = "trace-item-main";
    const name = document.createElement("div");
    name.className = "trace-item-name";
    const title = document.createElement("span");
    title.textContent = item.display_name;
    const type = document.createElement("span");
    type.className = `trace-state${item.status === "stored" ? " stored" : ""}`;
    type.textContent = `${TRACE_TYPE_LABELS[item.item_type] || item.item_type} · ${TRACE_STATUS_LABELS[item.status] || item.status}`;
    name.append(title, type);
    const code = document.createElement("div");
    code.className = "trace-item-code";
    code.textContent = item.item_code;
    const meta = document.createElement("div");
    meta.className = "trace-item-meta";
    if (item.source_step_code) {
      const step = document.createElement("span");
      step.textContent = `生成步骤：${item.source_step_code}`;
      meta.appendChild(step);
    }
    if (item.quantity != null) {
      const quantity = document.createElement("span");
      quantity.textContent = `数量：${item.quantity} ${item.unit || ""}`;
      meta.appendChild(quantity);
    }
    if (item.storage_location_code) {
      const location = document.createElement("span");
      location.textContent = `位置：${item.storage_location_code}`;
      meta.appendChild(location);
    }
    if (item.hold_until_ms) {
      const hold = document.createElement("span");
      hold.textContent = `最晚使用：${fmtTime(item.hold_until_ms)}`;
      if (Date.now() > item.hold_until_ms) hold.style.color = "var(--danger)";
      meta.appendChild(hold);
    }
    if (item.parents?.length) {
      const parent = document.createElement("span");
      parent.textContent = `来源：${item.parents.map(value => value.item_code).join("、")}`;
      meta.appendChild(parent);
    }
    main.append(name, code, meta);
    const actions = document.createElement("div");
    actions.className = "trace-item-actions";
    if (item.item_type === "intermediate" && item.status === "active") {
      const store = document.createElement("button");
      store.className = "secondary";
      store.type = "button";
      store.textContent = "暂存";
      store.onclick = () => openTraceStoreDialog(item);
      actions.appendChild(store);
    }
    if (item.item_type === "intermediate" && item.status === "stored") {
      const retrieve = document.createElement("button");
      retrieve.className = "secondary";
      retrieve.type = "button";
      retrieve.textContent = "取回";
      retrieve.onclick = () => retrieveTraceItem(item);
      actions.appendChild(retrieve);
    }
    const print = document.createElement("button");
    print.className = "ghost";
    print.type = "button";
    print.textContent = item.events?.some(event =>
      ["label_print_requested","label_reprint_requested"].includes(event.event_type)
    ) ? "补打标签" : "打印标签";
    print.onclick = () => requestTraceLabels(
      [item],
      print.textContent === "补打标签" ? "reprint" : "initial"
    );
    actions.appendChild(print);
    row.append(main, actions);
    list.appendChild(row);
  });
}

function openTraceItemDialog(itemType) {
  const isIntermediate = itemType === "intermediate";
  byId("traceItemType").value = itemType;
  byId("traceItemDialogTitle").textContent = isIntermediate
    ? "暂存中间品"
    : "生成成品标签";
  byId("traceItemDialogHint").textContent = isIntermediate
    ? "系统按当前步骤生成独立容器编号，并记录存放位置。"
    : "系统自动关联本批最近的中间品并生成成品编号。";
  byId("traceItemName").value = isIntermediate
    ? `${state.step_labels?.[state.experiment.current_step_code] || state.experiment.current_step_code}中间品`
    : `${state.experiment.membrane_system} 功能膜成品`;
  byId("traceContainerCount").value = "1";
  byId("traceQuantity").value = "";
  byId("traceStorageFields").classList.toggle("hidden", !isIntermediate);
  byId("traceHoldField").classList.toggle("hidden", !isIntermediate);
  byId("traceLocationCode").required = isIntermediate;
  byId("traceItemSubmit").textContent = isIntermediate
    ? "暂存并生成标签"
    : "生成成品并打印";
  setTraceStatus("traceItemStatus");
  if (isIntermediate && !(state.traceability?.locations || []).length) {
    setTraceStatus(
      "traceItemStatus",
      "还没有存储位置。请先建立并打印一个位置标签。",
      "error"
    );
  }
  byId("traceItemDialog").showModal();
  setTimeout(() => byId("traceItemName").focus(), 0);
}

function openTraceStoreDialog(item) {
  byId("traceStoreItemId").value = item.id;
  byId("traceStoreItemName").textContent = `${item.display_name} · ${item.item_code}`;
  byId("traceStoreLocationCode").value = "";
  byId("traceStoreHoldHours").value = "24";
  setTraceStatus("traceStoreStatus");
  byId("traceStoreDialog").showModal();
  setTimeout(() => byId("traceStoreLocationCode").focus(), 0);
}

function openTraceRetrieveDialog() {
  byId("traceRetrieveCode").value = "";
  setTraceStatus("traceRetrieveStatus");
  byId("traceRetrieveDialog").showModal();
  setTimeout(() => byId("traceRetrieveCode").focus(), 0);
}

function openTraceLocationDialog() {
  byId("traceLocationForm").reset();
  setTraceStatus("traceLocationStatus");
  byId("traceLocationDialog").showModal();
  setTimeout(() => byId("traceNewLocationCode").focus(), 0);
}

function closeTraceDialog(id) {
  const dialog = byId(id);
  if (dialog?.open) dialog.close();
}

async function lookupTraceItem(code) {
  const result = await api(
    `/api/trace/lookup?code=${encodeURIComponent(normalizeTraceCode(code))}`
  );
  if (result.kind !== "trace_item") throw new Error("扫描到的是存储位置，不是实物标签");
  if (result.item.experiment_id !== experimentId) {
    throw new Error("该实物不属于当前实验批次");
  }
  return result.item;
}

function openPrintWindow() {
  const preview = window.open("about:blank", "_blank");
  if (preview) {
    preview.document.title = "正在生成标签…";
    preview.document.body.textContent = "正在生成标签…";
  }
  return preview;
}

function navigatePrintWindow(preview, url) {
  const link = byId("tracePrintLink");
  link.href = url;
  link.classList.remove("hidden");
  if (preview && !preview.closed) {
    preview.location.href = url;
    return;
  }
  toast("标签已生成，请点击“打开刚生成的标签”。");
}

async function requestTraceLabels(items, reason = "initial", preview = null) {
  const printPreview = preview || openPrintWindow();
  try {
    const result = await mutate(
      `/api/experiments/${experimentId}/trace-labels`,
      {
        ...eventPayload(state.experiment.operator, `trace-label-${reason}`),
        item_ids:items.map(item => item.id),
        reason,
        copies:1
      }
    );
    navigatePrintWindow(printPreview, result.print_url);
    toast(reason === "reprint" ? "补打任务已记录。" : "标签已生成。");
    await loadDetail();
    return result;
  } catch (error) {
    if (printPreview && !printPreview.closed) printPreview.close();
    toast(error.message, true);
    throw error;
  }
}

async function createTraceItems(event) {
  event.preventDefault();
  const itemType = byId("traceItemType").value;
  const isIntermediate = itemType === "intermediate";
  const containerCount = Number(byId("traceContainerCount").value);
  const quantityText = byId("traceQuantity").value.trim();
  const locationCode = normalizeTraceCode(byId("traceLocationCode").value);
  if (!Number.isInteger(containerCount) || containerCount < 1 || containerCount > 20) {
    setTraceStatus("traceItemStatus", "容器数量必须是 1–20。", "error");
    return;
  }
  if (isIntermediate && !locationCode) {
    setTraceStatus("traceItemStatus", "请扫描或选择存储位置。", "error");
    return;
  }
  const preview = openPrintWindow();
  const submit = byId("traceItemSubmit");
  submit.disabled = true;
  submit.textContent = "正在生成实物编号…";
  setTraceStatus("traceItemStatus", "正在建立实物身份…");
  try {
    const items = await mutate(
      `/api/experiments/${experimentId}/trace-items`,
      {
        ...eventPayload(state.experiment.operator, `trace-create-${itemType}`),
        item_type:itemType,
        display_name:byId("traceItemName").value.trim(),
        container_count:containerCount,
        quantity:quantityText ? Number(quantityText) : null,
        unit:quantityText ? byId("traceUnit").value : null
      }
    );
    if (isIntermediate) {
      setTraceStatus("traceItemStatus", "实物编号已生成，正在记录暂存位置…");
      for (const item of items) {
        await mutate(
          `/api/trace-items/${item.id}/store`,
          {
            ...eventPayload(state.experiment.operator, "trace-store"),
            location_code:locationCode,
            hold_hours:byId("traceHoldHours").value || null
          }
        );
      }
    }
    setTraceStatus(
      "traceItemStatus",
      isIntermediate
        ? "暂存信息已记录，正在生成标签…"
        : "成品身份已建立，正在生成标签…",
      "success"
    );
    await requestTraceLabels(items, "initial", preview);
    closeTraceDialog("traceItemDialog");
  } catch (error) {
    if (preview && !preview.closed) preview.close();
    setTraceStatus("traceItemStatus", error.message, "error");
  } finally {
    submit.disabled = false;
    submit.textContent = isIntermediate ? "暂存并生成标签" : "生成成品并打印";
  }
}

async function retrieveTraceItem(item, statusTarget = "traceRetrieveStatus") {
  try {
    if (item.status !== "stored") throw new Error("该实物当前不在暂存状态");
    await mutate(
      `/api/trace-items/${item.id}/retrieve`,
      eventPayload(state.experiment.operator, "trace-retrieve")
    );
    setTraceStatus(statusTarget, `已取回：${item.item_code}`, "success");
    toast(`已取回 ${item.item_code}`);
    await loadDetail();
    if (statusTarget === "traceRetrieveStatus") {
      closeTraceDialog("traceRetrieveDialog");
    }
  } catch (error) {
    setTraceStatus(statusTarget, error.message, "error");
  }
}

async function submitTraceRetrieve(event) {
  event.preventDefault();
  setTraceStatus("traceRetrieveStatus", "正在核对实物身份…");
  try {
    const item = await lookupTraceItem(byId("traceRetrieveCode").value);
    await retrieveTraceItem(item);
  } catch (error) {
    setTraceStatus("traceRetrieveStatus", error.message, "error");
  }
}

async function submitTraceStore(event) {
  event.preventDefault();
  const itemId = Number(byId("traceStoreItemId").value);
  const locationCode = normalizeTraceCode(byId("traceStoreLocationCode").value);
  if (!locationCode) {
    setTraceStatus("traceStoreStatus", "请扫描或选择存储位置。", "error");
    return;
  }
  setTraceStatus("traceStoreStatus", "正在记录暂存位置…");
  try {
    const stored = await mutate(
      `/api/trace-items/${itemId}/store`,
      {
        ...eventPayload(state.experiment.operator, "trace-store"),
        location_code:locationCode,
        hold_hours:byId("traceStoreHoldHours").value || null
      }
    );
    setTraceStatus("traceStoreStatus", `已暂存：${stored.item_code}`, "success");
    toast(`已暂存至 ${locationCode}`);
    await loadDetail();
    closeTraceDialog("traceStoreDialog");
  } catch (error) {
    setTraceStatus("traceStoreStatus", error.message, "error");
  }
}

async function submitTraceLocation(event) {
  event.preventDefault();
  const preview = openPrintWindow();
  setTraceStatus("traceLocationStatus", "正在建立位置身份…");
  try {
    const location = await mutate(
      "/api/storage-locations",
      {
        ...eventPayload(currentOperator, "trace-location-create"),
        location_code:normalizeTraceCode(byId("traceNewLocationCode").value),
        display_name:byId("traceNewLocationName").value.trim(),
        storage_condition:byId("traceNewLocationCondition").value.trim()
      }
    );
    const printed = await mutate(
      `/api/storage-locations/${location.id}/print`,
      {
        ...eventPayload(currentOperator, "trace-location-label"),
        reason:"initial",
        copies:1
      }
    );
    navigatePrintWindow(preview, printed.print_url);
    setTraceStatus("traceLocationStatus", "位置标签已生成。", "success");
    toast(`已建立位置 ${location.location_code}`);
    await loadDetail();
    closeTraceDialog("traceLocationDialog");
  } catch (error) {
    if (preview && !preview.closed) preview.close();
    setTraceStatus("traceLocationStatus", error.message, "error");
  }
}

function renderProgress() {
  const current = state.experiment.current_step_code;
  const index = Math.max(0, MAIN_STEPS.indexOf(current));
  byId("progressValue").style.width = `${Math.round((index / (MAIN_STEPS.length - 1)) * 100)}%`;
  const completed = new Set(state.steps.filter(s => s.status === "completed").map(s => s.step_code));
  const strip = byId("stepStrip");
  strip.textContent = "";
  MAIN_STEPS.forEach(step => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `step-dot${completed.has(step) ? " done" : ""}${step === current ? " current" : ""}${step === viewedStepCode ? " viewing" : ""}`;
    item.textContent = String(MAIN_STEPS.indexOf(step) + 1);
    const label = state.step_labels?.[step] || step;
    if (completed.has(step)) {
      item.title = `查看已保存的“${label}”结果`;
      item.onclick = () => {
        viewedStepCode = step;
        renderProgress();
        renderCurrentStep();
        byId("currentStep")?.scrollIntoView({
          behavior:"smooth",
          block:"start"
        });
      };
    } else if (step === current) {
      item.title = `当前步骤：${label}`;
      item.onclick = () => {
        viewedStepCode = null;
        renderProgress();
        renderCurrentStep();
      };
      item.setAttribute("aria-current", "step");
    } else {
      item.title = `${label}（尚未完成）`;
      item.disabled = true;
    }
    item.setAttribute("aria-label", `${MAIN_STEPS.indexOf(step) + 1}. ${label}`);
    strip.appendChild(item);
  });
}

function renderCurrentStep() {
  const exp = state.experiment;
  const step = exp.current_step_code;
  const box = byId("currentStep");
  const previousForm = byId("stepResultForm");
  if (previousForm) {
    saveStepDraft(previousForm.dataset.stepCode, previousForm);
    captureStepFormUiState(previousForm);
  }
  if (viewedStepCode && viewedStepCode !== step) {
    renderHistoricalStep(box, viewedStepCode);
    return;
  }
  viewedStepCode = null;
  box.classList.remove("history-view");
  box.textContent = "";
  const code = document.createElement("div");
  code.className = "step-code";
  const stepIndex = Math.max(0, MAIN_STEPS.indexOf(step));
  code.textContent = `第 ${stepIndex + 1} 步 / 共 ${MAIN_STEPS.length} 步`;
  code.title = `内部步骤编号 ${step}`;
  const name = document.createElement("div");
  name.className = "step-name";
  name.textContent = state.step_labels?.[step] || "实验已完成";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = STEP_GUIDES[step] || "本批次当前没有待执行步骤。";
  box.append(code, name, hint);
  const missingDeviceTypes = renderStepDeviceSurface(box, step);

  const active = state.active_step;
  if (step === "R201-80") {
    const openDeviations = (state.deviations || []).filter(item => item.status !== "closed");
    if (openDeviations.length) {
      const warning = document.createElement("div");
      warning.className = "inline-finding";
      warning.innerHTML = `<strong>还有 ${openDeviations.length} 条异常未完成处置</strong><p>请先核对异常影响；处理完成后即可锁定实验记录。</p>`;
      const locate = document.createElement("button");
      locate.className = "secondary";
      locate.type = "button";
      locate.textContent = "查看待处理异常";
      locate.onclick = () => byId("deviationPanel")?.scrollIntoView({behavior:"smooth",block:"start"});
      warning.appendChild(locate);
      box.appendChild(warning);
    }
    const button = document.createElement("button");
    button.className = "primary";
    button.textContent = exp.reviewer ? "提交复核" : "完成实验并锁定记录";
    button.disabled = openDeviations.length > 0;
    button.onclick = submitExperiment;
    box.appendChild(button);
    return;
  }
  if (step === "R201-90" || ["released","terminated"].includes(exp.status)) {
    const result = document.createElement("div");
    result.className = "chip";
    result.textContent = exp.status === "released"
      ? `实验记录已锁定：${exp.disposition || "已完成"}`
      : `当前状态：${STATUS_LABELS[exp.status] || exp.status}`;
    box.appendChild(result);
    return;
  }
  if (!active) {
    const button = document.createElement("button");
    button.className = "primary";
    button.textContent = STEP_ACTIONS[step]?.start || "开始并打标";
    button.disabled = missingDeviceTypes.length > 0;
    if (missingDeviceTypes.length) {
      button.title = "请先选择本批次使用的设备";
    }
    button.onclick = startCurrentStep;
    box.appendChild(button);
    return;
  }
  const form = document.createElement("form");
  form.className = "form-grid";
  form.id = "stepResultForm";
  form.dataset.stepCode = step;
  form.noValidate = true;
  if (step === "R201-01") renderEnvironmentStep(form);
  else if (step === "R201-02") renderGlasswareStep(form);
  else if (step === "R201-03") renderMaterialStep(form);
  if (!["R201-01","R201-02","R201-03"].includes(step)) {
    const autoFields = AUTO_STEP_FIELDS[step] || new Set();
    for (const [key, labelText, type] of (STEP_FIELDS[step] || [])) {
      if (autoFields.has(key) || key === "endpoint_confirmed") continue;
      form.appendChild(createStepField(key, labelText, type, false));
    }
    appendManualFallback(form, step);
  }
  const findingPanel = document.createElement("div");
  findingPanel.id = "stepFindingPanel";
  findingPanel.className = "field full hidden";
  form.appendChild(findingPanel);
  if (!["R201-01","R201-02","R201-03"].includes(step)) {
    appendCompleteButton(
      form,
      STEP_ACTIONS[step]?.complete || "完成并进入下一步",
      step === "R201-50" && !state.endpoint_ready
    );
  }
  form.onsubmit = completeCurrentStep;
  box.appendChild(form);
  restoreStepDraft(step, form);
  restoreStepFormUiState(step, form);
  const fallback = form.querySelector(".manual-fallback");
  if (fallback) {
    fallback.addEventListener("toggle", () => {
      captureStepFormUiState(form);
    });
  }
  if (missingDeviceTypes.length) {
    form.dataset.deviceBindingMissing = "true";
    form.querySelectorAll('button[type="submit"]').forEach(button => {
      button.disabled = true;
      button.title = "请先选择本批次使用的设备";
    });
  }
  form.addEventListener("input", () => saveStepDraft(step, form));
  form.addEventListener("change", () => saveStepDraft(step, form));
}

function latestCompletedStep(stepCode) {
  return [...(state.steps || [])].reverse().find(
    item => item.step_code === stepCode && item.status === "completed"
  ) || null;
}

function historyValue(key, value) {
  if (typeof value === "boolean") return value ? "已确认" : "未确认";
  if (key === "device_checks" && Array.isArray(value)) {
    return value.join("、") || "无设备记录";
  }
  if (key === "glassware_items" && Array.isArray(value)) {
    return value.map(item => (
      typeof item === "object"
        ? `${item.name || "器皿"}${item.dry ? "（已确认干燥）" : ""}`
        : String(item)
    )).join("、");
  }
  if (key === "materials" && Array.isArray(value)) {
    return value.map(item => {
      if (!item || typeof item !== "object") return String(item);
      const quantity = item.actual != null
        ? `${item.actual} ${item.unit || ""}`.trim()
        : "未记录用量";
      const lot = item.lot ? ` · 批号 ${item.lot}` : "";
      return `${item.name || "物料"}：${quantity}${lot}`;
    }).join("\n");
  }
  if (Array.isArray(value)) return value.map(String).join("、");
  if (value && typeof value === "object") return JSON.stringify(value);
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function renderHistoricalStep(box, stepCode) {
  const completed = latestCompletedStep(stepCode);
  if (!completed) {
    viewedStepCode = null;
    renderProgress();
    renderCurrentStep();
    return;
  }
  box.classList.add("history-view");
  box.textContent = "";
  const stepIndex = MAIN_STEPS.indexOf(stepCode);
  const code = document.createElement("div");
  code.className = "step-code";
  code.textContent = `历史记录 · 第 ${stepIndex + 1} 步 / 共 ${MAIN_STEPS.length} 步`;
  const name = document.createElement("div");
  name.className = "step-name";
  name.textContent = state.step_labels?.[stepCode] || stepCode;
  const notice = document.createElement("div");
  notice.className = "history-notice";
  const noticeCopy = document.createElement("div");
  const noticeTitle = document.createElement("strong");
  noticeTitle.textContent = "正在查看已完成步骤";
  const noticeText = document.createElement("p");
  noticeText.textContent = `这里只显示当时保存的结果，不会回退进度或修改后续数据。当前执行到：${state.step_labels?.[state.experiment.current_step_code] || state.experiment.current_step_code}。`;
  noticeCopy.append(noticeTitle, noticeText);
  const back = document.createElement("button");
  back.type = "button";
  back.className = "secondary";
  back.textContent = "返回当前步骤";
  back.onclick = () => {
    viewedStepCode = null;
    renderProgress();
    renderCurrentStep();
  };
  notice.append(noticeCopy, back);
  const meta = document.createElement("div");
  meta.className = "history-meta";
  meta.append(
    badge(`开始 ${fmtTime(completed.started_effective_at_ms)}`),
    badge(`完成 ${fmtTime(completed.ended_effective_at_ms)}`),
    badge(`操作员 ${completed.ended_by || completed.started_by || "—"}`)
  );
  const grid = document.createElement("div");
  grid.className = "history-result-grid";
  const resultEntries = Object.entries(completed.result || {}).filter(
    ([key]) => key !== "data_provenance"
  );
  if (!resultEntries.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "该步骤只记录了开始和完成时间，没有额外填写项。";
    grid.appendChild(empty);
  } else {
    resultEntries.forEach(([key, value]) => {
      const item = document.createElement("div");
      item.className = "history-result";
      const label = document.createElement("span");
      label.textContent = FIELD_LABELS[key] || {
        glassware_items:"已确认器皿",
        materials:"物料记录",
        dose_delivered_ml:"实际加入量 mL",
        dose_error_ml:"加入量偏差 mL",
        acid_calculation_error_pct:"酸水计算偏差 %",
        net_product_mass_g:"产品净重 g"
      }[key] || key;
      const valueNode = document.createElement("strong");
      valueNode.textContent = historyValue(key, value);
      item.append(label, valueNode);
      grid.appendChild(item);
    });
  }
  box.append(code, name, notice, meta, grid);
}

function devicesOfType(type) {
  return (state.available_devices || []).filter(item => item.type === type);
}

function selectedDeviceForType(type) {
  const role = PROCESS_ROLE_BY_DEVICE_TYPE[type];
  const process = state.process_status?.[role];
  const selectedId = process?.bound
    ? (process.device?.id ?? process.binding?.device_id)
    : null;
  const candidates = devicesOfType(type);
  if (selectedId != null) {
    return candidates.find(item => Number(item.id) === Number(selectedId)) || null;
  }
  return candidates.length === 1 ? candidates[0] : null;
}

function missingDeviceBindings(step) {
  return (STEP_DEVICE_TYPES[step] || []).filter(type => {
    const candidates = devicesOfType(type);
    return candidates.length > 1 && !selectedDeviceForType(type);
  });
}

function renderDevicePicker(parent, type, candidates, selectedDevice) {
  const picker = document.createElement("div");
  picker.className = "device-binding-picker";
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = `选择本批使用的${DEVICE_TYPE_LABELS[type] || "设备"}`;
  const note = document.createElement("p");
  note.textContent = selectedDevice
    ? "如现场更换设备，请重新选择；后续数据将切换到新设备。"
    : `系统检测到 ${candidates.length} 台，请选择现场正在使用的设备。每个批次只需选择一次。`;
  copy.append(title, note);

  const actions = document.createElement("div");
  actions.className = "device-binding-actions";
  const select = document.createElement("select");
  select.setAttribute("aria-label", `选择${DEVICE_TYPE_LABELS[type] || "设备"}`);
  const prompt = document.createElement("option");
  prompt.value = "";
  prompt.textContent = "请选择现场设备";
  select.appendChild(prompt);
  [...candidates]
    .sort((left, right) => (
      Number(deviceReadingIsFresh(right)) - Number(deviceReadingIsFresh(left))
    ))
    .forEach(device => {
      const option = document.createElement("option");
      option.value = String(device.id);
      option.textContent = `${device.alias || device.name} · ${deviceReadingIsFresh(device) ? "在线" : "离线"}`;
      option.selected = Number(device.id) === Number(
        pendingDeviceSelections.get(type) ?? selectedDevice?.id
      );
      select.appendChild(option);
    });
  const bind = document.createElement("button");
  bind.type = "button";
  bind.className = "secondary";
  bind.textContent = selectedDevice ? "确认更换" : "绑定本批次";
  bind.disabled = !select.value;
  select.onchange = () => {
    if (select.value) {
      pendingDeviceSelections.set(type, Number(select.value));
    } else {
      pendingDeviceSelections.delete(type);
    }
    bind.disabled = !select.value;
  };
  bind.onclick = async () => {
    bind.disabled = true;
    await selectProcessDevice(Number(select.value), type);
  };
  actions.append(select, bind);
  picker.append(copy, actions);
  parent.appendChild(picker);
}

async function selectProcessDevice(deviceId, type) {
  try {
    await mutate(
      `/api/experiments/${state.experiment.id}/device-bindings`,
      {
        device_id:deviceId,
        selected_at_ms:Date.now(),
        ...eventPayload(state.experiment.operator, `select-${type}`)
      }
    );
    openDevicePickers.delete(type);
    pendingDeviceSelections.delete(type);
    toast(`已绑定本批次${DEVICE_TYPE_LABELS[type] || "设备"}。`);
    await loadDetail();
  } catch (error) {
    toast(error.message, true);
    if (!viewedStepCode && !preserveStepFormDuringLiveUpdate()) {
      renderCurrentStep();
    }
  }
}

function renderStepDeviceSurface(parent, step) {
  const types = STEP_DEVICE_TYPES[step] || [];
  if (!types.length) return [];
  const surface = document.createElement("div");
  surface.className = "step-device-surface";
  const header = document.createElement("div");
  header.className = "capture-header";
  const title = document.createElement("strong");
  title.textContent = "当前设备数据";
  const source = document.createElement("span");
  source.className = "capture-source";
  source.textContent = "设备自动采集";
  header.append(title, source);
  surface.appendChild(header);

  for (const type of types) {
    const candidates = devicesOfType(type);
    const device = selectedDeviceForType(type);
    if (
      candidates.length > 1
      && (!device || openDevicePickers.has(type))
    ) {
      renderDevicePicker(surface, type, candidates, device);
      if (!device) continue;
    }
    const row = document.createElement("div");
    row.className = "step-device-row";
    const identity = document.createElement("div");
    identity.className = "step-device-identity";
    const online = deviceReadingIsFresh(device);
    const name = document.createElement("strong");
    name.textContent = device?.alias || device?.name || DEVICE_TYPE_LABELS[type] || type;
    const status = document.createElement("span");
    status.className = online ? "online" : "offline";
    status.textContent = online ? "在线" : device?.latest ? "数据已中断" : "离线";
    identity.append(name, status);
    if (device && candidates.length > 1 && !openDevicePickers.has(type)) {
      const change = document.createElement("button");
      change.type = "button";
      change.className = "device-change";
      change.textContent = "更换";
      change.onclick = () => {
        openDevicePickers.add(type);
        renderCurrentStep();
      };
      identity.appendChild(change);
    }
    const metrics = document.createElement("div");
    metrics.className = "step-live-metrics";
    const relevant = (device ? deviceMetrics(device) : []).filter(([,value]) =>
      value !== null && value !== undefined && value !== ""
    ).slice(0, 3);
    if (relevant.length) {
      relevant.forEach(([label,value,unit]) => {
        const metric = document.createElement("div");
        metric.innerHTML = `<span></span><b></b>`;
        metric.querySelector("span").textContent = label;
        metric.querySelector("b").textContent = formatDeviceValue(value, unit);
        metrics.appendChild(metric);
      });
    } else {
      const empty = document.createElement("p");
      empty.textContent = online ? "等待稳定读数…" : "完成步骤时可展开人工补录";
      metrics.appendChild(empty);
    }
    row.append(identity, metrics);
    surface.appendChild(row);
  }
  if (["R201-40","R201-50"].includes(step) && (state.temperature_series || []).length) {
    const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    chart.setAttribute("class", "trend capture-trend");
    chart.setAttribute("viewBox", "0 0 600 150");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "反应温度实时趋势");
    surface.appendChild(chart);
    drawTrend(
      chart,
      state.temperature_series.map(point => ({x:point.ts_ms,y:Number(point.value),valid:true})),
      "℃",
      [
        state.experiment.spec_snapshot?.reaction_temp_min_c,
        state.experiment.spec_snapshot?.reaction_temp_max_c
      ]
    );
  }
  parent.appendChild(surface);
  return missingDeviceBindings(step);
}

function createStepField(key, labelText, type, fallback = false) {
  const wrap = document.createElement("div");
  wrap.className = `field${type === "textarea" ? " full" : ""}`;
  const label = document.createElement("label");
  label.textContent = fallback ? `${labelText}（人工补录）` : labelText;
  let input;
  if (key === "appearance") {
    input = document.createElement("select");
    ["澄清均一","轻微浑浊","分层","沉淀或絮状物","其他异常"].forEach(value => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      input.appendChild(option);
    });
  } else if (type === "textarea") {
    input = document.createElement("textarea");
  } else {
    input = document.createElement("input");
    input.type = type;
    if (type === "number") input.step = "any";
  }
  input.name = key;
  input.dataset.label = labelText;
  input.required = !fallback;
  if (fallback) input.dataset.autoFallback = "true";
  if (key === "acc_volume_unit") input.value = "mL";
  if (key === "filter_spec") input.value = "G4+0.45µm PTFE";
  wrap.append(label, input);
  return wrap;
}

function appendManualFallback(form, step) {
  const autoFields = AUTO_STEP_FIELDS[step] || new Set();
  if (!autoFields.size || step === "R201-01") return;
  const details = document.createElement("details");
  details.className = "field full manual-fallback";
  details.id = "manualFallback";
  const summary = document.createElement("summary");
  summary.textContent = "设备离线？人工补录";
  const note = document.createElement("p");
  note.textContent = "正常情况下无需填写。只有设备没有实时数据时，才在这里补录并保留人工来源标记。";
  const fields = document.createElement("div");
  fields.className = "form-grid";
  for (const [key, labelText, type] of (STEP_FIELDS[step] || [])) {
    if (!autoFields.has(key)) continue;
    fields.appendChild(createStepField(key, labelText, type, true));
  }
  details.append(summary, note, fields);
  form.appendChild(details);
}

function appendCompleteButton(form, text, disabled = false) {
  const actions = document.createElement("div");
  actions.className = "field full action-row";
  const complete = document.createElement("button");
  complete.type = "submit";
  complete.className = "primary";
  complete.textContent = text;
  complete.disabled = disabled;
  actions.appendChild(complete);
  form.appendChild(actions);
}

function average(values) {
  const valid = values
    .filter(value => value !== null && value !== undefined && value !== "")
    .map(Number)
    .filter(Number.isFinite);
  if (!valid.length) return null;
  return valid.reduce((sum, value) => sum + value, 0) / valid.length;
}

function deviceReadingIsFresh(device) {
  const latest = device?.latest;
  if (!latest || latest.state === "offline") return false;
  const timestamp = Number(latest.ts_ms);
  return Number.isFinite(timestamp) && Date.now() - timestamp <= 15000;
}

function environmentReading() {
  const device = selectedDeviceForType("whd46");
  const latest = device?.latest;
  if (!deviceReadingIsFresh(device)) return {device, temp:null, humidity:null};
  const metrics = latest.metrics || {};
  return {
    device,
    temp:latest.temp_c != null && Number.isFinite(Number(latest.temp_c))
      ? Number(latest.temp_c)
      : null,
    humidity:average([
      metrics.ch1_humid_rh,
      metrics.ch2_humid_rh,
      metrics.ch3_humid_rh
    ])
  };
}

function readingCard(label, value, unit, source) {
  const card = document.createElement("div");
  card.className = "reading";
  const caption = document.createElement("span");
  caption.textContent = label;
  const number = document.createElement("b");
  number.textContent = `${value.toFixed(1)} ${unit}`;
  const detail = document.createElement("small");
  detail.textContent = source;
  card.append(caption, number, detail);
  return card;
}

function appendNumberField(parent, name, labelText) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  const label = document.createElement("label");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = "number";
  input.step = "0.1";
  input.name = name;
  input.required = true;
  input.dataset.label = labelText;
  wrap.append(label, input);
  parent.appendChild(wrap);
}

function renderEnvironmentStep(form) {
  const reading = environmentReading();
  const sourceName = reading.device?.alias || reading.device?.name || "WHD46";
  if (reading.temp != null && reading.humidity != null) {
    form.dataset.environmentTemp = String(reading.temp);
    form.dataset.environmentHumidity = String(reading.humidity);
    const readings = document.createElement("div");
    readings.className = "field full reading-grid";
    readings.append(
      readingCard("环境温度", reading.temp, "℃", `${sourceName} 自动读取`),
      readingCard("环境湿度", reading.humidity, "%RH", `${sourceName} 三通道平均值`)
    );
    form.appendChild(readings);
  } else {
    const fallback = document.createElement("div");
    fallback.className = "field full fallback-box";
    const message = document.createElement("p");
    message.textContent = "WHD46 暂无实时数据。设备恢复后会自动填入；如需继续，可临时补录一次。";
    const fields = document.createElement("div");
    fields.className = "form-grid";
    appendNumberField(fields, "environment_temp_c", "临时环境温度 ℃");
    appendNumberField(fields, "environment_humidity_rh", "临时环境湿度 %RH");
    fallback.append(message, fields);
    form.appendChild(fallback);
  }
  const status = document.createElement("div");
  status.className = "field full confirm-item";
  const devices = state.available_devices || [];
  const online = devices.filter(item => item.latest && item.latest.state !== "offline").length;
  status.innerHTML = `<strong>设备状态已自动记录</strong><span>${online}/${devices.length} 台在线</span>`;
  form.appendChild(status);
  appendCompleteButton(form, "确认环境与设备，进入下一步");
}

function renderGlasswareStep(form) {
  const items = ["两口烧瓶","冷凝管","搅拌子","接收容器","注射器"];
  const list = document.createElement("div");
  list.className = "field full quick-confirm";
  items.forEach(name => {
    const row = document.createElement("div");
    row.className = "confirm-item";
    const title = document.createElement("strong");
    title.textContent = name;
    const status = document.createElement("span");
    status.textContent = "确认干燥";
    row.append(title, status);
    list.appendChild(row);
  });
  form.appendChild(list);
  appendCompleteButton(form, "器皿均已干燥，进入下一步");
}

function materialTarget(name) {
  const normalized = name.toUpperCase();
  return (state.recipe_parameters || []).find(parameter => {
    const code = String(parameter.parameter_code || "").toUpperCase();
    const display = String(parameter.display_name || "").toUpperCase();
    return code.includes(normalized) || display.includes(normalized);
  }) || null;
}

function renderMaterialStep(form) {
  const intro = document.createElement("p");
  intro.className = "field full material-note";
  intro.textContent = "物料名称由体系自动带入。只填写实际用量；批号可扫码或稍后补录。";
  form.appendChild(intro);
  const list = document.createElement("div");
  list.className = "field full material-list";
  const names = MATERIAL_PRESETS[state.experiment.membrane_system] || [];
  names.forEach((name, index) => {
    const target = materialTarget(name);
    const unit = target?.unit || "mL";
    const row = document.createElement("div");
    row.className = "material-row";
    row.dataset.materialName = name;
    row.dataset.materialUnit = unit;
    row.dataset.theoretical = target?.target_value ?? "";
    const identity = document.createElement("div");
    identity.className = "material-name";
    const title = document.createElement("strong");
    title.textContent = name;
    const theory = document.createElement("small");
    theory.textContent = target?.target_value == null
      ? `单位 ${unit}`
      : `配方量 ${target.target_value} ${unit}`;
    identity.append(title, theory);

    const actualWrap = document.createElement("div");
    actualWrap.className = "field";
    const actualLabel = document.createElement("label");
    actualLabel.textContent = `实际用量（${unit}）`;
    const actual = document.createElement("input");
    actual.type = "number";
    actual.step = "any";
    actual.min = "0";
    actual.name = `material_actual_${index}`;
    actual.dataset.label = `${name} 的实际用量`;
    actual.required = true;
    if (target?.target_value != null) actual.value = target.target_value;
    actualWrap.append(actualLabel, actual);

    const lotWrap = document.createElement("div");
    lotWrap.className = "field";
    const lotLabel = document.createElement("label");
    lotLabel.textContent = "批号（可选/可扫码）";
    const lot = document.createElement("input");
    lot.type = "text";
    lot.name = `material_lot_${index}`;
    lot.placeholder = "可稍后补录";
    lotWrap.append(lotLabel, lot);
    row.append(identity, actualWrap, lotWrap);
    list.appendChild(row);
  });
  form.appendChild(list);
  appendCompleteButton(form, "确认物料，进入下一步");
}

function collectStepResult(form, step) {
  if (step === "R201-01") {
    if (form.dataset.environmentTemp && form.dataset.environmentHumidity) {
      return {};
    }
    const temp = Number(form.dataset.environmentTemp || form.elements.environment_temp_c?.value);
    const humidity = Number(form.dataset.environmentHumidity || form.elements.environment_humidity_rh?.value);
    if (!Number.isFinite(temp)) throw new Error("WHD46 暂无数据，请临时补录环境温度");
    if (!Number.isFinite(humidity)) throw new Error("WHD46 暂无数据，请临时补录环境湿度");
    return {
      environment_temp_c:temp,
      environment_humidity_rh:humidity,
      device_checks:(state.available_devices || []).map(item => item.alias || item.name)
    };
  }
  if (step === "R201-02") {
    return {
      glassware_items:["两口烧瓶","冷凝管","搅拌子","接收容器","注射器"]
        .map(name => ({name,dry:true}))
    };
  }
  if (step === "R201-03") {
    const materials = [];
    form.querySelectorAll("[data-material-name]").forEach((row, index) => {
      const name = row.dataset.materialName;
      const actual = Number(form.elements[`material_actual_${index}`].value);
      if (!Number.isFinite(actual) || actual <= 0) {
        throw new Error(`请填写 ${name} 的实际用量`);
      }
      const theoretical = Number(row.dataset.theoretical);
      materials.push({
        name,
        lot:form.elements[`material_lot_${index}`].value.trim(),
        theoretical:Number.isFinite(theoretical) && row.dataset.theoretical !== "" ? theoretical : null,
        actual,
        unit:row.dataset.materialUnit
      });
    });
    return {materials};
  }
  if (step === "R201-50") {
    if (!state.endpoint_ready) throw new Error("粘度终点条件尚未满足");
    return {endpoint_confirmed:true};
  }
  const result = {};
  for (const [key, , type] of (STEP_FIELDS[step] || [])) {
    const input = form.elements[key];
    if (!input) continue;
    if (input.dataset.autoFallback === "true" && !String(input.value).trim()) {
      continue;
    }
    if (type === "checkbox") {
      if (input.required && !input.checked) throw new Error(`请确认：${input.dataset.label}`);
      result[key] = input.checked;
    } else {
      if (input.required && !String(input.value).trim()) {
        throw new Error(`请填写：${input.dataset.label}`);
      }
      result[key] = type === "number" ? Number(input.value) : input.value.trim();
    }
  }
  return result;
}

async function startCurrentStep() {
  const exp = state.experiment;
  if (missingDeviceBindings(exp.current_step_code).length) {
    toast("请先选择本批次现场使用的设备。", true);
    return;
  }
  try {
    await mutate(
      `/api/experiments/${exp.id}/steps/${exp.current_step_code}/start`,
      {row_version:exp.row_version, ...eventPayload(exp.operator,"start")}
    );
    clearStepDraft(exp.current_step_code);
    toast("步骤已开始，时间戳已写入。");
    await loadDetail();
  } catch (error) { toast(error.message, true); }
}

async function completeCurrentStep(event) {
  event.preventDefault();
  const exp = state.experiment;
  const form = event.currentTarget;
  if (missingDeviceBindings(exp.current_step_code).length) {
    toast("请先选择本批次现场使用的设备。", true);
    return;
  }
  try {
    const result = collectStepResult(form, exp.current_step_code);
    const preview = await api(
      `/api/experiments/${exp.id}/steps/${exp.current_step_code}/completion-preview`,
      {
        method:"POST",
        body:JSON.stringify({
          row_version:exp.row_version,
          result,
          ...eventPayload(exp.operator,"preview-complete")
        })
      }
    );
    if (preview.missing_fields?.length) {
      const labels = preview.missing_fields.map(field => FIELD_LABELS[field] || field);
      showStepMissingData(form, labels);
      return;
    }
    if (preview.findings?.length) {
      showStepFindings(form, preview);
      return;
    }
    await finishStepCompletion(form, preview.result, []);
  } catch (error) {
    if (error.details?.code === "FINDING_CONFIRMATION_REQUIRED") {
      showStepFindings(form, error.details);
      return;
    }
    toast(error.message, true);
  }
}

function showStepMissingData(form, labels) {
  const panel = form.querySelector("#stepFindingPanel");
  panel.className = "field full inline-finding";
  panel.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = "设备没有提供完成本步骤所需的数据";
  const text = document.createElement("p");
  text.textContent = `缺少：${labels.join("、")}。请检查设备连接；确需继续时再使用人工补录。`;
  panel.append(title, text);
  const fallback = form.querySelector("#manualFallback");
  if (fallback) {
    fallback.open = true;
    fallback.scrollIntoView({behavior:"smooth",block:"center"});
  }
}

function showStepFindings(form, preview) {
  const findings = preview.findings || [];
  const panel = form.querySelector("#stepFindingPanel");
  panel.className = "field full inline-finding";
  panel.innerHTML = "";
  const title = document.createElement("strong");
  title.textContent = "数据超出建议范围";
  const list = document.createElement("div");
  list.className = "finding-list";
  findings.forEach(item => {
    const row = document.createElement("p");
    row.textContent = `${item.description}：当前 ${item.actual_value || "—"}，建议 ${item.standard_value || "—"}`;
    list.appendChild(row);
  });
  const actions = document.createElement("div");
  actions.className = "action-row";
  const inspect = document.createElement("button");
  inspect.className = "secondary";
  inspect.type = "button";
  inspect.textContent = "返回检查";
  inspect.onclick = () => {
    panel.className = "field full hidden";
    panel.textContent = "";
    const fallback = form.querySelector("#manualFallback");
    if (fallback) fallback.open = true;
  };
  const confirm = document.createElement("button");
  confirm.className = "warning-action";
  confirm.type = "button";
  confirm.textContent = "数据无误，记录异常";
  confirm.onclick = () => finishStepCompletion(
    form,
    preview.result,
    findings.map(item => item.rule_code)
  );
  actions.append(inspect, confirm);
  panel.append(title, list, actions);
  panel.scrollIntoView({behavior:"smooth",block:"center"});
}

async function finishStepCompletion(form, result, confirmedFindingCodes) {
  const exp = state.experiment;
  try {
    await mutate(
      `/api/experiments/${exp.id}/steps/${exp.current_step_code}/complete`,
      {
        row_version:exp.row_version,
        result,
        require_finding_confirmation:true,
        confirmed_finding_codes:confirmedFindingCodes,
        ...eventPayload(exp.operator,"complete")
      }
    );
    form.dataset.skipDraftSave = "true";
    clearStepDraft(exp.current_step_code);
    toast(
      confirmedFindingCodes.length
        ? "数据已确认，异常与处置已写入时间轴。"
        : "步骤已完成，自动数据已写入时间轴。"
    );
    await loadDetail();
  } catch (error) {
    if (error.details?.code === "FINDING_CONFIRMATION_REQUIRED") {
      showStepFindings(form, error.details);
      return;
    }
    toast(error.message, true);
  }
}

function deviceMetrics(device) {
  const latest = device.latest;
  const metrics = latest?.metrics || {};
  if (device.type === "tyd02") {
    return [
      ["加酸速率", metrics.inject_rate, "mL/min"],
      ["累计加入量", latest?.acc_volume, latest?.acc_unit || "mL"],
      ["目标加入量", metrics.target_volume, metrics.target_unit || "mL"],
      ["运行进度", latest?.progress_pct, "%"]
    ];
  }
  if (device.type === "stirrer") {
    return [
      ["实际温度", latest?.temp_c, "℃"],
      ["实际转速", metrics.speed, "rpm"],
      ["设定温度", metrics.set_temp, "℃"],
      ["设定转速", metrics.set_speed, "rpm"]
    ];
  }
  if (device.type === "viscometer") {
    return [
      ["粘度", metrics.viscosity_mPas, "mPa·s"],
      ["样品温度", latest?.temp_c, "℃"],
      ["扭矩", metrics.torque_pct, "%"],
      ["剪切速率", metrics.shear_rate_1s, "1/s"]
    ];
  }
  if (device.type === "whd46") {
    return [
      ["平均温度", latest?.temp_c, "℃"],
      ["平均湿度", average([
        metrics.ch1_humid_rh,
        metrics.ch2_humid_rh,
        metrics.ch3_humid_rh
      ]), "%RH"],
      ["1 通道温度", metrics.ch1_temp_c, "℃"],
      ["1 通道湿度", metrics.ch1_humid_rh, "%RH"]
    ];
  }
  return [["设备状态", latest?.state ? 1 : null, ""]];
}

function formatDeviceValue(value, unit) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${number.toFixed(2)}${unit ? ` ${unit}` : ""}`;
}

function renderDevices(devices) {
  const box = byId("devices");
  box.textContent = "";
  for (const dev of devices) {
    const card = document.createElement("div");
    card.className = "device";
    const top = document.createElement("div");
    top.className = "device-top";
    const name = document.createElement("div");
    name.innerHTML = `<div class="device-name"></div><div class="device-type"></div>`;
    name.querySelector(".device-name").textContent = dev.alias || dev.name;
    name.querySelector(".device-type").textContent = DEVICE_TYPE_LABELS[dev.type] || dev.type;
    const online = document.createElement("span");
    const readingFresh = deviceReadingIsFresh(dev);
    online.className = readingFresh ? "online" : "offline";
    online.textContent = readingFresh
      ? STATUS_LABELS[dev.latest?.state || "running"] || dev.latest?.state
      : dev.latest ? "数据已中断" : "离线";
    top.append(name, online);
    const live = document.createElement("div");
    live.className = "live";
    const metrics = deviceMetrics(dev);
    metrics.forEach(([label,value,unit]) => {
      const item = document.createElement("div");
      item.className = "metric";
      const strong = document.createElement("b");
      strong.textContent = formatDeviceValue(value, unit);
      const small = document.createElement("span");
      small.textContent = label;
      item.append(strong,small);
      live.appendChild(item);
    });
    card.append(top,live);
    box.appendChild(card);
  }
  if (!devices.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "当前没有已配置设备，仍可记录人工步骤，但设备数据将标记缺失。";
    box.appendChild(empty);
  }
}

async function recordViscosity(event) {
  event.preventDefault();
  const viscosityForm = event.currentTarget;
  const form = new FormData(viscosityForm);
  const data = {};
  for (const [key,value] of form.entries()) {
    if (!String(value).trim()) continue;
    data[key] = ["rotor"].includes(key) ? value : Number(value);
  }
  try {
    await mutate(
      `/api/experiments/${experimentId}/measurements/viscosity`,
      {...data,source_type:"device_confirmed",...eventPayload(state.experiment.operator,"viscosity")}
    );
    viscosityForm.reset();
    toast("粘度读数已锁定。");
    await loadDetail();
  } catch (error) {
    const field = String(error.message).match(/^([a-z0-9_]+) is required$/)?.[1];
    if (field) {
      byId("viscosityFallback").open = true;
      toast(`设备没有提供${FIELD_LABELS[field] || field}，请检查连接或人工补录。`, true);
      return;
    }
    toast(error.message, true);
  }
}

function renderMeasurements() {
  const rows = byId("viscosityRows");
  rows.textContent = "";
  const items = state.measurements.filter(v => v.measurement_type === "viscosity");
  for (const item of items) {
    const v = item.values;
    const tr = document.createElement("tr");
    [
      fmtTime(item.effective_at_ms),
      v.viscosity_mpas,
      v.torque_pct,
      v.sample_temp_c,
      `${v.rotor}/${v.rpm}`,
      item.source_type === "device_confirmed" ? "设备自动" : "人工补录",
      item.valid ? "✓" : "无效"
    ]
      .forEach(value => { const td=document.createElement("td");td.textContent=value ?? "—";tr.appendChild(td); });
    rows.appendChild(tr);
  }
  const badge = byId("endpointBadge");
  badge.textContent = state.endpoint_ready ? "终点条件已满足" : "未达终点";
  badge.className = `status${state.endpoint_ready ? " released" : ""}`;
  drawTrend(
    byId("viscosityTrend"),
    items.map(item => ({
      x:item.effective_at_ms,
      y:Number(item.values.viscosity_mpas),
      valid:item.valid
    })),
    "mPa·s",
    [
      state.experiment.target_viscosity_min_mpas,
      state.experiment.target_viscosity_max_mpas
    ]
  );
}

function renderTimeline() {
  const box = byId("timeline");
  box.textContent = "";
  const events = state.events
    .filter(item => item.event_type !== "data_source_bound")
    .sort((a,b) => b.effective_at_ms - a.effective_at_ms)
    .slice(0,40);
  if (!events.length) { box.textContent = "暂无事件"; return; }
  for (const item of events) {
    const row = document.createElement("div");
    row.className = "timeline-item";
    const time = document.createElement("div");
    time.className = "timeline-time";
    time.textContent = new Date(item.effective_at_ms).toLocaleTimeString("zh-CN",{hour12:false});
    const marker = document.createElement("div");
    marker.className = "timeline-marker";
    const content = document.createElement("div");
    content.className = "timeline-content";
    const title = document.createElement("b");
    const eventStep = item.payload?.step_code;
    if (item.event_type === "step_started" && eventStep) {
      title.textContent = (STEP_ACTIONS[eventStep]?.start || "开始步骤").replace("并打标","");
    } else if (item.event_type === "step_completed" && eventStep) {
      title.textContent = (STEP_ACTIONS[eventStep]?.complete || "完成步骤").replace("并打标","");
    } else {
      title.textContent = EVENT_LABELS[item.event_type] || item.event_type;
    }
    content.appendChild(title);
    const automatic = automaticEventSummary(item);
    if (automatic) {
      const captured = document.createElement("span");
      captured.className = "timeline-capture";
      captured.textContent = automatic;
      content.appendChild(captured);
    }
    const meta = document.createElement("small");
    const clockLabel = item.clock_sync_status === "trusted"
      ? "时间已校准"
      : item.clock_sync_status === "untrusted"
        ? "时间待确认"
        : "未校准";
    meta.textContent = `${item.actor} · ${clockLabel}`;
    content.appendChild(meta);
    row.append(time,marker,content);
    box.appendChild(row);
  }
}

function automaticEventSummary(item) {
  const result = item.payload?.result;
  const provenance = result?.data_provenance || {};
  const fields = Object.keys(provenance).filter(field =>
    result[field] !== null && result[field] !== undefined && field !== "device_checks"
  ).slice(0, 4);
  if (fields.length) {
    return `自动生成：${fields.map(field =>
      `${FIELD_LABELS[field] || field} ${result[field]}`
    ).join(" · ")}`;
  }
  if (item.event_type !== "step_started") return "";
  const types = STEP_DEVICE_TYPES[item.payload?.step_code] || [];
  const capture = item.payload?.device_capture || {};
  const devices = capture.devices || [];
  const summaries = [];
  for (const type of types) {
    const role = PROCESS_ROLE_BY_DEVICE_TYPE[type];
    const selectedDeviceId = capture.role_device_ids?.[role];
    const candidates = devices.filter(value =>
      value.type === type && value.snapshot?.state !== "offline"
    );
    const device = selectedDeviceId != null
      ? candidates.find(value =>
        String(value.device_id) === String(selectedDeviceId)
      )
      : candidates.length === 1 ? candidates[0] : null;
    if (!device) continue;
    const snapshot = device.snapshot;
    const metrics = snapshot.metrics || {};
    if (type === "tyd02") {
      if (snapshot.acc_volume != null) summaries.push(`累计量 ${snapshot.acc_volume} ${snapshot.acc_unit || "mL"}`);
      if (metrics.inject_rate != null) summaries.push(`速度 ${metrics.inject_rate} mL/min`);
    } else if (type === "stirrer") {
      if (metrics.speed != null) summaries.push(`转速 ${metrics.speed} rpm`);
      if (snapshot.temp_c != null) summaries.push(`温度 ${snapshot.temp_c} ℃`);
    } else if (type === "viscometer" && metrics.viscosity_mPas != null) {
      summaries.push(`粘度 ${metrics.viscosity_mPas} mPa·s`);
    }
    if (summaries.length) summaries.push(`来源 ${device.alias || device.name}`);
  }
  return summaries.length ? `自动捕获：${summaries.slice(0, 4).join(" · ")}` : "";
}

async function openDeviation() {
  const description = prompt("请描述偏差或异常：");
  if (!description) return;
  const immediate = prompt("立即采取的措施（可留空）：") || "";
  try {
    await mutate(
      `/api/experiments/${experimentId}/deviations`,
      {
        ...eventPayload(state.experiment.operator, "manual-deviation"),
        description,
        immediate_action:immediate,
        opened_by:state.experiment.operator,
        severity:"warning"
      }
    );
    toast("偏差已记录，原始数据未被修改。");
    await loadDetail();
  } catch (error) { toast(error.message, true); }
}

function renderDeviations() {
  const box = byId("deviations");
  box.textContent = "";
  if (!state.deviations.length) { box.textContent = "暂无偏差"; return; }
  state.deviations.forEach(item => {
    const card = document.createElement("div");
    card.className = "device";
    const title = document.createElement("b");
    title.textContent = item.deviation_no;
    const desc = document.createElement("p");
    desc.style.margin = "6px 0 0";
    desc.textContent = `${item.description} · ${item.status === "closed" ? "已处置" : "待处置"}`;
    card.append(title,desc);
    if (item.status !== "closed") {
      const resolve = document.createElement("button");
      resolve.className = "secondary";
      resolve.style.marginTop = "8px";
      resolve.textContent = "评估并处置";
      resolve.onclick = () => resolveDeviation(item);
      card.appendChild(resolve);
    }
    box.appendChild(card);
  });
}

async function resolveDeviation(item) {
  const impact = prompt("影响评估（必填）：");
  if (!impact) return;
  const cause = prompt("初步原因（可留空）：") || "";
  const dispositionText = prompt(
    "处置方式：请输入“继续”“返工”或“终止”",
    "继续"
  ) || "";
  const disposition = {继续:"continue",返工:"rework",终止:"terminate"}[dispositionText] || dispositionText;
  let reworkStepCode = null;
  if (disposition === "rework") {
    reworkStepCode = prompt(
      "返工步骤代码（例如 R201-30）：",
      state.experiment.current_step_code
    ) || "";
    if (!reworkStepCode) return;
  }
  try {
    await mutate(
      `/api/experiments/${experimentId}/deviations/${item.id}/resolve`,
      {
        ...eventPayload(state.experiment.reviewer || state.experiment.operator, "resolve-deviation"),
        reviewed_by:state.experiment.reviewer || state.experiment.operator,
        impact_assessment:impact,
        cause,
        disposition,
        row_version:state.experiment.row_version,
        rework_step_code:reworkStepCode
      }
    );
    toast("偏差评估与处置已写入审计链。");
    await loadDetail();
  } catch (error) { toast(error.message, true); }
}

async function submitExperiment() {
  const exp = state.experiment;
  try {
    await mutate(
      `/api/experiments/${exp.id}/submit`,
      {row_version:exp.row_version,...eventPayload(exp.operator,"submit")}
    );
    toast(exp.reviewer ? "已提交复核。" : "实验记录已锁定。");
    await loadDetail();
  } catch (error) { toast(error.message, true); }
}

async function reviewExperiment(action) {
  const exp = state.experiment;
  let disposition = action === "release" ? "合格" : "偏差待评估";
  if (action === "release") disposition = prompt("复核结论：", "合格") || "";
  try {
    await mutate(
      `/api/experiments/${exp.id}/review`,
      {
        row_version:exp.row_version,action,reviewer:exp.reviewer,disposition,
        ...eventPayload(exp.reviewer, `review-${action}`)
      }
    );
    toast(action === "release" ? "并行验证复核完成。" : "已退回修订。");
    await loadDetail();
  } catch (error) { toast(error.message, true); }
}

async function connectLive() {
  if (!experimentId || liveConnecting) return;
  liveConnecting = true;
  try {
    await api(`/api/experiments/${experimentId}/evaluate-telemetry`, {
      method:"POST",
      body:"{}"
    });
  } catch (_) {}
  const source = new EventSource(`/api/experiments/${experimentId}/stream`);
  source.addEventListener("snapshot", event => {
    const data = JSON.parse(event.data);
    byId("liveState").textContent = `实时 · ${new Date().toLocaleTimeString("zh-CN",{hour12:false})}`;
    if (data.available_devices) {
      state.available_devices = data.available_devices;
      renderDevices(data.available_devices);
    }
    state.process_status = data.process_status;
    state.temperature_series = data.temperature_series;
    state.temperature_checkpoints = data.temperature_checkpoints;
    state.reached_temperature = data.reached_temperature;
    state.telemetry_gaps = data.telemetry_gaps;
    state.telemetry_integrity_status = data.telemetry_integrity_status;
    renderCurrentStep();
    source.close();
    liveConnecting = false;
  });
  source.onerror = () => {
    byId("liveState").textContent = "实时连接重试中";
    source.close();
    liveConnecting = false;
  };
}

async function init() {
  await syncClock();
  const sessionState = await loadSession();
  if (sessionState === false) return;
  updateOutboxStatus();
  window.addEventListener("online", flushOutbox);
  await flushOutbox();
  if (experimentId) {
    byId("listView").classList.add("hidden");
    byId("detailView").classList.remove("hidden");
    byId("viscosityForm").addEventListener("submit", recordViscosity);
    byId("deviationButton").addEventListener("click", openDeviation);
    byId("releaseButton").addEventListener("click", () => reviewExperiment("release"));
    byId("returnButton").addEventListener("click", () => reviewExperiment("return"));
    byId("intermediateTraceButton").addEventListener(
      "click", () => openTraceItemDialog("intermediate")
    );
    byId("finalTraceButton").addEventListener(
      "click", () => openTraceItemDialog("final_product")
    );
    byId("retrieveTraceButton").addEventListener(
      "click", openTraceRetrieveDialog
    );
    byId("locationTraceButton").addEventListener(
      "click", openTraceLocationDialog
    );
    byId("batchTraceLabelButton").addEventListener("click", async () => {
      const batchItem = state.traceability?.items?.find(
        item => item.item_type === "batch"
      );
      if (!batchItem) {
        toast("批次身份尚未就绪，请刷新页面。", true);
        return;
      }
      const printed = batchItem.events?.some(event =>
        ["label_print_requested","label_reprint_requested"].includes(event.event_type)
      );
      await requestTraceLabels(
        [batchItem], printed ? "reprint" : "initial"
      ).catch(() => {});
    });
    byId("traceItemForm").addEventListener("submit", createTraceItems);
    byId("traceRetrieveForm").addEventListener(
      "submit", submitTraceRetrieve
    );
    byId("traceStoreForm").addEventListener("submit", submitTraceStore);
    byId("traceLocationForm").addEventListener(
      "submit", submitTraceLocation
    );
    document.querySelectorAll("[data-close-dialog]").forEach(button => {
      button.addEventListener("click", () => {
        closeTraceDialog(button.dataset.closeDialog);
      });
    });
    await loadDetail();
    setInterval(connectLive, 3000);
  } else {
    byId("createForm").addEventListener("submit", createExperiment);
    byId("refreshList").addEventListener("click", loadList);
    byId("system").addEventListener("change", async () => {
      const system = byId("system").value;
      byId("recipeNo").value = `R-${system}-001`;
      const button = byId("createButton");
      button.disabled = true;
      button.textContent = "正在生成批次号…";
      setCreateStatus("");
      try {
        await refreshNextBatchId();
        button.disabled = false;
        button.textContent = "创建并进入实验";
      } catch (error) {
        button.disabled = true;
        button.textContent = "批次号生成失败";
        setCreateStatus(`批次号生成失败：${error.message}`, "error");
        toast(error.message, true);
      }
    });
    byId("recipeNo").value = `R-${byId("system").value}-001`;
    const results = await Promise.allSettled([
      refreshNextBatchId(),
      loadList(),
    ]);
    const batchResult = results[0];
    const button = byId("createButton");
    if (batchResult.status === "fulfilled") {
      button.disabled = false;
      button.textContent = "创建并进入实验";
      if (!byId("createStatus").classList.contains("error")) {
        setCreateStatus("");
      }
    } else {
      button.disabled = true;
      button.textContent = "批次号生成失败";
      setCreateStatus(
        `无法生成批次号：${batchResult.reason?.message || "请检查本地服务后刷新页面"}`,
        "error"
      );
    }
  }
}

init().catch(error => {
  if (byId("operatorDisplay")) byId("operatorDisplay").textContent = "读取失败";
  if (byId("batchId")) byId("batchId").textContent = "生成失败";
  if (byId("createButton")) {
    byId("createButton").disabled = true;
    byId("createButton").textContent = "页面初始化失败";
  }
  setCreateStatus(`页面初始化失败：${error.message}`, "error");
  toast(error.message, true);
});
