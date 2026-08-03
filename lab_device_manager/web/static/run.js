// Run-detail page. All rendering is DOM-only (textContent) — no innerHTML
// with server data, so run/event fields cannot inject markup.
const $ = (id) => document.getElementById(id);
const fmtTs = (ms) => (ms == null) ? "-" : new Date(ms).toLocaleString();
const orDash = (v) => ((v == null || v === "") ? "-" : v);
const RUN_ID = Number(location.pathname.split("/").pop());

function kv(k, v) {
  const wrap = document.createElement("div");
  const kk = document.createElement("span"); kk.className = "k"; kk.textContent = k;
  const vv = document.createElement("span"); vv.className = "val"; vv.textContent = orDash(v);
  wrap.append(kk, vv);
  return wrap;
}

let chartInstance = null;

function chartTheme() {
  const styles = getComputedStyle(document.documentElement);
  const token = (name) => styles.getPropertyValue(name).trim();
  return {
    accent: token("--pc-accent"),
    muted: token("--pc-muted"),
    line: token("--pc-line-soft"),
  };
}

function refreshChartTheme() {
  if (!chartInstance) return;
  const colors = chartTheme();
  chartInstance.data.datasets[0].borderColor = colors.accent;
  for (const axis of ["x", "y"]) {
    chartInstance.options.scales[axis].ticks.color = colors.muted;
    chartInstance.options.scales[axis].grid.color = colors.line;
  }
  chartInstance.update("none");
}

function renderMeta(run) {
  const box = $("meta"); box.textContent = "";
  const card = document.createElement("div"); card.className = "card";
  const title = document.createElement("div"); title.className = "name"; title.textContent = "运行 #" + run.id;
  const grid = document.createElement("div"); grid.className = "kv";
  const dur = run.duration_ms == null ? "-" : (run.duration_ms >= 60000 ? (run.duration_ms/60000).toFixed(1)+" 分" : (run.duration_ms/1000).toFixed(0)+" 秒");
  grid.append(
    kv("设备", run.device_id),
    kv("开始", fmtTs(run.started_ms)),
    kv("结束", fmtTs(run.ended_ms)),
    kv("时长", dur),
    kv("状态", run.end_status),
    kv("操作人", run.operator),
    kv("项目", run.project_tag),
    kv("实验", run.experiment_tag),
    kv("本次液量", (run.actual_volume == null ? "-" : run.actual_volume) + " " + (run.actual_unit || "")),
    kv("累计液量", (run.result_acc_volume == null ? "-" : run.result_acc_volume) + " " + (run.result_acc_unit || ""))
  );
  card.append(title, grid); box.appendChild(card);
}

function renderChart(samples) {
  const ctx = $("flowChart").getContext("2d");
  const colors = chartTheme();
  if (chartInstance) { chartInstance.destroy(); }
  const labels = samples.map((s) => fmtTs(s.ts_ms));
  const data = samples.map((s) => s.flow_rate == null ? 0 : s.flow_rate);
  chartInstance = new Chart(ctx, {
    type: "line",
    data: { labels, datasets: [{ label: "流速", data, borderColor: colors.accent, tension: 0.2, pointRadius: 2 }] },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: {grid: {color: colors.line}, ticks: {color: colors.muted}},
        y: {grid: {color: colors.line}, ticks: {color: colors.muted}},
      }
    }
  });
}

document.documentElement.addEventListener("puricore:themechange", refreshChartTheme);

function renderEvents(events) {
  const tb = $("events").querySelector("tbody"); tb.textContent = "";
  const empty = $("empty");
  if (!events || !events.length) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  for (const e of events) {
    const tr = document.createElement("tr");
    const cell = (txt, cls) => { const td = document.createElement("td"); if (cls) td.className = cls; td.textContent = (txt == null || txt === "") ? "-" : txt; return td; };
    tr.appendChild(cell(fmtTs(e.ts_ms)));
    tr.appendChild(cell(e.event_type));
    tr.appendChild(cell(e.severity, "s-" + (e.severity === "critical" ? "alarm" : "stopped")));
    tb.appendChild(tr);
  }
}

function downloadPdf() {
  window.location = "/api/runs/" + RUN_ID + "/report.pdf";
}

async function load() {
  if (!RUN_ID || !Number.isFinite(RUN_ID)) return;
  let data;
  try { data = await (await fetch("/api/runs/" + RUN_ID)).json(); }
  catch (e) { return; }
  if (data.error) { $("title").textContent = "运行不存在"; return; }
  $("title").textContent = "运行 #" + data.run.id;
  renderMeta(data.run);
  renderChart(data.samples || []);
  renderEvents(data.events || []);
}

load();
