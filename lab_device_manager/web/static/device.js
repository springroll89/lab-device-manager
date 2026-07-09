// Device-detail page. All rendering is DOM-only (textContent) — no innerHTML
// with server data, so device/run fields cannot inject markup.
const $ = (id) => document.getElementById(id);
const fmtDur = (ms) => (ms == null) ? "-" : (ms >= 60000 ? (ms/60000).toFixed(1)+" 分" : (ms/1000).toFixed(0)+" 秒");
const fmtTs = (ms) => (ms == null) ? "-" : new Date(ms).toLocaleString();
const orDash = (v) => ((v == null || v === "") ? "-" : v);

// device id comes from the URL path: /device/<id>
const DEVICE_ID = Number(location.pathname.split("/").pop());

function kv(k, v) {
  const wrap = document.createElement("div");
  const kk = document.createElement("span"); kk.className = "k"; kk.textContent = k;
  const vv = document.createElement("span"); vv.className = "val"; vv.textContent = orDash(v);
  wrap.append(kk, vv);
  return wrap;
}

function renderStatus(dev, latest) {
  const box = $("status"); box.textContent = "";
  const card = document.createElement("div"); card.className = "card";
  const name = document.createElement("div"); name.className = "name"; name.textContent = dev.alias || dev.name || ("设备 " + dev.id);
  const L = latest || {};
  const st = document.createElement("div"); st.className = "v s-" + (L.state || "offline"); st.textContent = L.state || "offline";
  const flow = document.createElement("div"); flow.className = "v"; flow.textContent = (L.flow_rpm == null ? "-" : (+L.flow_rpm).toFixed(2)) + " rpm";
  // Show current-run volume (consumed_volume) and target while running/paused;
  // fall back to lifetime accumulator when stopped/offline.
  const M = (L.metrics || {});
  const running = (L.state === "running" || L.state === "paused");
  const vol = document.createElement("div"); vol.className = "v";
  if (running) {
    // Prefer target - remaining (same unit as target); fall back to consumed_volume.
    let cur = "-", u = M.target_unit || "";
    if (M.target_volume != null && L.remaining_volume != null) {
      cur = (M.target_volume - L.remaining_volume).toFixed(3);
    } else if (L.consumed_volume != null) {
      cur = L.consumed_volume;
      u = L.consumed_unit || u;
    }
    const tgt = (M.target_volume == null ? "-" : M.target_volume);
    vol.textContent = "本次 " + cur + " / " + tgt + " " + u;
  } else {
    vol.textContent = "累计 " + (L.acc_volume == null ? "-" : L.acc_volume) + " " + (L.acc_unit || "");
  }
  const temp = document.createElement("div"); temp.className = "k"; temp.textContent = "温度 " + (L.temp_c == null ? "-" : L.temp_c) + " ℃";
  const prog = document.createElement("div"); prog.className = "k"; prog.textContent = "进度 " + (L.progress_pct == null ? "-" : (+L.progress_pct).toFixed(1) + "%");
  const rtime = document.createElement("div"); rtime.className = "k"; rtime.textContent = "读数时刻 " + fmtTs(L.ts_ms);
  card.append(name, st, flow, vol, temp, prog, rtime);
  box.appendChild(card);
}

function renderConfig(latest, runs) {
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
  grid.append(
    kv("模式", latest && latest.work_mode),
    kv("注射器", M.syringe_name != null ? M.syringe_name : (sp.syringe_name || sp.syringe_code)),
    kv("目标液量", M.target_volume != null ? (M.target_volume + " " + (M.target_unit || "")) : (sp.target_volume != null ? sp.target_volume : null)),
    kv("注入速率", M.inject_rate != null ? (M.inject_rate + " " + (M.inject_rate_unit || "")) : (sp.inject_rate != null ? sp.inject_rate : null)),
    kv("暂停间隔(ms)", M.pause_delay_ms != null ? M.pause_delay_ms : sp.pause_delay_ms),
    kv("重复次数", M.repeat_count != null ? M.repeat_count : sp.repeat_count),
    kv("推力", M.force != null ? M.force : sp.force),
    kv("步长(μL/步)", M.step_length_ul_per_step != null ? M.step_length_ul_per_step.toFixed(4) : (sp.step_length_ul_per_step != null ? sp.step_length_ul_per_step : null)),
    kv("步长(mm/步)", M.step_length_mm_per_step != null ? M.step_length_mm_per_step.toFixed(6) : (sp.step_length_mm_per_step != null ? sp.step_length_mm_per_step : null)),
  );
  card.append(title, grid); box.appendChild(card);
}

function renderLifetime(latest, runs) {
  const box = $("lifetime"); box.textContent = "";
  const card = document.createElement("div"); card.className = "card";
  const title = document.createElement("div"); title.className = "k"; title.textContent = "累计液量（寿命）";
  // Lifetime total comes from the pump's accumulator, not from summing runs.
  const L = latest || {};
  const lifetimeTotal = L.acc_volume;
  const lifetimeUnit = L.acc_unit || "";
  let count = 0; let alarms = 0;
  for (const r of (runs || [])) {
    if (r.end_status === "completed") count += 1;
    if (r.alarm_count != null) alarms += r.alarm_count;
  }
  const tot = document.createElement("div"); tot.className = "v"; tot.textContent = (lifetimeTotal == null ? "-" : lifetimeTotal) + " " + lifetimeUnit;
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
    const statusCls = "s-" + (r.end_status === "alarm_abort" ? "alarm" : (r.end_status === "completed" ? "running" : "stopped"));
    tr.appendChild(cell(r.end_status, statusCls));
    tr.appendChild(cell((r.actual_volume == null ? "-" : r.actual_volume) + " " + (r.actual_unit || "")));
    tr.appendChild(cell((r.result_acc_volume == null ? "-" : r.result_acc_volume) + " " + (r.result_acc_unit || "")));
    tr.appendChild(cell(r.operator));
    tr.appendChild(cell(r.project_tag));
    tb.appendChild(tr);
  }
}

async function load() {
  if (!DEVICE_ID || !Number.isFinite(DEVICE_ID)) { return; }
  let data;
  try {
    data = await (await fetch("/api/devices/" + DEVICE_ID)).json();
  } catch (e) { return; }
  const dev = data.device || { id: DEVICE_ID };
  $("title").textContent = dev.alias || dev.name || ("设备 " + DEVICE_ID);
  renderStatus(dev, data.latest);
  renderConfig(data.latest, data.runs);
  renderLifetime(data.latest, data.runs);
  renderRuns(data.runs);
}

load();
setInterval(load, 2000);
